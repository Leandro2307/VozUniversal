# -*- coding: utf-8 -*-
"""Banco de vozes (2 por tipo de personagem) e geracao da fala NA HORA.

Motor "clonada": XTTS v2 na placa de video, clonando a amostra de cada voz do banco.
                 Fala em streaming (comeca a tocar antes de terminar de gerar).
Motor "rapida":  Piper (local, CPU) + Edge (online); serve tambem de reserva.

O tom de cada personagem e mudado reamostrando o audio (sobe/desce a voz inteira, como um
"pitch" de verdade) e a velocidade e compensada na geracao, entao o ritmo nao muda.
"""
import collections
import io
import json
import os
import queue
import re
import shutil
import threading
import time

import numpy as np

from .diagnostico import diag
from .leitor import dividir_frases
from .util import BANCO, PIPER_DIR, log, normalizar, ler_json, gravar_json

os.environ["COQUI_TOS_AGREED"] = "1"


# ----------------------------------------------------------------- texto -> fala
def expandir_abreviacoes(t):
    """"Sr. Pearson" -> "Senhor Pearson" (a voz nao le "esse erre ponto")."""
    from .leitor import ABREVIACOES, _RE_ABREV
    return _RE_ABREV.sub(lambda m: ABREVIACOES[m.group(1)], t)


def preparar_fala(texto):
    t = expandir_abreviacoes(texto)
    t = re.sub(r"[\"“”«»`´]", "", t)
    t = re.sub(r"(?<!\w)'|'(?!\w)", "", t)
    t = re.sub(r"^[\s\-–—•·*_|]+", "", t)
    t = re.sub(r"\s+[\-–—]+\s+", ", ", t)
    t = t.replace(";", ",")
    t = re.sub(r"(?<=[^\W\d_])\.(?=[^\W\d_])", ". ", t)
    t = re.sub(r"(?<=[^\W\d_])([!?,:])(?=[^\W\d_])", r"\1 ", t)
    t = re.sub(r"\s+([.,!?…:])", r"\1", t)
    t = t.replace("…", "...")
    t = re.sub(r"\.{2,}(?=\s*[^\s.!?])", ",", t)
    t = re.sub(r",\s*,+", ",", t)
    return re.sub(r"\s{2,}", " ", t).strip(" ,")


EN = set("the you and to is of it that what are this my your have be in for with was not i'm don't can will".split())
PT = set("o de que e você não é um uma para com eu meu do da se está isso os as ele ela muito mas".split())


def idioma(texto):
    p = re.findall(r"[\w']+", texto.lower())
    return "en" if sum(w in EN for w in p) > sum(w in PT for w in p) else "pt"


def aparar(wav, sr, lim=0.012, antes=0.02, depois=0.05):
    if len(wav) == 0:
        return wav
    idx = np.flatnonzero(np.abs(wav) > lim)
    if len(idx) == 0:
        return wav
    return wav[max(0, idx[0] - int(sr * antes)):min(len(wav), idx[-1] + int(sr * depois))]


def pausa_apos(frase):
    s = frase.rstrip()
    if s.endswith("..."):
        return 0.28
    if s[-1:] in "?!":
        return 0.16
    if s[-1:] in ".:":
        return 0.14
    return 0.08


class Reamostrador:
    """Muda o tom tocando o audio mais rapido/devagar, pedaco a pedaco, sem estalo nas emendas."""

    def __init__(self, fator):
        self.f = float(fator)
        self.pos = 0.0
        self.ultimo = None

    def __call__(self, x):
        if abs(self.f - 1) < 0.005:
            return x
        if len(x) == 0:
            return x
        if self.ultimo is not None:          # a ultima amostra do pedaco anterior vira o indice 0
            x = np.concatenate([[self.ultimo], x]).astype(np.float32)
        n = len(x)
        idx = np.arange(self.pos, n - 1, self.f)
        self.ultimo = x[-1]
        if len(idx) == 0:
            self.pos -= (n - 1)
            return np.zeros(0, np.float32)
        self.pos = idx[-1] + self.f - (n - 1)
        return np.interp(idx, np.arange(n), x).astype(np.float32)


