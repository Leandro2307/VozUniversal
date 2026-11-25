# -*- coding: utf-8 -*-
"""Sujeira do OCR no comeco da fala, etiqueta de idioma e frase sem dono que se repete (GTA 6, 23/11)."""
import difflib
import re

from .util import normalizar, semelhanca

_IDIOMAS = ("espanhol", "ingles", "frances", "italiano", "crioulo", "portugues")


def tirar_idioma(texto):
    """O GTA poe o idioma original antes da fala traduzida ("Espanhol Que se foda isso!"). O OCR le
    "Ilsspanh ll", "flespanh lil (", "Fspanhol": a 1a palavra parecida com o idioma, seguida de
    Maiuscula, sai (era falada como se fosse parte da frase)."""
    m = re.match(r"^\W{0,2}([^\s.,!?…:;]{5,11})[\])|!]?\s+(?:[lI1i|!\])]{1,3}\s+)?[(\[|]{0,2}([A-ZÀ-Ý¿¡].*)$", texto or "")
    if not m:
        return texto
    p = normalizar(re.sub(r"[\W\d_]", "", m.group(1)))
    # "Italiano!" ou "Espanhol" dito de verdade segue em minuscula ou com virgula: nao cai aqui
    p2 = re.sub(r"^[il]+", "", p)             # o "[" da etiqueta lido como I/l
    if len(p) >= 5 and any(difflib.SequenceMatcher(None, x, i).ratio() >= (0.7 if i == "espanhol" else 0.85)
                           for i in _IDIOMAS for x in (p, p2)):
        return m.group(2)
    return texto


def _parece_nome(pedaco, nome):
    # ("Pessoas210cais" = Pessoas locais: o OCR troca l/o por 1/2/0)
    p, n = normalizar(pedaco).translate(str.maketrans("0125", "olls")), normalizar(nome)
    return bool(p and n) and (semelhanca(p, n) >= 0.8 or difflib.SequenceMatcher(None, p, n).ratio() >= 0.8)


_CORES_NOME = re.compile(r"\s+(?:vermelh[oa]|laranja|amarel[oa]|verde|ciano|azul|rox[oa]|magenta|rosa|branc[oa])$")
# palavras curtas de verdade que podem vir em minuscula antes de um Nome ("do Brian", "com Donnie")
_CURTAS_ANTES = set("""a à e é o ó ou os as às um uma de do da dos das no na nos nas em ao aos com por pro pra para até
 que se já só sem sou foi era vai vi ver tem tá tô né não sim meu seu tio tia dr sr sra são rio ele ela eu tu me te
 mas nem lá aí ali oi ei ah oh hm hã pô ô cê vá pé""".split())


def prefixo_estragado(fala):
    """Antes de procurar o nome: "Do:. De nada." "faca:.: e daí" "que: porra" (dois-pontos que nao existem)."""
    m = re.match(r"^\W{0,2}([^\s:]{1,8}):[.:;,]+\s+(\S.*)$", fala)
    if m:                                                                         # (nome inteiro fica)
        fala = m.group(2) if len(m.group(1)) <= 3 or m.group(1)[:1].islower() else m.group(1) + ": " + m.group(2)
    fala = re.sub(r"^[A-ZÀ-Ý]{1,2}:\s+(?=[A-ZÀ-Ý][a-zà-ÿ])", "", fala)                # "IA: Você tá bem?"
    fala = re.sub(r"^\s*:\s*(?=\S)", "", fala)
    fala = re.sub(r"\b([Qq]ue|de|com|pra|pro|e|o|a):\s+(?=[a-zà-ÿ])", r"\1 ", fala)
    return fala


