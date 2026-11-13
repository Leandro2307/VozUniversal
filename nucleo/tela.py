# -*- coding: utf-8 -*-
"""Achar o jogo, capturar a tela, ler o texto (OCR do Windows) e achar a caixa de dialogo."""
import collections
import difflib
import os
import re
import time

import numpy as np
import psutil
import win32gui
import win32process
import winocr
from PIL import Image

from .util import FIM, RECURSOS, log, normalizar, semelhanca


# ----------------------------------------------------------------- janela do jogo (qualquer jogo)
class Janela:
    """Acompanha a janela do jogo. Sem jogo fixo, o jogo e a ultima janela grande que ficou em
    primeiro plano e nao e de um programa da lista de ignorados (navegador, explorer, etc.)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.raiz = None
        self.alvo = None
        self.exe = ""
        self.titulo = ""
        self.meu_pid = os.getpid()
        self.prox_busca = 0.0

    def _exe(self, h):
        try:
            _, pid = win32process.GetWindowThreadProcessId(h)
            if pid == self.meu_pid:
                return None
            return psutil.Process(pid).name().lower()
        except Exception:
            return None

    def _grande(self, h):
        try:
            if not win32gui.IsWindowVisible(h) or win32gui.IsIconic(h):
                return False
            _, _, r, b = win32gui.GetClientRect(h)
            return r >= 480 and b >= 270
        except Exception:
            return False

    def _serve(self, h):
        exe = self._exe(h)
        if not exe or exe in [p.lower() for p in self.cfg.get("ignorar_processos", [])]:
            return None
        return exe if self._grande(h) else None

    def _alvo_render(self, raiz):
        """Janela filha de renderizacao (a maior), sem menu/barra de status."""
        try:
            _, _, cw, ch = win32gui.GetClientRect(raiz)
        except Exception:
            return raiz
        filhos = []

        def cbf(c, _):
            try:
                if win32gui.IsWindowVisible(c):
                    _, _, r, b = win32gui.GetClientRect(c)
                    if r * b > 0.4 * cw * ch:
                        filhos.append((r * b, c))
            except Exception:
                pass
            return True

        try:
            win32gui.EnumChildWindows(raiz, cbf, None)
        except Exception:
            pass
        return max(filhos)[1] if filhos else raiz

    def _adotar(self, h, exe):
        mudou = h != self.raiz
        self.raiz, self.exe = h, exe
        self.alvo = self._alvo_render(h)
        try:
            self.titulo = win32gui.GetWindowText(h) or exe
        except Exception:
            self.titulo = exe
        return mudou

    def _procurar_fixo(self, exe_fixo):
        janelas = []

        def cb(h, _):
            if self._exe(h) == exe_fixo and self._grande(h):
                _, _, r, b = win32gui.GetClientRect(h)
                janelas.append((r * b, h))
            return True

        win32gui.EnumWindows(cb, None)
        return max(janelas)[1] if janelas else None

    def _exe_qualquer(self, h):
        try:
            _, pid = win32process.GetWindowThreadProcessId(h)
            return psutil.Process(pid).name().lower()
        except Exception:
            return "janela"

    def atualizar(self):
        """Confere de onde ler agora. -> True se trocou de fonte.

        cfg["fonte"]: "auto"   = o jogo que estiver em primeiro plano (ignora navegador etc.)
                      "tela"   = a tela inteira de um monitor (cfg["fonte_monitor"])
                      "janela" = uma janela escolhida no painel (cfg["fonte_hwnd"]), ex.: YouTube
        """
        fonte = self.cfg.get("fonte") or "auto"
        if fonte == "tela":
            mon = int(self.cfg.get("fonte_monitor") or 0)
            chave = f"tela_inteira_{mon + 1}"
            if self.exe != chave:
                self.raiz = self.alvo = win32gui.GetDesktopWindow()
                self.exe, self.titulo = chave, f"Tela inteira (monitor {mon + 1})"
                return True
            return False
        if fonte == "janela":
            h = int(self.cfg.get("fonte_hwnd") or 0)
            if h and self.raiz == h:
                try:
                    if win32gui.IsWindow(h):
                        try:
                            self.titulo = win32gui.GetWindowText(h) or self.titulo
                        except Exception:
                            pass
                        return False
                except Exception:
                    pass
            if h and win32gui.IsWindow(h):
                return self._adotar(h, self._exe_qualquer(h))
            # a janela escolhida fechou: tenta outra do mesmo programa
            exe = (self.cfg.get("fonte_exe") or "").lower()
            if exe and time.time() >= self.prox_busca:
                self.prox_busca = time.time() + 1.0
                h = self._procurar_fixo(exe)
                if h:
                    self.cfg["fonte_hwnd"] = h
                    return self._adotar(h, exe)
            if self.raiz:
                self.raiz, self.exe, self.titulo = None, "", ""
                return True
            return False
        fixo = (self.cfg.get("jogo_fixo") or "").lower()
        if fixo:
            if self.raiz and self.exe == fixo and self._grande(self.raiz):
                return False
            if time.time() < self.prox_busca:
                return False
            self.prox_busca = time.time() + 1.0
            h = self._procurar_fixo(fixo)
            if h:
                return self._adotar(h, fixo)
            self.raiz = None
            return False
        try:
            h = win32gui.GetForegroundWindow()
        except Exception:
            h = None
        if h and h != self.raiz:
            exe = self._serve(h)
            if exe:
                return self._adotar(h, exe)
        if self.raiz:
            try:
                ok = win32gui.IsWindow(self.raiz)
            except Exception:
                ok = False
            if not ok:
                self.raiz, self.exe, self.titulo = None, "", ""
                return True
        return False

    def area(self):
        """(x, y, w, h) em pixels de tela da area do jogo, ou None (minimizado/fechado)."""
        if not self.raiz:
            return None
        if self.exe.startswith("tela_inteira"):
            mons = monitores()
            m = mons[min(int(self.cfg.get("fonte_monitor") or 0), len(mons) - 1)]
            return m
        try:
            if win32gui.IsIconic(self.raiz) or not win32gui.IsWindowVisible(self.raiz):
                return None
            if not win32gui.IsWindow(self.alvo):
                self.alvo = self._alvo_render(self.raiz)
            _, _, w, h = win32gui.GetClientRect(self.alvo)
            x0, y0 = win32gui.ClientToScreen(self.alvo, (0, 0))
        except Exception:
            return None
        if w < 200 or h < 120:
            return None
        return (x0, y0, w, h)

    def a_vista(self, reg):
        """True se e o jogo que aparece em toda a regiao (nenhuma janela por cima)."""
        try:
            x0, y0 = reg["left"] + 4, reg["top"] + 4
            x1, y1 = reg["left"] + reg["width"] - 5, reg["top"] + reg["height"] - 5
            xm, ym = (x0 + x1) // 2, (y0 + y1) // 2
            tela = self.exe.startswith("tela_inteira")
            for x, y in ((xm, ym), (x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                h = win32gui.WindowFromPoint((int(x), int(y)))
                if tela:
                    # tela inteira: vale qualquer janela, menos a do proprio programa (senao ele
                    # le o proprio painel e fica falando o que ele mesmo escreveu)
                    _, pid = win32process.GetWindowThreadProcessId(win32gui.GetAncestor(h, 2))
                    if pid == self.meu_pid:
                        return False
                elif win32gui.GetAncestor(h, 2) != self.raiz:
                    return False
            return True
        except Exception:
            return True


def monitores():
    """[(x, y, w, h)] de cada monitor, o principal primeiro."""
    import win32api
    out = []
    try:
        for hm, _, r in win32api.EnumDisplayMonitors():
            info = win32api.GetMonitorInfo(hm)
            x0, y0, x1, y1 = info["Monitor"]
            out.append(((info.get("Flags", 0) & 1) == 0, (x0, y0, x1 - x0, y1 - y0)))
    except Exception:
        pass
    out.sort(key=lambda t: t[0])
    return [m for _, m in out] or [(0, 0, 1920, 1080)]


_NAO_LISTAR = {"textinputhost.exe", "nvidia overlay.exe", "applicationframehost.exe", "shellexperiencehost.exe",
               "searchhost.exe", "startmenuexperiencehost.exe", "lockapp.exe", "widgets.exe", "pythonw.exe"}


def listar_janelas():
    """Janelas abertas que podem ser lidas (para escolher no painel)."""
    meu = os.getpid()
    lista = []

    def cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h) or win32gui.GetWindow(h, 4):     # 4 = GW_OWNER
                return True
            titulo = win32gui.GetWindowText(h)
            if not titulo or titulo in ("Program Manager",):
                return True
            _, _, r, b = win32gui.GetClientRect(h)
            if r < 400 or b < 250:
                return True
            _, pid = win32process.GetWindowThreadProcessId(h)
            if pid == meu or titulo == "Voz Universal":
                return True
            exe = psutil.Process(pid).name()
            if exe.lower() in _NAO_LISTAR:
                return True
            lista.append({"hwnd": int(h), "titulo": titulo[:90], "exe": exe,
                          "minimizada": bool(win32gui.IsIconic(h))})
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return lista


def regiao_em_tela(area, frac):
    x, y, w, h = area
    return {"left": int(x + frac[0] * w), "top": int(y + frac[1] * h),
            "width": max(8, int((frac[2] - frac[0]) * w)), "height": max(8, int((frac[3] - frac[1]) * h))}


def capturar(sct, reg):
    im = sct.grab(reg)
    return Image.frombytes("RGB", im.size, im.bgra, "raw", "BGRX")


# ----------------------------------------------------------------- OCR
def ocr_linhas(img, lingua="pt-BR"):
    """-> [(texto, x0, y0, x1, y1)] em pixels da imagem."""
    esc = 1
    if img.height < 60:
        esc = 2
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    try:
        r = winocr.recognize_pil_sync(img.convert("RGBA"), lingua)
    except Exception:
        return []
    linhas = []
    for l in (r.get("lines", []) if isinstance(r, dict) else []):
        ws = l.get("words") or []
        if not ws:
            continue
        x0 = min(w["bounding_rect"]["x"] for w in ws)
        y0 = min(w["bounding_rect"]["y"] for w in ws)
        x1 = max(w["bounding_rect"]["x"] + w["bounding_rect"]["width"] for w in ws)
        y1 = max(w["bounding_rect"]["y"] + w["bounding_rect"]["height"] for w in ws)
        linhas.append((l.get("text", ""), x0 / esc, y0 / esc, x1 / esc, y1 / esc))
    return linhas


def limpar(t):
    t = t.replace("\n", " ")
    t = t.replace("l<", "k").replace("I<", "k").replace("|<", "k")
    t = re.sub(r"[^\w\s.,!?;:'\"()\-…]", " ", t)
    t = t.replace("_", " ")
    t = re.sub(r"\s+([.,!?;:…])", r"\1", t)
    t = re.sub(r"[.,]{2,}", lambda m: "..." if "." in m.group() else m.group(), t)
    t = re.sub(r"(?<=[a-zà-ÿ])[A-ZÀ-Ý](?=[a-zà-ÿ])", lambda m: m.group().lower(), t)
    # numero/parentese no lugar de letra no comeco da palavra: "0h" -> "Oh", "01?" -> "Oi?", "()i" -> "Oi"
    t = re.sub(r"(?<![\w(])\(\)(?=[a-zà-ÿ])", "O", t)
    t = re.sub(r"(?<![\w\d])0(?=[a-zà-ÿ])", "O", t)
    t = re.sub(r"(?<![\w\d])01(?=[?!])", "Oi", t)
    return re.sub(r"\s+", " ", t).strip()


def suavizar(img, lo=90, hi=200):
    """So o que e claro (texto branco) em preto sobre branco: 2a opiniao para o OCR."""
    g = np.asarray(img).min(-1).astype(np.float32)
    v = np.clip((g - lo) / (hi - lo), 0, 1)
    return Image.fromarray((255 - v * 255).astype(np.uint8), "L").convert("RGB")


def em_ordem(linhas):
    """Ordem de leitura: de cima para baixo e, na mesma linha, da esquerda para a direita."""
    if not linhas:
        return []
    linhas = sorted(linhas, key=lambda l: (l[2] + l[4]) / 2)
    ordenadas, fila = [], [linhas[0]]
    for l in linhas[1:]:
        ref = fila[-1]
        if (l[2] + l[4]) / 2 - (ref[2] + ref[4]) / 2 < 0.5 * (ref[4] - ref[2]):
            fila.append(l)
        else:
            ordenadas += sorted(fila, key=lambda k: k[1])
            fila = [l]
    return ordenadas + sorted(fila, key=lambda k: k[1])


def agrupar_blocos(linhas):
    """Junta as linhas que formam um mesmo bloco de texto (uma embaixo da outra)."""
    blocos = []
    for l in sorted(linhas, key=lambda k: k[2]):
        for b in blocos:
            ult = b[-1]
            h = ult[4] - ult[2]
            vao = l[2] - ult[4]
            sobre = min(l[3], max(k[3] for k in b)) - max(l[1], min(k[1] for k in b))
            if -0.4 * h <= vao <= 1.1 * h and sobre > 0 and 0.5 < (l[4] - l[2]) / max(1, h) < 1.8:
                b.append(l)
                break
        else:
            blocos.append([l])
    return blocos


def parece_fala(texto):
    """Frase de verdade, e nao rotulo de menu ou aviso de botao."""
    if len(re.findall(r"[^\W\d_]", texto)) < 3:
        return False
    fim = texto.rstrip("\"')")[-1:]
    n = len(texto.split())
    return (n >= 2 and fim in FIM + ",") or n >= 5 or (n == 1 and fim in "!?")


# ----------------------------------------------------------------- legendas de video
def _juntar_nome(esq, dir_):
    """Junta dois pedacos da mesma linha. Se o da esquerda e curto (1-2 palavras, comeca com
    maiuscula/letra confundivel) e o da direita e a frase, o da esquerda e o NOME em outra cor:
    garante o ":" mesmo que o OCR tenha lido "Dutclv", "l)urchí", "Hill(" ou nada."""
    e, d = esq.strip(), dir_.strip()
    ps = e.split()
    if (1 <= len(ps) <= 2 and len(d.split()) >= 2 and len(e) <= 22 and ":" not in e[:-1]
            and ps[0].lower().strip(":;í(") not in _NAO_COMECA_NOME and e.lower().strip(":;í(") not in _NAO_E_NOME
            and ps[0].lower().strip(":;í(") not in _VERBOS_OBJETIVO
            and (e[:1].isupper() or e[:2] in ("l)", "I)", "|)", "1)")) and e[-1:] not in ".!?,"):
        nome = re.sub(r"[:;í(!1]+$", "", e).strip()
        if len(re.sub(r"[^\wÀ-ÿ]", "", nome)) >= 2:
            return f"{nome}: {d}"
    return esq.rstrip() + " " + dir_.lstrip()


def juntar_mesma_linha(linhas, w):
    """Pedacos lidos separados que estao na MESMA linha e colados viram uma linha so
    (ex.: o nome "Swanson:" em cinza e a fala em branco: o OCR le como duas linhas)."""
    out = []
    for l in sorted(linhas, key=lambda k: ((k[2] + k[4]) / 2, k[1])):
        for i, o in enumerate(out):
            alt = max(o[4] - o[2], l[4] - l[2])
            if abs((o[2] + o[4]) / 2 - (l[2] + l[4]) / 2) < 0.45 * alt:
                esq, dir_ = (o, l) if o[1] <= l[1] else (l, o)
                if dir_[1] - esq[3] < 0.06 * w:
                    out[i] = (_juntar_nome(esq[0], dir_[0]), min(o[1], l[1]), min(o[2], l[2]),
                              max(o[3], l[3]), max(o[4], l[4]))
                    break
        else:
            out.append(tuple(l[:5]))
    return out


def caixa_alta(t, dic=None):
    """Creditos/titulos ("RICK STIRLING", "JAMES WHITLHOFT"): so maiusculas, sem pontuacao de frase
    e (com 2 palavras) a maioria nao e palavra do portugues. "NAO ACREDITO NISSO" continua valendo."""
    letras = [c for c in t if c.isalpha()]
    if len(letras) < 6 or sum(c.isupper() for c in letras) / len(letras) <= 0.85:
        return False
    # painel/leitura do jogo em maiusculas ("SISTEMA DE APOIO VITAL:", "ERRO 23", "CHEGADA: 191 s",
    # "HIDROXIZINA (171)"): numero, parenteses ou dois-pontos no fim. Fala gritada nao tem isso.
    if re.search(r"\d|\(|:\s*$", t):
        return True
    if t.rstrip()[-1:] in "!?":
        return False
    pal = [p.strip(".,;:-'()") for p in t.split() if p.strip(".,;:-'()")]
    # nome de item/lugar ("OFERENDA LUMINOSA", "TEMPLO DO TEMPO" fica de fora): 2 palavras, sem ponto
    if len(pal) == 2 and not re.search(r"[.,;…]", t):
        return True
    if len(pal) < 2:
        return False
    if dic is not None and dic.ok:
        from .identidade import nomes_proprios
        nomes = nomes_proprios()
        # nome proprio ("MICHAEL KANE") conta como "nao e palavra": e credito
        desconhecidas = sum(p.lower() in nomes or not dic.conhece(p.lower()) for p in pal)
        return desconhecidas / len(pal) >= 0.5
    return len(pal) >= 3


def lixo_ocr(t, dic):
    """-> (palavras com cara de erro de OCR, total). Nome proprio (Maiuscula + so letras) nao conta
    como erro; letra solta sem sentido ("c", "x") conta."""
    total = ruins = 0
    for tok in t.split():
        p = tok.strip(".,!?;:…\"'()-")
        if not p:
            continue
        total += 1
        if len(p) == 1:
            ruins += p.lower() not in "aeoéàyi"
        elif p[0].isupper() and p.isalpha() and len(p) >= 3:
            continue
        elif not dic.conhece(p):
            ruins += 1
    return ruins, total


def rotulos(t):
    """Painel/menu do jogo ("Cadência Recarga Precisão Condição REGULAR"): 3+ palavras, quase todas
    com Maiuscula e nenhuma pontuacao de frase. Frase de verdade quase nunca e assim."""
    if re.search(r"[.!?,;…:]", t):
        return False
    ps = [p for p in t.split() if any(c.isalpha() for c in p)]
    if len(ps) < 3:
        return False
    return sum(p[0].isupper() for p in ps) / len(ps) >= 0.75


def achar_legenda(img, linhas=None, dic=None, motivos=None):
    """Na parte de baixo de um video, acha o bloco que e a LEGENDA: centralizado na horizontal
    (titulo de capitulo do YouTube, relogio e botoes ficam de lado), com cara de frase.
    Entre varios, fica com o que tem mais texto e esta mais embaixo. -> linhas do bloco ou None.
    motivos (lista): recebe (texto, por que foi descartado) de cada bloco, para o diagnostico."""
    if linhas is None:
        linhas = [l for l in ocr_linhas(img) if len(re.findall(r"[^\W_]", l[0])) >= 2]
    w, h = img.size
    linhas = juntar_mesma_linha(linhas, w)
    melhor, nota_m = None, 0.0

    def fora(t, m):
        if motivos is not None:
            motivos.append((t[:60], m))

    for b in agrupar_blocos(linhas):
        b = em_ordem(b)
        x0, x1 = min(l[1] for l in b), max(l[3] for l in b)
        bruto = " ".join(l[0] for l in b)
        t = limpar(bruto)
        # capitulo do YouTube na barra do player ("Final go... >"): o limpar tira a setinha
        if re.search(r"(\.\.\.|…)\s*[>›»]\s*$", bruto):
            fora(t, "capitulo do YouTube")
            continue
        com_nome = bool(_PREFIXO.match(consertar_prefixo(b[0])[0]))
        if abs((x0 + x1) / 2 / w - 0.5) > 0.12 and not com_nome:
            fora(t, "fora do centro")
            continue                                   # legenda e centralizada
        # menu de escolhas (Cyberpunk: "Fale sobre Westbrook" / "Quantos maridos..."): 3+ linhas
        # ALINHADAS A ESQUERDA (mesmo x inicial, larguras diferentes); legenda e centralizada
        if len(b) >= 3 and not com_nome:
            xs = [l[1] for l in b]
            larguras = [l[3] - l[1] for l in b]
            if max(xs) - min(xs) <= 0.012 * w and max(larguras) - min(larguras) >= 0.12 * w:
                fora(t, "menu de escolhas")
                continue
        if len(t.split()) < 2 and t[-1:] not in "!?":
            fora(t, "palavra solta")
            continue                                   # palavra solta: botao/rotulo
        if not com_nome and len(t.split()) <= 3 and t.rstrip("\"')")[-1:] not in FIM + ",":
            fora(t, "rotulo curto do jogo")
            continue                                   # "Mais rápido", "Acelerar (Segurar)": HUD
        if re.fullmatch(r"[\d:/ .-]+", t):
            fora(t, "relogio")
            continue                                   # relogio do video
        if eh_interface(t):
            fora(t, "interface do site")
            continue
        if caixa_alta(t, dic):
            fora(t, "creditos em caixa alta")
            continue
        if rotulos(t):
            fora(t, "rotulos da tela do jogo")
            continue
        if dic is not None and dic.ok:
            ruins, total = lixo_ocr(t, dic)
            if total and ruins / total > 0.6 and total <= 6:
                fora(t, f"lixo do OCR ({ruins}/{total} palavras estranhas)")
                continue
        nota = len(t) + 40 * (max(l[4] for l in b) / h)
        if _PREFIXO.match(consertar_prefixo(b[0])[0]):
            nota += 120            # "Nome: fala" e dialogo; dica de botao/tutorial fica atras
        if re.search(r"\b(aperte|pressione|segure|use|clique|toque)\b", t, re.I):
            nota -= 60             # instrucao de controle do jogo
        if nota > nota_m:
            melhor, nota_m = b, nota
    return melhor


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    uniao = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / uniao if uniao > 0 else 0.0


class DetectorVideo:
    """Acha onde esta o VIDEO na tela pelo movimento.

    - So olha dentro da janela da frente (o navegador/jogo): o que muda em outras janelas
      (um terminal, um chat) nunca entra.
    - Junta as manchas de movimento dos ultimos 20 s: numa cena calma so um pedaco se mexe, mas
      somando as cenas o retangulo cobre o video inteiro.
    - Se a janela da frente muda (entrou/saiu da tela cheia, foi movida), recomeca na hora.
    Guarda o ultimo retangulo bom (video pausado ou cena escura nao apagam)."""

    W, H = 192, 108
    JANELA_S = 20.0

    def __init__(self):
        self.ant = None
        self.calor = None
        self.rect = None          # [x0, y0, x1, y1] em fracoes da tela
        self.confianca = 0.0
        self.manchas = collections.deque(maxlen=200)    # (instante, retangulo)
        self.limite = None        # janela da frente em fracoes da tela
        self.assinatura = None
        self.desde = time.time()

    def zerar(self):
        self.__init__()

    def definir_limite(self, assinatura, limite):
        """Janela da frente (onde o video pode estar). Mudou -> esquece tudo e recomeca."""
        if assinatura != self.assinatura:
            self.__init__()
            self.assinatura = assinatura
            self.limite = limite
            self.desde = time.time()
            return True
        return False

    def olhar(self, img):
        a = np.asarray(img.convert("L").resize((self.W, self.H), Image.BILINEAR), np.float32)
        if self.ant is not None and a.shape == self.ant.shape:
            mudou = (np.abs(a - self.ant) > 10).astype(np.float32)
            self.calor = mudou if self.calor is None else self.calor * 0.86 + mudou * 0.14
        self.ant = a
        if self.calor is None:
            return self.rect
        from scipy import ndimage
        calor = self.calor
        if self.limite:
            m = np.zeros_like(calor)
            x0, y0, x1, y1 = self.limite
            m[int(y0 * self.H):int(np.ceil(y1 * self.H)), int(x0 * self.W):int(np.ceil(x1 * self.W))] = 1
            calor = calor * m
        ativo = ndimage.binary_closing(calor > 0.2, structure=np.ones((3, 3)), iterations=1)
        rotulos, n = ndimage.label(ativo)
        agora = time.time()
        if n:
            tamanhos = ndimage.sum(ativo, rotulos, range(1, n + 1))
            for i, (ys, xs) in enumerate(ndimage.find_objects(rotulos)):
                caixa = (ys.stop - ys.start) * (xs.stop - xs.start)
                cheio = tamanhos[i] / max(1, caixa)
                # mancha de video: razoavel e CHEIA (texto rolando e o mouse sao ralos/pequenos)
                if caixa / (self.W * self.H) >= 0.01 and cheio >= 0.35:
                    self.manchas.append((agora, [xs.start / self.W, ys.start / self.H,
                                                 xs.stop / self.W, ys.stop / self.H]))
                    self.confianca = round(float(cheio), 2)
        recentes = [r for t, r in self.manchas if agora - t <= self.JANELA_S]
        if not recentes:
            return self.rect
        r = [min(x[0] for x in recentes), min(x[1] for x in recentes),
             max(x[2] for x in recentes), max(x[3] for x in recentes)]
        if (r[2] - r[0]) * (r[3] - r[1]) >= 0.03:
            self.rect = r
        return self.rect


# ----------------------------------------------------------------- dicionario (conserta o OCR)
class Dicionario:
    TROCAS = [("ri", "n"), ("rn", "m"), ("m", "rn"), ("ii", "n"), ("cl", "d"), ("vv", "w"),
              ("1", "l"), ("0", "o"), ("I", "l"), ("l", "I"), ("l", "i"), ("i", "l")]
    ACENTOS = {"a": "áãâà", "e": "éê", "i": "í", "o": "óõô", "u": "ú", "c": "ç"}

    def __init__(self):
        self.freq = {}
        caminho = os.path.join(RECURSOS, "palavras_pt.txt")
        if os.path.exists(caminho):
            try:
                with open(caminho, encoding="utf-8") as f:
                    for linha in f:
                        p = linha.split()
                        if len(p) == 2 and p[1].isdigit():
                            self.freq[p[0]] = int(p[1])
            except Exception as e:
                log("Dicionário não carregado:", e)
        self.ok = len(self.freq) > 1000

    def aprender(self, palavra):
        self.freq[palavra.lower()] = 10 ** 7

    def conhece(self, tok):
        p = tok.strip(".,!?;:…\"'()-").lower()
        if not p or p.isdigit():
            return True
        f = self.freq.get(p, 0)
        if f:
            return f >= (20000 if len(p) <= 2 else 300 if len(p) == 3 else 1)
        if "-" in p:
            return all(self.conhece(x) for x in p.split("-"))
        return False

    def desconhecidas(self, texto):
        return sum(not self.conhece(t) for t in texto.split())

    def fundir(self, a, b):
        ta, tb = a.split(), b.split()
        sm = difflib.SequenceMatcher(None, [normalizar(t) for t in ta], [normalizar(t) for t in tb], autojunk=False)
        out = []
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            sa, sb = ta[i1:i2], tb[j1:j2]
            if op == "equal" or not sa or not sb:
                out += sa
            elif (self.desconhecidas(" ".join(sb)) < self.desconhecidas(" ".join(sa))
                  and semelhanca(normalizar("".join(sa)), normalizar("".join(sb))) >= 0.5):
                out += sb
            else:
                out += sa
        return " ".join(out)

    def acentuar(self, tok):
        m = re.match(r"^(\W*)(.*?)(\W*)$", tok, re.S)
        ini, miolo, fim = m.groups()
        base = miolo.lower()
        if len(base) < 2 or "-" in base or not base.isalpha():
            return tok
        f0 = self.freq.get(base, 0)
        melhor, fmelhor = None, max(1000, 20 * f0)
        for i, ch in enumerate(base):
            for ac in self.ACENTOS.get(ch, ""):
                c = base[:i] + ac + base[i + 1:]
                f = self.freq.get(c, 0)
                if f >= fmelhor:
                    melhor, fmelhor = c, f
        if not melhor:
            return tok
        if miolo[:1].isupper():
            melhor = melhor[:1].upper() + melhor[1:]
        return ini + melhor + fim

    def separar_grudadas(self, tok):
        """Palavra desconhecida que e duas conhecidas grudadas (o OCR come o espaco em letra miuda)."""
        m = re.match(r"^(\W*)([^\W\d_]+)(\W*)$", tok)
        if not m or len(m.group(2)) < 4:
            return tok
        ini, pal, fim = m.groups()
        if pal.lower() in self.GRUDADAS:
            novo = self.GRUDADAS[pal.lower()]
            return ini + (novo[:1].upper() + novo[1:] if pal[:1].isupper() else novo) + fim
        if self.freq.get(pal.lower(), 0) > 0:
            return tok              # palavra que existe ("portador", "títulos") nunca e separada
        base = pal.lower()
        melhor, nota_m = None, 0
        for i in range(1, len(base)):
            a, fa = self._melhor_variante(base[:i])
            b, fb = self._melhor_variante(base[i:])
            # as duas partes precisam ser palavras bem comuns (senao separa nome proprio a toa)
            def minimo(x):
                return 50000 if len(x) <= 2 else 3000 if len(x) <= 5 else 300
            if fa >= minimo(a) and fb >= minimo(b):
                nota = min(fa, fb)
                if nota > nota_m and nota > 2 * self.freq.get(base, 0):
                    melhor, nota_m = (a, b), nota
        if melhor is None:
            return tok
        a, b = melhor
        if pal[:1].isupper():
            a = a[:1].upper() + a[1:]
        return ini + a + " " + b + fim

    def _separar_texto(self, texto):
        """Separa as palavras grudadas, menos nome proprio: palavra com Maiuscula fora do comeco
        da frase ("Javier", nao "Ja vier") e nomes da lista ficam como estao."""
        from .identidade import nomes_proprios
        nomes = nomes_proprios()
        out, inicio = [], True
        for t in texto.split():
            limpo = t.strip(".,!?;:…\"'()-").lower()
            if (t[:1].isupper() and not inicio) or limpo in nomes:
                out.append(t)
            else:
                out.append(self.separar_grudadas(t))
            inicio = t.rstrip("\"')")[-1:] in FIM
        return " ".join(out)

    # juntas que o OCR produz sempre e que o dicionario aceita como palavra ("oque")
    GRUDADAS = {"oque": "o que", "doque": "do que", "porisso": "por isso"}

    # "m" lido como "rn" em palavras curtas que tambem existem em ingles (por isso o dicionario
    # nao estranhava): "Born" -> "Bom", "Bern" -> "Bem", "corn" -> "com"
    RN_M = {"born": "bom", "bern": "bem", "corn": "com", "sern": "sem", "ern": "em", "urn": "um",
            "tarnbém": "também", "rnas": "mas", "rnais": "mais", "rnuito": "muito", "rneu": "meu",
            "rninha": "minha", "rnesmo": "mesmo", "rnorto": "morto", "rnorrer": "morrer"}

    def _juntar_terminacao(self, texto):
        """Futuro/condicional partido pelo OCR: "afundar ei" -> "afundarei", "faria m" -> "fariam"."""
        def junta(m):
            inteira = m.group(1) + m.group(2)
            if self.conhece(inteira.lower()):
                return inteira
            # futuro = infinitivo + terminacao (o dicionario nao tem todos: "afundarei")
            if (m.group(2) in ("ei", "emos", "ão") and re.search(r"(ar|er|ir)$", m.group(1))
                    and self.conhece(m.group(1).lower())):
                return inteira
            return m.group(0)
        return re.sub(r"\b(\w{3,}(?:ar|er|ir|ri|ra))\s(ei|emos|ão|ia|iam|ás|á|m)\b", junta, texto)

    def _rn_por_m(self, tok):
        m = re.match(r"^(\W*)(.*?)(\W*)$", tok, re.S)
        ini, miolo, fim = m.groups()
        novo = self.RN_M.get(miolo.lower())
        if not novo:
            return tok
        if miolo[:1].isupper():
            novo = novo[:1].upper() + novo[1:]
        return ini + novo + fim

    TROCA_ACENTO = str.maketrans({"à": "ã", "é": "ê", "ê": "é", "ó": "ô", "ô": "ó", "á": "â", "â": "á",
                                  "À": "Ã", "É": "Ê", "Ó": "Ô"})
    TROCA_TIL = str.maketrans({"à": "ã", "á": "ã", "â": "ã", "ó": "õ", "ò": "õ", "ô": "õ"})

    def trocar_acento(self, tok):
        """ "nào" -> "não", "Entào" -> "Então", "vocé" -> "você": o OCR troca o tipo de acento.
        So troca se a palavra lida nao existe (ou quase nao aparece) e a trocada e comum."""
        m = re.match(r"^(\W*)(.*?)(\W*)$", tok, re.S)
        ini, miolo, fim = m.groups()
        if not miolo or not re.search(r"[àéêóôáâÀÉÓ]", miolo):
            return tok
        f0 = self.freq.get(miolo.lower(), 0)
        for tabela in (self.TROCA_ACENTO, self.TROCA_TIL):
            novo = miolo.translate(tabela)
            if novo == miolo:
                continue
            fn = self.freq.get(novo.lower(), 0)
            if fn >= max(1000, 20 * f0):
                return ini + novo + fim
            # palavra lida nao existe e a trocada termina em -ão/-ões/-ães: o OCR troca o til
            # ("Deportaçào" -> "Deportação", "distraçóes" -> "distrações"), mesmo palavra rara
            if f0 == 0 and fn >= 1 and re.search(r"(ão|ões|ães|ãe)$", novo.lower()):
                return ini + novo + fim
        return tok

    def _melhor_variante(self, p):
        """p com o acento que o OCR confunde trocado (é/ê, ó/ô, á/â) -> (forma mais comum, frequencia)."""
        trocas = str.maketrans({"é": "ê", "ê": "é", "ó": "ô", "ô": "ó", "á": "â", "â": "á"})
        cands = {p, p.translate(trocas)}
        if len(p) >= 2:
            cands.add(self.acentuar(p).lower())
        melhor = max(cands, key=lambda c: self.freq.get(c, 0))
        return melhor, self.freq.get(melhor, 0)

    def consertar(self, texto):
        out, inicio = [], True
        # "H" lido como "1--l"/"I-I" no meio da fala ("1--losea" -> "Hosea", "1--lobart" -> "Hobart")
        texto = re.sub(r"(?<![\w-])[1Il|]-{1,2}[lI|1](?=[a-zà-ÿ]{2})", "H", texto)
        texto = self._separar_texto(texto)
        texto = self._juntar_terminacao(texto)
        for tok in texto.split():
            novo = self._rn_por_m(tok)
            if novo == tok:
                novo = self.trocar_acento(tok)
            if novo == tok:
                novo = self.acentuar(tok)
            if novo != tok:
                out.append(novo)
                inicio = tok[-1:] in FIM
                continue
            if not self.conhece(tok) and (inicio or not tok[:1].isupper()):
                m = re.match(r"^(\W*)(.*?)(\W*)$", tok, re.S)
                ini, miolo, fim = m.groups()
                cands = {}
                for de, para in self.TROCAS:
                    pos = miolo.find(de)
                    while pos >= 0:
                        c = miolo[:pos] + para + miolo[pos + len(de):]
                        if len(c) >= 3 and self.freq.get(c.lower(), 0) >= 1000:
                            ant = cands.get(c.lower())
                            if ant is None or sum(x.isupper() for x in c[1:]) < sum(x.isupper() for x in ant[1:]):
                                cands[c.lower()] = c
                        pos = miolo.find(de, pos + 1)
                if len(cands) == 1:
                    novo = ini + next(iter(cands.values())) + fim
            out.append(novo)
            inicio = tok[-1:] in FIM
        return " ".join(out)


# ----------------------------------------------------------------- nome de quem fala
_PREFIXO = re.compile(r"^\s*[\[【(]?\s*([A-ZÀ-Ý][\wÀ-ÿ'.\- ]{1,26}?)\s*[\]】)]?\s*[:：]\s*(.+)$")
_PALAVRAS_NAO_NOME = {"sim", "nao", "não", "ok", "voltar", "sair", "continuar", "menu", "opcoes", "opções",
                      "salvar", "carregar", "pular", "falar", "abrir", "pegar", "aceitar", "cancelar"}


_NOME_NO_MEIO = re.compile(r"[.!?…]\s+([A-ZÀ-Ý][\wÀ-ÿ'\-]{1,20}(?: [A-ZÀ-Ý][\wÀ-ÿ'\-]{1,20})?)\s*:\s+\S")
_NOME_NO_MEIO_SOLTO = re.compile(r"\s([A-ZÀ-Ý][\wÀ-ÿ'\-]{1,20}):\s+\S")
# palavras que vem antes de ":" mas NAO sao nome de quem fala
_NAO_E_NOME = {"atencao", "atenção", "dica", "nota", "aviso", "objetivo", "missao", "missão", "capitulo",
               "capítulo", "parte", "fase", "local", "hora", "obs", "importante", "novo", "nova",
               "sinopse", "descrição", "descricao", "resumo", "legenda", "legendas", "inscrito", "inscritos",
               "visualizações", "comentários", "comentarios", "capítulos", "transcrição", "link", "site"}

# textos da interface do YouTube/navegador
INTERFACE = re.compile(r"\b(pesquisar|inscrever-se|mostrar mais|mostrar menos|compartilhar|salvar|"
                       r"reproduzir|anúncio|próximo|pular)\b", re.I)            # so em texto curto (botao)
INTERFACE_SEMPRE = re.compile(r"(visualizaç|inscritos|pular anúncio|\bhá \d+ (anos?|meses|dias)\b|"
                              r"\besc\b|full ?screen|sair da tela cheia)", re.I)


def eh_interface(t):
    # botao/rotulo: ate 3 palavras e sem pontuacao de frase ("Vamos pesquisar a cabana." e fala)
    botao = len(t.split()) <= 3 and t.rstrip()[-1:] not in ".!?…"
    # dica da aba do navegador: "(257) Cyberpunk 2077 - O Filme (Dublado..." (contador de avisos)
    if re.match(r"^\W{0,2}\(\d{1,4}\)\s+\S", t.strip()):
        return True
    return bool(INTERFACE_SEMPRE.search(t)) or (botao and bool(INTERFACE.search(t)))


# instrucao de controle do jogo ("Aperte O para acalmar o cavalo", "Segure para mirar") e acao com o
# botao no fim ("Abrir algibeira O", "Falar A", "Pular +")
DICA_CONTROLE = re.compile(r"^(aperte|pressione|segure|mantenha|use|clique|toque|solte)\b.*\bpara\b"
                           r"|^(\S+\s){0,3}\S+\s(?:[A-Z]|[+\-]|LB|RB|LT|RT|ZL|ZR|L|R)$", re.I)

# barra de comandos do jogo ("Mover para frente trás Co Girar (segurar) n Cancelar"): os botoes viram
# lixo no OCR e cada comando comeca com maiuscula
_COMANDOS = set("""mover girar cancelar segurar separar confirmar voltar selecionar equipar soltar pular
 mirar agachar correr interagir acelerar frear trocar usar abrir fechar pegar largar lançar lancar atirar
 recarregar mapa menu inventário inventario pausar continuar sair ampliar reduzir zoom rotacionar anexar
 fixar montar desmontar assobiar posição posicao""".split())


def eh_barra_de_comandos(t):
    t = t.strip()
    if not t or t[-1:] in ".!?…\"'”":
        return False
    pal = re.findall(r"[A-Za-zÀ-ÿ]+", t)
    if re.search(r"\((segurar|manter|pressionar|segure)\)", t, re.I):
        return True
    # nome de item no menu ("Arco de constructo reforçado", "Bastão de madeira maciça")
    if re.fullmatch(r"[A-ZÀ-Ý][a-zà-ÿ]+ d[eao]s? [a-zà-ÿ]+(?: [a-zà-ÿ]+){0,2}", t):
        return True
    caps = [i for i, p in enumerate(pal) if p[:1].isupper() and p.lower() in _COMANDOS]
    if len(caps) >= 2 or any(i > 0 for i in caps):
        return True
    # rotulo curto que comeca com um comando no infinitivo ("Girar na vertical", "Acoplar um material 020")
    p0 = pal[0] if pal else ""
    infinitivo = p0[:1].isupper() and len(p0) >= 4 and p0[-2:].lower() in ("ar", "er", "ir")
    return (bool(caps) or infinitivo) and len(pal) <= 5


# objetivos/instrucoes de jogo comecam com verbo no imperativo: nunca sao nome de quem fala
_VERBOS_OBJETIVO = set("""derrote siga va vá pegue mate encontre volte proteja persiga use monte fuja ajude
 atire investigue aproxime-se aproxime reverta conecte entre saia suba desca desça corra pule colete leve
 traga fale espere escape esconda-se esconda segure aperte pressione mire procure vasculhe roube amarre
 acompanhe alcance destrua elimine defenda sobreviva escolte cavalgue galope beba coma durma socialize
 converse cumprimente dance brinde explore visite compre venda entregue retorne vá descubra observe""".split())


def _cara_de_nome(t):
    t = t.strip()
    if not t or t[-1:] in FIM + ",;" or len(t) > 28:
        return False
    ps = t.split()
    if not 1 <= len(ps) <= 3:
        return False
    if normalizar(t) in _PALAVRAS_NAO_NOME:
        return False
    if t.strip().lower() in _NAO_E_NOME:
        return False
    if len(ps) >= 2 and ps[-1].isdigit():          # "Guarda 2", "Soldado 1"
        ps = ps[:-1]
        t = " ".join(ps)
        if t.lower() in _NAO_E_NOME:               # "Capítulo 2", "Parte 3" nao sao personagem
            return False
    letras = re.sub(r"[^\wÀ-ÿ]", "", t)
    if len(letras) < 2 or any(c.isdigit() for c in letras):
        return False
    if ps[0].lower() in _VERBOS_OBJETIVO:
        return False                                  # "Derrote o atacante" nao e "Derrote: ..."
    # Nome Proprio, NOME EM CAIXA ALTA, ou "Dono da Loja"
    return ps[0][:1].isupper() and all(p[:1].isupper() or len(p) <= 3 for p in ps)


_OCR_INICIO = [(r"^[lI|1]\)", "D"), (r"^\(\)", "O"), (r"^0(?=[a-z])", "O"), (r"^1(?=[a-z])", "I"),
               (r"^[lI|]<", "K"), (r"^\|(?=[a-z])", "I"), (r"^I\.(?=[a-z])", "L"), (r"^l\.(?=[a-z])", "L"),
               (r"^li(?=[aeiouo][a-z])", "H"), (r"^I-I(?=[a-z])", "H"), (r"^l-l(?=[a-z])", "H"),
               (r"^Il(?=[aeiou][a-z])", "H"), (r"^[I|l]['’`](?=[a-z]{4})", "T"),
               (r"^[lI|]h(?=[a-z]{3,})", "D")]   # "lhltch" -> "Dltch" (Dutch)   # "I'estemunha"
# 1a palavra que nunca e nome de quem fala ("Ele disse: ..." / "Eu acho: ...")
_NAO_COMECA_NOME = {"ele", "ela", "eu", "voce", "você", "nos", "nós", "eles", "elas", "isso", "isto", "aqui",
                    "ali", "la", "lá", "mas", "e", "o", "a", "os", "as", "um", "uma", "se", "quando", "entao",
                    "então", "por", "para", "que", "como", "veja", "olha", "disse", "tipo"}


def consertar_prefixo(linha):
    """Arruma o comeco de uma linha "Nome: fala" lida com erro ("l)utch:" -> "Dutch:")."""
    t = linha[0]
    # tracinho/aspas soltos antes do nome ("- Ilosea: E melhor...")
    sem_lixo = re.sub(r"^[\s\-–—•·'\"]+(?=[A-Za-zÀ-ÿ|(])", "", t)
    if sem_lixo != t and re.match(r"^\S{2,20}\s*:", sem_lixo):
        t = sem_lixo
        linha = (t,) + tuple(linha[1:])
    # o ":" depois do nome as vezes sai como "í", "lv" ou "(": "I)utchí Isso..." -> "I)utch: Isso..."
    m0 = re.match(r"^\s*(\S{2,20}?)(í|lv|\()\s+(?=[A-ZÀ-Ý])", t)
    if m0 and (m0.group(1)[:1].isupper() or m0.group(1)[:2] in ("l)", "I)", "|)", "1)")):
        t = m0.group(1) + ": " + t[m0.end():]
        linha = (t,) + tuple(linha[1:])
    m = re.match(r"^\s*(\S{2,20})(\s*:)", t)
    if not m:
        return linha
    nome = m.group(1)
    for de, para in _OCR_INICIO:
        nome = re.sub(de, para, nome)
    if nome != m.group(1):
        return (nome + t[m.end(1):],) + tuple(linha[1:])
    return linha


def _nome_com_minuscula(t):
    """ "Pessoa desconhecida", "Homem misterioso": 1a maiuscula, ate 3 palavras, nao comeca
    com pronome/verbo (senao "Ele disse: ..." viraria nome)."""
    ps = t.strip().split()
    if len(ps) >= 2 and ps[-1].isdigit():          # "Pessoa desconhecida 2"
        ps = ps[:-1]
        t = " ".join(ps)
    if t.strip().lower() in _NAO_E_NOME:
        return False
    return (1 <= len(ps) <= 3 and ps[0][:1].isupper() and ps[0].lower() not in _NAO_COMECA_NOME
            and ps[0].lower() not in _VERBOS_OBJETIVO        # "Socialize com: os convidados"
            and len(t) <= 28 and all(p.replace("-", "").isalpha() for p in ps))


def separar_nome(linhas, zona_nome=None, tam=None):
    """linhas ja em ordem de leitura -> (nome, linhas_da_fala).

    Jeitos de mostrar o nome que da para reconhecer em qualquer jogo:
      - numa zona marcada pelo usuario (zona_nome, em fracoes da regiao);
      - "Nome: fala..." no comeco do texto;
      - uma linha curta em cima, sozinha, com cara de nome (plaquinha / titulo da caixa).
    """
    if not linhas:
        return "", []
    linhas = [consertar_prefixo(l) for l in linhas]
    if zona_nome and tam:
        w, h = tam
        dentro = [l for l in linhas if zona_nome[0] * w <= (l[1] + l[3]) / 2 <= zona_nome[2] * w
                  and zona_nome[1] * h <= (l[2] + l[4]) / 2 <= zona_nome[3] * h]
        if dentro:
            return " ".join(l[0] for l in dentro).strip(), [l for l in linhas if l not in dentro]
    # Duas legendas empilhadas na troca (a velha em cima, a nova embaixo): se uma linha de baixo
    # comeca com "Nome:", a fala atual comeca ali. Tambem dentro da mesma linha:
    # "...um deles. Arthur: Nesse frio?" -> "Arthur: Nesse frio?"
    for i in range(len(linhas) - 1, -1, -1):
        txt = linhas[i][0]
        m_meio = _NOME_NO_MEIO.search(txt)
        if not m_meio:
            # sem pontuacao antes ("...equipamentos Dutch: e uma saida"): so vale nome CONHECIDO
            m2 = _NOME_NO_MEIO_SOLTO.search(txt)
            if m2:
                from .identidade import nomes_proprios
                if m2.group(1).lower() in nomes_proprios():
                    m_meio = m2
        if m_meio and _cara_de_nome(m_meio.group(1)):
            linhas = [(txt[m_meio.start(1):],) + tuple(linhas[i][1:])] + list(linhas[i + 1:])
            break
        if i > 0:
            m2 = _PREFIXO.match(txt)
            if m2 and _cara_de_nome(m2.group(1)):
                linhas = list(linhas[i:])
                break
    primeira = linhas[0]
    # "Pessoa desconhecida 2:" com sujeira antes ("-4 ", "av-", "V)essoa", ".\"Qessoa")
    m_pd = re.match(r"^.{0,5}?\w?essoa\s+desconhecid[ao](?:\s+(\d{1,2}))?\s*[:;]\s*(\S.*)$", primeira[0], re.I)
    if m_pd:
        nome_pd = "Pessoa desconhecida" + (f" {m_pd.group(1)}" if m_pd.group(1) else "")
        return nome_pd, [(m_pd.group(2),) + tuple(primeira[1:])] + linhas[1:]
    m = _PREFIXO.match(primeira[0])
    if m and m.group(2)[:1].isdigit():
        m = None                       # "São 10:30 da manhã": horario, nao "Nome: fala"
    if m and (_cara_de_nome(m.group(1)) or _nome_com_minuscula(m.group(1))):
        resto = (m.group(2),) + tuple(primeira[1:])
        return m.group(1).strip(), [resto] + linhas[1:]
    # lixo antes do nome ("' hornas l)ownes: Eu nao tenho nada."): tenta so a ultima palavra
    # antes dos dois-pontos, com as correcoes de OCR ("l)ownes" -> "Downes")
    m_ult = re.match(r"^(.{1,12}?)\s(\S{3,20})\s*:\s+(\S.*)$", primeira[0])
    lixo_antes = m_ult and (re.search(r"[^\w\s]", m_ult.group(1))
                            or (len(m_ult.group(1).split()) == 1 and len(m_ult.group(1).strip()) <= 6))
    if m_ult and lixo_antes and not m_ult.group(3)[:1].isdigit():
        ult = consertar_prefixo((m_ult.group(2) + ": x",))[0].split(":")[0]
        if ult[:1].isupper() and _cara_de_nome(ult) and ult.lower() not in _NAO_COMECA_NOME:
            return ult, [(m_ult.group(3),) + tuple(primeira[1:])] + linhas[1:]
    if len(linhas) >= 2 and _cara_de_nome(primeira[0]):
        seg = linhas[1]
        sozinha = not any(abs(((l[2] + l[4]) / 2) - ((primeira[2] + primeira[4]) / 2)) < 0.5 * (primeira[4] - primeira[2])
                          for l in linhas[1:])
        curta = (primeira[3] - primeira[1]) < 0.6 * max(l[3] - l[1] for l in linhas[1:])
        if sozinha and curta and seg[2] >= primeira[4] - 2:
            return primeira[0].strip(" :"), linhas[1:]
    return "", linhas


def nome_estavel(ultimos):
    """Nome de quem fala a partir das ultimas leituras (o OCR do nome falha as vezes)."""
    l = [n for n in ultimos if n]
    if not l:
        return ""
    if len(l) >= 2 and l[-1] == l[-2]:
        return l[-1]
    n, k = collections.Counter(l).most_common(1)[0]
    return n if k >= 2 or len(l) == 1 else l[-1]


# ----------------------------------------------------------------- achar a caixa de dialogo sozinho
class Descobridor:
    """Sem regiao marcada: le a tela inteira de vez em quando e procura um bloco de texto que
    aparece sempre no mesmo lugar com frases diferentes (isso e a caixa de dialogo)."""

    def __init__(self):
        self.vistos = collections.deque(maxlen=40)    # (instante, caixa normalizada, texto norm)

    def zerar(self):
        self.vistos.clear()

    def olhar(self, img):
        """-> regiao [x0, y0, x1, y1] em fracoes da tela quando tiver certeza, senao None."""
        w, h = img.size
        agora = time.time()
        linhas = [l for l in ocr_linhas(img) if len(re.findall(r"[^\W_]", l[0])) >= 2]
        for b in agrupar_blocos(linhas):
            b = em_ordem(b)
            texto = limpar(" ".join(l[0] for l in b))
            if not parece_fala(texto) or len(texto) < 12:
                continue
            x0, y0 = min(l[1] for l in b) / w, min(l[2] for l in b) / h
            x1, y1 = max(l[3] for l in b) / w, max(l[4] for l in b) / h
            alt_linha = float(np.median([l[4] - l[2] for l in b])) / h
            self.vistos.append((agora, (x0, y0, x1, y1, alt_linha), normalizar(texto)))
        recentes = [v for v in self.vistos if agora - v[0] < 90]
        melhor = None
        for _, cx, _ in recentes:
            grupo = [v for v in recentes if _parecida(v[1], cx)]
            textos = {v[2] for v in grupo}
            if len(grupo) >= 3 and len(textos) >= 2 and (melhor is None or len(grupo) > len(melhor)):
                melhor = grupo
        if not melhor:
            return None
        x0 = min(v[1][0] for v in melhor)
        y0 = min(v[1][1] for v in melhor)
        x1 = max(v[1][2] for v in melhor)
        y1 = max(v[1][3] for v in melhor)
        al = max(v[1][4] for v in melhor)
        # folga: a caixa e maior que o texto ja visto (frases mais compridas, nome em cima)
        reg = [max(0.0, x0 - 0.04), max(0.0, y0 - 1.6 * al), min(1.0, x1 + 0.08), min(1.0, y1 + 1.2 * al)]
        self.zerar()
        return [round(v, 4) for v in reg]


def _parecida(a, b):
    """Duas caixas de texto no mesmo lugar? (comeco igual a esquerda e mesma faixa de altura)"""
    sobre_y = min(a[3], b[3]) - max(a[1], b[1])
    mesmo_x = abs(a[0] - b[0]) < 0.06 or abs((a[0] + a[2]) / 2 - (b[0] + b[2]) / 2) < 0.05   # alinhado ou centralizado
    return mesmo_x and sobre_y > -0.02 and abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2) < 0.12
