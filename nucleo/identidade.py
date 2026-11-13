# -*- coding: utf-8 -*-
"""Quem esta falando? Descobre se o personagem e menino, menina, homem, mulher, idoso ou idosa
juntando tres tipos de pista, e da a ele uma voz do banco que ainda nao foi usada no jogo.

  1. o NOME   ("Vovó Apotecária", "Dona", "Rei", "Garotinha", "Vendedora", nomes terminados em -a)
  2. o TEXTO  ("estou cansada", "obrigado", "meu jovem", "quando eu crescer")
  3. a IMAGEM (CLIP: modelo que compara a tela com frases como "uma garotinha", "um velho")

Cada pista soma pontos para um sexo (m/f) e uma idade (crianca/adulto/idoso). A ficha guarda
os pontos, entao o personagem vai ficando mais certo a cada fala; quando o usuario corrige no
painel, a ficha fica travada.
"""
import hashlib
import math
import re
import threading
import time

from .util import log, sem_acento

CATEGORIAS = ["menino", "menina", "homem", "mulher", "idoso", "idosa"]
ROTULOS = {"menino": "Menino", "menina": "Menina", "homem": "Homem", "mulher": "Mulher",
           "idoso": "Idoso", "idosa": "Idosa", "narrador": "Narrador"}
SEXO = {"menino": "m", "homem": "m", "idoso": "m", "menina": "f", "mulher": "f", "idosa": "f"}
IDADE = {"menino": "crianca", "menina": "crianca", "homem": "adulto", "mulher": "adulto",
         "idoso": "idoso", "idosa": "idoso"}


def categoria_de(sexo, idade):
    return {("m", "crianca"): "menino", ("f", "crianca"): "menina", ("m", "adulto"): "homem",
            ("f", "adulto"): "mulher", ("m", "idoso"): "idoso", ("f", "idoso"): "idosa"}[(sexo, idade)]


# ----------------------------------------------------------------- pistas do nome
# (palavra -> lista de (dimensao, valor, peso)). Com acento: "vovó" e "vovô" sao pessoas diferentes.
_NOME = {}


def _pal(palavras, *efeitos):
    for p in palavras.split():
        _NOME[p.replace("_", " ")] = list(efeitos)


_pal("vovó avó vó velha velhinha anciã matriarca bruxa grandma granny", ("s", "f", 3), ("i", "idoso", 3))
_pal("vovô avô vô velho velhinho ancião sábio eremita patriarca grandpa elder sage", ("s", "m", 3), ("i", "idoso", 3))
_pal("menina garota garotinha mocinha filhinha princesinha girl", ("s", "f", 3), ("i", "crianca", 3))
_pal("menino garoto garotinho moleque guri filhinho rapazinho boy kid", ("s", "m", 3), ("i", "crianca", 3))
_pal("criança pequeno pequena pirralho pirralha child little", ("i", "crianca", 1.5))
_pal("mulher senhora dona rainha mãe mamãe mama tia irmã moça dama lady woman queen mother miss mrs madame "
     "esposa noiva viúva freira enfermeira bruxa feiticeira sacerdotisa guerreira princesa duquesa condessa",
     ("s", "f", 3), ("i", "adulto", 1.2))
_pal("homem senhor seu sr rei pai papai tio irmão moço cavalheiro man king father sir mr lord "
     "marido noivo viúvo padre guarda soldado capitão cavaleiro general príncipe duque conde barão",
     ("s", "m", 3), ("i", "adulto", 1.2))

_pal("srta sra senhorita miss mrs ms dra madame lady", ("s", "f", 3), ("i", "adulto", 1.0))
_pal("sr mr dr monsieur sir", ("s", "m", 3), ("i", "adulto", 1.0))

_FEM_SUFIXO = re.compile(r"(ora|eira|ária|aria|esa|isa|triz|ã|ina|ona)$")
_MAS_SUFIXO = re.compile(r"(or|eiro|ário|ario|ês|ão|ino|ão|ista)$")
_NEUTRAS = {"testemunha", "pessoa", "crianca", "vitima", "guarda", "sentinela", "recruta", "pirata",
            "guia", "vigia", "celebridade", "autoridade", "criatura", "figura", "sombra", "voz",
            "multidao", "plateia", "tripulacao", "gangue", "turma", "equipe", "patrulha"}


