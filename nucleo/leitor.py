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


def inicio_trocado(a, b):
    """Legendas que so diferem no COMECO sao falas diferentes ("Mano... eu nem quero ouvir essa
    merda." -> "Tipo, eu nem quero ouvir essa merda."): o resto igual nao pode fazer a segunda
    parecer a mesma legenda. Erro de OCR troca 1-2 letras, nao uma palavra inteira."""
    if len(a) < 8 or len(b) < 8:
        return False
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    prim = next((m for m in sm.get_matching_blocks() if m.size >= 4), None)
    if prim is None or prim.a < 3 or prim.b < 3:
        return False
    return semelhanca(a[:prim.a], b[:prim.b]) < 0.5


def alinhar(falado, total):
    n = len(falado)
    if n < 8:
        return (1.0 if parecidos(total[:n], falado) else semelhanca(total[:n], falado)), min(n, len(total))
    if inicio_trocado(falado, total):
        return 0.0, 0
    sm = difflib.SequenceMatcher(None, falado, total, autojunk=False)
    blocos = [b for b in sm.get_matching_blocks() if b.size >= 4]
    if not blocos:
        return 0.0, 0
    ult = blocos[-1]
    fim = ult.b + ult.size + (n - (ult.a + ult.size))
    return sum(b.size for b in blocos) / n, max(0, min(len(total), fim))


def _final_da_anterior(fala, anterior, minimo=2):
    """`fala` e o final exato da anterior, com a MESMA pontuacao no fim. Eco de verdade muda o tom:
    "Essa noite?" depois de "...tem que ser feito essa noite." e fala da outra pessoa e fica."""
    w = [normalizar(x) for x in fala.split() if normalizar(x)]
    p = [normalizar(x) for x in anterior.split() if normalizar(x)]
    fim = lambda t: t.rstrip("\"' )")[-1:]
    return len(w) >= minimo and len(p) > len(w) and p[-len(w):] == w and fim(fala) == fim(anterior)


