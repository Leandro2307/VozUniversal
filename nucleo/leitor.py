# -*- coding: utf-8 -*-
"""Decide o que falar a partir das leituras sucessivas da caixa de dialogo.

Fala cada frase assim que ela termina de aparecer (sem esperar a caixa inteira)
e nunca repete o que ja falou da mesma pagina.
"""
import collections
import difflib
import re

from .diagnostico import diag
from .util import FIM, normalizar, semelhanca


def hesitacao(f):
    s = f.rstrip("\"')")
    return (s.endswith("...") or s.endswith("…")) and len(s.split()) <= 2


# abreviacoes: o ponto delas NAO termina a frase ("Srta. Jones, traga..." e uma frase so)
ABREVIACOES = {"Sr": "Senhor", "Sra": "Senhora", "Srta": "Senhorita", "Dr": "Doutor", "Dra": "Doutora",
               "Prof": "Professor", "Profa": "Professora", "Mr": "Mister", "Mrs": "Missis", "Ms": "Miss",
               "Sto": "Santo", "Sta": "Santa", "Cap": "Capitão", "Ten": "Tenente", "Sgt": "Sargento",
               "Gen": "General", "Cel": "Coronel", "Exmo": "Excelentíssimo", "Exma": "Excelentíssima",
               "Vv": "Vossa", "V.Sa": "Vossa Senhoria"}
_RE_ABREV = re.compile(r"\b(" + "|".join(sorted((re.escape(a) for a in ABREVIACOES), key=len, reverse=True))
                       + r")\.(?=\s+\S)")
_TROCA = "․"      # "ponto" que nao conta como fim de frase, so dentro desta funcao


def dividir_frases(texto):
    texto = _RE_ABREV.sub(lambda m: m.group(1) + _TROCA, texto)
    partes = [p.strip().replace(_TROCA, ".") for p in re.findall(r"\S.*?(?:[.!?…]+[\"')]*(?=\s|$)|$)", texto) if p.strip()]
    out = []
    for p in partes:
        ant = out[-1] if out else ""
        if ant and (p[:1].islower() or hesitacao(ant)):
            out[-1] += " " + p
        else:
            out.append(p)
    return out


def parecidos(a, b):
    if not a or not b:
        return False
    r = semelhanca(a, b)
    if max(len(a), len(b)) <= 6:
        # textos curtos: so um erro pequeno de OCR ("Oi, Lori!" NAO e "Olha pr...")
        return a == b or (r >= 0.67 and abs(len(a) - len(b)) <= 1)
    return r >= 0.9


def alinhar(falado, total):
    n = len(falado)
    if n < 8:
        return (1.0 if parecidos(total[:n], falado) else semelhanca(total[:n], falado)), min(n, len(total))
    sm = difflib.SequenceMatcher(None, falado, total, autojunk=False)
    blocos = [b for b in sm.get_matching_blocks() if b.size >= 4]
    if not blocos:
        return 0.0, 0
    ult = blocos[-1]
    fim = ult.b + ult.size + (n - (ult.a + ult.size))
    return sum(b.size for b in blocos) / n, max(0, min(len(total), fim))


def cortar_apos(texto, n):
    if n <= 0:
        return 0
    acum, soma = [], 0
    for ch in texto:
        soma += len(normalizar(ch))
        acum.append(soma)
    melhor, dist = len(texto), None
    for m in re.finditer(r"\S+", texto):
        d = abs(acum[m.end() - 1] - n)
        if dist is None or d <= dist:
            melhor, dist = m.end(), d
    return melhor


# palavras que pedem a seguinte: o pedaco falado antes da frase acabar nao termina nelas
LIGACAO = set("""a o as os um uma uns umas de do da dos das no na nos nas em ao aos à às pelo pela pelos pelas
 e ou mas que se com sem por para pra sobre entre até após como quando onde porque pois nem meu minha
 seu sua teu tua nosso nossa esse essa este esta aquele aquela isso isto muito muita mais menos tão
 não já lhe me te nos vos the of to and a an in on at for with my your""".split())