_NOMES_PROPRIOS = {}


def nomes_proprios():
    """recursos\nomes.txt: nome proprio -> 'f' ou 'm' (Susan, Abigail, Hosea, Dutch...)."""
    if not _NOMES_PROPRIOS:
        import os
        from .util import RECURSOS
        try:
            for linha in open(os.path.join(RECURSOS, "nomes.txt"), encoding="utf-8"):
                p = linha.split()
                if len(p) == 2 and p[1] in ("f", "m") and not linha.startswith("#"):
                    _NOMES_PROPRIOS[p[0].lower()] = p[1]
        except Exception:
            _NOMES_PROPRIOS["_"] = ""
    return _NOMES_PROPRIOS


def pistas_nome(nome):
    saida = []
    if not nome:
        return saida
    palavras = [p.strip(".,:;!?'\"()") for p in nome.lower().split()]
    achou = False
    for p in palavras:
        for ef in _NOME.get(p, []):
            saida.append((*ef, f"nome tem “{p}”"))
            achou = True
    if achou:
        return saida
    # nome proprio conhecido (vale o primeiro que achar: "Arthur Morgan" -> Arthur)
    lista = nomes_proprios()
    for p in palavras:
        g = lista.get(p) or lista.get(sem_acento(p))
        if g:
            return [("s", g, 2.5, f"“{p.title()}” é nome {'feminino' if g == 'f' else 'masculino'}")]
    p0 = palavras[0] if palavras else ""
    # palavras femininas na gramatica mas que servem pra homem e mulher: sem pista nenhuma
    if sem_acento(p0) in _NEUTRAS:
        return saida
    if _FEM_SUFIXO.search(p0) and len(p0) > 3:
        saida.append(("s", "f", 1.4, f"“{p0}” é palavra feminina"))
    elif _MAS_SUFIXO.search(p0) and len(p0) > 3:
        saida.append(("s", "m", 1.4, f"“{p0}” é palavra masculina"))
    else:
        ult = sem_acento(palavras[-1] if len(palavras) == 1 else p0)
        # ("y" nao entra: Tony, Billy, Jimmy, Davey, Trelawny sao homens)
        if ult.endswith(("a", "ine", "elle", "ette")):
            saida.append(("s", "f", 0.7, "nome com cara de feminino"))
        elif ult.endswith(("o", "us", "os", "or", "er", "k", "d", "n", "r")):
            saida.append(("s", "m", 0.5, "nome com cara de masculino"))
    return saida


# ----------------------------------------------------------------- pistas da fala
# verbos na 1a pessoa ("estava"/"tava" tambem sao de "ele", entao so valem com "eu" antes)
_RE_ADJ = re.compile(
    r"(?:\b(?:estou|tô|sou|fiquei|fico|ando|serei|estarei|me sinto|sinto-me|vou ficar|continuo|nasci)"
    r"|\beu (?:estava|tava|era|fui|fiquei|estou|sou))"
    r"\s+(?:muito |tão |tao |meio |bem |um pouco |super |todo |toda |completamente )?([a-zà-ÿ]{3,})s?\b")
_ADJ_CONHECIDOS = set("""cansad preocupad sozinh nov velh pront bonit louc perdid cert seri ocupad
 assustad apaixonad casad solteir orgulhos ansios curios feliz contente furios nervos chatead
 animad empolgad encantad emocionad surpres obrigad sentad deitad acordad molhad machucad ferid
 cansadinh quiet calad ric pobr magr gord fort fraco fraca bravo brav sadi doent""".split())

