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
from nucleo.tela import (Dicionario, caixa_alta, eh_interface, juntar_mesma_linha, limpar,  # noqa: E402
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
confere("grito sem ponto", caixa_alta("NÃO ACREDITO NISSO", dic), False)

print(f"{'TUDO CERTO' if not falhas else 'FALHAS'}: {len(falhas)} problema(s)")
for f in falhas:
    print("  ✗", f)
sys.exit(1 if falhas else 0)