def resto_do_nome(fala, nome):
    """O nome de quem fala (inteiro, estragado ou so o fim) colado SEM dois-pontos (GTA 6, 24/11):
    "Mattia Depois você volta", "tia Eu falei, viu?", "focal Quer atenção", "Final'. Cala a boca",
    "Pete Vou pousar aqui.", "Capítulo Fipa17 Conta logo". "" = era so o nome."""
    from .tela import _FREQ
    nome = _CORES_NOME.sub("", nome)
    ws = nome.split()

    def comum(w):                              # "Donnie Oficialmente" sim; "Jason Duval" (sobrenome) nao
        return not _FREQ or _FREQ.get(re.sub(r"[^\wÀ-ÿ]", "", w).lower(), 0) >= 1000

    # so o nome da pessoa, dito por ela mesma ("Mattia." / "Pessoa local,")
    if normalizar(fala) == normalizar(nome) and not fala.rstrip().endswith(("!", "?", "...")):
        return ""
    # nome inteiro estragado, sem ":" ("Capítulo Fipa17 Conta logo", "Pessoas locais. O Dr. Furacão")
    if len(ws) >= 2:
        m = re.match(r"^\W{0,3}((?:[^\s:]+\s+){%d}[^\s:]+?)['.,;]{0,3}\s+(?=[A-ZÀ-Ý])(\S.*)$" % (len(ws) - 1), fala)
        if m and difflib.SequenceMatcher(None, normalizar(m.group(1)), normalizar(nome)).ratio() >= 0.78:
            return m.group(2)
        # "Pessoas eu podia ter morrido"
        m = re.match(r"^\W{0,2}([A-ZÀ-Ý][^\W\d_]{3,})\s+((?:eu|você|cê|a gente|ele|ela|não|tá|tô)\b.*)$", fala)
        if m and _parece_nome(m.group(1), ws[0]):
            return m.group(2)
    # a ultima palavra (ou o nome de uma palavra so): "Mattia Depois você volta", "Final'. Cala a boca"
    m = re.match(r"^\W{0,3}([^\s:.,;'!?]+)(['.,;\-]{0,3})\s+([A-ZÀ-Ý][^\s]*)(.*)$", fala)
    if m and len(ws[-1]) >= 4 and normalizar(m.group(1)) == normalizar(ws[-1]) and comum(m.group(3)) \
            and not (m.group(2) == "." and len(ws) == 1):
        return m.group(3) + m.group(4)
    # "i annie: Tá, bora" (Donnie partido em dois)
    m = re.match(r"^\W{0,2}[a-zà-ÿ]\s([^\s:]{3,14}):\s+(\S.*)$", fala)
    if m and any(difflib.SequenceMatcher(None, normalizar(m.group(1)), normalizar(w)).ratio() >= 0.6 for w in ws):
        return m.group(2)
    # so o fim do nome, em minuscula: "tia Eu falei", "focal Quer", ")cal. Eu vou", "dor Por"
    m = re.match(r"^\W{0,3}([a-zà-ÿ]{1,6})[-.,:']{0,2}\s+([A-ZÀ-Ý]\S*)(.*)$", fala)
    if m and m.group(2).rstrip("!?.,…").lower() != "i":
        p = normalizar(m.group(1))
        fim = any(len(p) >= 2 and (normalizar(w).endswith(p) or (len(p) >= 4 and difflib.SequenceMatcher(
            None, p, normalizar(w)).ratio() >= 0.75)) for w in ws)
        lixo = len(p) <= 3 and m.group(1) not in _CURTAS_ANTES and comum(m.group(2))
        if (fim and (m.group(1) not in _CURTAS_ANTES or comum(m.group(2)))) or lixo:
            return m.group(2) + m.group(3)
    return fala


_UMA_PALAVRA = set("""claro valeu obrigado obrigada merda porra caralho tchau beleza desculpa exato verdade pronto
 isso nada nunca talvez certo calma tranquilo entendi cuzão cuzões babaca idiota""".split())


def pedaco_de_objetivo(fala):
    """So o fim do objetivo da missao, sem nome e em minuscula ("até a pista de pouso.", "taco de sinuca.",
    "para a Shirley.", "prisão."): a 1a linha dele foi descartada ou lida em outra hora."""
    from .tela import palavra_que_e_fala
    f = (fala or "").strip()
    if not f[:1].islower() or len(f.split()) > 6 or not f.endswith("."):
        return False
    return bool(re.match(r"^(?:até|para)\s+[ao]s?\s", f) or re.fullmatch(r"[a-zà-ÿ]{4,}\.", f) and f[:-1] not in _UMA_PALAVRA and not palavra_que_e_fala(f)
                or re.fullmatch(r"[a-zà-ÿ]{3,}\s(?:de|do|da|para)\s(?:[ao]\s)?[\wÀ-ÿ]+\.", f))


