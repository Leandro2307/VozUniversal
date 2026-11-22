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
SALA = -1          # "janela" da fonte sala: o PS5 direto da Sala 3D (nao e uma janela do Windows)


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
        self.sala = None                       # nucleo/sala.py (fonte "sala")

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
        if fonte == "sala":
            # o PS5 direto da Sala 3D: a imagem chega pela rede (nucleo/sala.py), nao ha janela
            if self.raiz != SALA:
                self.raiz = self.alvo = SALA
                self.exe, self.titulo = "ps5_sala", "PS5 (Sala 3D)"
                return True
            return False
        if self.raiz == SALA:                  # saiu da sala: procura a fonte de antes
            self.raiz, self.alvo, self.exe, self.titulo = None, None, "", ""
            return True
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
        if self.raiz == SALA:
            return self.sala.area() if self.sala else None
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
        if self.raiz == SALA:
            return True                        # a imagem vem da sala: nada fica por cima
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
    if hasattr(sct, "recortar"):          # fonte "sala": recorte do quadro do PS5 (nucleo/sala.py)
        return sct.recortar(reg)
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
    # marcacao de idioma antes da fala ("[Espanhol] Vai, vai!" lido "Espanhol Vai, vai!"): nao e fala
    t = _RE_IDIOMA.sub("", t)
    # ...tambem depois de uma frase, na mesma legenda ("Bom dia. [Espanhol] Bom dia.", 18/11)
    t = _RE_IDIOMA_MEIO.sub(" ", t)
    # travessao solto grudado no comeco de uma palavra no meio da frase ("então -só deixa"): lixo do
    # OCR. Hifen DENTRO da palavra ("viva-voz") e o travessao de dialogo ("- Ei. - Tipo") ficam
    t = re.sub(r"([^\W\d_][,;:]?\s)[-–—](?=[^\W\d_])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


_IDIOMAS = (r"(?i:espanhol|ingl[eê]s|franc[eê]s|italiano|alem[aã]o|russo|japon[eê]s|chin[eê]s|[aá]rabe|coreano|"
            r"portugu[eê]s|crioulo|haitiano|polon[eê]s|hebraico|latim)")
# o "[" as vezes sai como "I", "l", "|", "1" ou outro numero ("I Espanhol Um café", "3 Espanhol Esses
# filhotes...", GTA 6 18/11): uma letra/numero solto antes do idioma tambem e o colchete
_RE_IDIOMA = re.compile(r"^[\s\[(\-–—]*(?:[Il|\d]\s*)?(?i:em\s+)?" + _IDIOMAS +
                        r"[\s\])\-–—:]+(?=[\-–—\"'¿¡]*[A-ZÀ-Ý])[\-–—\s]*")
_RE_IDIOMA_MEIO = re.compile(r"(?<=[.!?…])\s*[\[(]?(?:[Il|\d]\s*)?" + _IDIOMAS +
                             r"[\s\])\-–—:]+(?=[\-–—\"'¿¡]*[A-ZÀ-Ý])")


def isolar_contorno(img, lo=205, escuro=110, raio=7):
    """Texto BRANCO com SOMBRA/CONTORNO escuro (legenda de video e de jogo) em preto sobre branco.
    Luz clara do cenario atras da legenda (lampadas do espelho, ceu) nao tem contorno escuro e some
    (no suavizar ela virava "texto" e o OCR embaralhava: "bo i o" por "bonito"). Medido em 22
    recortes da cena do espelho: 17 leituras perfeitas, contra 1 do jeito antigo (15/11)."""
    from PIL import ImageFilter
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    v = a.max(-1)
    claro = (v >= lo) & (v - a.min(-1) <= 60)
    perto = np.asarray(Image.fromarray(((v < escuro) * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(raio))) > 0
    return Image.fromarray(np.where(claro & perto, 0, 255).astype(np.uint8)).convert("RGB")


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


def palavra_que_e_fala(t):
    """Uma palavra sozinha so e fala se terminar como frase. "Vamos!" e "Sério?" sempre; com ponto
    ou reticencias ("Vamos.", "Não.", "Amigão...") so se comecar com maiuscula: minuscula e pedaco
    cortado pelo OCR ("ão.", "erda.", "bar."). Sem pontuacao e botao/rotulo ("Use", "CÂMERA").
    Antes so aceitava "!" e "?": "Vamos." (8x), "Não.", "Valeu.", "Oi." etc. nao eram faladas (14/11)."""
    t = t.strip().rstrip("\"')»”")
    letras = re.findall(r"[^\W\d_]", t)
    if len(letras) < 2:
        return False
    if t[-1:] in "!?":
        return True
    if t[-1:] in ".…":
        return letras[0].isupper() and not t.isupper()
    return False


# texto sem "Nome:" maior que isto nao e legenda (aviso, texto legal, descricao de menu)
LEGENDA_MAX = 250


def eh_texto_de_tela(b):
    """Bloco com 4+ linhas: paragrafo escrito NA CENA (mensagem no celular, carta, bilhete), nao
    legenda (legenda tem no maximo 3 linhas)."""
    return len(b) >= 4 and len(limpar(" ".join(l[0] for l in b)).split()) >= 12


def achar_legenda(img, linhas=None, dic=None, motivos=None, textos_tela=None, y_min=None):
    """Na parte de baixo de um video, acha o bloco que e a LEGENDA: centralizado na horizontal
    (titulo de capitulo do YouTube, relogio e botoes ficam de lado), com cara de frase.
    Entre varios, fica com o que tem mais texto e esta mais embaixo. -> linhas do bloco ou None.
    motivos (lista): recebe (texto, por que foi descartado) de cada bloco, para o diagnostico.
    textos_tela (lista): recebe os blocos que sao texto escrito na cena (celular, carta): eles nao
    competem com a legenda (antes o texto do celular, mais comprido, ganhava dela, 18/11).
    y_min (pixels da imagem): so vale bloco com o meio abaixo disso (a faixa da legenda, embaixo
    no centro); texto mais acima (placas, HUD, menus no meio da tela) nao e lido."""
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
        if y_min is not None and (min(l[2] for l in b) + max(l[4] for l in b)) / 2 < y_min:
            fora(t, "acima da faixa da legenda")
            continue                                   # legenda fica embaixo
        if eh_texto_de_tela(b) and not com_nome:
            fora(t, "texto na cena (celular/carta)")
            if textos_tela is not None:
                textos_tela.append(b)
            continue
        if len(t) > LEGENDA_MAX and not com_nome:
            # aviso de epilepsia, texto legal da produtora, descricao no menu do console: 23 a 72 s
            # de fala que atrasaram as legendas seguintes em ate 18 s (18/11, 301 a 1059 letras).
            # Legenda de verdade passa pouco de 200 (3 linhas compridas no Far Cry)
            fora(t, "texto longo demais para legenda")
            continue
        # legenda e centralizada; "Nome: fala" tem um pouco mais de folga (o nome desloca o meio),
        # mas texto encostado num lado (chat, HUD, lista) nunca e legenda
        if abs((x0 + x1) / 2 / w - 0.5) > (0.25 if com_nome else 0.12):
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
        if len(t.split()) < 2 and not palavra_que_e_fala(t):
            fora(t, "palavra solta")
            continue                                   # palavra solta: botao/rotulo
        if not com_nome and len(t.split()) <= 3 and t.rstrip("\"')")[-1:] not in FIM + ",":
            fora(t, "rotulo curto do jogo")
            continue                                   # "Mais rápido", "Acelerar (Segurar)": HUD
        if re.fullmatch(r"[\d:/ .-]+", t) or eh_relogio(t):
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
            # linha comprida de creditos em ingles/embaralhada passando atras da cena ("den11ifen xalbe
            # manx adam san dan dan chew Rowan...", 8 s de fala no GTA 6, 18/11): com 7+ palavras
            # cai se metade for estranha (nas ~3500 leituras gravadas ate 18/11, legenda de verdade
            # ficou abaixo de 40%; de 50% para cima era creditos, interface ou leitura ilegivel)
            # (com ":" pode ter uma legenda "Nome: fala" no fim do bloco: o separar_nome tira o lixo)
            if total and ((ruins / total > 0.6 and total <= 6)
                          or (total > 6 and ruins / total >= 0.5 and ":" not in t)):
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
    AQUECER_S = 6.0           # nos primeiros segundos todo movimento conta (acha o video inteiro)

    def __init__(self):
        self.ant = None
        self.calor = None
        self.rect = None          # [x0, y0, x1, y1] em fracoes da tela
        self.confianca = 0.0
        self.manchas = collections.deque(maxlen=200)    # (instante, retangulo)
        self.limite = None        # janela da frente em fracoes da tela
        self.excluir = []         # janelas do proprio programa (fracoes da tela): nunca sao video
        self.nucleo = None        # parte que com certeza e video (sem rolagem/miniaturas)
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

    @staticmethod
    def _separar(cru, ys, xs):
        """Mancha que junta o player e a coluna de miniaturas do YouTube (coladas): corta onde
        ha uma faixa de colunas PARADAS (o fundo da pagina entre os dois)."""
        bloco = cru[ys, xs]
        if bloco.shape[1] < 12:
            return [(ys, xs)]
        col = bloco.mean(axis=0)
        partes, ini = [], None
        for j, v in enumerate(list(col) + [0.0]):
            if v >= 0.04 and ini is None:
                ini = j
            elif v < 0.04 and ini is not None:
                partes.append((ini, j))
                ini = None
        if len(partes) <= 1:
            return [(ys, xs)]
        return [(ys, slice(xs.start + a, xs.start + b)) for a, b in partes if b - a >= 3]

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
        for x0, y0, x1, y1 in self.excluir:
            # janela do proprio programa por cima do navegador (a foto do painel muda): nao e video
            calor = calor.copy() if calor is self.calor else calor
            calor[max(0, int(y0 * self.H)):int(np.ceil(y1 * self.H)), max(0, int(x0 * self.W)):int(np.ceil(x1 * self.W))] = 0
        cru = calor > 0.2
        ativo = ndimage.binary_closing(cru, structure=np.ones((3, 3)), iterations=1)
        rotulos, n = ndimage.label(ativo)
        agora = time.time()
        if n:
            tamanhos = ndimage.sum(ativo, rotulos, range(1, n + 1))
            for i, (ys, xs) in enumerate(ndimage.find_objects(rotulos)):
                for ys2, xs2 in self._separar(cru, ys, xs):
                    caixa = (ys2.stop - ys2.start) * (xs2.stop - xs2.start)
                    cheio = (float(ativo[ys2, xs2].sum()) if (ys2, xs2) != (ys, xs) else tamanhos[i]) / max(1, caixa)
                    # mancha de video: razoavel e CHEIA (texto rolando e o mouse sao ralos/pequenos)
                    if caixa / (self.W * self.H) >= 0.01 and cheio >= 0.35:
                        r = [xs2.start / self.W, ys2.start / self.H, xs2.stop / self.W, ys2.stop / self.H]
                        # NUCLEO = movimento dentro do video ja achado (ou nos primeiros segundos)
                        nuc = self.nucleo
                        dentro = nuc is None or agora - self.desde < self.AQUECER_S or not (
                            r[0] < nuc[0] - 0.04 or r[1] < nuc[1] - 0.04 or r[2] > nuc[2] + 0.04 or r[3] > nuc[3] + 0.04)
                        self.manchas.append([agora, r, dentro])
                        self.confianca = round(float(cheio), 2)
        recentes = [m for m in self.manchas if agora - m[0] <= self.JANELA_S]
        if not recentes:
            return self.rect
        # Movimento FORA do nucleo (rolar a pagina, abrir outra, mouse nas miniaturas do YouTube,
        # cena mais larga) so entra no video ENQUANTO continua aparecendo, e so vira nucleo se
        # durar mais de 4 s (cena larga de verdade). Antes qualquer movimento esticava o "video"
        # ate a pagina inteira por 20 s e a legenda, no meio do player, era jogada fora como
        # "fora do centro" (15/11: "a legenda so pega em tela cheia").
        extras = [m for m in recentes if not m[2]]
        aceitas = []
        for m in extras:
            if agora - m[0] > 5.0:
                continue
            if self.nucleo is not None and not _sobrepoe(m[1], self.nucleo):
                continue          # longe do video (miniatura, outra parte da pagina)
            viz = {m2[0] for m2 in extras if _sobrepoe(m[1], m2[1])}
            if len(viz) >= 8 and max(viz) - min(viz) >= 2.5:
                aceitas.append(m[1])
                if len(viz) >= 16 and max(viz) - min(viz) >= 4.0:
                    m[2] = True
        nucleo = [m[1] for m in recentes if m[2]]
        if nucleo:
            self.nucleo = _uniao(nucleo)
        usar = nucleo + aceitas
        if not usar:
            return self.rect
        r = _uniao(usar)
        if (r[2] - r[0]) * (r[3] - r[1]) >= 0.03:
            self.rect = r
        return self.rect


def _sobrepoe(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _uniao(rs):
    return [min(x[0] for x in rs), min(x[1] for x in rs), max(x[2] for x in rs), max(x[3] for x in rs)]


# ----------------------------------------------------------------- dicionario (conserta o OCR)
_FREQ = {}        # frequencia das palavras do portugues (preenchida pelo Dicionario)


def palavra_comum(t):
    """ "Até", "Com", "Ei", "Tipo": palavra MUITO usada do portugues, nao nome de quem fala
    (em video legendado, a 1a linha curta "Até" virava personagem e roubava a voz das falas)."""
    t = t.strip()
    if not t or " " in t:
        return False
    from .identidade import nomes_proprios
    return _FREQ.get(t.lower(), 0) >= 100000 and t.lower() not in nomes_proprios()


class Dicionario:
    TROCAS = [("ri", "n"), ("rn", "m"), ("m", "rn"), ("ii", "n"), ("cl", "d"), ("vv", "w"),
              ("1", "l"), ("0", "o"), ("I", "l"), ("l", "I"), ("l", "i"), ("i", "l"),
              ("e", "c"), ("c", "e")]          # "fiea atrás de mim" = "fica" (GTA 6, 18/11)
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
        if self.ok and not _FREQ:
            _FREQ.update(self.freq)

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
            elif len(sa) == len(sb) and all(self._mesma_sem_1a(x, y) for x, y in zip(sa, sb)):
                out += sb          # "osso" / "Nosso": a outra leitura viu a 1a letra
            elif (self.desconhecidas(" ".join(sb)) < self.desconhecidas(" ".join(sa))
                  and semelhanca(normalizar("".join(sa)), normalizar("".join(sb))) >= 0.5):
                out += sb
            else:
                out += sa
        return " ".join(out)

    @staticmethod
    def _mesma_sem_1a(a, b):
        """b e a palavra a com uma letra a mais no comeco (o OCR comeu a 1a letra em a)."""
        na, nb = normalizar(a), normalizar(b)
        return len(nb) == len(na) + 1 and nb[1:] == na and nb[:1].isalpha()

    _SEM_ACENTO = None      # palavra sem acento -> (palavra mais comum com essa grafia, frequencia)

    def acento_estranho(self, tok):
        """Acento/letra que nao existe ali ("nâda", "çom", "maño", "engraçadõ", GTA 6 18-20/11): se a
        palavra nao existe mas SEM os acentos e uma palavra comum, fica a grafia certa dela."""
        from .util import sem_acento
        m = re.match(r"^(\W*)(.*?)(\W*)$", tok, re.S)
        ini, miolo, fim = m.groups()
        if len(miolo) < 3 or not re.search(r"[^\x00-\x7f]", miolo) or self.conhece(miolo):
            return tok
        if Dicionario._SEM_ACENTO is None:
            idx = {}
            for w, f in self.freq.items():
                s = sem_acento(w)
                if f > idx.get(s, ("", 0))[1]:
                    idx[s] = (w, f)
            Dicionario._SEM_ACENTO = idx
        certo, f = Dicionario._SEM_ACENTO.get(sem_acento(miolo.lower()), ("", 0))
        if not certo or f < 500 or certo == miolo.lower():
            return tok
        if miolo[:1].isupper():
            certo = certo[:1].upper() + certo[1:]
        return ini + certo + fim

    def letra_tripla(self, tok):
        """ "porrra" -> "porra", "nãããão" fica (grito de verdade so se a palavra nao existir mesmo)."""
        m = re.match(r"^(\W*)(.*?)(\W*)$", tok, re.S)
        ini, miolo, fim = m.groups()
        if not re.search(r"(\w)\1\1", miolo):
            return tok
        for c in (re.sub(r"(\w)\1\1+", r"\1\1", miolo), re.sub(r"(\w)\1\1+", r"\1", miolo)):
            # (3+ letras: "kkkk" virava "k" e "AAA" virava "AA": risada e grito ficam)
            if len(c) >= 3 and self.freq.get(c.lower(), 0) >= 500:
                return ini + c + fim
        return tok

    def letra_perdida(self, tok):
        """Palavra que nao existe e que, com UMA letra a mais no comeco, e palavra comum: o OCR comeu
        a 1a letra ("roblema" -> "problema", "essoas" -> "pessoas", "uando" -> "quando"). Era o erro
        mais comum na legenda branca sobre fundo colorido (GTA V, 14/11)."""
        m = re.match(r"^(\W*)([^\W\d_]{3,})(\W*)$", tok)
        if not m or self.freq.get(m.group(2).lower(), 0) >= 50:
            return tok
        ini, pal, fim = m.groups()
        base = pal.lower()
        cands = sorted(((self.freq.get(c + base, 0), c + base) for c in "abcdefghijlmnopqrstuvxzç"),
                       reverse=True)
        (f1, c1), (f2, _) = cands[0], cands[1]
        if f1 < 300 or f1 < 10 * max(1, f2):
            return tok                       # nenhuma, ou mais de uma palavra possivel: nao chuta
        if pal[:1].isupper():
            c1 = c1[:1].upper() + c1[1:]
        return ini + c1 + fim

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

    def _virgula_no_meio(self, texto):
        """Virgula grudada entre letras (sujeira lida como virgula, ou o OCR comeu o espaco):
        "gen,te" -> "gente", "t,á" -> "tá", "r,nais" -> "mais"; entre duas palavras que existem
        ("você,revelará") ganha o espaco. Antes a voz parava no meio da palavra (GTA 6, 18/11)."""
        def troca(m):
            a, b = m.group(1), m.group(2)
            junta = a + b
            certa = self._rn_por_m(junta)
            if certa != junta or self.conhece(junta) or len(a) == 1 or len(b) == 1:
                return certa
            return a + ", " + b
        return re.sub(r"(?<![\w,])([^\W\d_]+),([^\W\d_]+)(?![\w])", troca, texto)

    def consertar(self, texto):
        out, inicio = [], True
        texto = self._virgula_no_meio(texto)
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
            if novo == tok and not self.conhece(tok):
                novo = self.letra_perdida(tok)
            if novo == tok and not self.conhece(tok):
                novo = self.acento_estranho(tok)
            if novo == tok and not self.conhece(tok):
                novo = self.letra_tripla(tok)
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
# cargo/papel como nome de quem fala, com a 2a palavra minuscula ("Policial penal:", "Pessoa local:",
# "Pessoas locais:", "Pessoa desconhecida:" no GTA 6). No MEIO da linha, ate sem pontuacao antes
# ("...pó. Policial penal: Melendez, não me vem com essa cara.", 18/11: era falado na voz da Shayna)
# (a fala depois do ":" comeca com Maiuscula: "...com o Gerente: ele disse" e frase, nao legenda)
_NOME_PAPEL_NO_MEIO = re.compile(r"(?:^|\s)([A-ZÀ-Ý][a-zà-ÿ]{2,14}(?: [a-zà-ÿ]{3,15})?(?: \d{1,2})?)\s*:\s+"
                                 r"(?=[A-ZÀ-Ý\"'“«¿¡\-–—.…])")
_PAPEIS = set("""pessoa pessoas policial policiais guarda guardas segurança seguranca atendente motorista
 gerente cliente clientes turista turistas agente agentes oficial soldado soldados detento detenta detentos
 detentas prisioneiro prisioneira presidiário presidiária presidiario presidiaria funcionário funcionária
 vendedor vendedora garçom garçonete homem mulher homens mulheres garoto garota menino menina voz locutor
 locutora repórter reporter apresentador apresentadora piloto médico médica enfermeiro enfermeira
 transeunte pedestre vizinho vizinha gravação gravacao rádio radio operador operadora capanga membro
 chefe recepcionista bandido bandida traficante criminoso criminosa""".split())


def _eh_papel(nome):
    """ "Policial penal", "Pessoas locais": cargo conhecido + palavra minuscula (nao e frase)."""
    ps = nome.split()
    return bool(ps) and ps[0].lower() in _PAPEIS and _nome_com_minuscula(nome)


# palavras que vem antes de ":" mas NAO sao nome de quem fala
_NAO_E_NOME = {"atencao", "atenção", "dica", "nota", "aviso", "objetivo", "missao", "missão", "capitulo",
               "capítulo", "parte", "fase", "local", "hora", "obs", "importante", "novo", "nova",
               "sinopse", "descrição", "descricao", "resumo", "legenda", "legendas", "inscrito", "inscritos",
               "visualizações", "comentários", "comentarios", "capítulos", "transcrição", "link", "site"}

# textos da interface do YouTube/navegador
INTERFACE = re.compile(r"\b(pesquisar|inscrever-se|mostrar mais|mostrar menos|compartilhar|salvar|"
                       r"reproduzir|anúncio|próximo|pular)\b", re.I)            # so em texto curto (botao)
INTERFACE_SEMPRE = re.compile(r"(visualizaç|inscritos|pular anúncio|\bhá \d+ (anos?|meses|dias)\b|"
                              r"\besc\b|full ?screen|sair da tela cheia|esrb\.org|inappropriate for children|"
                              # miniaturas da pagina inicial do YouTube tocam previa sozinhas e o
                              # titulo era lido como legenda ("Haka 120 assistindo", 15/11)
                              r"\d+\s*(mil\s+)?assistindo|\bspeedrun\b|\bwr attempt\b|(?-i:\bAO VIVO\b)|"
                              r"\d+\s*(mil|mi)\s+(de\s+)?visualiza|\bhá \d+ (horas?|minutos?|semanas?)\b|"
                              # aviso do Chrome quando um site grava a tela (15/11 foi falado)
                              r"compartilhando sua tela|parar de compartilhar|is sharing your screen|"
                              r"^o app \S+\.\S+ est|\bonrender\.com\b|^o app (\S+ )?est[áa] compart)", re.I)


def eh_relogio(t):
    """Tempo do player ("I I 7:09 26.49", "0:42 / 12:30"): tem hora e nenhuma palavra de verdade.
    (sessao de 15/11: o tempo do YouTube no canto foi falado como fala)"""
    return bool(re.search(r"\d{1,2}:\d{2}", t)) and not re.search(r"[A-Za-zÀ-ÿ]{3,}", t)


def eh_interface(t):
    # botao/rotulo: ate 3 palavras e sem pontuacao de frase ("Vamos pesquisar a cabana." e fala)
    botao = len(t.split()) <= 3 and t.rstrip()[-1:] not in ".!?…"
    # dica da aba do navegador: "(257) Cyberpunk 2077 - O Filme (Dublado..." (contador de avisos)
    if re.match(r"^\W{0,2}\(\d{1,4}\)\s+\S", t.strip()):
        return True
    return bool(INTERFACE_SEMPRE.search(t)) or (botao and bool(INTERFACE.search(t)))


# instrucao de controle do jogo ("Aperte O para acalmar o cavalo", "Segure para mirar") e acao com o
# botao no fim ("Abrir algibeira O", "Falar A", "Pular +")
# (com um icone antes lido como letra solta: "A Puxe para cima para uma busca", celular do GTA 6)
DICA_CONTROLE = re.compile(r"^(?:[A-Z0-9]\s)?(aperte|pressione|segure|mantenha|use|clique|toque|solte|puxe|"
                           r"deslize|arraste|role|gire)\b.*\b(?:para|pra|pam|pana)\b"
                           r"|^(\S+\s){0,3}\S+\s(?:[A-Z]|[+\-]|LB|RB|LT|RT|ZL|ZR|L|R)$"
                           r"|^para selecionar\b|\bem qualquer direção\.?$", re.I)

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
 converse cumprimente dance brinde explore visite compre venda entregue retorne vá descubra observe
 coloque ponha vista tire selecione abra feche acenda apague retire recupere dirija atravesse cruze chegue
 alimente escove acalme laceie lace sele domine guarde jogue carregue empurre arrombe
 capture resgate salve cace esfole pesque amanse leve-o leve-a solte dome desmonte
 passe faça faca aguarde ganhe aposte vença venca assista termine participe""".split())


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
    if len(ps) == 1 and palavra_comum(t.strip(" :")):
        return False
    if len(ps) >= 2 and ps[0].lower() in _NAO_COMECA_NOME:
        return False                                  # "Vocês fora" (linha de cima de "Vocês foram bem.")
    if len(ps) >= 2 and ps[-1].isdigit():          # "Guarda 2", "Soldado 1"
        ps = ps[:-1]
        t = " ".join(ps)
        if t.lower() in _NAO_E_NOME:               # "Capítulo 2", "Parte 3" nao sao personagem
            return False
    letras = re.sub(r"[^\wÀ-ÿ]", "", t)
    # 3+ letras: "Bo" (pedaco de "Bom, o Brian...") virava quem fala (15/11)
    if len(letras) < 3 or any(c.isdigit() for c in letras):
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
_NAO_COMECA_NOME = {"ele", "ela", "eu", "voce", "você", "voces", "vocês", "nos", "nós", "eles", "elas", "tu", "a gente", "isso", "isto", "aqui",
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


# palavras de conversa que nunca fazem parte de um nome ("Ok você tá" nao e personagem)
_PALAVRAS_DE_FALA = {"você", "voce", "vocês", "voces", "tá", "ta", "tô", "to", "eu", "tu", "é", "não", "nao",
                     "sim", "ok", "já", "ja", "aqui", "agora", "cê", "ce", "tão", "tao",
                     # 'Só fala: "Oi, prazer"' virava a personagem "Sofia" (15/11)
                     "só", "so", "fala", "falou", "disse", "diz", "dizer", "falar", "pergunta", "responde"}


def _nome_com_minuscula(t):
    """ "Pessoa desconhecida", "Homem misterioso": 1a maiuscula, ate 3 palavras, nao comeca
    com pronome/verbo (senao "Ele disse: ..." viraria nome)."""
    ps = t.strip().split()
    if len(ps) >= 2 and ps[-1].isdigit():          # "Pessoa desconhecida 2"
        ps = ps[:-1]
        t = " ".join(ps)
    if t.strip().lower() in _NAO_E_NOME:
        return False
    if any(p.lower() in _PALAVRAS_DE_FALA for p in ps):
        return False                                  # "Ok você tá" (da fala "Ok, você tá parecendo...")
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
        if not (m_meio and _cara_de_nome(m_meio.group(1))):
            m_meio = next((m3 for m3 in _NOME_PAPEL_NO_MEIO.finditer(txt)
                           if m3.start(1) > 0 and _eh_papel(m3.group(1))), None)
        if m_meio:
            linhas = [(txt[m_meio.start(1):],) + tuple(linhas[i][1:])] + list(linhas[i + 1:])
            break
        if i > 0:
            m2 = _PREFIXO.match(txt)
            if m2 and (_cara_de_nome(m2.group(1)) or _eh_papel(m2.group(1))):
                linhas = list(linhas[i:])
                break
    primeira = linhas[0]
    # "Pessoa desconhecida 2:" com sujeira antes ("-4 ", "av-", "V)essoa", ".\"Qessoa")
    m_pd = re.match(r"^.{0,5}?\w?essoa\s+desconhecid[ao](?:\s+(\d{1,2}))?\s*[:;]\s*(\S.*)$", primeira[0], re.I)
    if m_pd:
        nome_pd = "Pessoa desconhecida" + (f" {m_pd.group(1)}" if m_pd.group(1) else "")
        return nome_pd, [(m_pd.group(2),) + tuple(primeira[1:])] + linhas[1:]
    # lixo curto antes do "Nome:" ("e Guarda 2: Por que...", "- Guarda 2: Anda!"): tira, se o que
    # sobra comeca com um nome de verdade (15/11: o "Guarda 2:" era falado e na voz do outro)
    m_lixo = re.match(r"^(?:[-–—•'\"|]+\s*|[a-zà-ÿ]{1,2}\s+)(?=[A-ZÀ-Ý][^:：]{1,26}[:：]\s)", primeira[0])
    if m_lixo:
        resto_l = primeira[0][m_lixo.end():]
        m_t = _PREFIXO.match(resto_l)
        if m_t and _cara_de_nome(m_t.group(1)):
            primeira = (resto_l,) + tuple(primeira[1:])
    m = _PREFIXO.match(primeira[0])
    if m and m.group(2)[:1].isdigit():
        m = None                       # "São 10:30 da manhã": horario, nao "Nome: fala"
    # nome com 2a palavra minuscula ("Pessoa desconhecida") so com a fala comecando em Maiuscula, ou
    # cargo conhecido: "Presta atenção: isso é sério." e "Lembra disso: nada..." sao fala, nao nome
    if m and (_cara_de_nome(m.group(1)) or (_nome_com_minuscula(m.group(1)) and not palavra_comum(m.group(1))
                                            and (m.group(2)[:1].isupper() or m.group(2)[:1] in "\"'“«-–—¿¡…."
                                                 or _eh_papel(m.group(1))))):
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


# cargo no meio do bloco com o ":" estragado pelo OCR ("Pessoa loeal;. Que bairro...", 18/11)
_PAPEL_TOLERANTE = re.compile(r"(?:^|[\s\-–—])([A-ZÀ-Ý][a-zà-ÿ]{2,14}(?: [a-zà-ÿ]{3,15})?(?: \d{1,2})?)\s*[:;][.,]?\s+"
                              r"(?=[A-ZÀ-Ý\"'“«¿¡\-–—.…])")
_PAPEL_2A = ("local", "locais", "desconhecida", "desconhecido", "desconhecidas", "desconhecidos", "penal")
# figurante: gente da rua / multidao (fala de fundo, a primeira a ser pulada quando a voz atrasa)
_FIGURANTES = {"pessoa", "pessoas", "transeunte", "transeuntes", "pedestre", "pedestres", "turista", "turistas",
               "cliente", "clientes", "vizinho", "vizinha", "vizinhos", "multidão", "multidao", "fã", "fãs",
               "manifestante", "manifestantes", "torcedor", "torcedora", "torcedores", "frequentador",
               "frequentadora", "frequentadores", "banhista", "banhistas"}


def eh_figurante(nome):
    """ "Pessoa local", "Pessoas locais", "Transeunte", "Turista 2": gente da multidao, sem nome."""
    ps = (nome or "").lower().split()
    return bool(ps) and ps[0] in _FIGURANTES


def _papel_canonico(nome):
    """ "Pessoa beal" / "Pessoa loeal" / "Pessoa penal" -> "Pessoa local" (2a palavra estragada pelo OCR)."""
    ps = nome.split()
    if len(ps) >= 2 and not ps[1].isdigit():
        p0 = ps[0].lower()
        cands = (("local", "locais", "desconhecida", "desconhecido", "desconhecidas", "desconhecidos")
                 if p0.startswith("pessoa") else ("penal",) if p0.startswith("polic") else _PAPEL_2A)
        if ps[1] not in cands:
            melhor = max(cands, key=lambda w: difflib.SequenceMatcher(None, ps[1], w).ratio())
            # "Pessoa" so tem 3 opcoes bem diferentes: "beal" (0,44 de local) ja e local
            if difflib.SequenceMatcher(None, ps[1], melhor).ratio() >= (0.4 if p0.startswith("pessoa") else 0.6):
                ps[1] = melhor
    return " ".join(ps)


_IDIOMA = re.compile(r"^[\s(\[]*(?:espanhol|ingl[eê]s|franc[eê]s|italiano|alem[aã]o|russo|chin[eê]s|japon[eê]s|"
                     r"coreano|[aá]rabe|crioulo|portugu[eê]s)[\s)\]]*[:'’\"]*\s+(?=\S)", re.I)


def tirar_idioma(t):
    """ "Espanhol ' Ai, Santa Bárbara..." / "(Espanhol) Ai..." -> "Ai, Santa Bárbara...": a etiqueta
    de idioma da legenda nao e fala (era lida em voz alta)."""
    return _IDIOMA.sub("", t, count=1)


_CORES = [(12, "vermelho"), (45, "laranja"), (65, "amarelo"), (90, "verde-limão"), (150, "verde"),
          (195, "ciano"), (255, "azul"), (290, "roxo"), (335, "magenta"), (361, "rosa")]


def cor_do_nome(img, linha, escala=1.0):
    """Cor do NOME no comeco da linha ("Pessoa local:" em rosa, a fala em branco). No GTA 6 cada
    pessoa da cena tem o nome numa cor: duas "Pessoa local" empilhadas ao mesmo tempo sao gente
    diferente (20/11). -> tom em graus (0-360) ou None (nome branco/sem cor clara ou linha sem nome).
    Pega so os pixels de LETRA (os mais claros) do trecho do nome e compara com o resto da linha."""
    import colorsys
    t, x0, y0, x1, y1 = linha[:5]
    # (o nome as vezes vem numa linha so dele: "Pessoa local:" separado da fala, outra cor)
    m = (re.match(r"^\W{0,3}([A-ZÀ-Ýa-zà-ÿ][^:;]{1,28}?)\s*[:;](?:\s+\S|\s*$)", t)
         or re.match(r"^\W{0,3}((?:Pessoas?|Policial|Policiais|Turistas?|Transeuntes?)\s[a-zà-ÿ']{2,14})\s+[A-ZÀ-Ý]", t))
    if not m or img is None:
        return None
    try:
        a = np.asarray(img.crop((int(x0 / escala), int(y0 / escala), int(x1 / escala), int(y1 / escala)))
                       .convert("RGB"), np.float32) / 255
    except Exception:
        return None
    if a.ndim != 3 or a.shape[1] < 20 or a.shape[0] < 5:
        return None
    mx, mn = a.max(2), a.min(2)
    sat = (mx - mn) / np.maximum(mx, 1e-6)
    corte = min(a.shape[1], int(a.shape[1] * (m.end(1) + 1) / max(1, len(t))))
    if corte < 8:
        return None

    def letras(v, s):
        return v >= max(0.55, float(np.percentile(v, 85)))

    sel_n = letras(mx[:, :corte], sat[:, :corte])
    if sel_n.sum() < 20:
        return None
    s_n = sat[:, :corte][sel_n]
    s_r = sat[:, corte:][letras(mx[:, corte:], sat[:, corte:])] if a.shape[1] - corte > 10 else np.zeros(1)
    sn, sr = float(np.median(s_n)), float(np.median(s_r)) if len(s_r) else 0.0
    # (ciano claro do GTA 6: 0,10 contra 0,01 do texto branco; o fundo nao entra: so letra)
    if sn < 0.07 or sn < sr + 0.06:
        return None
    px = a[:, :corte][sel_n]
    px = px[s_n > 0.1] if (s_n > 0.1).sum() > 10 else px
    return round(colorsys.rgb_to_hsv(*np.median(px, 0))[0] * 360, 1)


def nome_da_cor(h):
    """Tom (graus) -> nome da cor, so para o rotulo ("Pessoa local (rosa)")."""
    return next(nome for lim, nome in _CORES if h < lim)


def distancia_cor(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


# "Pessoa local Ah, olha só você!" / "Pessoa oca Ah..." (o OCR perdeu os dois-pontos do nome colorido)
_PAPEL_SEM_DOIS_PONTOS = re.compile(r"(?:^|(?<=[.!?…]))\s*((?:Pessoas?|Policial|Policiais|Turistas?|Transeuntes?)"
                                    r"\s[a-zà-ÿ']{2,14})\s+(?=[A-ZÀ-Ý])")


def separar_falantes(linhas):
    """Bloco com VARIAS legendas empilhadas (multidao: "Lucia: Dinheiro no balcão! Pessoa local: Que
    bairro de merda. Atendente: Tá, tá...") -> [(nome, texto), ...] na ordem da tela, um por pessoa.
    O separar_nome fica so com a ULTIMA (a de baixo); as de cima se perdiam ou saiam todas na voz da
    primeira, com o "Pessoa local:" falado junto (GTA 6, 18/11 00:15). Sem nome no meio: 1 item."""
    if not linhas:
        return []
    linhas = [consertar_prefixo(l) for l in linhas]
    tudo = " ".join(l[0] for l in linhas).strip()
    from .identidade import nomes_proprios
    marcas = []
    for m in _NOME_NO_MEIO.finditer(tudo):
        if _cara_de_nome(m.group(1)):
            marcas.append((m.start(1), m.end(1), m.group(1), False))
    for m in _NOME_NO_MEIO_SOLTO.finditer(tudo):
        if m.group(1).lower() in nomes_proprios():
            marcas.append((m.start(1), m.end(1), m.group(1), False))
    for m in _PAPEL_TOLERANTE.finditer(tudo):
        if m.start(1) > 0 and _eh_papel(m.group(1)):
            marcas.append((m.start(1), m.end(1), _papel_canonico(m.group(1)), True))
    for m in _PAPEL_SEM_DOIS_PONTOS.finditer(tudo):
        nome_c = _papel_canonico(m.group(1))
        if m.start(1) > 0 and nome_c.split()[-1] in _PAPEL_2A:     # "Pessoa oca" -> "Pessoa local"
            marcas.append((m.start(1), m.end(1), nome_c, True))
    def fala_comeca_bem(fim_nome):
        # a fala depois do "Nome:" comeca com Maiuscula/aspas: "Brian: Ah. Certo: esqueci que..." nao
        # tem uma pessoa "Certo"
        resto = re.sub(r"^\s*(?:[:;][.,]?)?\s*", "", tudo[fim_nome:])
        return bool(re.match(r"[A-ZÀ-Ý\"'“«¿¡\-–—.…(\[]", resto))

    marcas = sorted(m for m in marcas if m[0] > 0 and fala_comeca_bem(m[1]))
    unicas = []
    for mk in marcas:
        if not unicas or mk[0] >= unicas[-1][1]:
            unicas.append(mk)
    if not unicas:
        nome, corpo = separar_nome(linhas)
        return [(nome, tirar_idioma(" ".join(l[0] for l in corpo).strip()))]
    saida = []
    cab = tudo[:unicas[0][0]].strip()
    if cab:
        n0, c0 = separar_nome([(cab, 0, 0, 1, 1)])
        t0 = " ".join(l[0] for l in c0).strip()
        if re.search(r"\w", t0):
            saida.append((n0, t0))
    for k, (ini, fim_nome, nome, _papel) in enumerate(unicas):
        sep = re.match(r"\s*[:;][.,]?\s*", tudo[fim_nome:])
        comeco = fim_nome + (sep.end() if sep else 0)
        ate = unicas[k + 1][0] if k + 1 < len(unicas) else len(tudo)
        txt = tirar_idioma(tudo[comeco:ate].strip())
        if re.search(r"\w", txt):
            saida.append((nome.strip(), txt))
    return saida or [("", tudo)]


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
