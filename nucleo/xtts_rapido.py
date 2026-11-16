# -*- coding: utf-8 -*-
"""
Gerador rapido do GPT do XTTS (so na placa NVIDIA).

O gerador original (transformers) leva ~22 ms por codigo numa RTX 3060 Ti, mas a conta do modelo
em si leva ~3 ms: o resto e o Python mandando ~500 operacoes pequenas para a placa, uma a uma.
Aqui um passo inteiro (30 camadas + cabeca) e GRAVADO uma vez como "CUDA graph" e depois so
repetido, com a memoria das camadas (KV cache) fixa na placa. A amostragem (penalidade de
repeticao, temperatura, top-k, top-p) e a mesma do gerador original, na mesma ordem.
"""
import math

import torch
import torch.nn.functional as F


class GeradorRapido:
    MAX = 1024              # posicoes no cache (amostra 32 + texto <= ~200 + ate 605 codigos)

    def __init__(self, modelo):
        self.m = modelo
        g = modelo.gpt
        self.g = g
        self.gpt2 = g.gpt
        cfg = self.gpt2.config
        if getattr(cfg, "scale_attn_by_inverse_layer_idx", False) or getattr(cfg, "reorder_and_upcast_attn", False):
            raise RuntimeError("GPT2 com atencao diferente do padrao")
        if cfg.activation_function != "gelu_new":
            raise RuntimeError(f"GPT2 com ativacao {cfg.activation_function}")
        self.dev = torch.device("cuda")
        self.dtype = next(self.gpt2.h.parameters()).dtype
        self.nh = cfg.n_head
        self.hd = cfg.n_embd // cfg.n_head
        self.dim = cfg.n_embd
        self.eos = g.stop_audio_token
        self.start = g.start_audio_token
        self.max_codigos = g.max_gen_mel_tokens
        L = len(self.gpt2.h)
        shape = (1, self.nh, self.MAX, self.hd)
        self.k = [torch.zeros(shape, device=self.dev, dtype=self.dtype) for _ in range(L)]
        self.v = [torch.zeros(shape, device=self.dev, dtype=self.dtype) for _ in range(L)]
        self.tok = torch.zeros((1, 1), dtype=torch.long, device=self.dev)
        self.pos_emb = torch.zeros((1,), dtype=torch.long, device=self.dev)    # indice do codigo (0, 1, ...)
        self.pos_cache = torch.zeros((1,), dtype=torch.long, device=self.dev)  # posicao absoluta no cache
        self.ar = torch.arange(self.MAX, device=self.dev)
        self.grafo = None
        self._gravar()

    # ------------------------------------------------------------------ um passo (gravado)
    @torch.no_grad()
    def _passo(self):
        g, gpt2 = self.g, self.gpt2
        x = g.mel_embedding(self.tok) + g.mel_pos_embedding.emb(self.pos_emb).unsqueeze(0)
        x = x.to(self.dtype)
        # mascara somada (0 ate a posicao atual, -inf depois): calculada 1 vez para as 30 camadas
        mascara = torch.where(self.ar <= self.pos_cache, 0.0, -float("inf")).to(self.dtype).view(1, 1, self.MAX)
        escala = 1.0 / math.sqrt(self.hd)
        for i, b in enumerate(gpt2.h):
            h = b.ln_1(x)
            q, k, v = b.attn.c_attn(h).view(1, 3, self.nh, self.hd).unbind(1)    # (1, nh, hd) cada
            self.k[i].index_copy_(2, self.pos_cache, k.unsqueeze(2))
            self.v[i].index_copy_(2, self.pos_cache, v.unsqueeze(2))
            # atencao de 1 consulta: (nh,1,hd) x (nh,hd,MAX) -> softmax -> x (nh,MAX,hd)
            s = torch.baddbmm(mascara, q.view(self.nh, 1, self.hd), self.k[i][0].transpose(1, 2), alpha=escala)
            a = torch.bmm(s.softmax(-1), self.v[i][0])       # softmax em fp16 ja soma em fp32 por dentro
            x = x + b.attn.c_proj(a.view(1, 1, self.dim))
            m = b.mlp
            x = x + m.c_proj(F.gelu(m.c_fc(b.ln_2(x)), approximate="tanh"))   # = gelu_new do GPT2
        h = gpt2.ln_f(x).float()
        lat = g.final_norm(h[:, -1])
        logits = g.mel_head(lat)
        return lat, logits

    def _gravar(self):
        # aquece fora do grafo (os kernels precisam existir antes de gravar)
        self.pos_cache.fill_(self.MAX - 1)
        s = torch.cuda.Stream()
        s.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(s):
            for _ in range(3):
                self._passo()
        torch.cuda.current_stream().wait_stream(s)
        self.grafo = torch.cuda.CUDAGraph()
        with torch.cuda.graph(self.grafo):
            self.lat, self.logits = self._passo()
        for t in self.k + self.v:
            t.zero_()

    # ------------------------------------------------------------------ amostragem
    @staticmethod
    def _amostrar(logits, visto, temperature, top_k, top_p, rep):
        s = logits.float()
        if rep != 1.0:
            sv = s[:, visto]
            s[:, visto] = torch.where(sv < 0, sv * rep, sv / rep)
        s = s / temperature
        if top_k and top_k < s.shape[-1]:
            corte = torch.topk(s, top_k).values[:, -1:]
            s = s.masked_fill(s < corte, -float("inf"))
        if top_p < 1.0:
            ord_s, idx = torch.sort(s, descending=False)
            acum = ord_s.softmax(-1).cumsum(-1)
            tirar = acum <= (1 - top_p)
            tirar[:, -1] = False
            s = s.masked_fill(tirar.scatter(1, idx, tirar), -float("inf"))
        return torch.multinomial(s.softmax(-1), 1)          # (1, 1)

    # ------------------------------------------------------------------ gerador
    @torch.no_grad()
    def gerar(self, prefixo, temperature=0.75, top_k=50, top_p=0.85, repetition_penalty=10.0, **_):
        """prefixo = embeddings (1, P, dim) da amostra + texto (o que compute_embeddings guarda).
        Devolve (codigo (1,), latente (1, dim)) a cada passo, como o get_generator do XTTS."""
        g = self.g
        P = prefixo.shape[1]
        if P + 1 + self.max_codigos > self.MAX:
            raise RuntimeError("texto comprido demais para o gerador rapido")
        inicio = torch.tensor([[self.start]], device=self.dev)
        emb = torch.cat([prefixo.float(), g.mel_embedding(inicio) + g.mel_pos_embedding.emb(
            torch.zeros(1, dtype=torch.long, device=self.dev)).unsqueeze(0)], dim=1)
        out = self.gpt2(inputs_embeds=emb, use_cache=True, return_dict=True)
        pkv = out.past_key_values
        if hasattr(pkv, "to_legacy_cache"):
            pkv = pkv.to_legacy_cache()
        n = P + 1
        for i, (kk, vv) in enumerate(pkv):
            self.k[i][:, :, :n].copy_(kk)
            self.v[i][:, :, :n].copy_(vv)
        # o ultimo estado do prefill ja passou pelo ln_f do GPT2 (last_hidden_state)
        lat = g.final_norm(out.last_hidden_state[:, -1].float())
        logits = g.mel_head(lat)
        del out, pkv
        visto = torch.zeros(g.num_audio_tokens, dtype=torch.bool, device=self.dev)
        visto[1] = True                       # os "1" falsos do prefixo e o codigo de inicio
        visto[self.start] = True              # (o gerador original penaliza os dois tambem)
        for passo in range(self.max_codigos):
            cod = self._amostrar(logits, visto, temperature, top_k, top_p, repetition_penalty)
            visto[cod.view(-1)] = True
            fim = int(cod) == self.eos
            yield cod.view(1), lat.clone()
            if fim:
                return
            self.tok.copy_(cod)
            self.pos_emb.fill_(passo + 1)
            self.pos_cache.fill_(P + 1 + passo)
            self.grafo.replay()
            lat, logits = self.lat, self.logits