def _pedaco_com_buracos(fala, anterior):
    """As palavras de `fala` (2+) aparecem em ordem na anterior, mas NAO seguidas."""
    w = [normalizar(x) for x in fala.split() if normalizar(x)]
    p = [normalizar(x) for x in anterior.split() if normalizar(x)]
    if len(w) < 2 or len(p) <= len(w):
        return False
    pos, k = [], 0
    for x in w:
        while k < len(p) and p[k] != x:
            k += 1
        if k == len(p):
            return False
        pos.append(k)
        k += 1
    return pos[-1] - pos[0] + 1 > len(w)        # tem buraco no meio


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
        self.sumiu_ate = 0.0              # ultima vez que a tela ficou sem legenda (>= 0,5 s)
        self.despejou = False
        self.repetir = ""
        self.visto = ("", 0.0)
        self.recentes_raw = collections.deque(maxlen=30)     # (instante, texto) das ultimas leituras
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
        self.digitando = False
        self.ativa = False
        self.faladas = []
        self.falado_norm = ""
        self.falou = False
        self.emitiu = False
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

    @staticmethod
    def _conhecida(w):
        from .tela import _FREQ
        w = re.sub(r"[^\w]", "", w.lower())
        if not w or w.isdigit():
            return True
        f = _FREQ.get(w, 0)
        return f >= (20000 if len(w) <= 2 else 300 if len(w) == 3 else 1)

    @classmethod
    def _emendar(cls, pals):
        """Palavra partida pelo OCR (letra comida vira espaco): "bo ito" / "boni o" -> "bonito"."""
        from .tela import _FREQ
        out, i = [], 0
        letras = [""] + list("abcdefghijlmnopqrstuvxzçãáéêíóõôú")
        while i < len(pals):
            a = pals[i]
            # tres pedacos curtos com letras comidas: "bo i o," -> "bonito,"
            if i + 2 < len(pals):
                c3 = [re.sub(r"[^\w]", "", x) for x in pals[i:i + 3]]
                if (all(x.isalpha() and len(x) <= 3 for x in c3) and not all(cls._conhecida(x) for x in pals[i:i + 3])
                        and not a.endswith((",", ".", "!", "?", ";", ":", "…"))
                        and not pals[i + 1].endswith((",", ".", "!", "?", ";", ":", "…"))):
                    melhor, fm = None, 0
                    for f1 in letras:
                        for f2 in letras:
                            w = c3[0] + f1 + c3[1] + f2 + c3[2]
                            f = _FREQ.get(w.lower(), 0)
                            if f > fm and len(w) >= 5:
                                melhor, fm = w, f
                    if melhor and fm >= 2000:
                        b = pals[i + 2]
                        out.append(a[:len(a) - len(a.lstrip("\"'(¿¡"))] + melhor + b[len(b.rstrip(".,;:!?…\"')")):])
                        i += 3
                        continue
            # par com palavra estranha, ou "boni o" (palavra rara + letra solta)
            par_estranho = i + 1 < len(pals) and (not (cls._conhecida(a) and cls._conhecida(pals[i + 1]))
                                                  or (len(re.sub(r"[^\w]", "", pals[i + 1])) == 1
                                                      and _FREQ.get(a.lower(), 0) < 2000))
            if par_estranho:
                b = pals[i + 1]
                ca, cb = re.sub(r"[^\w]", "", a), re.sub(r"[^\w]", "", b)
                pre, suf = a[:len(a) - len(a.lstrip("\"'(¿¡"))], b[len(b.rstrip(".,;:!?…\"')")):]
                melhor, fm = None, 0
                if ca and cb and ca.isalpha() and cb.isalpha() and not a.endswith((",", ".", "!", "?", ";", ":", "…")):
                    for meio in [""] + list("abcdefghijlmnopqrstuvxzçãáéêíóõôú"):
                        w = (ca + meio + cb)
                        f = _FREQ.get(w.lower(), 0)
                        # 4 letras so se for MUITO usada ("q er" -> "quer"; "oq e" nao vira "oque")
                        if f > fm and (len(w) >= 5 or (len(w) == 4 and f >= 50000 and w.lower() != "oque")):
                            melhor, fm = w, f
                if melhor and fm >= 2000:
                    out.append(pre + melhor + suf)
                    i += 2
                    continue
            # duas palavras que EXISTEM, mas juntas formam uma bem mais usada: "Nega ivo." ->
            # "Negativo." ("com o"/"a gente" nao viram "como"/"agente": as partes sao mais usadas)
            if i + 1 < len(pals) and not a.endswith((",", ".", "!", "?", ";", ":", "…")):
                b = pals[i + 1]
                ca, cb = re.sub(r"[^\w]", "", a), re.sub(r"[^\w]", "", b)
                if ca.isalpha() and cb.isalpha() and len(ca) >= 2 and len(cb) >= 2:
                    pre, suf = a[:len(a) - len(a.lstrip("\"'(¿¡"))], b[len(b.rstrip(".,;:!?…\"')")):]
                    partes = max(_FREQ.get(ca.lower(), 0), _FREQ.get(cb.lower(), 0))
                    melhor, fm = None, 0
                    for meio in [""] + list("abcdefghijlmnopqrstuvxzçãáéêíóõôú"):
                        w = ca + meio + cb
                        f = _FREQ.get(w.lower(), 0)
                        if f > fm and len(w) >= 6:
                            melhor, fm = w, f
                    if melhor and fm >= 5000 and fm > 3 * partes:
                        out.append(pre + melhor + suf)
                        i += 2
                        continue
            out.append(a)
            i += 1
        return out

    @classmethod
    def _juntar(cls, cands):
        """Junta varias leituras da MESMA legenda: parte da com mais palavras de verdade e completa os
        buracos com palavras certas das outras (cada leitura perde um pedaco diferente)."""
        def limpar(c):
            # pontuacao grudada pelo OCR: "bonito;então:..." -> "bonito, então..."
            c = re.sub(r"(?<=[^\W\d_])[;:,]+(?=[^\W\d_])", ", ", c)
            c = re.sub(r"[;:]+(?=\.\.\.|…)", "", c)
            c = re.sub(r"\b([Oo])q\s?e\b", r"\1 que", c)        # "oq e" / "oqe" -> "o que" (OCR comeu o "u")
            # aspas/tracos soltos no meio ("então ' só", "'que") e ";" no meio da frase
            c = re.sub(r"(?<=\s)['\"`´]+(?=\w)|(?<=\w)['`´]+(?=\s)|(?<=\s)['\"`´\-]+(?=\s)", "", c)
            c = re.sub(r"^[\s,;:.\-–—'\"`´]+(?=\w)", "", c)       # ", Onde você tá" / "- Calma"
            c = re.sub(r"(?<=\w)\.[-–—](?=\s)", "...", c)          # "vi algo.- no fim" (15/11)
            # ponto falso do OCR no meio da frase: "Só. pareça..." (frase nova comeca com maiuscula)
            c = re.sub(r"(?<=[^\W\d_])\.(?=\s+[a-zà-ÿ])", "", c)
            if c.count('"') % 2:                                  # 'O homem" da casa' (aspa sem par)
                c = re.sub(r'(?<=\w)"(?=\s+[a-zà-ÿ])', "", c)
            return re.sub(r"\s+", " ", re.sub(r"(?<=\w);(?=\s)", ",", c)).strip()
        listas = [cls._emendar(limpar(c).split()) for c in cands]
        # empate: fica a 1a (a que o leitor ja tinha escolhido por votos)
        listas.sort(key=lambda p: cls._nota(" ".join(p)), reverse=True)
        base = list(listas[0])
        for outra in listas[1:]:
            kb = [normalizar(w) for w in base]
            ko = [normalizar(w) for w in outra]
            sm = difflib.SequenceMatcher(None, kb, ko, autojunk=False)
            novo = []
            for op, i1, i2, j1, j2 in sm.get_opcodes():
                trecho_b, trecho_o = base[i1:i2], outra[j1:j2]
                o_ok = trecho_o and all(cls._conhecida(w) for w in trecho_o)
                if op == "insert" and o_ok and j2 - j1 <= 3 and not (i1 == len(base) and base and base[-1][-1:] in FIM) \
                        and not (i1 == 0 and trecho_o[-1].rstrip("\"')")[-1:] in FIM):
                    # (nem poe uma FRASE INTEIRA de outra legenda no comeco: "Ei. Tipo, eu nem quero...")
                    # (nunca pendura palavras DEPOIS do ponto final: uma leitura com as linhas fora de
                    # ordem fez "Fica de boa, não... você sabe." virar "...você sabe. não...")
                    novo += trecho_o
                elif op == "replace" and o_ok and not all(cls._conhecida(w) for w in trecho_b) \
                        and abs((j2 - j1) - (i2 - i1)) <= 2:
                    novo += trecho_o
                elif op == "equal" and trecho_o:
                    # mesma palavra: fica com a pontuacao/acento da que for palavra de verdade
                    novo += [wo if cls._conhecida(wo) and not cls._conhecida(wb) else wb
                             for wb, wo in zip(trecho_b, trecho_o)]
                else:
                    novo += trecho_b
            base = novo
        return " ".join(base)

    @classmethod
    def _tem_estranha(cls, texto):
        """Palavra de 5+ letras, em minuscula, que nao existe (nome proprio com maiuscula nao conta)."""
        from .tela import _FREQ
        if not _FREQ:
            return False
        return any(len(w) >= 5 and w[:1].islower() and not cls._conhecida(w)
                   for w in re.findall(r"[^\W\d_]+", texto))

    @staticmethod
    def _nota(texto):
        """Letras em palavras que existem no portugues contam +1, nas estranhas ("bo", "bemb") -1,5."""
        from .tela import _FREQ
        if not _FREQ:
            return 0.0
        nota = 0.0
        for w in re.findall(r"[^\W\d_]+", texto.lower()):
            f = _FREQ.get(w, 0)
            ok = f >= (20000 if len(w) <= 2 else 300 if len(w) == 3 else 1)
            nota += len(w) if ok else -1.5 * len(w)      # por letra: "in o" nao vale mais que "indo"
        return nota

    def _pedaco_de_leitura_maior(self, total, agora):
        if len(total) < 10:
            return False
        for t, c in reversed(self.recentes_raw):
            if agora - t > 1.5 or t < self.sumiu_ate:
                break
            nc = normalizar(c)
            if len(nc) >= len(total) + 6 and alinhar(total, nc)[0] >= 0.85:
                # FRASES INTEIRAS da leitura maior nao sao pedaco: na troca o OCR le a legenda
                # velha e a nova juntas ("Bora tirar esse pessoal daqui, cara! Lado errado, por
                # aqui.") e "Lado errado, por aqui." sumia
                fr = dividir_frases(c)
                inteiras = [normalizar(" ".join(fr[a:b])) for a in range(len(fr)) for b in range(a + 1, len(fr) + 1)
                            if (a, b) != (0, len(fr))]
                if any(abs(len(x) - len(total)) <= 4 and semelhanca(x, total) >= 0.8 for x in inteiras):
                    continue
                return True
        return False

    def _instavel(self, texto, agora):
        """-> (quando esta legenda comecou a aparecer, quantas leituras DIFERENTES dela houve)."""
        base = normalizar(texto)
        t_ini, vs = agora, set()
        for t, c in reversed(self.recentes_raw):
            nc = normalizar(c)
            if agora - t > 3.0 or t < getattr(self, "sumiu_ate", 0.0):
                break             # antes da tela ficar sem legenda: era outra legenda
            if not c or semelhanca(nc, base) < 0.6 or inicio_trocado(nc, base):
                continue          # leitura embaralhada pelo OCR (ou outra legenda): pula
            t_ini = t
            vs.add(normalizar(c))
        return t_ini, len(vs)

    @staticmethod
    def _tamanho_ok(c, texto):
        """Candidata de tamanho parecido (0,6-1,6x). Ate 2x so se for a MESMA frase com o meio que
        faltou nesta leitura (mesma 1a e ultima palavra: "Sabe, e você ... saindo?" x "Sabe, o que
        você ... bonito, então... vamos saindo?"). Duas legendas empilhadas mudam a 1a ou a ultima
        ("Calma. Merda!" x "Calma. Merda! Tia Tee! Qualé, pô!") e ficam de fora."""
        r = len(normalizar(c)) / max(1, len(normalizar(texto)))
        if 0.6 <= r <= 1.6:
            return True
        pc = [normalizar(w) for w in c.split() if normalizar(w)]
        pt = [normalizar(w) for w in texto.split() if normalizar(w)]
        return 0.5 <= r <= 2.0 and bool(pc) and bool(pt) and pc[0] == pt[0] and pc[-1] == pt[-1]

    def _melhor_versao(self, texto, agora, so_primeira=False):
        """so_primeira: `texto` e a 1a frase de uma legenda com varias; compara com a 1a frase das
        outras leituras ("Queg;qüeeu fale o quê?" + "Quer que eu fale o quê?" -> a certa)."""
        base = normalizar(texto)
        fim = texto.rstrip("\"' )")[-1:]
        # as leituras desta mesma legenda (desde que ela apareceu), terminando igual
        run = []
        for t, c in reversed(self.recentes_raw):
            nc = normalizar(c)
            if agora - t > 3.0 or t < getattr(self, "sumiu_ate", 0.0):
                break             # antes da tela ficar sem legenda: era outra legenda
            if so_primeira and c:
                fr = dividir_frases(c)
                c = fr[0] if fr else ""
                nc = normalizar(c)
            if not c or semelhanca(nc, base) < 0.6 or inicio_trocado(nc, base):
                continue          # leitura embaralhada pelo OCR (ou outra legenda): pula
            run.append(c)
        # (do mesmo tamanho: uma leitura com DUAS legendas empilhadas nao entra)
        # termina igual; se ESTA leitura veio cortada (sem pontuacao: "...Pra"), vale a completa
        # ("Quer que eu fale o quê? Pra botar na agenda?" tinha sido lida e ficou de fora, 15/11)
        cortada = fim not in FIM
        cands = [texto] + [c for c in dict.fromkeys(run) if c != texto
                           and (c.rstrip("\"' )")[-1:] == fim or (cortada and c.rstrip("\"' )")[-1:] in FIM))
                           and self._tamanho_ok(c, texto)]
        try:
            melhor = self._juntar(cands)
        except Exception as e:
            diag.ev("juntar_erro", erro=repr(e)[:120])
            return texto
        return melhor if self._nota(melhor) >= self._nota(texto) else texto

    def _fechar(self, quando):
        if self.falado_norm and not getattr(self, "emitiu", False) and self.ultima:
            # pagina que NAO FALOU nada (leitura embaralhada descartada, "Aproveita o jan ar pessoa."): nao
            # troca a lembranca da ultima fala por ela (senao a versao certa logo depois era falada
            # de novo: "Aproveita o jantar, pessoal. Eu volto pra sobremesa." 2x, 15/11)
            self.ultima = (self.ultima[0], quando)
        elif self.falado_norm:
            self.ultima = (self.falado_norm, quando)
            self.ultima_txt = self.repetir
        self._zerar()

    def _ja_falada(self, norm, agora):
        u = self.ultima
        if not u or agora - u[1] > self.cfg.get("memoria_s", 3):
            return False
        n = len(norm)
        return n <= len(u[0]) + 2 and parecidos(u[0][:n], norm) and not inicio_trocado(u[0], norm)

    def _emitir(self, frases, agora):
        if self._ja_falada(self.falado_norm, agora):
            return []
        fala = " ".join(f for f, _ in frases)
        nova = not self.falou
        if len(fala.split()) == 1 and normalizar(fala) in LIGACAO and fala.rstrip("\"')")[-1:] not in FIM:
            # so uma palavra de ligacao, sem pontuacao ("Pra" de "Pra botar na agenda?" cortado): nao
            # faz sentido sozinha (15/11)
            diag.ev("descartei", motivo="palavra de ligacao sozinha", texto=fala)
            return []
        u = self.ultima
        n = normalizar(fala)
        if nova and u and agora - u[1] <= 4.0 and len(n) >= 12 and not inicio_trocado(u[0], n):
            # a MESMA legenda lida embaralhada de novo (OCR instavel apos o fim dela): se quase tudo
            # do texto "novo" esta no que acabou de ser falado, nao repete (15/11: "Falaram que vão te
            # fazer uma visita..." saiu 7 vezes; "Deixa eu falar uma parada." + "e falar uma parada.")
            sm = difflib.SequenceMatcher(None, n, u[0], autojunk=False)
            if sum(b.size for b in sm.get_matching_blocks() if b.size >= 3) / len(n) >= 0.75:
                diag.ev("descartei", motivo="pedaco da fala anterior", texto=fala[:60])
                self.falou = True
                return []
        elif nova and u and agora - u[1] <= 2.0 and _final_da_anterior(fala, getattr(self, "ultima_txt", "")):
            # (2+ palavras: uma palavra so pode ser resposta da outra pessoa, "Lantejoulas?")
            # o FINAL da fala que acabou de sair, lido de novo ("na merda." logo apos "Sabe, a gente
            # tá afundado na merda.", 15/11)
            diag.ev("descartei", motivo="final da fala anterior", texto=fala[:60])
            self.falou = True
            return []
        elif nova and u and agora - u[1] <= 4.0 and len(n) >= 5 and _pedaco_com_buracos(fala, getattr(self, "ultima_txt", "")):
            # curta, mas e a fala anterior com palavras do MEIO faltando ("A sair dessa." logo apos "A
            # gente tem que sair dessa."): lixo do OCR. Resposta curta de verdade usa palavras SEGUIDAS
            # da anterior ("Coleta.", "Deve ser.") e continua sendo falada
            diag.ev("descartei", motivo="pedaco da fala anterior (com buracos)", texto=fala[:60])
            self.falou = True
            return []
        self.falou = True
        self.emitiu = True
        self.repetir = fala if nova else (self.repetir + " " + fala).strip()
        return [(fala, nova, min(t for _, t in frases))]

    def _despejar(self, agora):
        prazo = max(0.6, 2.5 * self.intervalo)
        cands = [t for t, (n, visto) in self.pend.items() if n >= 2 and agora - visto <= prazo]
        self.pend = {}
        if not cands:
            return []
        t = max(cands, key=len)
        melhor = self._melhor_versao(t, agora)
        if melhor != t:
            diag.ev("melhor_versao", lido=t, virou=melhor)
            t = melhor
        u = self.ultima
        if (not self.falado_norm and u and agora - u[1] <= 2.0 and semelhanca(normalizar(t), u[0]) >= 0.55
                and not inicio_trocado(u[0], normalizar(t))):
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
        if self.sumiu_desde is not None and agora - self.sumiu_desde >= 0.5:
            self.sumiu_ate = agora        # ficou sem legenda: as leituras de antes nao se juntam
        self.sumiu_desde = None
        self.despejou = False
        total = normalizar(texto)
        if len(total) < 2:
            return []
        # efeito de digitacao. Se a leitura anterior ja terminava a frase ("...vamos saindo?"), a
        # legenda apareceu INTEIRA: ela "crescer" agora e so o OCR piscando no fundo do video
        # (15/11: falou "Sabe, o e você tá vestindo agora já tá bem" + "saindo?")
        def termina(s):
            s = s.rstrip("\"' )")
            return bool(s) and s[-1] in FIM
        crescendo = (len(total) > len(self.visto[0]) and agora - self.visto[1] < 1.0
                     and not (termina(getattr(self, "visto_raw", "")) and termina(texto)))
        if (not self.falado_norm and not crescendo and not termina(texto)
                and self._pedaco_de_leitura_maior(total, agora)):
            # leitura que e so um PEDACO de uma leitura mais completa desta mesma legenda, vista ha
            # instantes (o OCR comeu o comeco: "fale o quê? Pra" depois de "Quer que eu fale o quê?
            # Pra botar na agenda?"): nao vota, senao o pedaco ganha e e falado (15/11)
            self.recentes_raw.append((agora, texto))
            return []
        self.visto_raw = texto
        self.recentes_raw.append((agora, texto))
        if (crescendo and len(self.visto[0]) >= 4 and total.startswith(self.visto[0][:max(4, len(self.visto[0]) - 2)])):
            self.digitando = True          # esta legenda foi vista CRESCENDO (texto sendo digitado)
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
                # sem ponto final ou com "...": espera mais, porque pode ainda estar sendo digitada.
                # Mas se a legenda apareceu INTEIRA de uma vez (RDR2, GTA, filmes), nao vai crescer:
                # 0,4 s bastam (antes 0,7 s; "Srta. Gaskill...", "Capítulo 1: Colter", 14/11)
                need = (self.estab if completa else self.sem_pont if getattr(self, "digitando", False)
                        else max(self.estab, float(self.cfg.get("sem_pontuacao_inteira_ms", 400)) / 1000))
                fechada = v[0] >= max(2, round(need / self.intervalo * 0.75)) and agora - v[1] >= need
                if j == 0 and not self.falado_norm:
                    # OCR instavel (cada leitura sai diferente: fundo claro atras da legenda): espera
                    # ~1 s desde que a legenda apareceu, para juntar leituras e escolher a completa
                    # (15/11: "Sabe, o você tá vestindo agora já tá bem vamos saindo?" sem "bonito, então")
                    t_ini, variantes = self._instavel(texto, agora)
                    espera = float(self.cfg.get("espera_instavel_s", 1.0))
                    if variantes >= 3 and agora - t_ini < espera:
                        fechada = False
                    elif (not fechada and variantes >= 3 and len(fr) == 1 and completa
                          and agora - t_ini >= espera + 0.3):
                        # tao embaralhado que nenhuma leitura se repete: fala JUNTANDO as leituras
                        fechada = True
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
        if prontas and ini == 0 and fim_raw >= len(texto.rstrip()) - 1:
            # a legenda INTEIRA vai ser falada de uma vez: entre as leituras dos ultimos instantes
            # (o OCR "pisca" no fundo claro do video), fala a com mais palavras de verdade
            melhor = self._melhor_versao(texto, agora)
            t_ini, _ = self._instavel(texto, agora)
            if self._tem_estranha(melhor) and agora - t_ini < float(self.cfg.get("espera_estranha_s", 1.0)):
                # ainda tem palavra embaralhada ("ueuoctldade"): espera mais leituras (a certa,
                # "velocidade", chegou na mesma hora, 15/11). Frase limpa sai na hora.
                return []
            if melhor.split() != texto.split():
                diag.ev("melhor_versao", lido=" ".join(f for f, _ in prontas), virou=melhor)
                prontas = [(melhor, min(t for _, t in prontas))]
                self._juntou = True
        elif prontas and ini == 0 and not self.falou:
            # so a 1a frase de uma legenda com varias vai ser falada agora: junta so ela
            f0, t0 = prontas[0]
            melhor = self._melhor_versao(f0, agora, so_primeira=True)
            if melhor.split() != f0.split():
                diag.ev("melhor_versao", lido=f0, virou=melhor)
                prontas = [(melhor, t0)] + prontas[1:]
        if prontas:
            self.faladas += [f for f, _ in prontas]
            # o que foi falado: o texto JUNTADO (mais limpo que esta leitura) -> as proximas leituras
            # embaralhadas da mesma legenda sao reconhecidas como ela (nao repete)
            self.falado_norm = (normalizar(prontas[0][0]) if getattr(self, "_juntou", False)
                                else normalizar(texto[:fim_raw]))
            self._juntou = False
            self.votos = {}
            self.pend = {}
            saida = self._emitir(prontas, agora)
        pend = texto[fim_raw:].strip()
        if len(normalizar(pend)) >= 2:
            p = self.pend.setdefault(pend, [0, agora])
            p[0] += 1
            p[1] = agora
        return saida