def objetivo_com_dono(fala):
    """"Siga o Donnie." "Entre no seu carro.": imperativo FORMAL com ponto, dentro da fala de alguem. Os
    personagens falam "segue/entra/espera"; nas 21 sessoes guardadas (20 a 24/11), 34 de 34 eram objetivo."""
    from .tela import _VERBOS_OBJETIVO
    ws = (fala or "").split()
    p = re.sub(r"[^\wÀ-ÿ-]", "", ws[0]).lower() if ws else ""
    return (3 <= len(ws) <= 9 and ws[0][:1].isupper() and p in _VERBOS_OBJETIVO and fala.rstrip().endswith(".")
            and not fala.rstrip().endswith("..") and "," not in fala
            and not re.search(r"\b(?:você|cê|eu|mim|porra|caralho|merda)\b", fala, re.I))


def sujeira_no_comeco(fala, nome):
    """Resto do nome de quem fala (ou texto do cenario) colado no comeco da fala. "" = a fala era so o nome.
    "Pessoas locais.sai da frente!", "loca;: Ei! Segura o volante!", 't": Ei cara', "A COUNTY Mas porra"."""
    original = fala
    if nome:
        fala = resto_do_nome(fala, nome)
        if not fala:
            return ""
        palavras = len(nome.split())
        # o nome inteiro repetido + "." ":" ";" ("Pessoas locais.sai da frente!", "Pessoa local.")
        m = re.match(r"^\W{0,3}((?:[^\s.:;,!?]+\s?){1,%d})[.:;]+['\"]?\s*(.*)$" % min(3, palavras), fala)
        if m and _parece_nome(m.group(1), nome):
            resto = m.group(2).strip()
            if resto:
                fala = resto[:1].upper() + resto[1:]
            elif palavras >= 2:
                return ""
        # so o fim do nome, com ":" ("loca;: Ei!")
        m = re.match(r"^\W{0,3}([^\s:]{3,14}?)[;.,'\"]?:['\"]?\s+(\S.*)$", fala)
        if m and any(difflib.SequenceMatcher(None, normalizar(m.group(1)), normalizar(w)).ratio() >= 0.75
                     for w in nome.split() if len(w) >= 4):
            fala = m.group(2)
        # so a 1a palavra do nome, antes de uma frase ("Pessoas Da próxima vez, ...", 23/11 18:42)
        m = re.match(r"^\W{0,2}([^\W\d_]{4,})\s+([A-ZÀ-Ý][a-zà-ÿ]*\s+\S+\s+\S.*)$", fala)
        p0 = nome.split()[0]
        if palavras >= 2 and len(p0) >= 4 and m and _parece_nome(m.group(1), p0):
            fala = m.group(2)
    # letra(s) minuscula(s) com ponto antes de uma frase: fim de palavra cortada ("ã! Não!")
    fala = re.sub(r"^[a-zà-ÿ]{1,2}[!.,?]\s+(?=[A-ZÀ-Ý])", "", fala)
    fala = re.sub(r"^0[-\s](?=[a-zà-ÿ])", "O ", fala)                 # "0-agente da condicional"
    fala = re.sub(r"(^|[.!?…]\s+)E,(?=\s)", r"\1É,", fala)             # "E, preciso disso" = "É, preciso"
    fala = re.sub(r"(?<=\s)['`´](?=[A-ZÀ-Ý])|(?<=[a-zà-ÿ])['`´](?=\s)", "", fala)   # "conta, 'Você" "Vai' tomar"
    fala = re.sub(r"(?<=[.!?…]\s)-(?=[A-ZÀ-Ý])", "", fala)            # "Chegar. -Eu dou"
    fala = re.sub(r"([!?])[.'\-]+$", r"\1", fala)                     # "não me toca!."
    fala = re.sub(r"\bno cul\b", "no cu", fala)
    fala = re.sub(r"(^|[.!?…]\s+)E(?=\s+quase\b|\s+isso,)", r"\1É", fala)   # "E quase um garotinho." "E isso, tô indo"
    # "Ah, nossa." lido "Ali, nossa." (o "h" vira "li")
    fala = re.sub(r"(^|[.!?…]\s+)Ali,(?=\s+(?:nossa|meu Deus|Deus|merda|cara|qual|vai|não|para)\b)", r"\1Ah,", fala)
    fala = re.sub(r"\btrá (?=(?:me|te|se)\s\w+ndo\b)", "tá ", fala)       # "você trá me encarando"
    # nome de varias palavras repetido sem ":" ("Agente da condicional Nah, minha perna")
    if nome and " " in nome:
        fala = re.sub(r"^" + re.escape(nome) + r"\s+['\"]?(?=[A-ZÀ-Ý])", "", fala, flags=re.I)
    # a fala de OUTRA pessoa colada depois ("Temos que ir Espanhol Passa a grana!"): ela vem na legenda dela
    fala = re.sub(r"(?<=\w)[.!?]?\sEspanhol\s+(?=[A-ZÀ-Ý]).*$", ".", fala)
    # palavra partida pelo OCR
    fala = re.sub(r"\batravessa dor(es)?\b", r"atravessador\1", fala)
    fala = re.sub(r"\b([Pp])or ra\b", r"\1orra", fala)
    fala = re.sub(r"\b([TtDdVv]) ([áôó])\b", r"\1\2", fala)              # "T á, peraí"
    fala = re.sub(r"\bC m (?=[a-zà-ÿ])", "Com ", fala)
    fala = re.sub(r"\b([A-ZÀ-Ýa-zà-ÿ][a-zà-ÿ]{2,}[aeo]) s(?=\s[a-zà-ÿ])", r"\1s", fala)   # "Pouso s repetidos"
    fala = re.sub(r"(?<=[a-zà-ÿ]{4}[aeor]) s(?=[.!?,]|$)", "s", fala)        # "desses arrombado s."
    fala = re.sub(r"(?<=[a-zà-ÿ]{5}r) s(?=\s[a-zà-ÿ])", "s", fala)          # "Kingfisher s soltos"
    fala = re.sub(r"\bh (ora|oras|oje|omem)\b", r"h\1", fala)         # "é h ora de"
    fala = re.sub(r"\bA h(?=[,.!…])", "Ah", fala)
    fala = re.sub(r"\b(\w{3,}[aei]r) em (?=(?:a|as|o|os)\s)", r"\1em ", fala)   # "encurralar em a gente"
    fala = re.sub(r"\bc m (?=[a-zà-ÿ])", "com ", fala)
    fala = re.sub(r"\b([Nn])âo\b", r"\1ão", fala)
    fala = re.sub(r"\bborn (?=[a-zà-ÿ])", "bom ", fala)                   # "como deve ser born ser o chefão"
    fala = re.sub(r"\bAgente (?=(?:também|já|só|não|vai|tá|tem|precisa|pode|se)\b)", "A gente ", fala)
    fala = re.sub(r"(^|[.!?…]\s+)Pará(?=[!,])", r"\1Para", fala)
    fala = re.sub(r"(^|[.!?…]\s+)Ali,(?=\s+(?:vão|eu|é|você|sim|tá|porra|que)\b)", r"\1Ah,", fala)
    fala = re.sub(r"[;:],(?=\s|$)", ",", fala)                             # "devesse;,"
    fala = re.sub(r"(?<=[a-zà-ÿ])['](?=bovina\b|verdade\b)", " ", fala)
    fala = re.sub(r"^([A-ZÀ-Ý]\S*\s[^\s.,!?]+)\s\1\b", r"\1", fala)       # "Qual é Qual é o seu problema?"
    fala = re.sub(r"^\d\.\s+(?=[A-ZÀ-Ý])", "", fala)
    fala = re.sub(r"^Herda(?=[!.])", "Merda", fala)
    fala = re.sub(r"\bena (?=nada|tudo|mim|você|casa)", "em ", fala)      # "não pensa ena nada"
    fala = re.sub(r"(^|[.!?…]\s+)E(?=\s+só\s+\w+[aei]r\b)", r"\1É", fala)   # "E só sentar e respirar."
    # pontuacao
    fala = re.sub(r"(?<=[a-zà-ÿ][!?])(?=[A-ZÀ-Ý])", " ", fala)           # "Lu!Sua"
    fala = re.sub(r"(?<=[a-zà-ÿ])\.(?=\s[a-zà-ÿ])", "", fala)            # "chamadas. perdidas"
    fala = re.sub(r"(?<=[a-zà-ÿ])-(?=\s)", "", fala)                     # "vai- pra casa"
    fala = re.sub(r"(?<=\w)[:;]$", ".", fala)
    fala = re.sub(r"^['\"]+(?=\w)", "", fala)
    if fala.count('"') % 2 and not re.match(r'^\S{0,3}"\S{0,2}:', fala):
        fala = fala.replace('"', "")
    # o proprio nome como se fosse chamado: "Lucia, Oi? O que foi?" dito pela Lucia
    if nome and " " not in nome:
        fala = re.sub(r"^" + re.escape(nome) + r"[,.]\s+(?=[A-ZÀ-Ý])", "", fala)
    fala = re.sub(r"(?<=[a-zà-ÿ])(?:\s[a-zà-ÿ]+-[a-z])?\s+[A-ZÀ-Ý]{5,}$", ".", fala)   # botao no fim: "...cabelo assina-o SENTAR"
    fala = re.sub(r"(?<=[a-zà-ÿ]);\s?-?(?=[a-zà-ÿ])", ", ", fala)        # "Donnie;-tô"
    fala = re.sub(r"^[a-zà-ÿ]{1,4}(?=[.,;'\-]*[;'])[.,;'\-]{2,}\S*\s+(?=[A-ZÀ-Ý])", "", fala)   # "da.';t- Porque"
    fala = re.sub(r"\b([A-ZÀ-Ý]?[a-zà-ÿ]{3,})([A-ZÀ-Ý])(?=[!?.,…]|$)", lambda m: m.group(1) + m.group(2).lower(), fala)
    # hifen falso entre palavras ("sem-problema", "você-que"); o dos verbos ("leve-o", "aproxime-se") fica
    fala = re.sub(r"(?<=[a-zà-ÿ])-(?!(?:me|te|se|lo|la|los|las|lhe|lhes|nos|vos|o|a|os|as|no|na|mos)\b)(?=[a-zà-ÿ]{2,})", " ", fala)
    fala = re.sub(r"^[Il1|]:\s+(?=[A-ZÀ-Ý])", "", fala)                  # "I: Ah, merda"
    fala = re.sub(r"^pp(?=[a-zà-ÿ])", "p", fala)                         # "ppra usar"
    # pedacinho com aspas/barra + ":" ('t": Ei cara')
    fala = re.sub(r"^\S{0,3}[\"';|]\S{0,2}:\s+(?=[A-ZÀ-Ý])", "", fala)
    # nome muito estragado: sinais no MEIO da palavra antes do ":" ("Potici?iis:' Não")
    fala = re.sub(r"^(?=[^\s:]{4,14}:)[^\W\d_]+[?!|;'\"/\\][^\s:]+:['\"]?\s+(?=[A-ZÀ-Ý])", "", fala)
    # placa do cenario (TUDO MAIUSCULO) antes de uma frase normal: "A COUNTY Mas porra, tô de condicional"
    m = re.match(r"^(?:[A-ZÀ-Ý]\s)?([A-ZÀ-Ý]{4,})\s+([A-ZÀ-Ý][a-zà-ÿ]*\b.*)$", fala)
    if m and re.search(r"[a-zà-ÿ]{2}", m.group(2)) and not re.search(r"\b[A-ZÀ-Ý]{4,}\b", m.group(2)):
        fala = m.group(2)
    return fala.strip() or original


