# -*- coding: utf-8 -*-
"""Banco de vozes (2 por tipo de personagem) e geracao da fala NA HORA.

Motor "clonada": XTTS v2 na placa de video, clonando a amostra de cada voz do banco.
                 Fala em streaming (comeca a tocar antes de terminar de gerar).
Motor "rapida":  Piper (local, CPU) + Edge (online); serve tambem de reserva.
Motor "local":   so o Piper (no proprio PC, sem internet): o mais rapido e nunca depende da rede.

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
from .util import BANCO, PIPER_DIR, RECURSOS, log, normalizar, ler_json, gravar_json, semelhanca

os.environ["COQUI_TOS_AGREED"] = "1"
KOKORO = "kokoro:"                                # voz local "kokoro:pf_dora" = Kokoro, nao Piper
KOKORO_DIR = os.path.join(RECURSOS, "kokoro")


# ----------------------------------------------------------------- texto -> fala
def expandir_abreviacoes(t):
    """"Sr. Pearson" -> "Senhor Pearson" (a voz nao le "esse erre ponto")."""
    from .leitor import ABREVIACOES, _RE_ABREV
    return _RE_ABREV.sub(lambda m: ABREVIACOES[m.group(1)], t)


# "É" maiusculo perde o acento no OCR e a voz lia "e" (conjuncao): "E um assalto!" (GTA 6, 20/11).
# So vira "É" no comeco da frase e quando o que vem depois so faz sentido com o verbo.
_E_VERBO = ("claro|sério|serio|verdade|melhor|pior|foda|tarde|cedo|demais|possível|impossível|fácil|"
            "difícil|perigoso|perigosa|óbvio|normal|estranho|estranha|engraçado|engraçada|legal|errado|"
            "errada|culpa|hora|nóis|nois|tipo isso|o seguinte|isso aí|isso ai|isso mesmo|agora ou nunca|"
            "minha vez|sua vez|nossa vez|pra já|pra ja|por isso|lógico|logico|uma pena|um prazer")
# ...e estas so com a frase acabando logo depois ("E isso." = "É isso.", mas "E isso que você fez?";
# "É mesmo?", mas "E mesmo assim..."; "É meu!", mas "E meu irmão?")
_E_VERBO_FIM = "isso|mesmo|assim|sempre|bom|boa|ruim|certo|certa|justo|meu|minha|seu|sua|nosso|nossa|dele|dela"
_RE_E_VERBO = re.compile(r"(^|[.!?…]\s+)E(?=\s+(?:(?:" + _E_VERBO + r")(?=[\s.,!?…]|$)|(?:"
                         + _E_VERBO_FIM + r")\s*[.!?…]))")
# "E um/uma X!" curtinho ("E um assalto!", "E uma cilada.") tambem e "É"
_RE_E_UM = re.compile(r"(^|[.!?…]\s+)E(?=\s+uma?\s+[\wÀ-ÿ-]+(?:\s+[\wÀ-ÿ-]+)?\s*[.!?…])")
# "E seu dia de sorte." / "E minha vez agora!" (afirmacao, termina em . ou !) = "É"; pergunta
# ("E seu irmão?") continua "e" (GTA 6, 20/11 09:37)
_RE_E_POSSE = re.compile(r"(^|[.!?…]\s+)E(\s+(?:seu|sua|meu|minha|nosso|nossa|teu|tua)\s+[^.!?…]*[.!])(?=\s|$)")
# ...se o resto da frase tiver verbo proprio e "e": "E meu pai morreu ontem."
_RE_TEM_VERBO = re.compile(r"\b(?:\w+(?:ou|eu|iu|ava|avam|ia|iam|aram|eram|iram|ará|erá|irá|ando|endo|indo)|tá|está|"
                           r"estava|foi|vai|vou|vão|tem|tinha|era|é|fez|disse|quer|pode|precisa|sabe|deu|fica|ficou)\b",
                           re.I)


def _e_posse(m):
    resto = m.group(2)
    if _RE_TEM_VERBO.search(resto.split(None, 2)[-1] if len(resto.split()) > 2 else ""):
        return m.group(0)
    return m.group(1) + "É" + resto
# "Escuta, mano." lido "Escutar mano." (a virgula virou "r"): no comeco da frase, palavra terminada em
# "r" + vocativo = imperativo + virgula
_RE_VOCATIVO = re.compile(r"(^|[.!?…]\s+)([A-ZÀ-Ý][a-zà-ÿ]{2,})r(\s+)(mano|mana|cara|parceiro|parça|irmão|irmao|"
                          r"velho|véi|moça|moço|amigo|amiga|gente|galera|chefe|truta|patrão|senhor|senhora|"
                          r"querido|querida|amor|garoto|garota)(?=[\s.,!?…]|$)")


# outras palavras que o OCR (ou a legenda) deixa sem acento e a voz le errado (log de 13 mil falas, 20/11):
# "Aonde você ta indo?" (ta = "tá"), "e dai você mata" (daí)
_RE_TA = re.compile(r"\b(você|voce|cê|ce|ele|ela|eles|elas|isso|isto|tudo|que|onde|aonde|quem|não|nao|"
                    r"já|ja|aqui|ali|lá|ainda|sempre|tu|gente)(\s+)ta\b", re.I)
_RE_DAI = re.compile(r"\b([Dd])ai\b(?!-)")


def acentuar_e(t):
    t = _RE_E_VERBO.sub(r"\1É", t)
    t = _RE_E_UM.sub(r"\1É", t)
    t = _RE_E_POSSE.sub(_e_posse, t)
    t = _RE_VOCATIVO.sub(r"\1\2,\3\4", t)
    t = _RE_TA.sub(r"\1\2tá", t)
    return _RE_DAI.sub(lambda m: m.group(1) + "aí", t)


def preparar_fala(texto):
    t = acentuar_e(expandir_abreviacoes(texto))
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


# fala que comeca com interjeicao: o XTTS segura a vogal 0,45-0,7 s ("Aaaaah...", 22/11; testado com
# "Ah", "Ah!", "Ah?", "Á!", "Hm", "Hum", "Hmm": todas esticam igual, nao adianta mudar o texto)
_RE_INTERJEICAO = re.compile(r"^\W*(?:a+h+|o+h+|u+h+|h+m+|hu+m+|e+h+|hã+|ah+n)(?![\wÀ-ÿ])", re.I)


def comeca_com_interjeicao(texto):
    return bool(_RE_INTERJEICAO.match(texto or ""))


def so_interjeicao(texto):
    """ "Ah...", "Oh.", "Hm" (nada mais na fala)."""
    return comeca_com_interjeicao(texto) and not re.search(r"[^\W\d_]", _RE_INTERJEICAO.sub("", texto, count=1))


def encurtar_interjeicao(w, sr, alvo=0.3, folga=0.08, sozinha=False):
    """Encurta a 1a vogal do audio se ela passar de alvo+folga segundos: tira um trecho do MEIO
    (onde o som esta parado) com tamanho multiplo do periodo da voz e emenda com cross-fade curto,
    sem estalo e sem mudar o tom. -> audio (igual ao recebido se nao precisar)."""
    w = np.asarray(w, dtype=np.float32)
    q = int(sr * 0.01)
    if len(w) < q * 10:
        return w
    nq = len(w) // q
    blocos = w[:nq * q].reshape(nq, q)
    env = np.sqrt(np.mean(blocos ** 2, axis=1))
    zcr = np.mean(np.abs(np.diff(np.sign(blocos), axis=1)) > 0, axis=1)
    lim = max(0.01, 0.15 * float(env.max()))
    sonoro = (env > lim) & (zcr < 0.25)          # vogal/"m" (o chiado do "s" nao conta)
    ini = next((i for i in range(min(nq, 15)) if sonoro[i]), None)
    if ini is None:
        return w
    fim, buraco = ini, 0
    for i in range(ini, nq):
        if sonoro[i]:
            fim, buraco = i, 0
        else:
            buraco += 1
            if buraco >= 2:
                break
    # o "Ah" emendado na palavra seguinte sem silencio ("Ah-merda", 22/11: espremia as duas em 0,3 s
    # e a palavra sumia): a vogal acaba onde o timbre muda (>= 7 dB de diferenca media em 16 faixas)
    # (fala SO com a interjeicao: todo o som e a vogal, nao precisa procurar onde ela acaba;
    # idem se depois dela vem uma PAUSA de verdade, >= 0,1 s: "Hm... Sim.", "Ah! Lucia!". O
    # fechamento de um "d"/"t" no meio da palavra ("Ah-merd-a") dura so 30-60 ms)
    pausa = 0
    for i in range(fim + 1, nq):
        if env[i] > lim:
            break
        pausa += 1
    sozinha = sozinha or (pausa >= 10 and fim + 1 + pausa < nq)
    n2 = 2 * q
    if not sozinha and fim - ini > 8 and (fim + 1) * q + q <= len(w):
        fq = np.fft.rfftfreq(n2, 1 / sr)
        bordas = np.geomspace(100, 5000, 17)
        faixa = np.digitize(fq, bordas) - 1
        jan = np.hanning(n2)
        perfis = []
        for i in range(ini, fim + 1):
            P = np.abs(np.fft.rfft(w[i * q:i * q + n2] * jan)) ** 2 if i * q + n2 <= len(w) else None
            if P is None:
                break
            b = np.bincount(faixa[(faixa >= 0) & (faixa < 16)], P[(faixa >= 0) & (faixa < 16)], 16) + 1e-10
            lb = 10 * np.log10(b)
            perfis.append(lb - lb.mean())
        if len(perfis) > 8:
            ref = np.mean(perfis[2:7], axis=0)
            seguidos = 0
            for k, pf in enumerate(perfis):
                seguidos = seguidos + 1 if np.abs(pf - ref).mean() >= 5 else 0
                if seguidos >= 2:
                    fim = ini + k - 2
                    break
    dur = (fim - ini + 1) * 0.01
    if dur <= alvo + folga:
        return w
    alvo = max(alvo, dur / (2.5 if sozinha else 2))   # nunca espreme demais
    # o tom costuma cair durante o "aaah" (um corte so no meio nao emenda): comprime a vogal
    # inteira com WSOLA (varias emendas pequenas, cada uma no ponto em que a onda continua igual)
    borda = int(sr * 0.03)
    a, b = ini * q + borda, (fim + 1) * q - borda
    if b - a < int(sr * 0.1):
        return w
    vogal = _wsola(w[a:b], (b - a) / max(1, (b - a) - int((dur - alvo) * sr)), sr)
    f = int(sr * 0.008)
    rampa = np.linspace(0, 1, f, dtype=np.float32)
    fim_v = vogal[-f:] * (1 - rampa) + w[b:b + f] * rampa       # volta ao som original sem estalo
    return np.concatenate([w[:a], vogal[:-f], fim_v, w[b + f:]]).astype(np.float32)


def _wsola(x, taxa, sr):
    """Comprime x (taxa > 1 = mais curto) sem mudar o tom."""
    n = int(sr * 0.02)                 # janela de 20 ms
    hs = n // 2                        # passo na saida
    ha = hs * taxa                     # passo na entrada
    tol = int(sr * 0.005)
    jan = np.hanning(n + 2)[1:-1].astype(np.float32)      # sem zero nas pontas (zerava a 1a amostra: estalo)
    total = int(len(x) / taxa) + n
    y = np.zeros(total, dtype=np.float32)
    peso = np.zeros(total, dtype=np.float32)
    pos_in, pos_out, prox = 0, 0, None
    while pos_out + n <= total:
        alvo_in = int(round(pos_out / hs * ha)) if prox is not None else 0
        if alvo_in + n + tol >= len(x):
            break
        if prox is None:
            pos_in = 0
        else:
            # o trecho (perto de alvo_in) mais parecido com a continuacao natural do anterior
            lo, hi = max(0, alvo_in - tol), min(len(x) - n, alvo_in + tol)
            cand = np.lib.stride_tricks.sliding_window_view(x[lo:hi + n], n)
            k = int(np.argmax(cand @ prox))
            pos_in = lo + k
        y[pos_out:pos_out + n] += x[pos_in:pos_in + n] * jan
        peso[pos_out:pos_out + n] += jan
        prox = x[pos_in + hs:pos_in + hs + n] if pos_in + hs + n <= len(x) else x[pos_in:pos_in + n]
        pos_out += hs
    fim = pos_out + hs
    peso[peso < 1e-9] = 1.0
    return (y / peso)[:max(hs, fim - hs)]



def pausa_apos(frase):
    s = frase.rstrip()
    if s.endswith("..."):
        return 0.28
    if s[-1:] in "?!":
        return 0.16
    if s[-1:] in ".:":
        return 0.14
    return 0.08


def juntar_frases(frases, maximo=300):
    """Frases da MESMA fala geradas juntas, num pedido so: "Olha, não quero machucar ninguém. Só
    quero a grana." em dois pedidos saia com a entonacao recomecando no meio e um buraco entre as
    frases (o mesmo problema do "João foi ao... MERCADO", so que entre frases). Junta as seguidas
    ate `maximo` letras. Na voz online o 1o som nao demora mais por isso (1 a 4 frases: ~0,8 s
    nas 5 medidas de 20/11), porque ela devolve o som conforme gera."""
    out = []
    for f in frases:
        if out and len(out[-1]) + 1 + len(f) <= maximo:
            out[-1] += " " + f
        else:
            out.append(f)
    return out


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


def _biquad(tipo, f0, sr, ganho_db=0.0, q=0.707):
    """Um filtro de 2a ordem (receitas do "Audio EQ Cookbook") em formato sos do scipy."""
    A = 10 ** (ganho_db / 40)
    w0 = 2 * np.pi * f0 / sr
    c, alfa = np.cos(w0), np.sin(w0) / (2 * q)
    if tipo == "passa_alta":
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
        a = [1 + alfa, -2 * c, 1 - alfa]
    elif tipo == "pico":
        b = [1 + alfa * A, -2 * c, 1 - alfa * A]
        a = [1 + alfa / A, -2 * c, 1 - alfa / A]
    elif tipo == "grave":           # low shelf
        r = 2 * np.sqrt(A) * alfa
        b = [A * ((A + 1) - (A - 1) * c + r), 2 * A * ((A - 1) - (A + 1) * c), A * ((A + 1) - (A - 1) * c - r)]
        a = [(A + 1) + (A - 1) * c + r, -2 * ((A - 1) + (A + 1) * c), (A + 1) + (A - 1) * c - r]
    else:                           # "agudo": high shelf
        r = 2 * np.sqrt(A) * alfa
        b = [A * ((A + 1) + (A - 1) * c + r), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - r)]
        a = [(A + 1) - (A - 1) * c + r, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - r]
    return np.array(b + a, np.float64) / a[0]


class Polidor:
    """Deixa a voz do Piper mais encorpada e menos "metalica", pedaco a pedaco, sem estalo nas
    emendas (o estado dos filtros passa de um pedaco para o outro). Comparado com as vozes reais
    do banco (14/11), o Piper sai com ~12-24 dB a menos de grave (60-100 Hz), ~5 dB a menos de
    presenca (2-4,5 kHz) e, na Faber, chiado sobrando em 8-10 kHz. Corrige isso com um EQ e
    segura os picos com um compressor leve. Custa ~1 ms por segundo de audio."""

    BLOCO = 0.005             # s: o compressor mede o volume a cada 5 ms
    LIMIAR = 10 ** (-20 / 20)
    RAZAO = 2.5
    ATAQUE, SOLTURA = 0.4, 0.06   # quanto o ganho anda por bloco ao baixar / ao voltar

    def __init__(self, sr):
        from scipy.signal import sosfilt_zi
        self.sr = sr
        self.sos = np.stack([
            _biquad("passa_alta", 55, sr),                    # tira o ronco abaixo da voz
            _biquad("grave", 140, sr, +4.0, 0.7),             # corpo
            _biquad("pico", 380, sr, -1.5, 1.0),              # tira o "abafado" que o grave traria
            _biquad("pico", 2800, sr, +3.0, 0.8),             # presenca: a voz "chega na frente"
            _biquad("agudo", 7800, sr, -4.0, 0.7),            # chiado metalico do Piper
        ])
        self.zi = sosfilt_zi(self.sos) * 0.0
        self.ganho = 1.0
        self.resto = np.zeros(0, np.float32)

    def __call__(self, w):
        from scipy.signal import sosfilt
        if len(w) == 0:
            return w
        y, self.zi = sosfilt(self.sos, np.asarray(w, np.float64), zi=self.zi)
        y = np.concatenate([self.resto, y.astype(np.float32)])
        jan = max(1, int(self.sr * self.BLOCO))
        n = len(y) // jan
        self.resto = y[n * jan:]
        if n == 0:
            return np.zeros(0, np.float32)
        blocos = y[:n * jan].reshape(n, jan)
        pico = np.abs(blocos).max(1)
        alvo = np.where(pico > self.LIMIAR, (self.LIMIAR * (pico / self.LIMIAR) ** (1 / self.RAZAO)) / np.maximum(pico, 1e-9), 1.0)
        ganhos = np.empty(n + 1, np.float32)
        ganhos[0] = g = self.ganho
        for i, a in enumerate(alvo):
            g += (a - g) * (self.ATAQUE if a < g else self.SOLTURA)
            ganhos[i + 1] = g
        self.ganho = g
        # ganho variando suave dentro de cada bloco (do valor anterior ao novo): sem degrau
        rampa = np.linspace(0, 1, jan, endpoint=False, dtype=np.float32)
        g_amostra = ganhos[:-1, None] + (ganhos[1:] - ganhos[:-1])[:, None] * rampa
        return (blocos * g_amostra).reshape(-1)

    def fim(self):
        r, self.resto = self.resto * self.ganho, np.zeros(0, np.float32)
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
                anterior = self.ganho
                self.ganho = alvo if self.ganho is None else 0.7 * self.ganho + 0.3 * alvo
                if anterior is not None and abs(self.ganho - anterior) > 1e-3:
                    # o ganho DESLIZA do valor do pedaco anterior ate o novo: antes mudava de uma vez
                    # na emenda e o volume dava um degrau ("A culpa é sua" saia em pedacos, 20/11)
                    y = w * np.linspace(anterior, self.ganho, len(w), dtype=np.float32)
                    return self._limitar(y)
        return self._limitar(w * (self.ganho or 1.0))

    @staticmethod
    def _limitar(y):
        a = np.abs(y)
        if a.max() > 0.8:                   # limita os picos com suavidade: nunca passa de 1.0
            y = np.where(a > 0.8, np.sign(y) * (0.8 + 0.2 * np.tanh((a - 0.8) / 0.2)), y)
        return y.astype(np.float32)


# ----------------------------------------------------------------- banco de vozes
def prioridade_placa(cfg):
    """Pede ao Windows prioridade na fila da placa de video (como o OBS faz para nao engasgar a
    gravacao no meio do jogo). Com um jogo ocupando a placa, em "tempo real" (5) a voz clonada
    comecou em 385 ms em vez de ~530, com 1 buraco em vez de 5-7 (2 rodadas cada, as 08:30).
    "alta" (4) nao mudou nada. 0 = nao mexe. Nao precisa de administrador.
    DESLIGADO por padrao: no RDR2 de verdade a voz saiu sempre, mas o FPS do jogo despencou (a
    placa estava 93-95% ocupada pelo jogo e a voz passava na frente; 14/11 09:00)."""
    classe = int(cfg.get("prioridade_placa", 0))
    if not classe:
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x0200 | 0x0400, False, os.getpid())   # SET_INFORMATION | QUERY_INFORMATION
        r = ctypes.windll.gdi32.D3DKMTSetProcessSchedulingPriorityClass(h, classe) & 0xFFFFFFFF
        k32.CloseHandle(h)
        diag.ev("prioridade_placa", classe=classe, resposta=hex(r))
        if r:
            log(f"Não deu para pedir prioridade na placa de vídeo (código {r:#x}).", tipo="aviso")
    except Exception as e:
        diag.ev("prioridade_placa", erro=repr(e)[:120])


class ClonadaTravou(Exception):
    def __init__(self, fase, esperou):
        super().__init__(f"voz clonada sem resposta ({fase}, {esperou:.1f} s)")
        self.fase, self.esperou = fase, esperou


class Banco:
    def __init__(self):
        self.arquivo = os.path.join(BANCO, "banco.json")
        self.vozes = ler_json(self.arquivo, {}) or {}
        # tom (Hz) de cada voz, medido na amostra (para casar com o tom da voz original do jogo).
        # Arquivo separado: o banco.json e regravado pelo painel. Voz nova sem tom: mede em 2o plano.
        self.arq_tons = os.path.join(BANCO, "tons.json")
        self.tons = ler_json(self.arq_tons, {}) or {}
        if any(v not in self.tons for v in self.vozes):
            threading.Thread(target=self._medir_tons, daemon=True, name="tons").start()

    def _medir_tons(self):
        try:
            import soundfile as sf
            from .util import tom_hz
            for vid in list(self.vozes):
                if vid in self.tons:
                    continue
                partes = []
                for w in self.amostras(vid):
                    x, sr = sf.read(w, dtype="float32", always_2d=True)
                    x = x.mean(1)
                    partes.append(np.interp(np.arange(0, len(x), sr / 16000), np.arange(len(x)), x).astype(np.float32))
                self.tons[vid] = tom_hz(np.concatenate(partes), 16000) if partes else None
            gravar_json(self.arq_tons, self.tons)
        except Exception as e:
            diag.ev("tons_erro", erro=repr(e)[:160])

    def tom(self, vid):
        return self.tons.get(vid)

    def da_categoria(self, cat):
        """Vozes que o sorteio pode usar. As "exclusivas" (ex.: Lucia) ficam de fora: so vao para
        o personagem com o nome delas ou para quem o usuario escolher no painel. As "desligadas"
        (botao "não usar" no painel: voz que soou mal) tambem."""
        return sorted(k for k, v in self.vozes.items()
                      if v.get("categoria") == cat and not v.get("exclusiva") and not v.get("desligada"))

    def do_personagem(self, nome):
        """Voz exclusiva reservada para este nome de personagem (campo "personagens" do banco.json)."""
        # sem acento e sem pontuacao em volta: o OCR le "Lúcia"/"Lucia" e as vezes pega o ":"
        from .util import sem_acento
        n = sem_acento((nome or "").strip(" .,:;!?'\"()").casefold())
        for k, v in self.vozes.items():
            if any(n == sem_acento(p.casefold()) for p in v.get("personagens", [])):
                return k
        return None

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
        self.tons.pop(vid, None)               # amostra nova: o tom dela e medido de novo
        threading.Thread(target=self._medir_tons, daemon=True, name="tons").start()
        return len(wav) / sr

    def editar(self, vid, **campos):
        v = self.vozes.get(vid)
        if not v:
            return None
        for k, val in campos.items():
            if val is not None and k in ("nome", "descricao", "tom", "velocidade"):
                v[k] = val
            elif val is not None and k == "desligada":
                if val:
                    v["desligada"] = True
                else:
                    v.pop("desligada", None)
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
        if self.dev == "cuda":
            prioridade_placa(self.cfg)
            if self.cfg.get("xtts_sem_cudnn", True):
                # sem a biblioteca cuDNN: -0,2 GB de RAM e a mesma velocidade (medido as 08:20)
                torch.backends.cudnn.enabled = False
        self._ler_modelo_mapeado(torch)
        self.torch = torch
        api = None
        self.modelo = self._carregar_na_placa(torch) if self.dev == "cuda" else None
        if self.modelo is None:
            from .util import TRAVA_IMPORTACAO
            with TRAVA_IMPORTACAO:
                from TTS.api import TTS
            api = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(self.dev)
            self.modelo = api.synthesizer.tts_model
        if self.dev == "cpu":
            self._gpt_8bits(torch)
        self.meio = False
        if self.dev == "cuda" and self.cfg.get("xtts_fp16", True):
            # GPT em meia precisao (fp16): 380 dos 467 milhoes de pesos ficam com metade do tamanho
            # (~0,7 GB a menos na placa). Com um jogo pesado (RDR2) a placa enchia, o Windows jogava
            # a voz para a memoria compartilhada e ela caia de 2x para 0,3x do tempo real (7 s de atraso).
            try:
                self._transformador_fp16(torch)
                self.meio = True
            except Exception as e:
                log("Não deu para usar meia precisão na voz clonada (segue normal):", repr(e), tipo="aviso")
        self.rapido = None
        if self.dev == "cuda" and self.cfg.get("xtts_grafo", True):
            # passo do GPT gravado como CUDA graph: ~22 ms -> poucos ms por codigo (xtts_rapido.py)
            try:
                from .xtts_rapido import GeradorRapido
                self.rapido = GeradorRapido(self.modelo)
                log("Voz clonada: gerador rápido ligado (CUDA graph).")
            except Exception as e:
                self.rapido = None
                log("Gerador rápido indisponível (segue no normal):", repr(e)[:160], tipo="aviso")
        self.leve = False
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
        self.stream_ok = True      # as amostras de cada voz sao preparadas na 1a vez que ela fala
        # aquece (a primeira geracao e lenta)
        for _ in self.gerar("Pronto.", "narrador", 1.0, 1.0):
            pass
        if self.dev == "cuda" and self.cfg.get("xtts_leve", True):
            # DEPOIS de aquecer: antes disso a RAM nao caia (ficava 3,3 GB em vez de 1,8; 16/11)
            try:
                self._tirar_preparo_da_placa(torch)
                self.leve = True
                self._limpar_placa()
            except Exception as e:
                log("Não deu para deixar a voz clonada mais leve (segue normal):", repr(e), tipo="aviso")
        try:
            import psutil
            log(f"Memória do programa depois de carregar a voz: {psutil.Process().memory_info().rss / 2 ** 30:.1f} GB")
        except Exception:
            pass
        log(f"Voz clonada pronta em {time.time() - t0:.0f}s.", tipo="ok")

    PASTA_XTTS = os.path.expandvars(r"%LOCALAPPDATA%\tts\tts_models--multilingual--multi-dataset--xtts_v2")

    def _carregar_na_placa(self, torch):
        """Monta o XTTS JA na placa de video e le os pesos mapeados do disco direto para ela. Antes
        (TTS.api) ele montava tudo na RAM e depois copiava: pico de 6,0 GB de RAM e 25 s; assim o
        pico e 4,5 GB (a maior parte e o arquivo mapeado, que o Windows solta sem gravar no disco)
        e carrega em 12 s (medido as 08:20). Sem o modelo baixado ainda -> None (usa o TTS.api, que baixa)."""
        pasta = self.PASTA_XTTS
        if not all(os.path.isfile(os.path.join(pasta, f)) for f in ("config.json", "model.pth", "vocab.json")):
            return None
        try:
            from .util import TRAVA_IMPORTACAO
            with TRAVA_IMPORTACAO:
                from TTS.tts.configs.xtts_config import XttsConfig
                from TTS.tts.models.xtts import Xtts
                import TTS.tts.models.xtts as modulo
            original = modulo.load_fsspec

            def ler(path, map_location=None, **kw):
                if os.path.isfile(str(path)):
                    try:
                        return torch.load(str(path), map_location="cuda", mmap=True, weights_only=False)
                    except Exception:
                        pass
                return original(path, map_location=map_location, **kw)

            modulo.load_fsspec = ler
            cfg = XttsConfig()
            cfg.load_json(os.path.join(pasta, "config.json"))
            with torch.device("cuda"):
                m = Xtts.init_from_config(cfg)
            m.load_checkpoint(cfg, checkpoint_dir=pasta, use_deepspeed=False)
            m.cuda()
            m.eval()                  # o eval() do XTTS nao devolve o modelo (retorna None)
            return m
        except Exception as e:
            log("Carga direta na placa falhou, usando o jeito antigo:", repr(e)[:160], tipo="aviso")
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
            return None

    def _transformador_fp16(self, torch):
        """So o transformador do GPT (GPT2, 30 camadas) em fp16; o resto do XTTS segue em fp32. A
        entrada vira fp16 e a saida volta a fp32 nas bordas dele. (Com torch.autocast a voz TRAVAVA
        dentro do driver quando o jogo enchia a placa; assim nao travou - medido as 20:20.)"""
        gpt2 = self.modelo.gpt.gpt
        gpt2.half()
        # o XTTS troca a posicao do GPT2 por zeros em fp32 (somados a entrada, voltavam tudo a fp32)
        dim = gpt2.config.n_embd
        gpt2.wpe = lambda r: torch.zeros((r.shape[0], r.shape[1], dim), device=r.device, dtype=torch.float16)

        def entrada(_mod, args, kwargs):
            for k in ("inputs_embeds", "encoder_hidden_states"):
                v = kwargs.get(k)
                if v is not None and v.dtype == torch.float32:
                    kwargs[k] = v.half()
            return args, kwargs

        def saida(_mod, _args, _kwargs, out):
            if hasattr(out, "last_hidden_state") and out.last_hidden_state is not None:
                out.last_hidden_state = out.last_hidden_state.float()
                if getattr(out, "hidden_states", None) is not None:
                    out.hidden_states = tuple(h.float() for h in out.hidden_states)
                return out
            if isinstance(out, tuple) and out and torch.is_tensor(out[0]):
                return (out[0].float(),) + tuple(out[1:])
            return out

        gpt2.register_forward_pre_hook(entrada, with_kwargs=True)
        gpt2.register_forward_hook(saida, with_kwargs=True)

    def _modulos_preparo(self):
        """Pecas do XTTS que so servem para preparar a amostra de uma voz NOVA (os latentes de cada
        voz ficam salvos no disco, em latentes.pt). O text_head so e usado no treino."""
        m = self.modelo
        return (m.gpt.conditioning_encoder, m.gpt.conditioning_perceiver,
                m.hifigan_decoder.speaker_encoder, m.gpt.text_head)

    def _tirar_preparo_da_placa(self, torch):
        """Deixa na placa so o que fala. Medido (RTX 3060 Ti, 16/11): placa 1,09 -> 0,86 GB usados
        e o programa 3,3 -> 1,8 GB de RAM, mesma velocidade. Voz nova sem latentes.pt: as pecas
        voltam para a placa so durante o preparo (latentes())."""
        m = self.modelo
        # o XTTS descobre onde roda pelo 1o peso (o conditioning_encoder, que vai para a CPU)
        m.__class__ = type("XttsLeve", (m.__class__,), {"device": property(lambda s: torch.device("cuda"))})
        for mod in self._modulos_preparo():
            mod.to("cpu")

    def _limpar_placa(self):
        """O PyTorch guarda para si a memoria da placa que a fala usou e nao devolve: depois de 3
        falas ficava 2,0 GB reservado para 0,9 GB em uso. Devolvendo no fim de cada fala fica 1,2 GB,
        sem perder velocidade (medido 16/11). Com o jogo enchendo a placa, e isso que evita o
        Windows jogar a voz para a memoria compartilhada (lenta)."""
        if self.dev == "cuda" and self.cfg.get("xtts_devolver_placa", True):
            try:
                self.torch.cuda.empty_cache()
            except Exception:
                pass

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
        if self.rapido is not None:
            gerador = self.rapido.gerar(m.gpt.gpt_inference.cached_prefix_emb, **am)
        else:
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
                todos.append(lat.float())
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
                if getattr(self, "leve", False):
                    for mod in self._modulos_preparo():
                        mod.to("cuda")
                try:
                    lat = self.modelo.get_conditioning_latents(audio_path=wavs, gpt_cond_len=30, max_ref_length=60)
                finally:
                    if getattr(self, "leve", False):
                        for mod in self._modulos_preparo():
                            mod.to("cpu")
                        self._limpar_placa()
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
        cfg = getattr(self, "cfg", None) or {}
        if cfg.get("frases_juntas", True):
            # a legenda inteira numa geracao so (ate o limite do XTTS, ~180 letras): entonacao
            # continua entre as frases e sem o buraco de recomecar a geracao
            out = juntar_frases(partes, 180)
        else:
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
        try:
            yield from self._gerar(texto, vid, tom, vel)
        finally:
            self._limpar_placa()

    def _gerar(self, texto, vid, tom, vel):
        lat =self.latentes(vid) or self.latentes("narrador")
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
                    # "Ah..."/"Hm" no comeco: segura o comeco (~1 s de audio, gerado bem mais rapido
                    # que isso) para encurtar a vogal esticada antes de tocar
                    segurar = comeca_com_interjeicao(frase)
                    guardado = []
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
                            if segurar:
                                guardado.append(w)
                                if feito < int(24000 * 1.1) and not cortou:
                                    continue
                                w, segurar = encurtar_interjeicao(np.concatenate(guardado), 24000,
                                                                  sozinha=so_interjeicao(frase)), False
                            yield ream(tira(w)), 24000, 0.0
                            if cortou:
                                partes.close()
                                break
                    if segurar and guardado:            # a fala inteira coube no que foi segurado
                        yield ream(tira(encurtar_interjeicao(np.concatenate(guardado), 24000,
                                                             sozinha=so_interjeicao(frase)))), 24000, 0.0
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
            w = aparar(w[:limite].astype(np.float32), 24000)
            if comeca_com_interjeicao(frase):
                w = encurtar_interjeicao(w, 24000, sozinha=so_interjeicao(frase))
            w = tira(w)
            yield ream(np.concatenate([w, tira.fim()])), 24000, pausa


# ----------------------------------------------------------------- motor rapido (Piper + Edge)
class Baixada:
    """Audio de UMA frase da voz online, chegando aos poucos: quem baixa chama receber() com os
    bytes do MP3 que ja chegaram; quem toca le os pedacos prontos com ler() enquanto o resto chega.
    Decodificar o MP3 pela metade da exatamente o mesmo som (testado 14/11), deixando as 2 ultimas
    janelas (96 ms) para a proxima vez. Corta o silencio do comeco (110-165 ms) e do fim (270-800 ms)
    que a Microsoft poe em toda frase; as pausas do meio (virgulas) ficam."""
    MARGEM = 2304                  # amostras (2 janelas do MP3) que ainda podem mudar
    LIM = 0.008                    # abaixo disso e silencio
    MIN_PEDACO = 0.18              # s: entrega pedacos de pelo menos isso (menos vai-e-vem)

    def __init__(self):
        self.cond = threading.Condition()
        self.partes = []
        self.sr = 24000
        self.fim = False
        self.erro = None
        self._feito = 0            # posicao no audio decodificado ate onde ja foi entregue/descartado
        self._comecou = False      # ja passou do silencio do comeco

    @classmethod
    def pronta(cls, w, sr):
        b = cls()
        b.partes, b.sr, b.fim = ([w] if len(w) else []), sr, True
        return b

    def receber(self, dados, fim=False):
        import soundfile as sf
        try:
            w, sr = sf.read(io.BytesIO(bytes(dados)), dtype="float32")
        except Exception:
            if fim:
                raise
            return
        w = w.mean(1) if w.ndim > 1 else w
        self.sr = sr
        ate = len(w) if fim else len(w) - self.MARGEM
        if ate <= self._feito:
            if fim:
                self._entregar([], fim=True)
            return
        seg = w[self._feito:ate]
        if not self._comecou:
            alto = np.flatnonzero(np.abs(seg) > self.LIM)
            if not len(alto):
                self._feito = max(self._feito, ate - int(sr * 0.03))
                if fim:
                    self._entregar([], fim=True)
                return
            ini = max(0, int(alto[0]) - int(sr * 0.03))
            seg = seg[ini:]
            self._feito += ini
            self._comecou = True
        alto = np.flatnonzero(np.abs(seg) > self.LIM)
        corte = min(len(seg), int(alto[-1]) + int(sr * 0.06)) if len(alto) else 0
        if fim:
            self._feito = ate
            self._entregar([np.ascontiguousarray(seg[:corte], np.float32)] if corte else [], fim=True)
            return
        if corte / sr < self.MIN_PEDACO:
            return                          # pouco som ainda: espera juntar mais
        self._feito += corte
        self._entregar([np.ascontiguousarray(seg[:corte], np.float32)])

    def _entregar(self, ws, fim=False):
        with self.cond:
            self.partes += ws
            self.fim = self.fim or fim
            self.cond.notify_all()

    def falhar(self, e):
        with self.cond:
            self.erro = e
            self.fim = True
            self.cond.notify_all()

    def esperar(self, timeout):
        with self.cond:
            self.cond.wait_for(lambda: self.fim, timeout)
            return self.fim

    def ler(self, timeout=6.0):
        i = 0
        while True:
            with self.cond:
                self.cond.wait_for(lambda: i < len(self.partes) or self.fim, timeout)
                if i < len(self.partes):
                    w = self.partes[i]
                elif self.erro is not None:
                    raise RuntimeError("a voz online não respondeu: " + repr(self.erro)[:60])
                elif self.fim:
                    return
                else:
                    raise RuntimeError("a voz online parou no meio")
            i += 1
            yield w

    def tudo(self):
        with self.cond:
            return np.concatenate(self.partes) if self.partes else np.zeros(0, np.float32)


class MotorRapida:
    nome = "rapida"

    def __init__(self, cfg, banco):
        self.cfg = cfg
        self.banco = banco
        self.piper = {}
        self.kokoro = None
        self.trava = threading.Lock()
        self.cache_edge = collections.OrderedDict()
        self._em_voo = {}                       # (voz, taxa, frase) -> Future do pedido em andamento
        self._edge_falhas, self._edge_ruim_ate, self._edge_sonda = 0, 0.0, 0.0
        self._exec = None
        self.pronta = threading.Event()        # vozes locais carregadas e aquecidas
        self._precarregando = False
        # so pre-carrega se ela for a voz escolhida (rapida/local). Com a clonada escolhida ela so
        # cobre os primeiros segundos/travadas e carrega sob demanda: assim nao disputa o disco e
        # o processador com a clonada ao abrir.
        if cfg.get("motor") != "clonada":
            self.precarregar()
        self.acordar_vozes()

    def precarregar(self):
        if self._precarregando or self.pronta.is_set():
            return
        self._precarregando = True
        threading.Thread(target=self._precarregar, daemon=True).start()

    def _precarregar(self):
        """Carrega as vozes locais logo ao abrir e ja fala um "oi" mudo com cada uma: a 1a fala de
        verdade levava 4,4 s (medido as 03:58: a voz ainda nao tinha carregado e nem aquecido)."""
        from piper.config import SynthesisConfig
        t0 = time.time()
        usadas = set()
        for d in self.banco.vozes.values():
            r = d.get("rapida") or {}
            usadas |= {r.get("voz"), (r.get("reserva") or {}).get("voz")}
        for v in sorted(usadas - {None}):
            if v.startswith("pt_BR-"):
                try:
                    for _ in self._voz_piper(v).synthesize("Oi, tudo bem?", SynthesisConfig()):
                        pass
                except Exception as e:
                    log(f"Voz {v} não carregou: {e!r}", tipo="aviso")
            elif v.startswith(KOKORO):
                try:
                    self._kokoro().create("Oi, tudo bem?", voice=v[len(KOKORO):], lang="pt-br")
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

    def _kokoro(self):
        """Kokoro (82M, int8): vozes mais naturais que o Piper, mas ~5x mais lento (medido 14/11:
        ~0,45 s por pedaco curto, 2,7x mais rapido que a fala com 4 nucleos; +150 MB de memoria).
        Um so modelo serve as 3 vozes brasileiras (pf_dora, pm_alex, pm_santa)."""
        if self.kokoro is None:
            with self.trava:
                if self.kokoro is None:
                    import onnxruntime as ort
                    from kokoro_onnx import Kokoro
                    so = ort.SessionOptions()
                    so.intra_op_num_threads = int(self.cfg.get("kokoro_nucleos", 4))
                    so.inter_op_num_threads = 1
                    sess = ort.InferenceSession(os.path.join(KOKORO_DIR, "kokoro-v1.0.int8.onnx"),
                                                sess_options=so, providers=["CPUExecutionProvider"])
                    self.kokoro = Kokoro.from_session(sess, os.path.join(KOKORO_DIR, "voices-v1.0.bin"))
        return self.kokoro

    @staticmethod
    def _pedacos_kokoro(frase, max_palavras=7):
        """O Kokoro gera o pedaco inteiro antes de soltar o 1o som: pedaco curto = fala comeca
        antes. Quebra nas virgulas e, se ainda ficar comprido, a cada ~max_palavras palavras."""
        out = []
        for parte in re.split(r"(?<=[,;:])\s+", frase):
            p = parte.split()
            n = max(1, -(-len(p) // max_palavras))           # quantos pedacos
            tam = -(-len(p) // n)
            out += [" ".join(p[i:i + tam]) for i in range(0, len(p), tam)]
        return [o for o in out if re.search(r"\w", o)]

    def _gerar_kokoro(self, texto, voz, tom, vel):
        k = self._kokoro()
        nome = voz[len(KOKORO):]
        primeiro = True
        for frase in dividir_frases(texto):
            if not re.search(r"\w", frase):
                continue
            ritmo = 0.92 if frase.endswith("...") else 1.07 if frase.endswith("!") else 1.0
            ream = Reamostrador(tom)
            pedacos = self._pedacos_kokoro(frase)
            if primeiro and len(pedacos[0].split()) > 5:
                # 1o pedaco da fala bem curto: a voz comeca antes e o resto e gerado enquanto ela fala
                p = pedacos[0].split()
                pedacos[:1] = [" ".join(p[:4]), " ".join(p[4:])]
            primeiro = False
            for i, p in enumerate(pedacos):
                w, sr = k.create(p, voice=nome, speed=max(0.5, min(2.0, vel * ritmo / tom)), lang="pt-br")
                w = aparar(np.asarray(w, np.float32), sr, antes=0.01, depois=0.03)
                ultimo = i == len(pedacos) - 1
                yield ream(w), sr, pausa_apos(frase) if ultimo else (0.12 if p.endswith((",", ";", ":")) else 0.03)

    def _gerar_piper(self, texto, voz, tom, vel):
        if voz.startswith(KOKORO):
            yield from self._gerar_kokoro(texto, voz, tom, vel)
            return
        from piper.config import SynthesisConfig
        v = self._voz_piper(voz)
        base = v.config.length_scale or 1.0
        polir = self.cfg.get("polir_local", True)
        pol = None                            # um so para a fala toda: o EQ nao "reinicia" a cada frase
        for frase in dividir_frases(texto):
            if not re.search(r"\w", frase):
                continue
            ritmo = 0.92 if frase.endswith("...") else 1.07 if frase.endswith("!") else 1.0
            sc = SynthesisConfig(length_scale=base / (vel * ritmo) * tom, noise_scale=0.75, noise_w_scale=0.9)
            ream = Reamostrador(tom)
            ant = None                        # segura 1 pedaco para emendar o resto do polidor no ultimo
            for ch in v.synthesize(frase, sc):
                w = ream(np.asarray(ch.audio_float_array, dtype=np.float32).reshape(-1))
                if polir:
                    pol = pol or Polidor(ch.sample_rate)
                    w = pol(w)
                if ant is not None:
                    yield ant[0], ant[1], 0.0
                ant = (w, ch.sample_rate)
            if ant is not None:
                w = np.concatenate([ant[0], pol.fim()]) if pol else ant[0]
                yield w, ant[1], pausa_apos(frase)

    async def _corrida(self, frase, voz, taxa, baixada):
        """Baixa a frase e vai entregando o audio para `baixada` CONFORME CHEGA (o 1o som chega em
        ~0,8 s; a frase inteira em ~1,05 s; antes esperava a frase inteira).
        Se o 1o som nao chegar em `edge_reforco_s` (1,25 s), faz mais um pedido igual e fica com o
        que comecar primeiro (a Microsoft as vezes empaca 2-10 s). Sem som em `edge_limite_s` (3 s),
        ou se o som parar no meio por 2 s -> erro (o programa usa a voz local de reserva)."""
        import asyncio
        reforco = float(self.cfg.get("edge_reforco_s", 1.25))
        t0 = time.time()
        limite = t0 + float(self.cfg.get("edge_limite_s", 3.0))
        maximo = int(self.cfg.get("edge_pedidos_max", 2))
        pedidos = []
        feitos = 0

        async def um_pedido(q):
            import edge_tts
            try:
                async for ch in edge_tts.Communicate(frase, voz, rate=taxa).stream():
                    if ch["type"] == "audio" and ch["data"]:
                        await q.put(ch["data"])
            except Exception as e:
                await q.put(e)
            await q.put(None)

        def novo_pedido():
            q = asyncio.Queue()
            pedidos.append((asyncio.ensure_future(um_pedido(q)), q))

        novo_pedido()
        feitos = 1
        ok, primeiro_ms = False, None
        try:
            vencedor = None
            while vencedor is None:
                agora = time.time()
                if agora >= limite or not pedidos:
                    raise RuntimeError("a voz online não respondeu")
                prox = t0 + reforco * feitos if feitos < maximo else limite
                gets = {asyncio.ensure_future(q.get()): (t, q) for t, q in pedidos}
                prontos, resto = await asyncio.wait(gets, timeout=max(0.01, min(prox, limite) - agora),
                                                    return_when=asyncio.FIRST_COMPLETED)
                for g in resto:
                    g.cancel()
                for g in prontos:
                    item = g.result()
                    t, q = gets[g]
                    if isinstance(item, (bytes, bytearray)):
                        if vencedor is None:
                            vencedor = (t, q, item)
                        else:
                            q.put_nowait(item)
                    elif (t, q) in pedidos:
                        pedidos.remove((t, q))           # esse pedido acabou sem som (erro)
                if vencedor is None and feitos < maximo and time.time() >= prox:
                    novo_pedido()
                    feitos += 1
            t_v, q_v, item = vencedor
            for t, q in pedidos:
                if t is not t_v:
                    t.cancel()
            primeiro_ms = round((time.time() - t0) * 1000)
            dados = bytearray(item)
            baixada.receber(dados)
            while True:
                item = await asyncio.wait_for(q_v.get(), 2.0)
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                dados += item
                baixada.receber(dados)
            baixada.receber(dados, fim=True)
            ok = True
            return bytes(dados)
        finally:
            for t, q in pedidos:
                t.cancel()
            diag.ev("edge", ms=round((time.time() - t0) * 1000), primeiro_ms=primeiro_ms, pedidos=feitos,
                    ok=ok, letras=len(frase), voz=voz.split("-")[2][:8] if voz.count("-") >= 2 else voz)

    def acordar_vozes(self):
        """A 1a frase de cada voz da Microsoft numa sessao demora mais (1,8-7 s; depois ~1 s).
        Pede uma palavra com cada voz usada, em segundo plano, ao abrir e a cada 5 minutos."""
        import asyncio
        vozes = sorted({(d.get("rapida") or {}).get("voz") for d in self.banco.vozes.values()
                        if (d.get("rapida") or {}).get("motor") == "edge"} - {None})

        async def todas():
            await asyncio.gather(*(asyncio.wait_for(self._baixar("Oi.", v, "+0%"), 8) for v in vozes),
                                 return_exceptions=True)

        def laco():
            while True:
                t0 = time.time()
                if self.so_local():
                    time.sleep(5)
                    continue
                try:
                    asyncio.run(todas())
                    diag.ev("vozes_acordadas", n=len(vozes), ms=round((time.time() - t0) * 1000))
                except Exception:
                    pass
                time.sleep(float(self.cfg.get("edge_acordar_s", 300)))

        if vozes and self.cfg.get("edge_acordar", True):
            threading.Thread(target=laco, daemon=True, name="acordar_vozes").start()

    async def _baixar(self, frase, voz, taxa):
        import edge_tts
        dados = bytearray()
        async for ch in edge_tts.Communicate(frase, voz, rate=taxa).stream():
            if ch["type"] == "audio":
                dados += ch["data"]
        return bytes(dados)

    @staticmethod
    def _taxa(vel, tom):
        return f"{round((vel / tom - 1) * 100):+d}%"

    def _futura(self, frase, voz, taxa):
        """Baixada da frase (voz, taxa): ja pronta (cache), ja sendo baixada (a antecipada: quem chega
        depois le o que ja veio e o resto conforme chega) ou comeca a baixar agora."""
        from concurrent.futures import ThreadPoolExecutor
        chave = (voz, taxa, frase)
        with self.trava:
            par = self.cache_edge.get(chave)
            if par is not None:
                return Baixada.pronta(*par)
            b = self._em_voo.get(chave)
            if b is not None and b.erro is None:
                return b
            if self._exec is None:
                self._exec = ThreadPoolExecutor(max_workers=8, thread_name_prefix="edge")
            b = Baixada()
            self._em_voo[chave] = b
            if len(self._em_voo) > 200:
                for k in [k for k, v in self._em_voo.items() if v.fim][:100]:
                    self._em_voo.pop(k, None)
            self._exec.submit(self._baixar_frase, frase, voz, taxa, b)
            return b

    def _edge_fora(self):
        return time.time() < self._edge_ruim_ate

    def _edge_ok(self):
        if self._edge_falhas >= 2 or self._edge_ruim_ate:
            log("✅ A voz online voltou.", tipo="ok")
            diag.ev("edge_voltou")
        self._edge_falhas, self._edge_ruim_ate = 0, 0.0

    def _edge_falhou(self, e):
        self._edge_falhas += 1
        if self._edge_falhas >= 2 and not self._edge_fora():
            self._edge_ruim_ate = time.time() + float(self.cfg.get("edge_pausa_s", 20))
            log("⚠ A voz online (Microsoft) não está respondendo: usando as vozes locais até ela voltar.",
                tipo="aviso")
            diag.ev("edge_fora", falhas=self._edge_falhas)
        elif self._edge_falhas < 2:
            log("Voz online (Edge) falhou, usando a local:", repr(e)[:80], tipo="aviso")

    def _sondar_edge(self, texto, voz, taxa):
        if time.time() - self._edge_sonda < 5:
            return
        self._edge_sonda = time.time()
        frase = (self._blocos(texto) or [texto])[0]
        b = self._futura(frase, voz, taxa)

        def ver():
            if b.esperar(10) and b.erro is None:
                self._edge_ok()
            else:
                self._edge_ruim_ate = max(self._edge_ruim_ate, time.time() + 5)
        threading.Thread(target=ver, daemon=True).start()

    def antecipar(self, texto, vid, tom, vel):
        """Ja pede a fala na voz `vid` (sem esperar), para quando ela for gerada de verdade ja estar
        pronta. Usado enquanto a escuta ainda decide se e voz de homem ou de mulher."""
        d = self.banco.vozes.get(vid) or {}
        r = d.get("rapida") or {}
        if r.get("motor") != "edge" or self._edge_fora() or self.so_local():
            return
        tom_total = float(r.get("tom", 1.0)) * tom
        vel_total = float(r.get("velocidade", 1.0)) * vel
        taxa = self._taxa(vel_total, tom_total)
        for f in self._blocos(texto):
            self._futura(f, r["voz"], taxa)

    def _blocos(self, texto):
        """Pedidos a voz online para esta fala: as frases juntas (ver juntar_frases). Config
        "frases_juntas": false volta a uma frase por pedido."""
        frases = dividir_frases(texto)
        if not self.cfg.get("frases_juntas", True):
            return frases
        return juntar_frases(frases, int(self.cfg.get("frases_juntas_max", 300)))

    def _gerar_edge(self, texto, voz, tom, vel):
        taxa = self._taxa(vel, tom)
        frases = self._blocos(texto)
        # (se ainda sairem varios pedidos, sao feitos todos de uma vez: o 2o ja chegou quando o 1o
        # termina de tocar)
        baixadas = [self._futura(f, voz, taxa) for f in frases]
        for frase, bx in zip(frases, baixadas):
            ream = Reamostrador(tom)
            ant = None
            for w in bx.ler():
                if ant is not None:
                    yield ream(ant), bx.sr, 0.0
                ant = w
            if ant is not None:
                yield ream(ant), bx.sr, pausa_apos(frase)

    def _baixar_frase(self, frase, voz, taxa, baixada):
        import asyncio
        try:
            asyncio.run(self._corrida(frase, voz, taxa, baixada))
            w = baixada.tudo()
            with self.trava:
                self.cache_edge[(voz, taxa, frase)] = (w, baixada.sr)
                while len(self.cache_edge) > 300:
                    self.cache_edge.popitem(last=False)
        except BaseException as e:
            baixada.falhar(e)

    def gerar(self, texto, vid, tom, vel):
        d = self.banco.vozes.get(vid) or self.banco.vozes.get("narrador") or {}
        r = d.get("rapida") or {"motor": "piper", "voz": "pt_BR-faber-medium"}
        tom_total = float(r.get("tom", 1.0)) * tom
        vel_total = float(r.get("velocidade", 1.0)) * vel
        if r.get("motor") == "edge" and self.so_local():
            r = r.get("reserva") or {"voz": "pt_BR-faber-medium", "tom": 1.2}
            tom_total = float(r.get("tom", 1.0)) * tom
            vel_total = float(r.get("velocidade", 1.0)) * vel
        elif r.get("motor") == "edge":
            usar_reserva = self._edge_fora()
            if usar_reserva:
                self._sondar_edge(texto, r["voz"], self._taxa(vel_total, tom_total))
            else:
                ok = False
                try:
                    for p in self._gerar_edge(texto, r["voz"], tom_total, vel_total):
                        ok = True
                        yield p
                    self._edge_ok()
                    return
                except Exception as e:
                    self._edge_falhou(e)
                    if ok:            # ja tinha comecado a falar: nao repete o comeco na outra voz
                        return
                    usar_reserva = True
            # cada personagem tem a sua voz local de reserva (homem na Cadu, mulher na Faber
            # mais aguda...); antes todo mundo ia para a Faber com o tom 20% mais alto
            res = r.get("reserva") or {"voz": "pt_BR-faber-medium", "tom": 1.2}
            tom_total = float(res.get("tom", 1.0)) * tom
            vel_total = float(res.get("velocidade", 1.0)) * vel
            r = res
        yield from self._piper_seguro(texto, r["voz"], tom_total, vel_total)

    def so_local(self):
        return self.cfg.get("motor") == "local"

    def _piper_seguro(self, texto, voz, tom, vel):
        """Voz local que nunca deixa a fala muda: se a voz do personagem falhar antes de comecar a
        falar (modelo faltando/corrompido), fala na Faber, que vem sempre com o programa."""
        ok = False
        try:
            for p in self._gerar_piper(texto, voz, tom, vel):
                ok = True
                yield p
        except Exception as e:
            if ok or voz == "pt_BR-faber-medium":
                raise
            log(f"Voz local {voz} falhou, usando a Faber:", repr(e)[:80], tipo="aviso")
            yield from self._gerar_piper(texto, "pt_BR-faber-medium", tom, vel)


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
        self.puladas = set()      # falas do MEIO que encavalaram (3 em sequencia): nao tocam
        self.no_toque = False     # escrevendo som agora
        self.acabou_atual = True  # a fala `tocando` ja terminou de tocar
        self.seq = 0             # numero de cada fala (para o diagnostico)
        self.ult_id = None
        self.ult_fim_toque = None
        self.gerando_fim = set()
        self.chegada = {}         # numero da fala -> quando ela apareceu
        # legenda com 2+ frases vira 2+ falas (a 1a sai logo, as outras "continuam"). A 2a frase foi
        # VISTA junto com a 1a, mas so pode comecar depois dela: medir o atraso dela desde a legenda
        # aparecer contava a 1a frase inteira como atraso e ela saia acelerada 35% (sessao de 18/11:
        # 204 de 311 continuacoes aceleradas, media +24%, em cima da velocidade 1,4 do config).
        self.ideal_fim = {}       # legenda (pag) -> quando a fala dela acabaria se tivesse saido na hora
        self.primeira_fid = {}    # legenda (pag) -> id da 1a fala dela (as outras sao continuacao)
        self.ritmo_fala = 1.0     # velocidade em que a fala atual esta tocando
        self.com_buraco = set()
        self.sem_buraco = 0
        # quantos segundos de voz a clonada gera por segundo (medido a cada fala). Na placa de video
        # passa de 1 (nao precisa esperar); no processador fica ~0,5 e o colchao adaptativo usa isso
        self.taxa_geracao = {}
        # escolhe a voz NA HORA de gerar (o nucleo ouve a voz original do jogo e decide se e homem
        # ou mulher): fn(pag, texto, voz, tom, vel, quem, cat, visto) -> (voz, tom, vel, quem, cat) ou None
        self.resolver_voz = None
        self.trava_resolver = threading.Lock()
        # fn(texto, voz, tom, vel, quem, cat) -> [(voz, tom, vel), ...] vozes que a fala PODE ter
        # (o nucleo diz: "Homem (pela voz)", "Mulher (pela voz)"...); a rapida ja as pede antes
        self.candidatas_voz = None   # escuta/elenco: uma fala por vez decide a voz
        self.clonada_lenta = False   # a clonada travou: usando a rapida ate ela voltar
        self._clonada_thread = None
        self._clonada_abandonada = False
        self.saida_id = None      # saida de som padrao do Windows (muda quando conecta um fone)
        self.saida_nome = ""
        self.saida_trocou = None  # nome do aparelho novo -> o _tocar reabre o audio nele
        self.espelho = None       # fn(bloco, sr): copia de cada pedaco tocado (Sala 3D, nucleo/sala.py)
        # CAMADA DE FUNDO: fala de figurante ("Pessoa local") enquanto alguem importante fala sai por
        # baixo, mais baixa, misturada na voz principal (burburinho de multidao), sem entrar na fila
        # e sem atrasar a Lucia/Jason (GTA 6, 18/11 00:15: 3-5 legendas empilhadas no assalto)
        self.fundo = np.zeros(0, np.float32)
        self.trava_fundo = threading.Lock()
        self.fundo_ids = set()       # ids de fala que vao para o fundo
        self.fundo_cortados = set()  # falas de fundo atrasadas demais (o resto delas nao toca)
        self.pags_figurante = set()  # legendas (pag) de figurante na fila principal: puladas primeiro
        threading.Thread(target=self._sintetizar, daemon=True).start()
        threading.Thread(target=self._tocar, daemon=True).start()
        threading.Thread(target=self._vigiar_saida, daemon=True, name="saida").start()

    def ligar_clonada(self, antes=None, avisar=True):
        if self.clonada or self.estado_clonada == "carregando":
            return
        from .util import cabe
        # na placa de video o GPT vai em fp16 (xtts_fp16): ocupa ~1,3 GB (antes 2,3 GB) e os pesos
        # nao ficam na RAM. Pedir 3,5 GB de RAM e 3 GB na placa impedia de carregar com o jogo aberto.
        if not cabe("a voz clonada", ram_gb=2.5, vram_gb=2.0, contar_paginacao=True, avisar=avisar):
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

    def _voz_unica(self, voz, tom, vel):
        """Botao "Voz da Lucia" / "Voz do Jason" ligado: todo mundo fala com essa voz, sem variacao
        de tom. config "voz_unica" = id da voz no banco ("lucia", "jason") ou "" (desligado).
        "dupla" (botao "Lucia + Jason"): voz de mulher/menina/idosa vira Lucia; o resto (homem,
        menino, idoso e o narrador) vira Jason.
        "so_lucia" e o nome antigo da opcao (so a Lucia)."""
        vid = self.cfg.get("voz_unica") or ("lucia" if self.cfg.get("so_lucia") else "")
        if vid == "dupla":
            cat = self.banco.vozes.get(voz, {}).get("categoria")
            vid = "lucia" if cat in ("mulher", "menina", "idosa") else "jason"
        if vid and vid in self.banco.vozes:
            return vid, 1.0, 1.0
        return voz, tom, vel

    def ocupado(self):
        """Tem voz principal tocando ou esperando (gerando ou na fila de som)."""
        return self._tocando_agora() or not self.fila.empty() or not self.audio.empty()

    def falar(self, texto, voz, tom=1.0, vel=1.0, quem="", categoria="", nova=True, visto=None, forcar=False,
              figurante=False, fundo=False):
        """nova=True: comecou uma fala/legenda nova.

        Sem cortar (padrao): a fala anterior ACELERA para alcancar e a nova entra logo depois, em
        velocidade normal. Se encavalarem (uma tocando, outra esperando e chega a nova), a do MEIO so
        e pulada se ja estiver atrasada demais: a que toca termina sem corte (ver _pular_meio).
        Com "interromper_ao_avancar": corta a anterior na hora (jeito antigo).
        figurante=True: gente da multidao; atrasou (`pular_figurante_s`), e a primeira a ser pulada.
        fundo=True: toca POR BAIXO da voz principal, mais baixa, sem entrar na fila dela."""
        self.seq += 1
        voz, tom, vel = self._voz_unica(voz, tom, vel)
        if voz == "narrador":
            # o narrador tem tom/ritmo proprios no banco (18/11: voz do Rogerio, tom 0.94, ritmo 1.03);
            # quem chama passa 1.0/1.0, sem ficha de personagem para guardar isso
            d = self.banco.vozes.get("narrador") or {}
            tom, vel = tom * float(d.get("tom", 1.0)), vel * float(d.get("velocidade", 1.0))
        diag.ev("fala", id=self.seq, texto=texto, voz=voz, quem=quem, nova=nova,
                espera_ms=round((time.time() - (visto or time.time())) * 1000),
                **({"fundo": True} if fundo else {}), **({"figurante": True} if figurante else {}))
        if fundo and self.cfg.get("fundo_multidao", True):
            self.fundo_ids.add(self.seq)
            self.fila.put((self.ger, self.pag, texto, voz, tom, vel, quem, categoria, visto or time.time(), forcar, self.seq))
            return
        if nova:
            self.pag += 1
            if figurante:
                self.pags_figurante.add(self.pag)
                self.pags_figurante = {p for p in self.pags_figurante if p > self.pag - 50}
            self.chegada[self.pag] = time.time()
            if self.cfg.get("interromper_ao_avancar", False):
                self.tol = float(self.cfg.get("tolerancia_fim_s", 1.0))
                self.ger += 1
            else:
                self._pular_meio()
        self.primeira_fid.setdefault(self.pag, self.seq)
        if len(self.primeira_fid) > 60:
            for p in [p for p in self.primeira_fid if p < self.pag - 30]:
                self.primeira_fid.pop(p, None)
                self.ideal_fim.pop(p, None)
        self.fila.put((self.ger, self.pag, texto, voz, tom, vel, quem, categoria, visto or time.time(), forcar, self.seq))

    def calar(self):
        self.tol = 0.0
        self.ger += 1
        with self.trava_fundo:
            self.fundo = np.zeros(0, np.float32)

    def _tocando_agora(self):
        """A fala `tocando` ainda esta saindo no alto-falante (ou entre dois pedacos dela)."""
        return bool(self.tocando) and not self.acabou_atual and (
            self.no_toque or time.time() - (self.ult_fim_toque or 0) < 0.5)

    def _pular_meio(self):
        """Chegou a legenda nova (self.pag) com outras ainda na frente dela.
        - Legenda do MEIO (esperando, ainda nao comecou) ja atrasada demais (`pular_atrasada_s`
          desde que apareceu): e pulada inteira e a voz vai direto para o que esta na tela. Pouco
          atrasada, fica: a anterior acelera e ela entra logo. (Pular SEMPRE a do meio perdia 1 de
          cada 7 falas numa conversa rapida: "Me passa uma cerveja.", "Saúde!"; bateria do GTA 6,
          19/11.) Na hora de comecar cada fala o _tocar confere de novo (_atrasada_demais).
        - Se ainda assim a que toca ficou `pular_atual_apos` legendas atras (prende demais mesmo
          acelerando), ela some suave."""
        novo = self.pag
        agora = time.time()
        base = self.tocando if self._tocando_agora() else self.tocando + 1
        pend = [p for p in range(max(base, self.pular_ate), novo) if p not in self.puladas]
        if not pend:
            return
        atual = pend[0]
        meio = [p for p in pend[1:] if 0 < self._limite_atraso(p) <= agora - self.chegada.get(p, agora)]
        for p in meio:
            self.puladas.add(p)
            diag.ev("pulou_meio", pag=p, tocando=atual, nova=novo, atraso_s=round(agora - self.chegada.get(p, agora), 2))
        if meio:
            log(f"⏭ Legendas encavaladas: pulei {len(meio)} atrasada(s) demais para alcançar a tela.", tipo="aviso")
        if len([p for p in pend if p not in self.puladas]) >= int(self.cfg.get("pular_atual_apos", 3)):
            self.pular_ate = max(self.pular_ate, atual + 1)
        self.puladas = {p for p in self.puladas if p >= self.pular_ate and p > novo - 50}

    def _limite_atraso(self, pag):
        """Quanto uma legenda que ainda nao comecou pode atrasar antes de ser pulada: figurante
        (multidao) bem menos que quem importa, para a voz nao ficar atras da Lucia/Jason."""
        lim = float(self.cfg.get("pular_atrasada_s", 3.0))
        if pag in getattr(self, "pags_figurante", ()) and lim > 0:
            return min(lim, float(self.cfg.get("pular_figurante_s", 1.0)))
        return lim

    def _atrasada_demais(self, pag, info):
        """Na hora de COMECAR uma fala: ja tem legenda mais nova esperando e esta sairia
        `pular_atrasada_s` atrasada (desde o inicio ideal, ver _inicio_ideal) -> pula inteira: o que
        esta na tela agora ja e outra coisa."""
        lim = self._limite_atraso(pag)
        if lim <= 0 or pag >= self.pag or not info.get("visto"):
            return False
        return time.time() - self._inicio_ideal(pag, info["visto"]) >= lim

    def _velha(self, ger, pag):
        return ger != self.ger or pag < self.pular_ate or pag in self.puladas

    def _ajustar_pedacos(self, fid):
        """Auto-ajuste: se a fala teve buraco (picotou), o 1o pedaco cresce (mais folga); depois de
        10 falas seguidas sem buraco, diminui um pouco (comeca a falar mais cedo)."""
        if not self.cfg.get("auto_pedacos", True) or not self.clonada:
            return
        plano = list(self.cfg.get("xtts_pedacos") or [8, 12, 20, 32])
        p0 = plano[0]
        piso = int(self.cfg.get("xtts_pedaco_min", 8))    # 1o pedaco maior = menos emenda no comeco da frase
        if fid in self.com_buraco:
            self.com_buraco.discard(fid)
            self.sem_buraco = 0
            novo = min(max(16, piso + 4), p0 + 2)
        else:
            self.sem_buraco += 1
            if self.sem_buraco < 8:
                return
            self.sem_buraco = 0
            novo = max(piso, p0 - 1)
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
        if pag >= self.pag or restante < 0.5:
            return 1.0
        espera = time.time() - self.chegada.get(pag + 1, time.time())
        if espera < 0.2:
            return 1.0
        # reage mais rapido: antes so comecava com 0,4 s de espera e levava ~2 s para chegar no
        # maximo; a fila entre falas era a maior parte do atraso que sobrou (bateria 14/11)
        return min(float(self.cfg.get("acelerar_ate", 1.35)), 1.1 + (espera - 0.2) * 0.35)

    def _inicio_ideal(self, pag, visto):
        """Quando esta fala deveria comecar se tudo estivesse em dia: na hora em que apareceu ou, se
        e continuacao da mesma legenda, quando a parte anterior acabaria (a 2a frase so comeca
        depois da 1a, tambem na voz original)."""
        return max(visto or time.time(), self.ideal_fim.get(pag, 0.0))

    def _ritmo_inicial(self, pag, info, dur):
        """Fala que ja COMECA atrasada (esperou a anterior terminar): ja sai um pouco mais rapida,
        em vez de comecar normal e so acelerar quando a proxima chegar. Atraso medido desde o
        inicio IDEAL (_inicio_ideal), nao desde a legenda aparecer."""
        if not info or not info.get("visto") or dur < 0.6:
            return 1.0
        atraso = time.time() - (info.get("ideal") or info["visto"])
        r = 1.0 if atraso < 1.3 else 1.0 + (atraso - 1.3) * 0.2
        return min(float(self.cfg.get("acelerar_ate", 1.35)), max(r, self._ritmo(pag, dur)))

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
            # a mesma fala lida de novo com 1-2 letras diferentes ("Ei, essa... essa parece que tá
            # certa." e "Ei, essas essa parece que tá certa."; "s Não, não, tá de boa."): saia duas
            # vezes seguidas (GTA 6, 18/11)
            if agora - t0 <= 8 and min(len(n), len(n0)) >= 12 and semelhanca(n, n0) >= 0.9:
                return True
        self.recentes.append((n, set(pal), agora))
        return False

    def _pressa(self, visto, pag):
        if not self.cfg.get("acelerar_se_atrasado", True):
            return 1.0
        atraso = time.time() - visto
        p = 1.22 if atraso > 3.0 else 1.12 if atraso > 1.6 else 1.0
        return p

    def _para_tocador(self, item):
        """Pedaco gerado -> fila do alto-falante. Fala de FUNDO vai direto para a camada de fundo:
        na fila ela esperava cada pedaco da voz principal acabar e o burburinho saia engasgado
        ("lagado", teste de 20/11)."""
        ger, _pag, w, sr, info = item
        fid = (info or {}).get("id")
        if fid in self.fundo_ids:
            self._para_fundo(ger, w, sr, info, fid)
        else:
            self.audio.put(item)

    def _sintetizar(self):
        """Pega as falas da fila. Na voz rapida, GERA AS PROXIMAS AO MESMO TEMPO que a atual (ate
        `falas_em_paralelo`), e entrega o som na ordem. Antes era uma de cada vez: com o jogo, a voz
        da Microsoft levava 2-4 s por fala, as falas chegavam a cada ~2,5 s e o atraso foi crescendo
        ate 10 s, com falas puladas (sessao de 14/11 10:15; na bateria: mediana 4 s, 6 nao tocaram)."""
        while True:
            item = self.fila.get()
            if not self._paralelo():
                self._processar(item, self._para_tocador)
                continue
            pend = collections.deque([self._iniciar_tarefa(item)])
            while pend:
                tarefa = pend[0]
                while True:
                    # enquanto espera a fala da vez, ja comeca a gerar as que chegaram depois
                    while len(pend) < int(self.cfg.get("falas_em_paralelo", 3)):
                        try:
                            pend.append(self._iniciar_tarefa(self.fila.get_nowait()))
                        except queue.Empty:
                            break
                    try:
                        x = tarefa.get(timeout=0.02)
                    except queue.Empty:
                        continue
                    if x is None:
                        break
                    self._para_tocador(x)
                pend.popleft()

    def previa(self, texto, voz="narrador", tom=1.0, vel=1.0, quem="", cat=""):
        """Ja pede a fala a voz online nas vozes que ela pode ter, SEM falar: quando ela for falada
        de verdade (mesmo texto), o som ja esta chegando. O nucleo chama na 1a leitura da legenda,
        antes dos ~0,2 s que o leitor espera para ter certeza do texto."""
        if not self._paralelo():
            return
        try:
            cands = (self.candidatas_voz(texto, voz, tom, vel, quem, cat) if self.candidatas_voz
                     else [(voz, tom, vel)])
            if self.cfg.get("voz_unica") or self.cfg.get("so_lucia"):
                # cada voz possivel vira Lucia/Jason (no "Lucia + Jason" podem ficar as duas)
                cands = list(dict.fromkeys(self._voz_unica(*c) for c in cands))
            for cv, ct, cvel in cands:
                dc = self.banco.vozes.get(cv, {})
                self.rapida.antecipar(preparar_fala(texto), cv, ct * float(dc.get("tom", 1.0)),
                                      cvel * float(dc.get("velocidade", 1.0)) * float(self.cfg.get("velocidade", 1.0)))
        except Exception as e:
            diag.ev("antecipar_erro", erro=repr(e)[:160])

    def _paralelo(self):
        return (self.cfg.get("gerar_em_paralelo", True) and self.motor_atual() is self.rapida)

    def _iniciar_tarefa(self, item):
        q = queue.Queue()

        def trabalhar():
            try:
                self._processar(item, q.put)
            except Exception as e:
                diag.ev("tarefa_erro", erro=repr(e)[:160])
            finally:
                q.put(None)

        threading.Thread(target=trabalhar, daemon=True, name="gerar").start()
        return q

    def _processar(self, item, saida):
        if True:
            ger, pag, texto, voz, tom, vel, quem, cat, visto, forcar, fid = item
            if self._velha(ger, pag):
                diag.ev("descartada", id=fid, motivo="ficou para trás antes de gerar")
                return
            if not forcar and self._repetido(texto):
                diag.ev("descartada", id=fid, motivo="repetida")
                return
            if self.resolver_voz and self.candidatas_voz and self._paralelo():
                # enquanto a escuta decide (homem? mulher?), ja pede a fala nas vozes possiveis
                self.previa(texto, voz, tom, vel, quem, cat)
            if self.resolver_voz:
                try:
                    with self.trava_resolver:
                        r = self.resolver_voz(pag, texto, voz, tom, vel, quem, cat, visto)
                    if r:
                        voz, tom, vel, quem, cat = r
                except Exception as e:
                    diag.ev("resolver_voz_erro", id=fid, erro=repr(e)[:160])
                if self._velha(ger, pag):
                    diag.ev("descartada", id=fid, motivo="ficou para trás enquanto ouvia a voz")
                    return
            voz, tom, vel = self._voz_unica(voz, tom, vel)   # a escuta pode ter trocado a voz
            # continuacao da mesma legenda: o "atraso" dela e a 1a frase tocando, nao pressa de
            # verdade; se a legenda toda estiver atrasada, o _tocar acelera (inicio ideal)
            continuacao = self.primeira_fid.get(pag, fid) != fid
            pressa = 1.0 if continuacao else self._pressa(visto, pag)
            d = self.banco.vozes.get(voz, {})
            vel_base = vel * float(d.get("velocidade", 1.0)) * float(self.cfg.get("velocidade", 1.0))
            vel_total = vel_base * pressa
            tom_total = tom * float(d.get("tom", 1.0))
            motores = [self.motor_atual()]
            if motores[0] is self.clonada and self.clonada is not None and self._clonada_presa():
                motores = []                      # a clonada ainda esta presa na fala anterior
            if not motores or motores[0] is not self.rapida:
                motores.append(self.rapida)
            vol = float(self.cfg.get("volume", 0.9))
            for m in motores:
                nivel = Nivelador()
                t0 = time.time()
                primeiro = True
                diag.ev("gerar_inicio", id=fid, motor=m.nome, voz=voz, quem=quem, pressa=round(pressa, 2),
                        espera_ms=round((t0 - visto) * 1000), fila=self.fila.qsize(), **self._uso_pc())
                k, total_audio = 0, 0.0
                try:
                    if m is self.clonada:
                        pedacos = self._vigiado(m, preparar_fala(texto), voz, tom_total, vel_total)
                    else:
                        # voz rapida: pede na velocidade normal (igual a fala antecipada, que ja pode
                        # estar pronta) e acelera aqui mesmo, sem mudar o tom, se estiver atrasada
                        pedacos = m.gerar(preparar_fala(texto), voz, tom_total, vel_base)
                        if pressa > 1.01:
                            pedacos = ((acelerar(w_, pressa, sr_), sr_, pausa_ / pressa)
                                       for w_, sr_, pausa_ in pedacos)
                    for w, sr, pausa in pedacos:
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
                                    "pressa": round(pressa, 2), "visto": visto,
                                    # duracao prevista da fala inteira (~12 letras/s, como no limite do XTTS)
                                    "previsto_s": len(normalizar(texto)) / 12.0 / max(0.5, vel_total),
                                    "taxa": self.taxa_geracao.get(m.nome)}
                            primeiro = False
                        saida((ger, pag, w, sr, dict(info or {}, id=fid, primeiro=info is not None)))
                    gasto = time.time() - t0
                    if total_audio >= 0.8 and not self._velha(ger, pag):
                        nova = total_audio / max(0.001, gasto)
                        ant = self.taxa_geracao.get(m.nome)
                        self.taxa_geracao[m.nome] = nova if ant is None else ant * 0.6 + nova * 0.4
                    diag.ev("gerar_fim", id=fid, total_ms=round((time.time() - t0) * 1000),
                            audio_s=round(total_audio, 2),
                            velocidade=round(total_audio / max(0.001, time.time() - t0), 2),
                            motor=m.nome, **self._uso_pc())
                    saida((ger, pag, None, 24000, {"id": fid, "fim": True}))
                    break
                except ClonadaTravou as e:
                    diag.ev("clonada_travou", id=fid, fase=e.fase, esperou_s=e.esperou, pedacos=k,
                            **self._uso_pc())
                    if not self.clonada_lenta:
                        self.clonada_lenta = True
                        log("⚠ A voz clonada travou (placa de vídeo cheia com o jogo): usando a voz rápida "
                            "até ela voltar.", tipo="aviso")
                    if not primeiro:              # ja tinha comecado a falar: termina aqui
                        saida((ger, pag, None, 24000, {"id": fid, "fim": True}))
                        break
                except Exception as e:
                    log(f"Erro ao gerar a fala ({m.nome}):", repr(e), tipo="erro")
                    if not primeiro:
                        break

    @staticmethod
    def _uso_pc():
        """Como estava o PC agora: % da placa de video em uso, memoria livre nela e RAM livre. Vai
        junto de cada fala no registro para saber se o atraso foi da placa ou da RAM."""
        d = {}
        try:
            from .util import medidor_placa
            d.update(medidor_placa().agora())
        except Exception:
            pass
        try:
            import psutil
            d["ram_livre_gb"] = round(psutil.virtual_memory().available / 2 ** 30, 2)
        except Exception:
            pass
        return d

    def _clonada_presa(self):
        """A geracao da voz clonada da fala anterior ainda nao voltou (travou na placa de video)."""
        t = self._clonada_thread
        presa = t is not None and t.is_alive() and self._clonada_abandonada
        if not presa and self.clonada_lenta and t is not None and not t.is_alive():
            self.clonada_lenta = False
            log("✅ A voz clonada voltou ao normal.", tipo="ok")
            diag.ev("clonada_voltou")
        return presa

    def _vigiado(self, m, texto, voz, tom, vel):
        """Gera com a voz clonada numa thread separada e espera cada pedaco com limite de tempo.
        Com um jogo pesado enchendo a placa (RDR2), o XTTS chegou a TRAVAR dentro do driver (nao
        voltava mais) ou a levar 7 s para comecar: a voz simplesmente nao saia. Passando do limite,
        larga a clonada e esta fala sai na voz rapida; as proximas tambem, ate a clonada voltar."""
        q, acabou, parar = queue.Queue(), object(), threading.Event()

        def trabalhar():
            try:
                for item in m.gerar(texto, voz, tom, vel):
                    if parar.is_set():
                        break
                    q.put(item)
            except Exception as e:
                q.put(e)
            finally:
                q.put(acabou)

        t = threading.Thread(target=trabalhar, daemon=True, name="clonada")
        self._clonada_thread, self._clonada_abandonada = t, False
        t.start()
        limite_1 = float(self.cfg.get("clonada_limite_s", 4.0))       # ate o 1o pedaco
        limite_n = float(self.cfg.get("clonada_limite_pedaco_s", 3.0))  # entre um pedaco e outro
        primeiro = True
        try:
            while True:
                lim = limite_1 if primeiro else limite_n
                try:
                    item = q.get(timeout=lim)
                except queue.Empty:
                    self._clonada_abandonada = True
                    raise ClonadaTravou("1o pedaco" if primeiro else "meio da fala", lim)
                if item is acabou:
                    return
                if isinstance(item, Exception):
                    raise item
                primeiro = False
                yield item
        finally:
            parar.set()

    def _vigiar_saida(self):
        """Confere a cada segundo qual e a saida de som padrao do Windows. O audio da voz fica aberto
        o tempo todo (manter_audio_ligado) e o PortAudio guarda a lista de aparelhos de quando abriu:
        se o padrao muda depois (fone Bluetooth conectou, o jogo trocou a saida), o jogo vai para o
        aparelho novo e a voz continuava saindo no antigo (ex.: alto-falante desligado) - parecia
        que a voz nao saia. Quando muda, marca para reabrir o audio no aparelho novo."""
        try:
            import comtypes
            try:
                # importar o comtypes nesta thread ja pode ter iniciado o COM (STA): qualquer modo serve
                comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            except OSError:
                pass
            from pycaw.pycaw import AudioUtilities
        except Exception as e:
            diag.ev("saida_vigia_erro", erro=repr(e)[:160])
            return
        while True:
            try:
                d = AudioUtilities.GetSpeakers()
                ident = getattr(d, "id", None) or d.GetId()
                nome = getattr(d, "FriendlyName", "") or ""
                if ident != self.saida_id:
                    if self.saida_id is not None:
                        self.saida_trocou = nome or "?"
                    self.saida_id, self.saida_nome = ident, nome
            except Exception:
                pass
            time.sleep(1.0)

    def _abrir(self, sr):
        import sounddevice as sd
        if self.saida_trocou and self.stream is not None:
            nome, self.saida_trocou = self.saida_trocou, None
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
            sd._terminate()                       # recarrega a lista de aparelhos (o padrao mudou)
            sd._initialize()
            log(f"🔈 Saída de som mudou: a voz agora sai em “{nome}”.", tipo="aviso")
            diag.ev("saida_trocou", aparelho=nome)
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
        try:
            diag.ev("saida_aberta", aparelho=sd.query_devices(self.stream.device)["name"])
        except Exception:
            pass

    def _escrever(self, bloco, sr):
        import sounddevice as sd
        esp = self.espelho
        if esp is not None:
            try:
                esp(bloco, sr)                    # Sala 3D: a mesma voz vai para quem joga
            except Exception:
                pass
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

    def _para_fundo(self, ger, wav, sr, info, fid):
        """Pedaco de uma fala de FUNDO (figurante): vai para a camada que o _escrever_mix soma
        por baixo da voz principal. Nao espera a fila: entra na hora, mais baixo."""
        info = info or {}
        if wav is None:
            self.fundo_ids.discard(fid)
            self.fundo_cortados.discard(fid)
            diag.ev("tocar_fim", id=fid, fundo=True)
            return
        if ger != self.ger or fid in self.fundo_cortados:
            return
        if info.get("primeiro"):
            atraso = time.time() - (info.get("visto") or time.time())
            lim = float(self.cfg.get("pular_atrasada_s", 3.0))
            if lim > 0 and atraso >= lim:
                self.fundo_cortados.add(fid)     # burburinho atrasado nao faz sentido: some
                diag.ev("pulou_fundo", id=fid, atraso_s=round(atraso, 2))
                return
            diag.ev("tocar_inicio", id=fid, atraso_ms=round(atraso * 1000), fundo=True)
            log(f"🔉 [{info.get('quem', '')} · {info.get('voz_nome', '')}] {info.get('texto', '')}   "
                f"(fundo, voz em {round(atraso * 1000)} ms)", tipo="fala")
        if sr != self.SR:
            wav = np.interp(np.arange(0, len(wav), sr / self.SR), np.arange(len(wav)), wav).astype(np.float32)
        ganho = float(self.cfg.get("fundo_volume", 0.55))
        with self.trava_fundo:
            self.fundo = np.concatenate([self.fundo, np.asarray(wav, np.float32).reshape(-1) * ganho])

    def _escrever_mix(self, bloco, sr):
        """Escreve no alto-falante somando a camada de fundo (figurantes) por baixo."""
        if len(self.fundo):
            with self.trava_fundo:
                k = min(len(bloco), len(self.fundo))
                f, self.fundo = self.fundo[:k], self.fundo[k:]
            bloco = np.array(bloco, np.float32).reshape(-1)
            bloco[:k] += f
            np.clip(bloco, -1.0, 1.0, out=bloco)
        self._escrever(bloco, sr)

    def _tocar(self):
        while True:
            try:
                ger, pag, wav, sr, info = self.audio.get(timeout=0.02)
            except queue.Empty:
                if self.falando and time.time() - (self.ult_fim_toque or 0) > 0.4:
                    self.falando = False
                    self.aviso("calado", {})
                if self.cfg.get("manter_audio_ligado", True) or len(self.fundo):
                    # manda silencio sem parar: placa/fone (Bluetooth, HDMI, USB) nunca "dorme",
                    # entao o comeco da proxima fala nao se perde (e o fundo toca nesse "silencio")
                    try:
                        self._escrever_mix(np.zeros(int(self.SR * 0.02), np.float32), self.SR)
                    except Exception:
                        time.sleep(0.02)
                continue
            fid = (info or {}).get("id")
            if fid in self.fundo_ids:
                self._para_fundo(ger, wav, sr, info, fid)
                continue
            if wav is None:                       # fim da fala: nao espera mais pedacos dela
                self.gerando_fim.add(fid)
                if pag == self.tocando:
                    self.acabou_atual = True      # o ultimo pedaco dela ja tocou (fila em ordem)
                diag.ev("tocar_fim", id=fid)
                self._ajustar_pedacos(fid)
                continue
            if self._velha(ger, pag):
                continue
            if info.get("primeiro") and self._atrasada_demais(pag, info):
                self.puladas.add(pag)            # os outros pedacos (e o resto da legenda) tambem
                diag.ev("pulou_meio", pag=pag, id=fid, nova=self.pag,
                        atraso_s=round(time.time() - self._inicio_ideal(pag, info["visto"]), 2))
                continue
            self.tocando = pag
            self.acabou_atual = False
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
                # (na voz rapida/online os pedacos chegam ~5x mais rapido do que tocam: nao espera)
                # voz clonada gerando a 2,5x+ (gerador rapido na placa): o 2o pedaco chega bem antes
                # do 1o acabar de tocar, esperar por ele so atrasava ~120 ms (16/11)
                folgada = (info.get("taxa") or 0) >= float(self.cfg.get("pre_buffer_taxa", 2.5))
                ate = time.time() + (0.0 if info.get("motor") == "rapida" or folgada else
                                     float(self.cfg.get("pre_buffer_ms", 120)) / 1000)
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
            dur_natural = len(wav) / sr           # duracao deste pedaco sem acelerar
            try:
                if info:
                    info["ideal"] = self._inicio_ideal(pag, info.get("visto"))
                    self.ideal_fim[pag] = info["ideal"]
                    if info.get("visto"):
                        # atraso DE VERDADE: da legenda aparecer ate o som sair agora (com as falas
                        # geradas em paralelo, a fala fica pronta antes e espera a anterior acabar)
                        info["atraso_ms"] = round((time.time() - info["visto"]) * 1000)
                    # atraso_ideal: descontando as frases anteriores da MESMA legenda (o que conta
                    # para acelerar; numa fala nova e igual ao atraso_ms)
                    diag.ev("tocar_inicio", id=fid, atraso_ms=info["atraso_ms"],
                            atraso_ideal_ms=round((time.time() - info["ideal"]) * 1000), fila_audio=self.audio.qsize())
                    self.falando = True
                    extra = f", +{round((info['pressa'] - 1) * 100)}%" if info.get("pressa", 1) > 1.01 else ""
                    log(f"🔊 [{info['quem']} · {info['voz_nome']}] {info['texto']}   "
                        f"(voz em {info['atraso_ms']} ms, {info['motor']}{extra})", tipo="fala")
                    self.aviso("falando", info)
                self.ideal_fim[pag] = self.ideal_fim.get(pag, time.time()) + dur_natural
                bloco = int(sr * 0.04)
                i, n, terminar, ritmo = 0, len(wav), False, 1.0
                # continua na velocidade em que o pedaco anterior DESTA fala terminou (sem vai-e-volta)
                if fid == self.ult_id and self.ritmo_fala > 1.01:
                    wav = acelerar(wav, self.ritmo_fala, sr)
                    n, ritmo = len(wav), self.ritmo_fala
                elif info:
                    r0 = self._ritmo_inicial(pag, info, self._restante(pag, n / sr))
                    if r0 > 1.03:
                        wav = acelerar(wav, r0, sr)
                        n, ritmo = len(wav), r0
                        diag.ev("acelerou", id=fid, fator=round(r0, 2), inicio=True)
                self.no_toque = True
                while i < n:
                    if ger != self.ger and not terminar:
                        if (n - i) / sr <= self.tol:
                            terminar = True
                        else:
                            fim = min(n, i + int(sr * 0.06))
                            self._escrever_mix(wav[i:fim] * np.linspace(1, 0, fim - i, dtype=np.float32), sr)
                            break
                    # ficou 2 falas atras: some suave e pula. Mas se falta menos de 1 s do pedaco, termina
                    # (15/11: cortou o "vamos saindo?" do fim com so 0,58 s faltando)
                    if (pag < self.pular_ate or pag in self.puladas) and (n - i) / sr > float(self.cfg.get("terminar_se_falta_s", 1.0)):
                        fim = min(n, i + int(sr * 0.08))
                        self._escrever_mix(wav[i:fim] * np.linspace(1, 0, fim - i, dtype=np.float32), sr)
                        log("⏭ Pulei o resto de uma fala que ficou muito atrás da tela.", tipo="aviso")
                        diag.ev("pulou", id=fid, resto_s=round((n - i) / sr, 2))
                        break
                    if (i // bloco) % 3 == 0:          # a cada 120 ms confere se precisa acelerar
                        alvo = self._ritmo(pag, self._restante(pag, (n - i) / sr) * ritmo)
                        if alvo - ritmo >= 0.05:          # dentro de uma fala so acelera, nunca desacelera
                            # muda aos poucos (ate 15% por vez), sem tranco; o tom nao muda
                            novo = ritmo + min(0.15, alvo - ritmo)
                            wav = np.concatenate([wav[:i], acelerar(wav[i:], novo / ritmo, sr)])
                            n, ritmo = len(wav), novo
                            diag.ev("acelerou", id=fid, fator=round(novo, 2))
                    self._escrever_mix(wav[i:i + bloco], sr)
                    i += bloco
                self.ult_id, self.ult_fim_toque, self.ritmo_fala = fid, time.time(), ritmo
                self.no_toque = False
            except Exception as e:
                self.no_toque = False
                log("Erro ao tocar:", repr(e), tipo="erro")
                time.sleep(0.5)