_PISTAS_TEXTO = [
    (r"\bobrigada\b", [("s", "f", 1.6)], "disse “obrigada”"),
    (r"\bobrigado\b", [("s", "m", 1.2)], "disse “obrigado”"),
    (r"\beu mesma\b", [("s", "f", 1.6)], "disse “eu mesma”"),
    (r"\beu mesmo\b", [("s", "m", 1.2)], "disse “eu mesmo”"),
    (r"\bsou (?:uma |a )?(?:menina|garota|garotinha)\b", [("s", "f", 3), ("i", "crianca", 3)], "disse que é menina"),
    (r"\bsou (?:um |o )?(?:menino|garoto|garotinho)\b", [("s", "m", 3), ("i", "crianca", 3)], "disse que é menino"),
    (r"\bsou (?:uma |a )?(?:velha|senhora de idade|idosa)\b", [("s", "f", 3), ("i", "idoso", 3)], "disse que é velha"),
    (r"\bsou (?:um |o )?(?:velho|idoso)\b", [("s", "m", 3), ("i", "idoso", 3)], "disse que é velho"),
    (r"\bsou (?:uma |a )mulher\b", [("s", "f", 3), ("i", "adulto", 1.5)], "disse que é mulher"),
    (r"\bsou (?:um |o )homem\b", [("s", "m", 3), ("i", "adulto", 1.5)], "disse que é homem"),
    (r"\bquando eu crescer\b|\bquando eu for grande\b|\bminha mamãe\b|\bmeu papai\b",
     [("i", "crianca", 2)], "fala como criança"),
    (r"\b(?:mamãe|papai)\b", [("i", "crianca", 0.8)], "chama mamãe/papai"),
    (r"\bminha (?:mãe|mae) (?:disse|falou|mandou|deixa|não deixa)\b", [("i", "crianca", 1.2)], "fala da mãe como criança"),
    (r"\bmeu jovem\b|\bminha jovem\b|\bjovenzinho\b|\brapazinho\b|\bminha criança\b|\bmeu filho\b.{0,30}\bvelho\b",
     [("i", "idoso", 1.5)], "trata o outro como jovem"),
    (r"\bmeus? net[oa]s?\b|\bminhas? netas?\b", [("i", "idoso", 2)], "fala dos netos"),
    (r"\bna minha idade\b|\bnos meus tempos\b|\bno meu tempo\b|\bquando eu era (?:jovem|moç[oa]|novinh[oa])\b|"
     r"\bmeus velhos ossos\b|\besses velhos olhos\b|\bjá estou velh[oa]\b|\banos atrás, quando\b",
     [("i", "idoso", 2)], "fala como pessoa idosa"),
    (r"\bmeu marido\b|\bmeu noivo\b", [("s", "f", 2), ("i", "adulto", 1)], "fala do marido"),
    (r"\bminha esposa\b|\bminha mulher\b|\bminha noiva\b", [("s", "m", 2), ("i", "adulto", 1)], "fala da esposa"),
    (r"\bmeus filhos\b|\bminha filha\b|\bmeu filho\b", [("i", "adulto", 0.7)], "fala dos filhos"),
]
_PISTAS_TEXTO = [(re.compile(r, re.I), ef, mot) for r, ef, mot in _PISTAS_TEXTO]


def pistas_texto(texto):
    saida = []
    t = " " + (texto or "").lower() + " "
    for rx, efeitos, motivo in _PISTAS_TEXTO:
        if rx.search(t):
            for ef in efeitos:
                saida.append((*ef, motivo))
    for m in _RE_ADJ.finditer(t):
        palavra = m.group(1)
        if palavra.endswith("s"):
            palavra = palavra[:-1]
        if palavra[-1:] not in "ao" or palavra.endswith("ndo"):
            continue
        raiz = palavra[:-1]
        if raiz in _ADJ_CONHECIDOS or re.search(r"(ad|id|os|inh)$", raiz) and len(raiz) >= 4:
            saida.append(("s", "f" if palavra.endswith("a") else "m", 1.0, f"falou de si: “{palavra}”"))
    return saida


# ----------------------------------------------------------------- pistas da imagem (CLIP)
_FRASES = {
    "menino": ["a little boy", "a young boy child", "a kid boy character"],
    "menina": ["a little girl", "a young girl child", "a kid girl character"],
    "homem": ["an adult man", "a young man", "a grown man with a beard"],
    "mulher": ["an adult woman", "a young woman", "a lady"],
    "idoso": ["an old man with white hair", "an elderly grandfather", "an old bearded man"],
    "idosa": ["an old woman with white hair", "an elderly grandmother", "an old lady"],
    "outro": ["a monster", "an animal", "a landscape with no people", "a menu screen"],
}