# palavras com que uma frase NUNCA termina: se o OCR "fechou" a frase nelas, quase sempre
# comeu o resto ("Quero dar uma boa olhada nesse." + "monstro.")
NAO_TERMINA = set("""a o as os um uma uns umas de do da dos das na nas em ao aos à às pelo pela pelos
 pelas e ou mas com sem por para pra pro sobre entre meu minha seu sua teu tua nosso nossa nesse nessa
 neste nesta naquele naquela desse dessa deste desta num numa the of to and an""".split())


class Leitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ultima = None
        self.sumiu_desde = None
        self.despejou = False
        self.repetir = ""
        self.visto = ("", 0.0)
        self._zerar()

    @property
    def estab(self):
        return self.cfg["estabilidade_ms"] / 1000

    @property
    def sem_pont(self):
        return max(self.cfg["sem_pontuacao_ms"] / 1000, self.estab)

    @property
    def intervalo(self):
        return self.cfg["intervalo_ms"] / 1000

    def _zerar(self):
        self.ativa = False
        self.faladas = []
        self.falado_norm = ""
        self.falou = False
        self.votos = {}
        self.pend = {}
        self.ult_trav = None      # palavras da frase que esta aparecendo, na leitura anterior

    def _parcial(self, f):
        """Frase ainda aparecendo (efeito de digitacao): devolve um pedaco ja firme para falar
        logo, sem esperar o ponto final. -> (pedaco, fim do pedaco em f) ou None.

        Palavra firme = ja apareceu a seguinte (nao esta pela metade) e foi lida igual na leitura
        anterior. Corta numa virgula/reticencias ou, sem pontuacao, a cada N palavras."""
        pal = [(m.group(), m.end()) for m in re.finditer(r"\S+", f)]
        norm = [normalizar(p) for p, _ in pal]
        ant, self.ult_trav = self.ult_trav or [], norm
        k = 0
        while k < min(len(ant), len(norm)) and ant[k] == norm[k] and norm[k]:
            k += 1
        completas = min(k, len(pal) - 1)
        if completas < 3:
            return None
        corte = None
        for i in range(completas - 1, 1, -1):          # pausa natural: "O Cal disse..."
            if re.search(r"(,|;|:|…|\.\.\.|—)[\"')]*$", pal[i][0]):
                corte = i + 1
                break
        n = int(self.cfg.get("palavras_por_pedaco", 5))
        if corte is None and completas >= n:
            corte = completas
            # nunca termina o pedaco numa palavra de ligacao ("...a tempestade de" / "ontem")
            while corte > 3 and norm[corte - 1] in LIGACAO:
                corte -= 1
            if norm[corte - 1] in LIGACAO:
                return None
        if not corte:
            return None
        fim = pal[corte - 1][1]
        pedaco = f[:fim].strip()
        if len(normalizar(pedaco)) < 8:          # "O Cal disse" ja vale
            return None
        self.ult_trav = norm[corte:]
        return pedaco, fim

    def _fechar(self, quando):
        if self.falado_norm:
            self.ultima = (self.falado_norm, quando)
        self._zerar()

    def _ja_falada(self, norm, agora):
        u = self.ultima
        if not u or agora - u[1] > self.cfg.get("memoria_s", 3):
            return False
        n = len(norm)
        return n <= len(u[0]) + 2 and parecidos(u[0][:n], norm)

    def _emitir(self, frases, agora):
        if self._ja_falada(self.falado_norm, agora):
            return []
        fala = " ".join(f for f, _ in frases)
        nova = not self.falou
        self.falou = True
        self.repetir = fala if nova else (self.repetir + " " + fala).strip()
        return [(fala, nova, min(t for _, t in frases))]

    def _despejar(self, agora):
        prazo = max(0.6, 2.5 * self.intervalo)
        cands = [t for t, (n, visto) in self.pend.items() if n >= 2 and agora - visto <= prazo]
        self.pend = {}
        if not cands:
            return []
        t = max(cands, key=len)
        u = self.ultima
        if not self.falado_norm and u and agora - u[1] <= 2.0 and semelhanca(normalizar(t), u[0]) >= 0.55:
            return []
        self.faladas.append(t)
        self.falado_norm += normalizar(t)
        self.votos = {}
        return self._emitir([(t, agora)], agora)

    def tick(self, texto, agora):
        """-> [(texto_para_falar, pagina_nova, visto_em)]"""
        if not texto:
            if self.sumiu_desde is None:
                self.sumiu_desde = agora
                return []
            ausente = agora - self.sumiu_desde
            saida = []
            if not self.despejou and ausente >= 0.2:
                self.despejou = True
                saida = self._despejar(agora)
            if self.ativa and ausente >= self.cfg["sumiu_ms"] / 1000:
                self._fechar(self.sumiu_desde)
            return saida
        self.sumiu_desde = None
        self.despejou = False
        total = normalizar(texto)
        if len(total) < 2:
            return []
        crescendo = len(total) > len(self.visto[0]) and agora - self.visto[1] < 1.0   # efeito de digitacao
        self.visto = (total, agora)
        fim_falado = 0
        if self.falado_norm:
            achado, fim_falado = alinhar(self.falado_norm, total)
            if achado < 0.7:
                self._fechar(agora)
                fim_falado = 0
        # A MESMA legenda voltando depois de uma leitura ruim do OCR (fundo claro faz o texto
        # "piscar"): comeca com o que acabou de ser falado -> continua dela, falando so o resto
        # (antes repetia a frase inteira: "Bom, na minha juventude," + a frase toda de novo)
        if not self.falado_norm and self.ultima and agora - self.ultima[1] <= 4.0 and len(self.ultima[0]) >= 6:
            ach, fim_u = alinhar(self.ultima[0], total)
            if ach >= 0.85:
                self.falado_norm, self.falou = self.ultima[0], True
                self.ultima = None
                fim_falado = fim_u
                diag.ev("mesma_legenda_voltou", texto=texto[:60])
        self.ativa = True

        ini = cortar_apos(texto, fim_falado) if self.falado_norm else 0
        resto = texto[ini:]
        fr = dividir_frases(resto)
        prontas, pos, fim_raw, travou = [], 0, ini, False
        j_trav = f_trav = ini_trav = None
        for j, f in enumerate(fr):
            idx = resto.find(f, pos)
            ini_f = idx if idx >= 0 else pos
            pos = ini_f + len(f)
            nf = normalizar(f)
            if not nf:
                if not travou:
                    fim_raw = ini + pos
                continue
            v = self.votos.setdefault((len(self.faladas) + j, nf), [0, agora, collections.Counter()])
            v[0] += 1
            v[2][f] += 1
            if travou:
                continue
            if j < len(fr) - 1:
                ult_pal = nf.split()[-1] if nf.split() else ""
                fechada = v[0] >= (5 if ult_pal in NAO_TERMINA else 2)
            else:
                completa = (f.rstrip("\"')")[-1:] in FIM and not hesitacao(f)
                            and (nf.split() or [""])[-1] not in NAO_TERMINA)
                need = self.estab if completa else self.sem_pont
                fechada = v[0] >= max(2, round(need / self.intervalo * 0.75)) and agora - v[1] >= need
            if fechada:
                prontas.append((v[2].most_common(1)[0][0], v[1]))
                fim_raw = ini + pos
            else:
                travou = True
                j_trav, f_trav, ini_trav = j, f, ini + ini_f
        # a ultima frase ainda esta aparecendo: fala o pedaco que ja esta firme
        if (self.cfg.get("falar_enquanto_aparece", True) and crescendo
                and j_trav is not None and j_trav == len(fr) - 1):
            p = self._parcial(f_trav)
            if p:
                prontas.append((p[0], agora))
                fim_raw = ini_trav + p[1]
                diag.ev("pronta", texto=p[0], parcial=True)
        elif j_trav is None:
            self.ult_trav = None
        saida = []
        if prontas:
            self.faladas += [f for f, _ in prontas]
            self.falado_norm = normalizar(texto[:fim_raw])
            self.votos = {}
            self.pend = {}
            saida = self._emitir(prontas, agora)
        pend = texto[fim_raw:].strip()
        if len(normalizar(pend)) >= 2:
            p = self.pend.setdefault(pend, [0, agora])
            p[0] += 1
            p[1] = agora
        return saida