def consertar_palavras_do_nome(nome, freq):
    """"TrabalhadoreS" -> "Trabalhadores"; "Desçonhecidos" -> "Desconhecidos" (ganhavam ficha e voz novas)."""
    out = []
    for w in nome.split(" "):
        if re.fullmatch(r"[A-ZÀ-Ý]?[a-zà-ÿ]{3,}[A-ZÀ-Ý]", w):
            w = w[:-1] + w[-1].lower()
        if "ç" in w and freq and not freq.get(w.lower()) and freq.get(w.lower().replace("ç", "c"), 0) >= 100:
            w = w.replace("ç", "c")
        out.append(w)
    return " ".join(out)


# avisos do proprio PS5 por cima do jogo (nunca sao legenda)
_PS5 = re.compile(r"ver tela de|amigos que jogam|nenhum amigo|instalando o arquivo|arquivo de atualiza|"
                  r"captura de tela salva|^digitando\W*$|trof[ée]u conquistado|controle sem fio conectado", re.I)


def nome_sem_fala(nome, texto):
    """"VJasorli Manda um" + "Manda um Buckme.": o comeco da fala entrou no nome."""
    ws = (nome or "").split()
    t = normalizar(texto)
    for k in range(1, len(ws)):
        rabo = normalizar(" ".join(ws[k:]))
        if len(ws) - k >= 2 and len(rabo) >= 5 and t.startswith(rabo):      # ("Pessoa local: Local errado." fica)
            return " ".join(ws[:k])
    return nome