class Visao:
    """Olha a imagem do jogo com o CLIP (roda na placa de video, ~20 ms por imagem).
    O modelo (~600 MB) e baixado sozinho na primeira vez."""

    NOME_MODELO = "openai/clip-vit-base-patch32"

    def __init__(self):
        self.pronta = False
        self.carregando = False
        self.erro = ""
        self.prox_tentativa = 0.0
        self.trava = threading.Lock()

    def preparar(self):
        """Carrega so quando precisar (1o personagem novo), e so se houver memoria sobrando."""
        if self.pronta or self.carregando or time.time() < self.prox_tentativa:
            return
        from .util import cabe
        if not cabe("a visão", ram_gb=3.0, vram_gb=1.5):
            self.prox_tentativa = time.time() + 300      # tenta de novo daqui a 5 min
            return
        self.carregando = True
        threading.Thread(target=self._carregar, daemon=True).start()

    def _carregar(self):
        try:
            t0 = time.time()
            import torch
            torch.set_num_threads(2)
            from transformers import CLIPModel, CLIPProcessor
            log("Visão: carregando o modelo CLIP (a 1ª vez baixa ~600 MB)...")
            self.torch = torch
            self.dev = "cuda" if torch.cuda.is_available() else "cpu"
            # na placa ja carrega em meia precisao: ~900 MB a menos de memoria do que carregar
            # inteiro e converter depois (medido as 04:25)
            tipo = torch.float16 if self.dev == "cuda" else torch.float32
            self.modelo = CLIPModel.from_pretrained(self.NOME_MODELO, torch_dtype=tipo).to(self.dev).eval()
            self.proc = CLIPProcessor.from_pretrained(self.NOME_MODELO)
            self.chaves, frases = [], []
            for k, fs in _FRASES.items():
                for f in fs:
                    self.chaves.append(k)
                    frases.append(f"a video game character: {f}" if k != "outro" else f"a video game screenshot of {f}")
            with torch.inference_mode():
                ent = self.proc(text=frases, return_tensors="pt", padding=True).to(self.dev)
                emb = self.modelo.get_text_features(**ent)
                self.textos = emb / emb.norm(dim=-1, keepdim=True)
            self.pronta = True
            import gc
            gc.collect()
            if self.dev == "cuda":
                torch.cuda.empty_cache()
            log(f"Visão pronta em {time.time() - t0:.1f}s ({self.dev}).")
        except Exception as e:
            self.erro = repr(e)
            log("Visão indisponível (segue só com nome e texto):", repr(e), tipo="aviso")
        finally:
            self.carregando = False

    def classificar(self, imagens):
        """imagens: lista de PIL -> {categoria: probabilidade} (media das imagens), ou None."""
        if not self.pronta or not imagens:
            return None
        torch = self.torch
        with self.trava, torch.inference_mode():
            ent = self.proc(images=imagens, return_tensors="pt").to(self.dev)
            if self.dev == "cuda":
                ent["pixel_values"] = ent["pixel_values"].half()
            emb = self.modelo.get_image_features(**ent)
            emb = emb / emb.norm(dim=-1, keepdim=True)
            logit = (100.0 * emb @ self.textos.T).float()
            # media das frases de cada categoria, depois softmax entre categorias
            cats = list(_FRASES)
            por_cat = torch.stack([logit[:, [i for i, k in enumerate(self.chaves) if k == c]].mean(1) for c in cats], 1)
            prob = por_cat.softmax(1).mean(0).cpu().tolist()
        return dict(zip(cats, prob))


