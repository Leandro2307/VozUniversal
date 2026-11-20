# -*- coding: utf-8 -*-
"""
Testes rapidos (sem placa de video, sem tela) com casos REAIS que apareceram nos testes ao vivo.
Rode depois de mexer no codigo:   python testes\\unitarios.py
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from nucleo.identidade import pistas_nome, pistas_texto           # noqa: E402
from nucleo.leitor import dividir_frases                          # noqa: E402
from nucleo.tela import (Dicionario, achar_legenda, caixa_alta, eh_interface, juntar_mesma_linha, limpar,  # noqa: E402
                         lixo_ocr, rotulos, separar_nome, DICA_CONTROLE)
from nucleo.vozes import MotorClonada, preparar_fala               # noqa: E402

dic = Dicionario()
falhas = []


def confere(nome, obtido, esperado):
    if obtido != esperado:
        falhas.append(f"{nome}: esperado {esperado!r}, veio {obtido!r}")


def L(t, x0=10, x1=None, y=0):
    return (t, x0, y, x1 or x0 + len(t) * 9, y + 20)


# ---- quem fala (nome no comeco da legenda)
casos_nome = [
    ("Swanson: A Abigail disse que ele tá morrendo, Dutch.", "Swanson"),
    ("l)utch: Deixem que eu cuido disso.", "Dutch"),
    ("I.enny: Tô escorregando!", "Lenny"),
    ("liosea: Tão inútil, não.", "Hosea"),
    ("I)utchí Isso, tire-o daqui.", "Dutch"),
    ("Pessoa desconhecida: Mas olha quem tá aqui...", "Pessoa desconhecida"),
    ("Guarda 2: Eu trabalho para o Cornwall.", "Guarda 2"),
    ("Ele disse: vamos embora.", ""),
    ("Atenção: o portão vai fechar.", ""),
    ("Capítulo 2: Mirante Horseshoe", ""),
    ("Sinopse: Depois que um assalto dá errado", ""),
    ("São 10:30 da manhã.", ""),
    ("Ninguém( sabe disso.", ""),
    ("- Ilosea: Ê melhor usar uma arma.", "Hosea"),
    ("Pessoa desconhecida 2: Saia daqui, amigo.", "Pessoa desconhecida 2"),
    ("- Não sei.", ""),
]

# nome muito estragado pelo OCR -> personagem ja conhecido
from nucleo.nucleo import Nucleo   # noqa: E402


class _Perfil:
    personagens = {"Arthur": {}, "Hosea": {}, "Dutch": {}, "Lenny": {}}


_n = Nucleo.__new__(Nucleo)
_n.perfil = _Perfil()
_n.dic = dic
for lido, certo in [("Aflhnr", "Arthur"), ("Allhur", "Arthur"), ("Imtny", "Lenny"), ("Dulch", "Dutch"),
                    ("Hosea", "Hosea"), ("Abigail", "Abigail"), ("Javier", "Javier")]:
    confere(f"nome conhecido {lido}", _n._nome_canonico(lido), certo)
for t, esperado in casos_nome:
    confere(f"nome em {t!r}", separar_nome([L(t)])[0], esperado)

# legendas empilhadas / nome no meio
confere("empilhadas", separar_nome([L("Dutch: Bom, antes de mais nada...", y=0), L("Dutch: e vai dar um jeito.", y=30)]),
        ("Dutch", [("e vai dar um jeito.",) + L("x", y=30)[1:]][0:0] or separar_nome([L("Dutch: e vai dar um jeito.", y=30)])[1]))
confere("nome no meio", separar_nome([L("...encontramos um deles. Arthur: Nesse frio?")])[0], "Arthur")
confere("nome conhecido sem pontuacao antes", separar_nome([L("Nossas necessidades, equipamentos Dutch: e uma saída.")])[0], "Dutch")

# nome em outra cor (pedacos separados na mesma linha)
for esq, dir_, esperado in [("Dutclv", "Vasculhem os corpos!", "Dutclv"), ("Hill(", "Reconhece algum deles?", "Hill"),
                            ("Eu", "não sei o que fazer.", ""), ("Derrote", "O atacante.", "")]:
    j = juntar_mesma_linha([L(esq, 10, 80, 100), L(dir_, 90, 400, 100)], 1300)
    confere(f"nome colorido {esq!r}", separar_nome(j)[0], esperado)

# ---- filtros do que NAO e legenda
for t, esperado in [("RICK STIRLING VALMOR GARCIA", True), ("JAMES WHITLHOFT", True), ("MICHAEL KANE", True),
                    ("NÃO ACREDITO NISSO", False), ("EU VOU TE MATAR", False)]:
    confere(f"creditos {t!r}", caixa_alta(t, dic), esperado)
for t, esperado in [("Q Pesquisar", True), ("Mostrar mais", True), ("651 mil visualizações há 3 anos", True),
                    ("Press Esc to exit full screen", True), ("Vamos pesquisar a cabana.", False),
                    ("A tela cheia de gente.", False), ("Dutch: Olá?", False)]:
    confere(f"interface {t!r}", eh_interface(t), esperado)
for t, esperado in [("Cadência Recarga Precisão Condição REGULAR", True), ("Bom te ver, Brian.", False)]:
    confere(f"rotulos {t!r}", rotulos(t), esperado)
for t, esperado in [("Aperte O para acalmar o cavalo.", True), ("Segure para mirar.", True),
                    ("Use isso com cuidado.", False), ("Investigue o celeiro.", False)]:
    confere(f"dica {t!r}", bool(DICA_CONTROLE.match(t)), esperado)
for t, lixo in [("Pez, c uisar", True), ("Arthur? Hosea?", False), ("Oi, Lori!", False)]:
    r, tot = lixo_ocr(t, dic)
    confere(f"lixo {t!r}", bool(tot and r / tot > 0.6 and tot <= 6), lixo)

# ---- conserto do texto lido
for t, esperado in [("Vocétá bem?", "Você tá bem?"), ("Comovocé tá aí?", "Como você tá aí?"),
                    ("É oque a gente tá fazendo.", "É o que a gente tá fazendo."),
                    ("Arthur, Lenny e Javier.", "Arthur, Lenny e Javier."),
                    ("Eles são títulos ao portador.", "Eles são títulos ao portador."),
                    ("Tào inútil, nào.", "Tão inútil, não."), ("Ele está à esquerda.", "Ele está à esquerda."),
                    ("Born, vamos ver.", "Bom, vamos ver."), ("Bern, conectei o fio.", "Bem, conectei o fio."),
                    ("Não me afundar ei mais, senhor.", "Não me afundarei mais, senhor."), ("1--losea, Bill, juntem-se à festa.", "Hosea, Bill, juntem-se à festa."),
                    ("aquele é 1--lobart Crawley...", "aquele é Hobart Crawley..."), ("Deportaçào?", "Deportação?"), ("Vou falar à ela.", "Vou falar à ela.")]:
    confere(f"conserto {t!r}", dic.consertar(t), esperado)

# frases da voz clonada que davam IndexError (sessao real das 03:31)
_m = MotorClonada.__new__(MotorClonada)
for t in ["Born. vamos ver se conseguimos rastreá lo. mas...", "Então, hã, quem vai dizer a ele que não conseguimos. nada pra comer?",
          "Mas se a gente fizer... a gente entra com tudo e resolve isso do jeito que a gente sabe. Certo?", "Sim."]:
    try:
        confere(f"frases da voz {t[:30]!r}", bool(_m._frases(preparar_fala(t))), True)
    except Exception as e:
        falhas.append(f"frases da voz {t[:30]!r}: {e!r}")
for t, esperado in [("0h... isso é...", "Oh... isso é..."), ("01?", "Oi?"), ("Faltam 01 dia", "Faltam 01 dia")]:
    confere(f"limpar {t!r}", limpar(t), esperado)

# ---- frases e voz
confere("abreviacao", dividir_frases("Srta. Jones, traga todos os cobertores."), ["Srta. Jones, traga todos os cobertores."])
confere("expande Sr.", preparar_fala("Sr. Pearson, veja."), "Senhor Pearson, veja.")
confere("sem ponto final", MotorClonada._sem_ponto("Cuidado com a ponte."), "Cuidado com a ponte")
confere("frase longa quebrada", all(len(p) <= 180 for p in MotorClonada._quebrar("palavra " * 60)), True)

# ---- sexo pelo nome e pelo texto
for n, s in [("Susan", "f"), ("Abigail", "f"), ("Hosea", "m"), ("Dutch", "m"), ("Srta. Jones", "f"), ("Vovó Apotecária", "f")]:
    confere(f"sexo {n}", next((v for d, v, *_ in pistas_nome(n) if d == "s"), None), s)
confere("obrigada", [v for d, v, *_ in pistas_texto("Muito obrigada!") if d == "s"], ["f"])

# ---- OCR piscando (sequencia real do RDR2): nao pode repetir a frase
from nucleo.leitor import Leitor   # noqa: E402
from nucleo.util import PADRAO     # noqa: E402
seq = [(0.0, "É assim que você se vê, não é? Como um maníaco?"), (0.1, "soo 62 CONO.k"),
       (0.2, "É assim que você se vê, não é? Como um maníaco?e"), (0.35, "t assim você se vê, é? como um maníaco?"),
       (0.6, "soo e."), (0.7, "É assim que você se vê, não é? Como um maníaco?"), (0.8, "'eq. CONO"),
       (0.95, "É assim que você se vê, não é? Como um maníaco?"), (1.3, ""),
       (1.5, "É assim que você se vê, não é? Como um maníaco?")]
lt, falado, i, t = Leitor(dict(PADRAO)), [], 0, 0.0
while t < 3.0:
    while i + 1 < len(seq) and seq[i + 1][0] <= t:
        i += 1
    falado += [f for f, _, _ in lt.tick(seq[i][1], t)]
    t += 0.11
confere("OCR piscando nao repete", " ".join(falado), "É assim que você se vê, não é? Como um maníaco?")


# "Pessoa desconhecida 1/2" sao pessoas diferentes, mesmo com sujeira do OCR antes
for t, esperado in [("V)essoa desconhecida 2: Felizmente pra vocês dois.", "Pessoa desconhecida 2"),
                    ("-4 Pessoa desconhecida 2: Consegue imaginar?", "Pessoa desconhecida 2"),
                    ("av-Pessoa desconhecida 1: E agora você é", "Pessoa desconhecida 1"),
                    ("Pessoa desconhecida: Sente-se.", "Pessoa desconhecida")]:
    confere(f"pessoa desconhecida {t[:25]!r}", separar_nome([(t, 0, 0, 300, 20)])[0], esperado)

# OCR piscando "fechava" a frase numa palavra que nunca termina frase ("...olhada nesse." + "monstro.")
lt = Leitor(dict(PADRAO))
seq = [(74.69, "Jogue-o aqui, no chão. Quero dar uma boa olfiada nesse"),
       (74.96, "Jogue-o aqui, no chão. Quero dar uma boa olhada nesse monstro."),
       (75.32, "Jogue-o aqui, no chão. Quero dar uma boa olhada nesse"),
       (75.42, "Jogue-o aqui, no chão. Quero dar uma boa olhada nesse. Po há o A chão.'"),
       (75.52, "Jogue-o aqui, no chão. Quero dar uma boa olhada nesse monstro."),
       (76.0, "Jogue-o aqui, no chão. Quero dar uma boa olhada nesseL monstro."),
       (76.34, "Jogue-o aqui, no chão. Quero dar uma boa olhada nesse monstro.")]
t, i, falado = 74.6, 0, []
while t < 78:
    while i + 1 < len(seq) and seq[i + 1][0] <= t:
        i += 1
    if seq[i][0] <= t:
        falado += [f for f, _, _ in lt.tick(seq[i][1], t)]
    t += 0.11
confere("frase nao termina em 'nesse'", "nesse." not in " ".join(falado) and "nesse monstro." in " ".join(falado), True)

# legenda seguinte que so muda o COMECO e outra fala (nao a mesma legenda continuando)
for seg, esperado in [("Tipo, eu nem quero ouvir essa merda.", 2), ("Wano... eu nem quero ouvir essa merda.", 1)]:
    lt, falado, t = Leitor(dict(PADRAO)), [], 0.0
    while t < 6:
        falado += [f for f, _, _ in lt.tick("Mano... eu nem quero ouvir essa merda." if t < 2.5 else seg, t)]
        t += 0.11
    confere(f"so o comeco muda: {seg[:12]!r}", len(falado), esperado)

# OCR piscando no fundo claro (cada leitura perde um pedaco diferente): junta as leituras e fala a
# frase COMPLETA, uma vez so (15/11, GTA 6: saia "Sabe, o você tá ... já tá bem vamos saindo?")
from nucleo.tela import Dicionario as _D   # noqa: E402
_D()
seq = [(69.546, "Sabe, o que você ta vestindo agora já tá bem vamos saindo?"),
       (69.686, "Sabe, oq e você tá vestindo agora lá tá bem bo ito, então... vamos saindo?"),
       (69.764, "Sabe, oq e você tá vestindo agora já tá bem bo ito, então... vamos saindo?"),
       (69.88, "Sabe, oq e você tá vestindo agora já tá bem bo vamos saindo?"),
       (70.025, "Sabe, o que você tá vestindo agora já tá bemb vamos saindo?"),
       (70.107, "Sabe, o você tá vestindo agora já tá bem vamos saindo?"),
       (70.2, "Sabe, o você tá vestindo agora já tá bem vamos saindo?"),
       (72.0, "")]
lt, falado, i, t = Leitor(dict(PADRAO)), [], -1, 69.5
while t < 73:
    while i + 1 < len(seq) and seq[i + 1][0] <= t:
        i += 1
    if i >= 0:
        falado += [f for f, _, _ in lt.tick(seq[i][1], t)]
    t += 0.11
confere("junta leituras do OCR piscando", falado,
        ["Sabe, o que você tá vestindo agora já tá bem bonito, então... vamos saindo?"])

# nome escrito no meio de uma fala ("O Signor Bronte vai ficar...") nao vira outro da lista ("Bonnie")
_n.palavras_vistas = {"bronte": 0.0, "luca": 0.0}
confere("Bronte visto no texto", _n._nome_canonico("Bronte"), "Bronte")
confere("Luca visto no texto", _n._nome_canonico("Luca"), "Luca")
_n.palavras_vistas = {}

# painel do jogo em maiusculas (Cyberpunk) nao e fala; grito em maiusculas e
from nucleo.tela import caixa_alta   # noqa: E402
for t, esperado in [("SISTEMA OE APOIO VITAL:", True), ("ENVIANDO SINAL OE EMERGÊNCIA ERRO ERRO 23a", True),
                    ("E TIMATIVA DÊ CHEGADA: 191 s", True), ("NÃO ACREDITO NISSO!", False),
                    ("SOCORRO! ME AJUDA!", False), ("JACKIE: Pega a seringa!", False)]:
    confere(f"maiusculas {t!r}", caixa_alta(t, dic), esperado)

# barra de comandos do jogo (Zelda) nao e fala
from nucleo.tela import eh_barra_de_comandos   # noqa: E402
for t, esperado in [("Mover para frente trás Co Girar (segurar) n Cancelar", True), ("Girar na vertical", True),
                    ("Mover para frente trás 020", True), ("Girar (Segurar): Separar Q Cancelar O", True),
                    ("Eu estava esperando por você.", False), ("Agora, é um lugar solitário", False),
                    ("Use o laço", False), ("Tilly e diga pro resto dos primos dele", False),
                    ("Acoplar um material 020", True), ("Vamos!", False)]:
    confere(f"barra de comandos {t!r}", eh_barra_de_comandos(t), esperado)
confere("item em maiusculas", caixa_alta("OFERENDA LUMINOSA", dic), True)

# palavra sozinha: fala de verdade ("Vamos.") x botao/rotulo ("Use") x pedaco do OCR ("ão.")
from nucleo.tela import palavra_que_e_fala   # noqa: E402
for t, esperado in [("Vamos.", True), ("Vamos!", True), ("Não.", True), ("Valeu.", True), ("Oi.", True),
                    ("Amigão...", True), ("Ei…", True), ("Sério?", True), ("Uhum.", True), ("\"Certo.\"", True),
                    ("Use", False), ("Vamos", False), ("CÂMERA", False), ("ão.", False), ("erda.", False),
                    ("bar.", False), ("Susan:", False), ("000.", False), ("HRP.", False), ("A.", False)]:
    confere(f"palavra solta {t!r}", palavra_que_e_fala(t), esperado)
confere("grito sem ponto", caixa_alta("NÃO ACREDITO NISSO", dic), False)

# ---- escuta da voz original: legenda SEM nome vira "Homem/Mulher (pela voz)"; incerta fica no narrador
from nucleo.identidade import Elenco, pistas_voz   # noqa: E402


class _PerfilE:
    def __init__(self):
        self.personagens = {}

    def salvar(self):
        pass


class _Banco:
    vozes = {v: {"nome": v} for v in ("homem_1", "homem_2", "mulher_1", "mulher_2", "menino_1", "menina_1",
                                      "idoso_1", "idosa_1", "narrador")}

    def da_categoria(self, c):
        return [v for v in self.vozes if v.startswith(c + "_")]


class _Escuta:
    ativa = True

    def __init__(self, res):
        self.res = res

    def ouvir(self, visto, ate=None, esperar=0.0, minimo=0.45):
        return dict(self.res)


def _res(sexo, certeza, crianca=0.0, voz_s=1.0):
    return {"sexo": sexo, "p_m": 0.9 if sexo == "m" else 0.1, "crianca": crianca, "idade": 35.0,
            "voz_s": voz_s, "certeza": certeza, "rms": 0.01}


_e = Nucleo.__new__(Nucleo)
_e.cfg = {"escuta": True, "escuta_espera_s": 0.0}
_e.perfil = _PerfilE()
_e.elenco = Elenco(_e.perfil, _Banco())
_e._voz_pag = {}
_e.emitir = lambda *a, **k: None
_e.personagens = lambda: []
import time as _t   # noqa: E402
_e.escuta = _Escuta(_res("m", 0.9))
r = _e._resolver_voz(1, "Ei, que porra é essa?", "narrador", 1.0, 1.0, "", "narrador", _t.time())
confere("legenda sem nome, voz de homem", (r[3], r[4], r[0].startswith("homem")) if r else None,
        ("Homem (pela voz)", "homem", True))
_e.escuta = _Escuta(_res("f", 0.8))
confere("pedaco da MESMA legenda nao troca de voz", _e._resolver_voz(1, "...continua", "narrador", 1, 1, "", "narrador",
                                                                     _t.time())[3], "Homem (pela voz)")
r = _e._resolver_voz(2, "Mais três champanhes, obrigada.", "narrador", 1.0, 1.0, "", "narrador", _t.time())
confere("legenda sem nome, voz de mulher", (r[3], r[0].startswith("mulher")) if r else None, ("Mulher (pela voz)", True))
# legenda com duas pessoas ("- Isso. - E você é o motorista?"): a 2a parte sai com a voz oposta
_e._oposto = {21: 20}
_e._voz_pag[20] = ("homem_1", 1.0, 1.0, "Homem (pela voz)", "homem")
r = _e._resolver_voz(21, "E você é o motorista?", "narrador", 1, 1, "", "narrador", _t.time())
confere("2a pessoa do dialogo com voz oposta", r[4] if r else None, "mulher")
confere("separa '- A. - B.'", Nucleo._RE_DIALOGO.match("- Isso. - E você é o motorista?").groups(),
        ("Isso.", "E você é o motorista?"))
confere("nao separa fala com um travessao", Nucleo._RE_DIALOGO.match("- Tipo, eu nem quero ouvir essa merda."), None)
_e.escuta = _Escuta(_res("m", 0.1))
confere("voz incerta fica no narrador", _e._resolver_voz(3, "Hã.", "narrador", 1, 1, "", "narrador", _t.time()), None)
# sem certeza, mas pendendo CLARAMENTE para um lado (certeza 0,2+): usa o palpite (15/11: "Lantejoulas?"
# pendia para mulher e saia na voz de homem)
_e.escuta = _Escuta(_res("f", 0.25))
r = _e._resolver_voz(5, "Lantejoulas?", "narrador", 1, 1, "", "narrador", _t.time())
confere("palpite da voz (mulher, pouca certeza)", r[3] if r else None, "Mulher (pela voz)")
_e.escuta = _Escuta(_res(None, 0.0, voz_s=0.0))
confere("sem fala (so musica) fica no narrador", _e._resolver_voz(4, "Polícia!", "narrador", 1, 1, "", "narrador",
                                                                  _t.time()), None)
# personagem com nome novo: a voz original decide a 1a voz dele
_e.escuta = _Escuta(_res("f", 0.9))
_e.elenco.observar("Lu")                                # nome sem pista nenhuma -> homem (padrao)
r = _e._resolver_voz(5, "Cobertura!", "homem_1", 1, 1, "Lu", "homem", _t.time())
confere("nome novo + voz de mulher -> mulher", r[4] if r else None, "mulher")
confere("pista de voz fraca nao vale", pistas_voz(_res("m", 0.1)), [])

# OCR comeu a 1a letra (legenda branca sobre fundo colorido, GTA V 14/11)
for t, esperado in [("o roblema de lidar", "o problema de lidar"), ("com as essoas", "com as pessoas"),
                    ("uando todo mundo", "quando todo mundo"), ("o osso quebrou", "o osso quebrou"),
                    ("Arthur e Dutch", "Arthur e Dutch")]:
    confere(f"letra perdida {t!r}", dic.consertar(t), esperado)
confere("outra leitura viu a 1a letra", dic.fundir("osso disfarce vai ser", "Nosso disfarce vai ser"),
        "Nosso disfarce vai ser")

# ---- sessao de 18/11 (GTA 6 no PS5, 2h16): casos reais
# marcacao de idioma com o "[" lido como letra/numero, e no meio da legenda
for t, esperado in [("I Espanhol Um café, por favor.", "Um café, por favor."),
                    ("3 Espanhol Esses filhotes são maiores que um pitbull.", "Esses filhotes são maiores que um pitbull."),
                    ("Espanhol Bom dia.4 Espanhol Bom did.", "Bom dia. Bom did."),
                    ("Ele fala espanhol. Eu não.", "Ele fala espanhol. Eu não."), ("Inglês? Não falo.", "Inglês? Não falo.")]:
    confere(f"idioma {t[:25]!r}", limpar(t), esperado)
# virgula grudada no meio da palavra
for t, esperado in [("Agora não, t,á?", "Agora não, tá?"), ("Droga, eu devia ter pedido r,nais.", "Droga, eu devia ter pedido mais."),
                    ("A gen,te vai só o quê?", "A gente vai só o quê?"), ("Custa 1,5 mil.", "Custa 1,5 mil."),
                    ("Suba em todas elas evocê,revelará o mapa.", "Suba em todas elas e você, revelará o mapa.")]:
    confere(f"virgula {t[:25]!r}", dic.consertar(t), esperado)
# cargo com 2a palavra minuscula no meio da legenda (era falado na voz de quem falou antes)
for t, esperado in [("Foi pega por multa de trânsito com dois baseados. Policial penal: Melendez, não me vem com essa cara.",
                     ("Policial penal", "Melendez, não me vem com essa cara.")),
                    ("Dinheiro no balcão, agora, porra!' Pessoa local: Que bairro de merda.", ("Pessoa local", "Que bairro de merda.")),
                    ('adam smith a san hages "benta gatei Pessoa desconhecida: Ok, acho que tá tudo bem.',
                     ("Pessoa desconhecida", "Ok, acho que tá tudo bem.")),
                    ("Presta atenção: isso é sério.", ("", "Presta atenção: isso é sério.")),
                    ("Eu falei com o Gerente: ele disse que não.", ("", "Eu falei com o Gerente: ele disse que não.")),
                    ("Lembra disso: nada de gracinha.", ("", "Lembra disso: nada de gracinha."))]:
    nm, corpo = separar_nome([L(t)])
    confere(f"cargo no meio {t[:30]!r}", (nm, " ".join(c[0] for c in corpo)), esperado)
confere("linha de baixo comeca com cargo", separar_nome([L("Foi pega por multa.", y=0), L("Policial penal: Mãos na parede.", y=30)])[0],
        "Policial penal")

# aviso/texto legal comprido e creditos em ingles nao sao legenda; legenda comprida de verdade e
from PIL import Image as _Img   # noqa: E402
_img = _Img.new("RGB", (1300, 300))


def _bloco(t, y=200):
    return [(t, 650 - len(t) * 3, y, 650 + len(t) * 3, y + 24)]


_legal = ("1997 - 2026. Rockstar Games, Grand Theft Auto, Grand Theft Auto VI, VI, Vice City e logotipos relacionados são "
          "marcas comerciais e direitos autorais da Take-Two Interactive Software, Inc. Todas as outras marcas comerciais "
          "pertencem aos seus respectivos donos. Todos os direitos reservados.")
confere("texto legal comprido", achar_legenda(_img, linhas=_bloco(_legal), dic=dic), None)
confere("creditos em ingles", achar_legenda(_img, linhas=_bloco("den11ifen xalbe manx adam san dan dan chew Rowan haa,i nab spampmara"),
                                            dic=dic), None)
_farcry = ("Vá, use as torres para mapear a ilha e localizar seus homens. Os encriptadores bloqueiam o acesso. "
           "Nosso povo não está interessado nesses dispositivos.")
confere("legenda comprida de verdade", bool(achar_legenda(_img, linhas=_bloco(_farcry), dic=dic)), True)

# nome lido CERTO nao vira outro personagem parecido; erro de OCR continua sendo consertado
class _PerfilGTA:
    personagens = {k: {} for k in ["Jason", "Lucia", "Clarice", "Bonnie", "Kevin", "Antonio", "Stefanie Gates",
                                   "Policial Daniels", "Cal", "Donnie", "Brian", "Pessoa local", "Isabella"]}


_g = Nucleo.__new__(Nucleo)
_g.perfil, _g.dic, _g.palavras_vistas = _PerfilGTA(), dic, {}
for lido, certo in [("Cliente", "Cliente"), ("Connie", "Connie"), ("Antonia", "Antonia"), ("Isabelle", "Isabelle"),
                    ("Stefanie", "Stefanie Gates"), ("Policial penal", "Policial penal"), ("Policial", "Policial"),
                    ("Pessoas locais", "Pessoa local"), ("Cak", "Cal"), ("Lucjp", "Lucia"), ("Donoje", "Donnie"),
                    ("Brjpn", "Brian"), ("D6nnie", "Donnie")]:
    confere(f"nome GTA {lido}", _g._nome_canonico(lido), certo)
# resto de nome cortado e letra solta no comeco da fala
for f, esperado in [("ia: Só água.", "Só água."), ("lá: Vai na minha, ok?", "Vai na minha, ok?"),
                    ("s Não, não, tá de boa.", "Não, não, tá de boa."), ("a gente vai", "a gente vai"),
                    ("e Você?", "e Você?")]:
    confere(f"comeco sujo {f!r}", _g._nome_na_fala(f)[1], esperado)

# cargo "-ista" nao e masculino nem feminino
confere("motorista sem sexo", [v for d, v, *_ in pistas_nome("Motorista") if d == "s"], [])

# 2a frase da MESMA legenda nao acelera so por ter esperado a 1a tocar (18/11: 204 de 311 aceleradas)
from nucleo.vozes import Narrador   # noqa: E402
_nv = Narrador.__new__(Narrador)
_nv.cfg = {"acelerar_ate": 1.35}
_nv.pag, _nv.chegada, _nv.ideal_fim = 1, {}, {}
_agora = _t.time()
_nv.ideal_fim[1] = (_agora - 6.0) + 5.4          # legenda vista ha 6 s; a 1a frase tem 5,4 s de fala
_info = {"visto": _agora - 6.0}
_info["ideal"] = _nv._inicio_ideal(1, _info["visto"])
confere("2a frase em dia nao acelera", _nv._ritmo_inicial(1, _info, 2.5), 1.0)
_nv.ideal_fim[1] = (_agora - 6.0) + 2.0           # a legenda inteira esta 4 s atrasada: acelera
_info["ideal"] = _nv._inicio_ideal(1, _info["visto"])
confere("legenda atrasada acelera", _nv._ritmo_inicial(1, _info, 2.5) > 1.3, True)
confere("fala nova: inicio ideal e quando apareceu", _nv._inicio_ideal(2, _agora - 1.0), _agora - 1.0)
# a mesma fala lida de novo com 1-2 letras diferentes nao repete
import collections as _col   # noqa: E402
_nv.recentes = _col.deque(maxlen=12)
confere("1a vez fala", _nv._repetido("Ei, essa... essa parece que tá certa."), False)
confere("lida diferente nao repete", _nv._repetido("Ei, essas essa parece que tá certa."), True)
confere("fala parecida mas outra frase", _nv._repetido("Eu não sei o que fazer agora."), False)
confere("outra frase parecida", _nv._repetido("Ninguém sabe o que fazer agora."), False)

# legendas encavaladas: a do MEIO so e pulada se ja estiver atrasada demais (pular sempre perdia 1 de
# cada 7 falas numa conversa rapida, bateria do GTA 6 19/11)
def _narrador_tocando(chegou_9_ha):
    nv = Narrador.__new__(Narrador)
    nv.cfg = {"pular_atrasada_s": 3.0, "pular_atual_apos": 3}
    nv.tocando, nv.acabou_atual, nv.no_toque, nv.ult_fim_toque = 8, False, True, _t.time()
    nv.pular_ate, nv.puladas, nv.ideal_fim = 0, set(), {}
    agora = _t.time()
    nv.chegada = {8: agora - 5.0, 9: agora - chegou_9_ha, 10: agora}
    nv.pag = 10
    nv._pular_meio()
    return nv


confere("do meio pouco atrasada fica", 9 in _narrador_tocando(0.6).puladas, False)
confere("do meio atrasada demais e pulada", 9 in _narrador_tocando(3.4).puladas, True)
_nv2 = _narrador_tocando(0.6)
confere("a que toca nao e cortada com 2 atras", _nv2.pular_ate, 0)
confere("na hora de tocar: atrasada com legenda nova esperando pula",
        _nv2._atrasada_demais(9, {"visto": _t.time() - 3.5}), True)
confere("na hora de tocar: pouco atrasada toca", _nv2._atrasada_demais(9, {"visto": _t.time() - 1.0}), False)
confere("na hora de tocar: a mais nova nunca pula", _nv2._atrasada_demais(10, {"visto": _t.time() - 9.0}), False)

# figurante (multidao) esperando na fila e pulado bem antes de quem importa
_nv3 = _narrador_tocando(1.4)
confere("pessoa importante 1,4 s atrasada fica", 9 in _nv3.puladas, False)
_nv4 = Narrador.__new__(Narrador)
_nv4.cfg = {"pular_atrasada_s": 3.0, "pular_atual_apos": 3, "pular_figurante_s": 1.0}
_nv4.tocando, _nv4.acabou_atual, _nv4.no_toque, _nv4.ult_fim_toque = 8, False, True, _t.time()
_nv4.pular_ate, _nv4.puladas, _nv4.ideal_fim, _nv4.pags_figurante = 0, set(), {}, {9}
_nv4.chegada = {8: _t.time() - 5.0, 9: _t.time() - 1.4, 10: _t.time()}
_nv4.pag = 10
_nv4._pular_meio()
confere("figurante 1,4 s atrasado e pulado", 9 in _nv4.puladas, True)

# camada de fundo: soma por baixo da voz principal, sem passar de 1
import threading as _th   # noqa: E402
import numpy as _np       # noqa: E402
_nv5 = Narrador.__new__(Narrador)
_nv5.trava_fundo = _th.Lock()
_nv5.fundo = _np.full(30, 0.5, _np.float32)
_saiu = []
_nv5._escrever = lambda b, sr: _saiu.append(_np.array(b))
_nv5._escrever_mix(_np.full(20, 0.8, _np.float32), 24000)
confere("fundo somado e limitado a 1", (_saiu[0].max() <= 1.0, len(_nv5.fundo)), (True, 10))
_nv5._escrever_mix(_np.zeros(20, _np.float32), 24000)
confere("fundo toca no silencio e acaba", (round(float(_saiu[1][:10].mean()), 2), float(_saiu[1][10:].max()), len(_nv5.fundo)),
        (0.5, 0.0, 0))

# legendas empilhadas de pessoas diferentes (assalto do GTA 6, 18/11 00:15): uma fala por pessoa
from nucleo.tela import eh_figurante, separar_falantes, tirar_idioma   # noqa: E402
for t, esperado in [
        ("Lucia: Você! Dinheiro no balcão, agora, porra! Pessoa local: Que bairro de merda, cara. Não dá pra fazer nada aqui.",
         [("Lucia", "Você! Dinheiro no balcão, agora, porra!"),
          ("Pessoa local", "Que bairro de merda, cara. Não dá pra fazer nada aqui.")]),
        ("Você! Dinheiro no balcão, agora, porra! Pessoa loeal;. Que bairro de merda, cara.",
         [("", "Você! Dinheiro no balcão, agora, porra!"), ("Pessoa local", "Que bairro de merda, cara.")]),
        ("Pessoa local: Que bairro de merda. Atendente: Ah! Tá, tá, só fica calma.",
         [("Pessoa local", "Que bairro de merda."), ("Atendente", "Ah! Tá, tá, só fica calma.")]),
        ("Só quero a grana. Pessoa local: Espanhol ' Ai, Santa Bárbara, me protege.",
         [("", "Só quero a grana."), ("Pessoa local", "Ai, Santa Bárbara, me protege.")]),
        ("Eu falei com o Gerente: ele disse que não.", [("", "Eu falei com o Gerente: ele disse que não.")]),
        ("Presta atenção: isso é sério.", [("", "Presta atenção: isso é sério.")]),
        ("Swanson: A Abigail disse que ele tá morrendo, Dutch.",
         [("Swanson", "A Abigail disse que ele tá morrendo, Dutch.")])]:
    confere(f"empilhadas {t[:35]!r}", separar_falantes([L(t)]), esperado)
confere("empilhadas em linhas", separar_falantes([L("Lucia: Rápido, porra!", y=0), L("Atendente: Pronto!", y=30)]),
        [("Lucia", "Rápido, porra!"), ("Atendente", "Pronto!")])
for n, esperado in [("Pessoa local", True), ("Pessoas locais", True), ("Turista 2", True), ("Lucia", False),
                    ("Policial", False), ("Atendente", False)]:
    confere(f"figurante {n!r}", eh_figurante(n), esperado)
# nivelador: o volume nao da degrau na emenda de dois pedacos ("A culpa é sua" em pedacos, 20/11)
from nucleo.vozes import Nivelador   # noqa: E402
_niv = Nivelador()
_p1 = _niv(_np.sin(_np.linspace(0, 600, 12000)).astype(_np.float32) * 0.05, 24000)
_p2 = _niv(_np.sin(_np.linspace(0, 600, 12000)).astype(_np.float32) * 0.2, 24000)
_ganho_fim1 = float(_np.abs(_p1[-600:]).max() / 0.05)
_ganho_ini2 = float(_np.abs(_p2[:600]).max() / 0.2)
confere("nivelador sem degrau na emenda", abs(_ganho_fim1 - _ganho_ini2) < 0.05 * _ganho_fim1, True)

# duas "Pessoa local" empilhadas com o nome em cores diferentes = duas pessoas (print do GTA 6, 20/11)
from PIL import ImageDraw as _Draw, ImageFont as _Fonte   # noqa: E402
from nucleo.tela import cor_do_nome, nome_da_cor   # noqa: E402
_im = _Img.new("RGB", (900, 70), (90, 90, 90))
_d = _Draw.Draw(_im)
try:
    _f = _Fonte.truetype("arial.ttf", 30)
except Exception:
    _f = None
_d.text((20, 10), "Pessoa local:", fill=(240, 150, 160), font=_f)
_d.text((215, 10), "Ah, olha só você!", fill=(255, 255, 255), font=_f)
_h_rosa = cor_do_nome(_im, ("Pessoa local: Ah, olha só você!", 20, 8, 500, 45))
confere("cor do nome rosa", _h_rosa is not None and nome_da_cor(_h_rosa), "rosa")
confere("fala branca nao tem cor de nome", cor_do_nome(_im, ("Ah, olha só você!", 215, 8, 500, 45)), None)
_n_cor = Nucleo.__new__(Nucleo)
_dup = [("Pessoa local", "Ah, olha só você!"), ("Pessoa local", "Belo trabalho, demente!")]
_im2 = _Img.new("RGB", (900, 120), (90, 90, 90))
_d2 = _Draw.Draw(_im2)
for _y, _cor, _fala in [(10, (240, 150, 160), "Ah, olha só você!"), (65, (150, 230, 230), "Belo trabalho, demente!")]:
    _d2.text((20, _y), "Pessoa local:", fill=_cor, font=_f)
    _d2.text((215, _y), _fala, fill=(255, 255, 255), font=_f)
_bl = [("Pessoa local: Ah, olha só você!", 20, 8, 470, 45), ("Pessoa local: Belo trabalho, demente!", 20, 63, 560, 100)]
_sep = [n for n, _ in _n_cor._separar_por_cor(_dup, _bl, _im2, 1.0)]
confere("empilhadas mesmo nome, cores diferentes", len(set(_sep)) == 2 and all(s.startswith("Pessoa local (") for s in _sep), True)
_sem_cor = [n for n, _ in Nucleo.__new__(Nucleo)._separar_por_cor(_dup, _bl, _Img.new("RGB", (900, 120)), 1.0)]
confere("empilhadas sem cor lida: numeradas diferentes", len(set(_sem_cor)), 2)

# voz pelo TOM da voz original + "Pessoa desconhecida" homem e mulher separados (20/11)
from nucleo.identidade import Elenco as _Elenco   # noqa: E402


class _BancoTom:
    vozes = {"h_grave": {"categoria": "homem", "nome": "G"}, "h_media": {"categoria": "homem", "nome": "M"},
             "m_1": {"categoria": "mulher", "nome": "F"}}
    tons = {"h_grave": 100.0, "h_media": 135.0, "m_1": 230.0}

    def da_categoria(self, c):
        return [k for k, v in self.vozes.items() if v["categoria"] == c]

    def tom(self, v):
        return self.tons.get(v)


class _PerfilVazio:
    def __init__(self):
        self.personagens = {}

    def salvar(self):
        pass


_el = _Elenco(_PerfilVazio(), _BancoTom())
confere("tom grave -> voz grave", _el._voz_para("A", "homem", 98)[0], "h_grave")
confere("tom medio -> voz media", _el._voz_para("B", "homem", 140)[0], "h_media")
confere("ajuste fino no maximo 5%", _el._voz_para("C", "homem", 60)[1], 0.95)
_el.perfil.personagens["Pessoa desconhecida"] = {"categoria": "homem", "voz": "h_grave", "tom": 1.0, "vel": 1.0}
confere("variante mulher", _el.variante("Pessoa desconhecida", "mulher", 210)[0], "Pessoa desconhecida (mulher)")
confere("variante usa voz de mulher", _el.perfil.personagens["Pessoa desconhecida (mulher)"]["voz"], "m_1")

# erros de OCR das falas do GTA 6 (18-20/11)
for lido, certo in [("Fica atrás de mim, amor, fiea atrás de mim.", "Fica atrás de mim, amor, fica atrás de mim."),
                    ("Não tem nâda aqui.", "Não tem nada aqui."), ("Vem çom a gente.", "Vem com a gente."),
                    ("Qual é, maño!", "Qual é, mano!"), ("Que porrra é essa?", "Que porra é essa?"),
                    ("Nâo sei.", "Não sei."), ("kkkk muito bom", "kkkk muito bom"), ("AAA!", "AAA!")]:
    confere(f"ocr {lido!r}", dic.consertar(lido), certo)

# "É" que o OCR leu sem acento: a voz lia "e" (teste do assalto, 20/11)
for t, esperado in [("E um assalto!", "É um assalto!"), ("E claro.", "É claro."), ("E isso.", "É isso."),
                    ("E isso que você fez?", "E isso que você fez?"), ("E mesmo assim ele foi.", "E mesmo assim ele foi."),
                    ("E um dia ele voltou pra casa.", "E um dia ele voltou pra casa."), ("E você?", "E você?"),
                    ("Isso. E sério?", "Isso. É sério?"),
                    # GTA 6, 20/11 09:37: legenda "Escuta, mano. É seu dia de sorte."
                    ("Escutar mano. E seu dia de sorte.", "Escuta, mano. É seu dia de sorte."),
                    ("E seu irmão?", "E seu irmão?"), ("E meu pai morreu ontem.", "E meu pai morreu ontem."),
                    ("Vou falar cara a cara.", "Vou falar cara a cara.")]:
    confere(f"acento no É {t!r}", preparar_fala(t), esperado)
confere("etiqueta de idioma",tirar_idioma("(Espanhol) Ai, Santa Bárbara."), "Ai, Santa Bárbara.")
confere("sem etiqueta", tirar_idioma("Espanta os pombos."), "Espanta os pombos.")

print(f"{'TUDO CERTO' if not falhas else 'FALHAS'}: {len(falhas)} problema(s)")
for f in falhas:
    print("  ✗", f)
sys.exit(1 if falhas else 0)