def placar(fala):
    """"100 4.900 para a próxima meta": numero do jogo na tela (dois numeros seguidos no comeco)."""
    return bool(re.match(r"^\W*\d[\d.,]*\s+\d[\d.,]*\s|^\W*\d[\d.,]*\s+para a próxima meta", fala or ""))


def aviso_do_ps5(fala):
    return bool(_PS5.search(fala or ""))


def botao_do_jogo(fala):
    """"O DEFINIR DESTINO,": dica de botao, tudo em maiuscula e sem nome de quem fala."""
    letras = re.findall(r"[^\W\d_]", fala or "")
    return len(letras) >= 6 and sum(c.isupper() for c in letras) >= 0.85 * len(letras)


# "Use e O para direcionar sua atenção." / "Solte e para expirar": dica de botao (o icone do botao vira 1-2
# letras ou some), colada no fim da fala de quem estava falando (meditacao do GTA 6, 23/11)
_DICA = re.compile(r"(?:^|(?<=[.!?…,])\s+|\s+(?=[A-Z]))(?:use|solte|aperte|pressione|segure|mova|toque|gire|mantenha|incline)"
                   r"\s+(?:[^\s.!?]{1,2}\s+){0,4}(?:para|pra)\s+[a-zà-ÿ][^.!?…]*[.,]?\s*$", re.I)