def pistas_visao(prob):
    """Probabilidades do CLIP -> pistas (com peso menor que nome e texto: a imagem engana)."""
    if not prob:
        return []
    pessoa = sum(v for k, v in prob.items() if k != "outro")
    if pessoa < 0.35 or max(prob[c] for c in CATEGORIAS) < 0.25:
        return []                  # imagem sem pessoa clara: nao opina
    pm = sum(prob[c] for c in ("menino", "homem", "idoso")) / pessoa
    pi = {i: sum(prob[c] for c in CATEGORIAS if IDADE[c] == i) / pessoa for i in ("crianca", "adulto", "idoso")}
    saida = []
    top = max(CATEGORIAS, key=lambda c: prob[c])
    motivo = f"imagem parece {ROTULOS[top].lower()} ({prob[top] * 100:.0f}%)"
    if abs(pm - 0.5) > 0.1:
        saida.append(("s", "m" if pm > 0.5 else "f", min(0.8, abs(pm - 0.5) * 2), motivo))
    i_top = max(pi, key=pi.get)
    if pi[i_top] > 0.65 and i_top != "adulto":
        saida.append(("i", i_top, min(0.5, (pi[i_top] - 0.6) * 2), motivo))
    return saida


# ----------------------------------------------------------------- elenco: fichas + escolha de voz
VARIACOES = [(1.0, 1.0), (1.06, 0.97), (0.94, 1.03), (1.1, 1.0), (0.9, 0.98), (1.03, 1.06), (0.97, 0.94),
             (1.13, 1.03), (0.88, 1.02), (1.08, 0.93)]
PRIOR = {"s": {"m": 0.3, "f": 0.0}, "i": {"crianca": 0.0, "adulto": 0.6, "idoso": 0.0}}