def acelerar(x, fator, sr):
    """Muda a VELOCIDADE sem mudar o TOM (WSOLA): pega janelas de 30 ms em passos maiores e
    encaixa cada uma onde ela mais combina com a anterior, para nao "picotar" a voz.
    fator 1.3 = 30% mais rapido. Leva poucos milissegundos por segundo de audio."""
    x = np.asarray(x, np.float32)
    if abs(fator - 1) < 0.02 or len(x) < int(sr * 0.08):
        return x
    N = int(sr * 0.03)            # janela
    Hs = N // 2                   # passo na saida
    Ha = int(round(Hs * fator))   # passo na entrada
    tol = int(sr * 0.008)         # quanto pode escorregar para achar o melhor encaixe
    xp = np.concatenate([np.zeros(tol, np.float32), x, np.zeros(N + Ha + 2 * tol, np.float32)])
    janela = np.hanning(N).astype(np.float32)
    n_quadros = max(1, (len(x) - N) // Ha + 1)
    y = np.zeros(n_quadros * Hs + N, np.float32)
    soma = np.zeros_like(y)
    natural = None
    for k in range(n_quadros):
        pa = tol + k * Ha
        if natural is None:
            d = 0
        else:
            trecho = xp[pa - tol: pa + tol + N]
            c = np.correlate(trecho, natural, "valid")
            d = int(np.argmax(c)) - tol
        q = xp[pa + d: pa + d + N]
        ps = k * Hs
        y[ps:ps + N] += q * janela
        soma[ps:ps + N] += janela
        natural = xp[pa + d + Hs: pa + d + Hs + N]
    y = y / np.maximum(soma, 1e-3)
    return y[:int(len(x) / fator)].astype(np.float32)


class TiraSilencio:
    """O XTTS as vezes fica segundos mudo no meio da frase. Encurta qualquer silencio para no
    maximo `maximo` segundos (pausas normais entre palavras ficam como estao)."""

    def __init__(self, sr, maximo=0.45):
        self.jan = int(sr * 0.02)
        self.max_jan = int(maximo / 0.02)
        self.mudo = 0          # janelas seguidas de silencio ate agora (atravessa os pedacos)
        self.resto = np.zeros(0, np.float32)

    def __call__(self, w):
        w = np.concatenate([self.resto, w]).astype(np.float32)
        n = len(w) // self.jan
        self.resto = w[n * self.jan:]
        if n == 0:
            return np.zeros(0, np.float32)
        blocos = w[:n * self.jan].reshape(n, self.jan)
        rms = np.sqrt((blocos ** 2).mean(1))
        manter = np.ones(n, bool)
        for i, r in enumerate(rms):
            if r < 0.006:
                self.mudo += 1
                if self.mudo > self.max_jan:
                    manter[i] = False
            else:
                self.mudo = 0
        return blocos[manter].reshape(-1)

    def fim(self):
        r, self.resto = self.resto, np.zeros(0, np.float32)
        return r


class Nivelador:
    """Deixa todas as vozes no mesmo volume (as do Piper saiam no talo e as online na metade).
    Mede o volume da fala em cada pedaco e ajusta aos poucos, sem "bombear"."""

    ALVO = 0.075      # volume medio da fala (RMS), ~ -22 dB

    def __init__(self):
        self.ganho = None

    def __call__(self, w, sr):
        if len(w) == 0:
            return w
        jan = max(1, int(sr * 0.03))
        n = len(w) // jan
        if n:
            rms = np.sqrt((w[:n * jan].reshape(n, jan) ** 2).mean(1))
            voz = rms[rms > 0.008]          # so os trechos com fala (ignora silencio)
            if len(voz):
                alvo = min(4.0, max(0.3, self.ALVO / float(np.median(voz))))
                self.ganho = alvo if self.ganho is None else 0.7 * self.ganho + 0.3 * alvo
        y = w * (self.ganho or 1.0)
        a = np.abs(y)
        if a.max() > 0.8:                   # limita os picos com suavidade: nunca passa de 1.0
            y = np.where(a > 0.8, np.sign(y) * (0.8 + 0.2 * np.tanh((a - 0.8) / 0.2)), y)
        return y.astype(np.float32)


# ----------------------------------------------------------------- banco de vozes
class Banco:
    def __init__(self):
        self.arquivo = os.path.join(BANCO, "banco.json")
        self.vozes = ler_json(self.arquivo, {}) or {}

    def da_categoria(self, cat):
        return sorted(k for k, v in self.vozes.items() if v.get("categoria") == cat)

    def amostras(self, vid):
        pasta = os.path.join(BANCO, vid)
        if not os.path.isdir(pasta):
            return []
        return sorted(os.path.join(pasta, f) for f in os.listdir(pasta) if f.lower().endswith(".wav"))

    def trocar_amostra(self, vid, dados_wav):
        """Coloca uma amostra nova (as antigas vao para banco_vozes\\_antigas)."""
        pasta = os.path.join(BANCO, vid)
        os.makedirs(pasta, exist_ok=True)
        velhas = os.path.join(BANCO, "_antigas", vid + time.strftime("_%Y%m%d_%H%M%S"))
        for w in self.amostras(vid):
            os.makedirs(velhas, exist_ok=True)
            shutil.move(w, os.path.join(velhas, os.path.basename(w)))
        for lat in ("latentes.pt",):
            try:
                os.remove(os.path.join(pasta, lat))
            except FileNotFoundError:
                pass
        destino = os.path.join(pasta, "amostra.wav")
        import soundfile as sf
        wav, sr = sf.read(io.BytesIO(dados_wav), dtype="float32")
        if wav.ndim > 1:
            wav = wav.mean(1)
        sf.write(destino, wav, sr)
        return len(wav) / sr

    def editar(self, vid, **campos):
        v = self.vozes.get(vid)
        if not v:
            return None
        for k, val in campos.items():
            if val is not None and k in ("nome", "descricao", "tom", "velocidade"):
                v[k] = val
        gravar_json(self.arquivo, self.vozes)
        return v

    def resumo(self):
        out = {}
        for k, v in self.vozes.items():
            dur = 0.0
            try:
                import soundfile as sf
                dur = sum(sf.info(w).duration for w in self.amostras(k))
            except Exception:
                pass
            out[k] = dict(v, id=k, segundos=round(dur, 1))
        return out


# ----------------------------------------------------------------- motor clonado (XTTS)
class MotorClonada:
    nome = "clonada"
    # um pouco menos "solto" que o padrao (0.75): menos chance de murmurio e frase esticada
    AMOSTRAGEM = dict(temperature=0.5, top_p=0.8, top_k=40, repetition_penalty=10.0)

    def __init__(self, cfg, banco):
        self.cfg = cfg
        self.banco = banco
        self.lat = {}
        self.trava = threading.Lock()
        t0 = time.time()
        import torch
        self.dev = "cuda" if torch.cuda.is_available() else "cpu"
        if self.dev == "cuda":
            log("Carregando a voz clonada (XTTS v2) na placa de vídeo...")
            torch.set_num_threads(2)
        else:
            # sem placa NVIDIA: roda no processador (mais lento, mas funciona). Medido num
            # i7-13620H (6 nucleos de desempenho + 4 economicos): 6 threads = 0,58x do tempo real,
            # 8 = 0,50x, 14 = 0,53x. Mais que os nucleos de desempenho so atrapalha.
            try:
                import psutil
                fisicos = psutil.cpu_count(logical=False) or 4
            except Exception:
                fisicos = 4
            nucleos = max(2, min(6, fisicos - 2))
            torch.set_num_threads(nucleos)
            log(f"Carregando a voz clonada (XTTS v2) no processador ({nucleos} núcleos) — "
                "sem placa NVIDIA ela demora mais para começar a falar...")
        from TTS.api import TTS
        self._ler_modelo_mapeado(torch)
        self.torch = torch
        api = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(self.dev)
        self.modelo = api.synthesizer.tts_model
        if self.dev == "cpu":
            self._gpt_8bits(torch)
        # o carregamento deixa ~3 GB de lixo na RAM (copia do arquivo do modelo): libera
        del api
        import gc
        gc.collect()
        if self.dev == "cuda":
            torch.cuda.empty_cache()
        try:
            ctypes_ = __import__("ctypes")
            ctypes_.windll.psapi.EmptyWorkingSet(ctypes_.windll.kernel32.GetCurrentProcess())
        except Exception:
            pass
        try:
            import psutil
            log(f"Memória do programa depois de carregar a voz: {psutil.Process().memory_info().rss / 2 ** 30:.1f} GB")
        except Exception:
            pass
        self.stream_ok = True      # as amostras de cada voz sao preparadas na 1a vez que ela fala
        # aquece (a primeira geracao e lenta)
        for _ in self.gerar("Pronto.", "narrador", 1.0, 1.0):
            pass
        log(f"Voz clonada pronta em {time.time() - t0:.0f}s.", tipo="ok")

    def _gpt_8bits(self, torch):
        """Sem placa de video, 77% do tempo da voz e o GPT gerando codigo a codigo, e a cada codigo
        ele rele ~1,5 GB de pesos (com a RAM quase cheia, isso vira leitura de disco). Com os pesos
        em 8 bits (int8) fica ~4x menor: medido num i7-13620H com 8 GB, a voz foi de 0,26x para
        0,6x do tempo real e o programa de 1,9 GB para 0,9 GB."""
        try:
            import torch.nn as nn
            from transformers.pytorch_utils import Conv1D

            def trocar(mod):       # o GPT2 usa Conv1D (= Linear com o peso transposto)
                for nome, f in mod.named_children():
                    if isinstance(f, Conv1D):
                        lin = nn.Linear(f.weight.shape[0], f.weight.shape[1])
                        lin.weight = nn.Parameter(f.weight.t().contiguous())
                        lin.bias = f.bias
                        setattr(mod, nome, lin)
                    else:
                        trocar(f)

            if self.cfg.get("xtts_8bits", True):
                trocar(self.modelo.gpt)
                self.modelo.gpt = torch.ao.quantization.quantize_dynamic(
                    self.modelo.gpt, {nn.Linear}, dtype=torch.qint8)
                log("Voz clonada: modelo comprimido em 8 bits (mais rápido no processador).")
        except Exception as e:
            log("Não deu para comprimir a voz clonada (segue normal):", repr(e), tipo="aviso")

    @staticmethod
    def _ler_modelo_mapeado(torch):
        """O XTTS copia o arquivo do modelo (1,8 GB) inteiro para a memoria antes de mandar para a
        placa de video; carregando fora da linha principal (como aqui), ~3 GB ficavam presos.
        Lendo o arquivo MAPEADO do disco (mmap) nao sobra copia: o programa fica com ~1,6 GB em vez
        de ~5,5 GB, e ainda carrega mais rapido."""
        try:
            import TTS.tts.models.xtts as modulo
            if getattr(modulo, "_voz_universal_mmap", False):
                return
            original = modulo.load_fsspec

            def ler(path, map_location=None, **kw):
                if os.path.isfile(str(path)):
                    try:
                        return torch.load(str(path), map_location=map_location, mmap=True, weights_only=False)
                    except Exception:
                        pass
                return original(path, map_location=map_location, **kw)

            modulo.load_fsspec = ler
            modulo._voz_universal_mmap = True
        except Exception as e:
            log("Leitura mapeada do modelo indisponível:", repr(e), tipo="aviso")

    def _stream(self, texto, lang, gpt, spk, velocidade, sobrepor=1024):
        """Como o inference_stream do XTTS, mas com o 1o pedaco bem pequeno (8 codigos ~ 0,2 s
        de espera em vez de 0,45 s) e os seguintes grandes (gera rapido o resto da frase)."""
        import torch.nn.functional as F
        torch = self.torch
        m = self.modelo
        escala = 1.0 / max(velocidade, 0.05)
        toks = torch.IntTensor(m.tokenizer.encode(texto.strip().lower(), lang=lang.split("-")[0])).unsqueeze(0).to(m.device)
        falsos = m.gpt.compute_embeddings(gpt.to(m.device), toks)
        am = self.AMOSTRAGEM
        gerador = m.gpt.get_generator(
            fake_inputs=falsos, top_k=am["top_k"], top_p=am["top_p"], temperature=am["temperature"],
            do_sample=True, num_beams=1, num_return_sequences=1, length_penalty=1.0,
            repetition_penalty=float(am["repetition_penalty"]), output_attentions=False,
            output_hidden_states=True, return_dict_in_generate=True)
        plano = list(self.cfg.get("xtts_pedacos") or [8, 24])    # tamanho de cada pedaco (o ultimo se repete)
        n_ped = 0
        novos, todos, ant, sob, fim, alvo = [], [], None, None, False, plano[0]
        while not fim:
            try:
                x, lat = next(gerador)
                novos.append(x)
                todos.append(lat)
            except StopIteration:
                fim = True
            if todos and (fim or len(novos) >= alvo):
                g = torch.cat(todos, dim=0)[None, :]
                if escala != 1.0:
                    g = F.interpolate(g.transpose(1, 2), scale_factor=escala, mode="linear").transpose(1, 2)
                w = m.hifigan_decoder(g, g=spk.to(m.device))
                pedaco, ant, sob = m.handle_chunks(w.squeeze(), ant, sob, sobrepor)
                n_ped += 1
                novos, alvo = [], plano[min(n_ped, len(plano) - 1)]
                yield pedaco

    def latentes(self, vid):
        wavs = self.banco.amostras(vid)
        if not wavs:
            return None
        assin = tuple((os.path.basename(w), round(os.path.getmtime(w))) for w in wavs)
        c = self.lat.get(vid)
        if c and c[0] == assin:
            return c[1]
        cache = os.path.join(os.path.dirname(wavs[0]), "latentes.pt")
        torch = self.torch
        lat = None
        try:
            d = torch.load(cache, map_location=self.dev)
            if tuple(tuple(x) for x in d["assin"]) == assin:
                lat = (d["gpt"], d["spk"])
        except Exception:
            pass
        if lat is None:
            with self.trava:
                lat = self.modelo.get_conditioning_latents(audio_path=wavs, gpt_cond_len=30, max_ref_length=60)
            try:
                torch.save({"assin": assin, "gpt": lat[0], "spk": lat[1]}, cache)
            except Exception:
                pass
        self.lat[vid] = (assin, lat)
        return lat

    def _frases(self, texto):
        """Divide em frases, mas junta as curtas com a seguinte: sozinha, uma frase como "Olá!"
        faz o XTTS se perder e inventar murmurios depois dela."""
        partes = [f.strip() for f in re.split(r"(?<=[.!?…])\s+", texto) if re.search(r"\w", f)]
        out = []
        for f in partes:
            if out and (len(out[-1]) < 40 and len(out[-1]) + len(f) < 200):
                out[-1] += " " + f
            else:
                out.append(f)
        if len(out) > 1 and len(out[-1]) < 20 and len(out[-2]) + len(out[-1]) < 220:
            curta = out.pop()                    # sobrou uma curtinha no fim: vai junto da anterior
            out[-1] += " " + curta               # (antes: out[-2] += out.pop() -> IndexError)
        final = []
        for f in out:
            final += self._quebrar(f)
        return [self._sem_ponto(f) for f in final]

    @staticmethod
    def _quebrar(f, limite=180):
        """O XTTS nao aceita frase muito comprida (dava IndexError): quebra nas virgulas e, se
        ainda passar do limite, entre palavras."""
        if len(f) <= limite:
            return [f]
        partes, atual = [], ""
        for pedaco in re.split(r"(?<=[,;:])\s+", f):
            if len(pedaco) > limite:
                for w in pedaco.split():
                    if atual and len(atual) + 1 + len(w) > limite:
                        partes.append(atual)
                        atual = w
                    else:
                        atual = (atual + " " + w).strip()
                continue
            if atual and len(atual) + 1 + len(pedaco) > limite:
                partes.append(atual)
                atual = pedaco
            else:
                atual = (atual + " " + pedaco).strip()
        if atual:
            partes.append(atual)
        return partes

    @staticmethod
    def _sem_ponto(f):
        """O XTTS em portugues as vezes FALA o ponto final ("...disse ponto"). Tira os pontos
        (a pausa no fim da frase quem faz e o programa); ponto no meio vira virgula."""
        f = re.sub(r"\.+(?=[\"')]*$)", "", f.strip())               # ponto(s) no fim
        f = re.sub(r"\.+\s+(\w)", lambda m: ", " + m.group(1).lower(), f)  # ponto no meio vira virgula
        f = re.sub(r"\s*,\s*$", "", f)
        return f.strip() or "..."

    def gerar(self, texto, vid, tom, vel):
        """-> pedacos (wav float32, sr, pausa_depois). Comeca a devolver antes de terminar."""
        lat = self.latentes(vid) or self.latentes("narrador")
        if lat is None:
            raise RuntimeError(f"voz {vid} sem amostra")
        gpt, spk = lat
        torch = self.torch
        lang = idioma(texto)
        for frase in self._frases(texto):
            ream = Reamostrador(tom)
            velocidade = max(0.6, min(1.6, vel / tom))
            pausa = 0.14 if not frase.rstrip().endswith(("?", "!")) else 0.16
            # O XTTS as vezes "dispara" e gera 15 s de murmurio no lugar de 3 s de fala.
            # Limite: 1,7x o tempo normal para esse texto (~12 letras por segundo) + 1 s.
            esperado = len(normalizar(frase)) / 12.0 / velocidade
            limite = int(max(2.5, esperado * 1.7 + 1.0) * 24000)     # frase curta ("Olá!") tem folga
            if self.stream_ok:
                try:
                    feito, cortou = 0, False
                    tira = TiraSilencio(24000)
                    with self.trava, torch.inference_mode():
                        partes = self._stream(frase, lang, gpt, spk, velocidade)
                        primeiro = True
                        for p in partes:
                            w = p.detach().float().cpu().numpy().reshape(-1)
                            if primeiro:
                                w = aparar(w, 24000, depois=10.0)
                                primeiro = False
                            if feito + len(w) > limite:
                                w = w[:max(0, limite - feito)]
                                if len(w):
                                    w = w * np.linspace(1, 0, len(w), dtype=np.float32) ** 2
                                cortou = True
                            feito += len(w)
                            yield ream(tira(w)), 24000, 0.0
                            if cortou:
                                partes.close()
                                break
                    if cortou:
                        log(f"✂ Voz {vid}: cortei um trecho estranho no fim de “{frase[:40]}”", tipo="aviso")
                        diag.ev("corte", voz=vid, frase=frase, limite_s=round(limite / 24000, 2))
                    yield ream(tira.fim()), 24000, pausa
                    continue
                except Exception as e:
                    log("Streaming do XTTS falhou, gerando a frase inteira:", repr(e), tipo="aviso")
                    self.stream_ok = False
            w = None
            for tentativa in range(2):
                with self.trava, torch.inference_mode():
                    out = self.modelo.inference(frase, lang, gpt, spk, speed=velocidade,
                                                enable_text_splitting=False, **self.AMOSTRAGEM)
                w = out["wav"]
                w = (w.detach().float().cpu().numpy() if hasattr(w, "detach") else np.asarray(w)).reshape(-1)
                if len(w) <= limite:
                    break
            tira = TiraSilencio(24000)
            w = tira(aparar(w[:limite].astype(np.float32), 24000))
            yield ream(np.concatenate([w, tira.fim()])), 24000, pausa


# ----------------------------------------------------------------- motor rapido (Piper + Edge)
class MotorRapida:
    nome = "rapida"

    def __init__(self, cfg, banco):
        self.cfg = cfg
        self.banco = banco
        self.piper = {}
        self.trava = threading.Lock()
        self.cache_edge = collections.OrderedDict()
        self.pronta = threading.Event()        # vozes locais carregadas e aquecidas
        threading.Thread(target=self._precarregar, daemon=True).start()

    def _precarregar(self):
        """Carrega as vozes locais logo ao abrir e ja fala um "oi" mudo com cada uma: a 1a fala de
        verdade levava 4,4 s (medido as 03:58: a voz ainda nao tinha carregado e nem aquecido)."""
        from piper.config import SynthesisConfig
        t0 = time.time()
        for v in sorted({(d.get("rapida") or {}).get("voz") for d in self.banco.vozes.values()} - {None}):
            if v.startswith("pt_BR-"):
                try:
                    for _ in self._voz_piper(v).synthesize("Oi, tudo bem?", SynthesisConfig()):
                        pass
                except Exception as e:
                    log(f"Voz {v} não carregou: {e!r}", tipo="aviso")
        diag.ev("rapida_pronta", ms=round((time.time() - t0) * 1000))
        self.pronta.set()

    def _voz_piper(self, nome):
        if nome in self.piper:
            return self.piper[nome]
        with self.trava:
            if nome not in self.piper:
                import onnxruntime as ort
                import piper
                from pathlib import Path
                from piper import PiperVoice
                from piper.config import PiperConfig
                modelo = os.path.join(PIPER_DIR, nome + ".onnx")
                with open(modelo + ".json", encoding="utf-8") as f:
                    conf = PiperConfig.from_dict(json.load(f))
                so = ort.SessionOptions()
                so.intra_op_num_threads = 2
                so.inter_op_num_threads = 1
                sess = ort.InferenceSession(modelo, sess_options=so, providers=["CPUExecutionProvider"])
                v = PiperVoice(config=conf, session=sess, espeak_data_dir=Path(piper.__file__).parent / "espeak-ng-data",
                               download_dir=Path(PIPER_DIR))
                for _ in v.synthesize("Pronto."):
                    pass
                self.piper[nome] = v
        return self.piper[nome]

    def _gerar_piper(self, texto, voz, tom, vel):
        from piper.config import SynthesisConfig
        v = self._voz_piper(voz)
        base = v.config.length_scale or 1.0
        for frase in dividir_frases(texto):
            if not re.search(r"\w", frase):
                continue
            ritmo = 0.92 if frase.endswith("...") else 1.07 if frase.endswith("!") else 1.0
            sc = SynthesisConfig(length_scale=base / (vel * ritmo) * tom, noise_scale=0.75, noise_w_scale=0.9)
            ream = Reamostrador(tom)
            for ch in v.synthesize(frase, sc):
                w = np.asarray(ch.audio_float_array, dtype=np.float32).reshape(-1)
                yield ream(w), ch.sample_rate, pausa_apos(frase)

    async def _baixar(self, frase, voz, taxa):
        import edge_tts
        dados = bytearray()
        async for ch in edge_tts.Communicate(frase, voz, rate=taxa).stream():
            if ch["type"] == "audio":
                dados += ch["data"]
        return bytes(dados)

    def _gerar_edge(self, texto, voz, tom, vel):
        from concurrent.futures import ThreadPoolExecutor
        taxa = f"{round((vel / tom - 1) * 100):+d}%"
        frases = dividir_frases(texto)
        # todas as frases sao pedidas juntas: a 2a ja chegou quando a 1a termina de tocar
        with ThreadPoolExecutor(max_workers=4) as ex:
            futuros = [ex.submit(self._baixar_frase, f, voz, taxa) for f in frases]
            for frase, fut in zip(frases, futuros):
                par = fut.result()
                yield Reamostrador(tom)(par[0].copy()), par[1], pausa_apos(frase)

    def _baixar_frase(self, frase, voz, taxa):
        import asyncio
        import soundfile as sf
        if True:
            chave = (voz, taxa, frase)
            par = self.cache_edge.get(chave)
            if par is None:
                mp3 = None
                for limite in (6, 10):          # sem limite, uma falha da internet deixava o narrador mudo
                    try:
                        mp3 = asyncio.run(asyncio.wait_for(self._baixar(frase, voz, taxa), limite))
                        if mp3:
                            break
                    except Exception:
                        mp3 = None
                if not mp3:
                    raise RuntimeError("a voz online não respondeu")
                w, sr = sf.read(io.BytesIO(mp3), dtype="float32")
                par = (w.mean(1) if w.ndim > 1 else w, sr)
                self.cache_edge[chave] = par
                while len(self.cache_edge) > 300:
                    self.cache_edge.popitem(last=False)
            return par

    def gerar(self, texto, vid, tom, vel):
        d = self.banco.vozes.get(vid) or self.banco.vozes.get("narrador") or {}
        r = d.get("rapida") or {"motor": "piper", "voz": "pt_BR-faber-medium"}
        tom_total = float(r.get("tom", 1.0)) * tom
        vel_total = float(r.get("velocidade", 1.0)) * vel
        if r.get("motor") == "edge":
            try:
                yield from self._gerar_edge(texto, r["voz"], tom_total, vel_total)
                return
            except Exception as e:
                log("Voz online (Edge) falhou, usando a local:", repr(e), tipo="aviso")
                r = {"voz": "pt_BR-faber-medium"}
                tom_total *= 1.2
        yield from self._gerar_piper(texto, r["voz"], tom_total, vel_total)


# ----------------------------------------------------------------- narrador (fila -> som)
class Narrador:
    def __init__(self, cfg, banco, aviso=None):
        self.cfg = cfg
        self.banco = banco
        self.aviso = aviso or (lambda *a: None)     # avisa o painel do que esta falando
        self.rapida = MotorRapida(cfg, banco)
        self.clonada = None
        self.estado_clonada = "desligada"
        self.ger = 0
        self.tol = 0.0
        self.fila = queue.Queue()
        self.audio = queue.Queue()
        self.stream = None
        self.sr = None
        self.SR = 24000           # o alto-falante fica sempre nesta taxa
        self.recentes = collections.deque(maxlen=12)
        self.falando = False
        self.pag = 0              # numero da fala/legenda mais nova que chegou
        self.pular_ate = 0        # falas com numero menor que este ficaram atras demais: pula
        self.tocando = 0
        self.seq = 0              # numero de cada fala (para o diagnostico)
        self.ult_id = None
        self.ult_fim_toque = None
        self.gerando_fim = set()
        self.chegada = {}         # numero da fala -> quando ela apareceu
        self.ritmo_fala = 1.0     # velocidade em que a fala atual esta tocando
        self.com_buraco = set()
        self.sem_buraco = 0
        # quantos segundos de voz a clonada gera por segundo (medido a cada fala). Na placa de video
        # passa de 1 (nao precisa esperar); no processador fica ~0,5 e o colchao adaptativo usa isso
        self.taxa_geracao = {}
        # escolhe a voz NA HORA de gerar (o nucleo ouve a voz original do jogo e decide se e homem
        # ou mulher): fn(pag, texto, voz, tom, vel, quem, cat, visto) -> (voz, tom, vel, quem, cat) ou None
        self.resolver_voz = None
        threading.Thread(target=self._sintetizar, daemon=True).start()
        threading.Thread(target=self._tocar, daemon=True).start()

    def ligar_clonada(self, antes=None):
        if self.clonada or self.estado_clonada == "carregando":
            return
        from .util import cabe
        if not cabe("a voz clonada", ram_gb=3.5, vram_gb=3.0, contar_paginacao=True):
            # nao muda self.cfg["motor"]: motor_atual() ja usa a rapida enquanto a clonada nao
            # carrega. Antes trocava para "rapida" e, ao mexer em qualquer ajuste no painel, isso era
            # SALVO no config.json e a voz clonada nunca mais carregava sozinha.
            self.estado_clonada = "sem_memoria"
            self.aviso("motor", {})
            return

        def carregar():
            self.estado_clonada = "carregando"
            self.aviso("motor", {})
            if antes:
                try:
                    antes()                  # ex.: a visao, ANTES (as duas juntas gastavam +3 GB)
                except Exception as e:
                    log("Falha antes da voz clonada:", repr(e), tipo="aviso")
            try:
                self.clonada = MotorClonada(self.cfg, self.banco)
                # chute inicial ate medir a 1a fala (sem isso a 1a fala picotava no processador)
                self.taxa_geracao.setdefault("clonada", 0.5 if self.clonada.dev == "cpu" else 3.0)
                self.estado_clonada = "pronta"
            except Exception as e:
                self.estado_clonada = "erro"
                log("Voz clonada indisponível, usando a rápida:", repr(e), tipo="aviso")
            self.aviso("motor", {})

        threading.Thread(target=carregar, daemon=True).start()

    def motor_atual(self):
        if self.cfg.get("motor") == "clonada" and self.clonada:
            return self.clonada
        return self.rapida

    def falar(self, texto, voz, tom=1.0, vel=1.0, quem="", categoria="", nova=True, visto=None, forcar=False):
        """nova=True: comecou uma fala/legenda nova.

        Sem cortar (padrao): a fala anterior ACELERA para alcancar e a nova entra logo depois, em
        velocidade normal. Se ficar 2 falas atras, a mais velha e pulada (nao acumula atraso).
        Com "interromper_ao_avancar": corta a anterior na hora (jeito antigo)."""
        self.seq += 1
        diag.ev("fala", id=self.seq, texto=texto, voz=voz, quem=quem, nova=nova,
                espera_ms=round((time.time() - (visto or time.time())) * 1000))
        if nova:
            self.pag += 1
            self.chegada[self.pag] = time.time()
            if self.cfg.get("interromper_ao_avancar", False):
                self.tol = float(self.cfg.get("tolerancia_fim_s", 1.0))
                self.ger += 1
            else:
                self.pular_ate = max(self.pular_ate, self.pag - 1)
        self.fila.put((self.ger, self.pag, texto, voz, tom, vel, quem, categoria, visto or time.time(), forcar, self.seq))

    def calar(self):
        self.tol = 0.0
        self.ger += 1

    def _velha(self, ger, pag):
        return ger != self.ger or pag < self.pular_ate

    def _ajustar_pedacos(self, fid):
        """Auto-ajuste: se a fala teve buraco (picotou), o 1o pedaco cresce (mais folga); depois de
        10 falas seguidas sem buraco, diminui um pouco (comeca a falar mais cedo)."""
        if not self.cfg.get("auto_pedacos", True) or not self.clonada:
            return
        plano = list(self.cfg.get("xtts_pedacos") or [8, 12, 20, 32])
        p0 = plano[0]
        if fid in self.com_buraco:
            self.com_buraco.discard(fid)
            self.sem_buraco = 0
            novo = min(16, p0 + 2)
        else:
            self.sem_buraco += 1
            if self.sem_buraco < 8:
                return
            self.sem_buraco = 0
            novo = max(8, p0 - 1)
        if novo != p0:
            self.cfg["xtts_pedacos"] = [novo, novo + 4, novo + 12, 32]
            diag.ev("auto_pedacos", de=plano, para=self.cfg["xtts_pedacos"])
            log(f"⚙ Ajuste automático: 1º pedaço da voz {p0} → {novo} (evita picotar).")

    def _restante(self, pag, resto_pedaco):
        """Segundos de fala que ainda faltam desta fala (o pedaco atual + os ja gerados na fila)."""
        try:
            fila = [(p, w, s) for (_, p, w, s, _) in list(self.audio.queue)]
        except Exception:
            fila = []
        return resto_pedaco + sum(len(w) / s for p, w, s in fila if p == pag and w is not None)

    def _ritmo(self, pag, restante):
        """Velocidade de reproducao. Acelera SO se estiver atrasado de verdade: a proxima fala
        ja espera ha mais de 0,4 s E ainda falta mais de 0,7 s desta. Quanto mais a proxima
        espera, mais acelera (ate "acelerar_ate"). Fala curta ou quase no fim: termina normal."""
        if pag >= self.pag or restante < 0.7:
            return 1.0
        espera = time.time() - self.chegada.get(pag + 1, time.time())
        if espera < 0.4:
            return 1.0
        return min(float(self.cfg.get("acelerar_ate", 1.35)), 1.0 + (espera - 0.4) * 0.25)

    def _repetido(self, texto):
        agora = time.time()
        n = normalizar(texto)
        pal = [normalizar(w) for w in texto.split() if normalizar(w)]
        for n0, pal0, t0 in self.recentes:
            if agora - t0 > 12:
                continue
            if n == n0 and len(n) >= 4:
                return True      # a mesma legenda de novo (piscou na tela): nao repete
            # pedaco de uma fala dita ha pouco (o OCR leu so um trecho dela): mesma ordem, 3+ palavras
            if agora - t0 <= 6 and len(pal) >= 3 and len(n) < len(n0) and n in n0:
                return True
        self.recentes.append((n, set(pal), agora))
        return False

    def _pressa(self, visto, pag):
        if not self.cfg.get("acelerar_se_atrasado", True):
            return 1.0
        atraso = time.time() - visto
        p = 1.22 if atraso > 3.0 else 1.12 if atraso > 1.6 else 1.0
        return p

    def _sintetizar(self):
        while True:
            ger, pag, texto, voz, tom, vel, quem, cat, visto, forcar, fid = self.fila.get()
            if self._velha(ger, pag):
                diag.ev("descartada", id=fid, motivo="ficou para trás antes de gerar")
                continue
            if not forcar and self._repetido(texto):
                diag.ev("descartada", id=fid, motivo="repetida")
                continue
            if self.resolver_voz:
                try:
                    r = self.resolver_voz(pag, texto, voz, tom, vel, quem, cat, visto)
                    if r:
                        voz, tom, vel, quem, cat = r
                except Exception as e:
                    diag.ev("resolver_voz_erro", id=fid, erro=repr(e)[:160])
                if self._velha(ger, pag):
                    diag.ev("descartada", id=fid, motivo="ficou para trás enquanto ouvia a voz")
                    continue
            pressa = self._pressa(visto, pag)
            d = self.banco.vozes.get(voz, {})
            vel_total = vel * float(d.get("velocidade", 1.0)) * float(self.cfg.get("velocidade", 1.0)) * pressa
            tom_total = tom * float(d.get("tom", 1.0))
            motores = [self.motor_atual()]
            if motores[0] is not self.rapida:
                motores.append(self.rapida)
            vol = float(self.cfg.get("volume", 0.9))
            for m in motores:
                nivel = Nivelador()
                t0 = time.time()
                primeiro = True
                diag.ev("gerar_inicio", id=fid, motor=m.nome, pressa=round(pressa, 2),
                        espera_ms=round((t0 - visto) * 1000), fila=self.fila.qsize())
                k, total_audio = 0, 0.0
                try:
                    for w, sr, pausa in m.gerar(preparar_fala(texto), voz, tom_total, vel_total):
                        k += 1
                        total_audio += (len(w) / sr) + pausa
                        diag.ev("pedaco", id=fid, k=k, pronto_ms=round((time.time() - t0) * 1000),
                                audio_s=round(len(w) / sr, 3))
                        if self._velha(ger, pag):
                            break
                        w = np.concatenate([nivel(w, sr) * vol, np.zeros(int(sr * pausa), np.float32)]).astype(np.float32)
                        if not len(w):
                            continue
                        info = None
                        if primeiro:
                            info = {"texto": texto, "quem": quem or "Narrador", "categoria": cat or "narrador",
                                    "voz": voz, "voz_nome": d.get("nome", voz), "motor": m.nome,
                                    "atraso_ms": round((time.time() - visto) * 1000),
                                    "gerou_ms": round((time.time() - t0) * 1000),
                                    "pressa": round(pressa, 2),
                                    # duracao prevista da fala inteira (~12 letras/s, como no limite do XTTS)
                                    "previsto_s": len(normalizar(texto)) / 12.0 / max(0.5, vel_total),
                                    "taxa": self.taxa_geracao.get(m.nome)}
                            primeiro = False
                        self.audio.put((ger, pag, w, sr, dict(info or {}, id=fid, primeiro=info is not None)))
                    gasto = time.time() - t0
                    if total_audio >= 0.8 and not self._velha(ger, pag):
                        nova = total_audio / max(0.001, gasto)
                        ant = self.taxa_geracao.get(m.nome)
                        self.taxa_geracao[m.nome] = nova if ant is None else ant * 0.6 + nova * 0.4
                    diag.ev("gerar_fim", id=fid, total_ms=round((time.time() - t0) * 1000),
                            audio_s=round(total_audio, 2),
                            velocidade=round(total_audio / max(0.001, time.time() - t0), 2))
                    self.audio.put((ger, pag, None, 24000, {"id": fid, "fim": True}))
                    break
                except Exception as e:
                    log(f"Erro ao gerar a fala ({m.nome}):", repr(e), tipo="erro")
                    if not primeiro:
                        break

    def _abrir(self, sr):
        import sounddevice as sd
        if self.stream is not None and self.sr == sr:
            return
        if self.stream is not None:
            try:
                self.stream.close()
            except Exception:
                pass
        self.stream = sd.OutputStream(samplerate=sr, channels=1, dtype="float32", latency="low")
        self.stream.start()
        self.sr = sr

    def _escrever(self, bloco, sr):
        import sounddevice as sd
        for tentativa in (1, 2):
            try:
                self._abrir(sr)
                self.stream.write(bloco.reshape(-1, 1))
                return
            except Exception as e:
                if tentativa == 2:
                    raise
                log("Saída de áudio reiniciada:", repr(e), tipo="aviso")
                try:
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
                sd._terminate()
                sd._initialize()

    def _colchao_adaptativo(self, ger, pag, fid, wav, sr, info):
        """PC sem placa de video: a voz clonada sai a ~0,5x do tempo real. Tocando cada pedaco assim
        que fica pronto, o audio acaba antes do proximo chegar e a fala picota. Aqui, antes de
        comecar, espera ter guardado audio suficiente para tocar ATE O FIM SEM PARAR:
            guardado >= falta_gerar * (1/taxa - 1)
        Num PC rapido (taxa >= 1) nao espera nada. Desliga com "colchao_adaptativo": false."""
        taxa = info.get("taxa")
        if not self.cfg.get("colchao_adaptativo", True) or not taxa or taxa >= 1.15:
            return
        taxa = max(0.15, taxa * 0.9)                 # 10% de folga: a velocidade varia um pouco
        previsto = float(info.get("previsto_s") or 0)
        t0 = time.time()
        limite = t0 + 30
        while time.time() < limite:
            guardado, acabou = len(wav) / sr, False
            try:
                for it in list(self.audio.queue):
                    if (it[4] or {}).get("id") != fid:
                        continue
                    if it[2] is None:
                        acabou = True
                    else:
                        guardado += len(it[2]) / it[3]
            except Exception:
                return
            if acabou:
                break
            falta_gerar = max(0.0, previsto - guardado)
            if guardado >= falta_gerar * (1.0 / taxa - 1.0) * max(1.0, self.ritmo_fala):
                break
            if self._velha(ger, pag) or ger != self.ger:
                break
            time.sleep(0.02)
        esperou = time.time() - t0
        if esperou > 0.05:
            diag.ev("colchao", id=fid, esperou_ms=round(esperou * 1000), taxa=round(taxa, 2),
                    previsto_s=round(previsto, 1))

    def _tocar(self):
        while True:
            try:
                ger, pag, wav, sr, info = self.audio.get(timeout=0.02)
            except queue.Empty:
                if self.falando and time.time() - (self.ult_fim_toque or 0) > 0.4:
                    self.falando = False
                    self.aviso("calado", {})
                if self.cfg.get("manter_audio_ligado", True):
                    # manda silencio sem parar: placa/fone (Bluetooth, HDMI, USB) nunca "dorme",
                    # entao o comeco da proxima fala nao se perde
                    try:
                        self._escrever(np.zeros(int(self.SR * 0.02), np.float32), self.SR)
                    except Exception:
                        time.sleep(0.02)
                continue
            fid = (info or {}).get("id")
            if wav is None:                       # fim da fala: nao espera mais pedacos dela
                self.gerando_fim.add(fid)
                diag.ev("tocar_fim", id=fid)
                self._ajustar_pedacos(fid)
                continue
            if self._velha(ger, pag):
                continue
            self.tocando = pag
            agora = time.time()
            # BURACO: a fala ja tinha comecado, o pedaco anterior acabou de tocar e este ainda
            # nao tinha chegado -> silencio no meio da frase (o "picotado")
            if (not info.get("primeiro") and fid == self.ult_id and self.ult_fim_toque
                    and agora - self.ult_fim_toque > 0.03):
                ms_b = round((agora - self.ult_fim_toque) * 1000)
                diag.ev("buraco", id=fid, ms=ms_b)
                if ms_b >= 80:
                    self.aviso("buraco", {"ms": ms_b})
                if ms_b >= 80:                    # abaixo disso quase nao se ouve: nao mexe no ajuste
                    self.com_buraco.add(fid)
            info = info if info.get("primeiro") else None
            if info:
                # COLCHAO: antes de comecar uma fala, espera um pouquinho (ate pre_buffer_ms) pelo 2o
                # pedaco. Com a placa de video dividida (video rodando), o 2o pedaco as vezes atrasa
                # e virava um buraco logo no comeco da frase. Se ele chegar antes, comeca na hora.
                ate = time.time() + float(self.cfg.get("pre_buffer_ms", 120)) / 1000
                while time.time() < ate:
                    try:
                        if any((it[4] or {}).get("id") == fid for it in list(self.audio.queue)):
                            break
                    except Exception:
                        break
                    time.sleep(0.01)
                self._colchao_adaptativo(ger, pag, fid, wav, sr, info)
            if sr != self.SR:                     # taxa unica: trocar de taxa reabre o audio e come o comeco
                wav = np.interp(np.arange(0, len(wav), sr / self.SR), np.arange(len(wav)), wav).astype(np.float32)
                sr = self.SR
            try:
                if info:
                    diag.ev("tocar_inicio", id=fid, atraso_ms=info["atraso_ms"], fila_audio=self.audio.qsize())
                    self.falando = True
                    extra = f", +{round((info['pressa'] - 1) * 100)}%" if info.get("pressa", 1) > 1.01 else ""
                    log(f"🔊 [{info['quem']} · {info['voz_nome']}] {info['texto']}   "
                        f"(voz em {info['atraso_ms']} ms, {info['motor']}{extra})", tipo="fala")
                    self.aviso("falando", info)
                bloco = int(sr * 0.04)
                i, n, terminar, ritmo = 0, len(wav), False, 1.0
                # continua na velocidade em que o pedaco anterior DESTA fala terminou (sem vai-e-volta)
                if fid == self.ult_id and self.ritmo_fala > 1.01:
                    wav = acelerar(wav, self.ritmo_fala, sr)
                    n, ritmo = len(wav), self.ritmo_fala
                while i < n:
                    if ger != self.ger and not terminar:
                        if (n - i) / sr <= self.tol:
                            terminar = True
                        else:
                            fim = min(n, i + int(sr * 0.06))
                            self._escrever(wav[i:fim] * np.linspace(1, 0, fim - i, dtype=np.float32), sr)
                            break
                    if pag < self.pular_ate:          # ficou 2 falas atras: some suave e pula
                        fim = min(n, i + int(sr * 0.08))
                        self._escrever(wav[i:fim] * np.linspace(1, 0, fim - i, dtype=np.float32), sr)
                        log("⏭ Pulei o resto de uma fala que ficou muito atrás da tela.", tipo="aviso")
                        diag.ev("pulou", id=fid, resto_s=round((n - i) / sr, 2))
                        break
                    if (i // bloco) % 3 == 0:          # a cada 120 ms confere se precisa acelerar
                        alvo = self._ritmo(pag, self._restante(pag, (n - i) / sr) * ritmo)
                        if alvo - ritmo >= 0.05:          # dentro de uma fala so acelera, nunca desacelera
                            # muda aos poucos (10% por vez), sem tranco; o tom nao muda
                            novo = ritmo + min(0.1, alvo - ritmo)
                            wav = np.concatenate([wav[:i], acelerar(wav[i:], novo / ritmo, sr)])
                            n, ritmo = len(wav), novo
                            diag.ev("acelerou", id=fid, fator=round(novo, 2))
                    self._escrever(wav[i:i + bloco], sr)
                    i += bloco
                self.ult_id, self.ult_fim_toque, self.ritmo_fala = fid, time.time(), ritmo
            except Exception as e:
                log("Erro ao tocar:", repr(e), tipo="erro")
                time.sleep(0.5)