def tirar_dica(texto):
    """-> texto sem a dica de botao do fim. "" = era so a dica."""
    m = _DICA.search(texto or "")
    return texto[:m.start()].rstrip(" ,") if m else texto


class Objetivos:
    """Objetivo da missao ("Voe até um local de entrega.") lido SOZINHO e sem nome: quando ele aparece junto
    de uma legenda, o OCR cola ele no fim da fala e o personagem lia ("Você tá bem? Voe até um local de
    entrega."), somando 2-3 s na fila do dialogo (GTA 6, 23/11 19:20)."""

    def __init__(self, segundos=900.0):
        self.segundos = segundos
        self.vistos = {}                      # normalizado -> [vezes, instante]

    def viu(self, texto, agora):
        from .tela import _VERBOS_OBJETIVO
        p = re.sub(r"[^\wÀ-ÿ-]", "", (texto.split() or [""])[0]).lower()
        if (p not in _VERBOS_OBJETIVO and p.split("-")[0] not in _VERBOS_OBJETIVO) or len(texto.split()) < 2 \
                or not texto.rstrip().endswith("."):
            return
        n = normalizar(texto)
        if len(n) < 10:
            return
        v = self.vistos.setdefault(n, [0, agora])
        v[0] += 1
        v[1] = agora
        if len(self.vistos) > 60:
            self.vistos.pop(min(self.vistos, key=lambda k: self.vistos[k][1]))

    def eh(self, frase, agora):
        n = normalizar(frase)
        if len(n) < 8:
            return False
        for k, (vezes, t) in self.vistos.items():
            if vezes < 2 or agora - t > self.segundos:
                continue
            if (len(n) <= len(k) + 4 and difflib.SequenceMatcher(None, n, k, autojunk=False).ratio() >= 0.82) \
                    or (len(n) >= 0.5 * len(k) and (k.startswith(n) or (len(n) >= 12 and k.endswith(n)))):
                return True
        return False

    def tirar(self, texto, agora):
        """-> texto sem o objetivo do fim. "" = era so o objetivo."""
        from .leitor import dividir_frases
        if not self.vistos or not texto:
            return texto
        if self.eh(texto, agora):
            return ""
        fr = dividir_frases(texto)
        if len(fr) < 2 or not self.eh(fr[-1], agora):
            return texto
        i = texto.rfind(fr[-1])
        return texto[:i].rstrip() if i > 0 else texto