class Elenco:
    """Fichas dos personagens de um jogo (vivem no perfil do jogo)."""

    def __init__(self, perfil, banco):
        self.perfil = perfil
        self.banco = banco
        self.trava = threading.RLock()

    # -- pontos -> categoria
    @staticmethod
    def _decidir(pontos):
        s = {k: PRIOR["s"][k] + pontos.get("s", {}).get(k, 0) for k in ("m", "f")}
        i = {k: PRIOR["i"][k] + pontos.get("i", {}).get(k, 0) for k in ("crianca", "adulto", "idoso")}
        sexo = max(s, key=s.get)
        idade = max(i, key=i.get)

        def certeza(d, k):
            outros = [v for kk, v in d.items() if kk != k]
            return 1 / (1 + math.exp(-(d[k] - max(outros))))

        conf = certeza(s, sexo) * certeza(i, idade)
        return categoria_de(sexo, idade), conf

    def _voz_para(self, nome, categoria):
        """Escolhe a voz da categoria menos usada no jogo e uma variacao de tom/ritmo que
        ninguem usa ainda com essa voz -> nunca dois personagens com a mesma voz."""
        vozes = self.banco.da_categoria(categoria)
        if not vozes:
            return None, 1.0, 1.0
        pers = self.perfil.personagens
        uso = {v: 0 for v in vozes}
        usadas = set()
        for n, f in pers.items():
            if n == nome:
                continue
            if f.get("voz") in uso:
                # quem falou nos ultimos 3 min esta NA CENA: pesa muito mais (o novo nao pode ter a
                # mesma voz de quem esta conversando com ele; medido as 05:11, Anthony = Arthur)
                recente = time.time() - f.get("ultima_t", 0) < 180
                uso[f["voz"]] += 100 if recente else 1
            usadas.add((f.get("voz"), round(f.get("tom", 1), 2), round(f.get("vel", 1), 2)))
        semente = int(hashlib.md5(nome.encode("utf-8")).hexdigest(), 16)
        menor = min(uso.values())
        candidatas = [v for v in vozes if uso[v] == menor]
        voz = candidatas[semente % len(candidatas)]
        # o 1o personagem de cada voz fica com ela "pura"; os seguintes ganham uma variacao livre
        ini = 0 if uso[voz] == 0 else 1 + (semente // 7) % (len(VARIACOES) - 1)
        for k in range(len(VARIACOES)):
            tom, vel = VARIACOES[(ini + k) % len(VARIACOES)]
            if (voz, round(tom, 2), round(vel, 2)) not in usadas:
                return voz, tom, vel
        return voz, round(1.0 + (semente % 21 - 10) / 100, 2), 1.0

    # -- API
    def ficha(self, nome):
        return self.perfil.personagens.get(nome)

    def observar(self, nome, texto="", visao=None):
        """Junta as pistas desta fala na ficha do personagem. -> ficha (criada se for novo)."""
        with self.trava:
            pers = self.perfil.personagens
            novo = nome not in pers
            f = pers.setdefault(nome, {"categoria": None, "voz": None, "tom": 1.0, "vel": 1.0,
                                       "confianca": 0.0, "fixo": False, "pistas": [], "pontos": {},
                                       "falas": 0, "olhadas": 0, "primeira_vez": time.strftime("%d/%m %H:%M")})
            f["falas"] = f.get("falas", 0) + 1
            f["ultima_t"] = time.time()
            f["ultima_vez"] = time.time()
            pistas = []
            if novo:
                pistas += pistas_nome(nome)
            pistas += pistas_texto(texto)
            if visao:
                pistas += pistas_visao(visao)
                f["olhadas"] = f.get("olhadas", 0) + 1
            pontos = f.setdefault("pontos", {})
            for dim, val, peso, motivo in pistas:
                d = pontos.setdefault(dim, {})
                d[val] = round(d.get(val, 0) + peso, 2)
                if motivo not in f["pistas"]:
                    f["pistas"] = (f["pistas"] + [motivo])[-8:]
            if not f.get("fixo"):
                cat, conf = self._decidir(pontos)
                f["confianca"] = round(conf, 2)
                # troca de voz so no comeco (depois de muitas falas, so se o usuario mudar)
                # ...ou UMA vez mais tarde, se as pistas novas forem fortes (o Jack so chamou "mamãe"
                # depois de 6 falas e ficou com voz de homem adulto)
                tarde = (f["falas"] > 6 and conf >= 0.7 and not f.get("mudou_tarde") and not f.get("fixo"))
                if cat != f.get("categoria") and (f.get("categoria") is None or f["falas"] <= 6 or tarde):
                    if f["falas"] > 6:
                        f["mudou_tarde"] = True
                    if f.get("categoria"):
                        log(f"🔄 {nome}: agora parece {ROTULOS[cat].lower()} (antes {ROTULOS[f['categoria']].lower()}).")
                    f["categoria"] = cat
                    f["voz"], f["tom"], f["vel"] = self._voz_para(nome, cat)
            if not f.get("voz") or f["voz"] not in self.banco.vozes:
                f["voz"], f["tom"], f["vel"] = self._voz_para(nome, f["categoria"])
            try:
                from .diagnostico import diag
                if novo or pistas:
                    diag.ev("ficha", nome=nome, novo=novo, categoria=f["categoria"], voz=f["voz"],
                            confianca=f["confianca"], pistas=[m for *_, m in pistas], pontos=pontos)
            except Exception:
                pass
            if novo:
                v = self.banco.vozes.get(f["voz"], {})
                log(f"🆕 Personagem novo: {nome} → {ROTULOS[f['categoria']]} "
                    f"({f['confianca'] * 100:.0f}% de certeza), voz {v.get('nome', f['voz'])}"
                    + (f" [{', '.join(f['pistas'][:3])}]" if f["pistas"] else ""), tipo="novo")
            self.perfil.salvar()
            return f

    def corrigir(self, nome, categoria=None, voz=None, tom=None, vel=None):
        """O usuario corrigiu no painel: trava a ficha."""
        with self.trava:
            f = self.perfil.personagens.get(nome)
            if not f:
                return None
            if categoria and categoria in CATEGORIAS and categoria != f.get("categoria"):
                f["categoria"] = categoria
                f["voz"], f["tom"], f["vel"] = self._voz_para(nome, categoria)
            if voz and voz in self.banco.vozes:
                f["voz"] = voz
            if tom is not None:
                f["tom"] = max(0.75, min(1.35, float(tom)))
            if vel is not None:
                f["vel"] = max(0.7, min(1.4, float(vel)))
            f["fixo"] = True
            f["confianca"] = 1.0
            self.perfil.salvar()
            return f

    def esquecer(self, nome):
        with self.trava:
            self.perfil.personagens.pop(nome, None)
            self.perfil.salvar()