class DitaHaPouco:
    """A MESMA pessoa com a mesma frase lida de outro jeito poucos segundos depois (legendas empilhadas
    piscando): "não, não, não me toca!" 2x, "Meu Deus, para... Me encontra no banco, ok?" + "Ah, meu banco,
    ok?", "...depositar meus, sei lá," + "sei lá," (23/11)."""

    def __init__(self, segundos=4.0):
        self.segundos = segundos
        self.ditas = []                       # (nome, texto normalizado, instante)

    def repetida(self, nome, fala, agora):
        n = normalizar(fala)
        self.ditas = [d for d in self.ditas if agora - d[2] <= self.segundos][-12:]
        rep = False
        for quem, antes, _ in self.ditas:
            # ("" = o nome ainda nao tinha sido lido: "Cara, que porra foi aquela?..." 2x, 24/11)
            if quem != nome and not (quem == "" and len(n) >= 16):
                continue
            if len(n) >= 8 and len(n) <= len(antes) + 4:
                sm = difflib.SequenceMatcher(None, n, antes, autojunk=False)
                rep = sum(b.size for b in sm.get_matching_blocks() if b.size >= 3) >= 0.8 * len(n)
            elif len(n) >= 4:
                rep = len(n) < len(antes) and antes.endswith(n) and fala.rstrip()[-1:] not in ".!?…"
            if rep:
                break
        if not rep:
            self.ditas.append((nome, n, agora))
        return rep


class SemNomeRepetida:
    """Objetivo da missao e previa do celular ficam (ou voltam) na tela por minutos, sem nome de quem fala:
    "Fale com o policial penal." + "Não acredito que voc..." sairam 140 vezes em 30 min (23/11), cada hora
    com um pedaco faltando. Frase sem nome ja dita ha pouco nao e dita de novo; o prazo conta da ultima
    vez que ela apareceu."""

    def __init__(self, segundos=180.0):
        self.segundos = segundos
        self.ditas = []                       # [texto normalizado, instante]

    @staticmethod
    def _mesma(a, b):
        if len(a) < 5 or len(b) < 5:
            return a == b
        curto, longo = (a, b) if len(a) <= len(b) else (b, a)
        if len(curto) < 0.75 * len(longo):
            # um pedaco da outra ("policial penal." de "Fale com o policial penal.")
            if len(curto) < 8:
                return False
            if curto in longo:
                return True
            if len(curto) < 0.5 * len(longo):
                return False
            sm = difflib.SequenceMatcher(None, curto, longo, autojunk=False)
            return sum(x.size for x in sm.get_matching_blocks() if x.size >= 3) >= 0.7 * len(curto)
        return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.82

    def repetida(self, fala, agora):
        n = normalizar(fala)
        self.ditas = [d for d in self.ditas if agora - d[1] <= self.segundos][-40:]
        for d in self.ditas:
            if self._mesma(n, d[0]):
                d[1] = agora
                if len(n) > len(d[0]) and len(n) <= len(d[0]) + 14:
                    d[0] = n                  # fica a leitura mais completa
                return True
        self.ditas.append([n, agora])
        return False
