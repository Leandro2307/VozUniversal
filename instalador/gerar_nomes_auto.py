"""Gera recursos\\nomes_auto.txt: sexo de ~100 mil nomes proprios + estatistica das terminacoes.

Com ele o programa descobre se o personagem e homem ou mulher pelo nome da legenda SEM ninguem
precisar escrever o nome em recursos\\nomes.txt ("Rochele" -> mulher; um nome inventado como
"Marizete" tambem, pela terminacao).

Fontes (baixadas na hora):
  - brasil.io "genero-nomes" (Censo 2010 do IBGE): nomes brasileiros e suas grafias alternativas
  - SSA dos EUA (espelho hadley/data-baby-names): nomes americanos mais dados de 1880 a 2008

Uso:  python instalador\\gerar_nomes_auto.py
"""
import collections
import csv
import gzip
import io
import os
import sys
import unicodedata
import urllib.request

URL_BR = "https://data.brasil.io/dataset/genero-nomes/nomes.csv.gz"
URL_US = "https://raw.githubusercontent.com/hadley/data-baby-names/master/baby-names.csv"
SAIDA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recursos", "nomes_auto.txt")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().strip()
    return s if s.isalpha() and len(s) >= 2 else ""


def baixar(url):
    print("baixando", url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=120).read()


def main():
    # nome -> [peso_f, peso_m] de cada fonte, em proporcao (0..1), para as fontes valerem igual
    br, us = {}, {}

    dados = gzip.decompress(baixar(URL_BR)).decode("utf-8")
    for r in csv.DictReader(io.StringIO(dados)):
        f, m = float(r["frequency_female"] or 0), float(r["frequency_male"] or 0)
        if f + m < 20:
            continue
        for n in [r["first_name"]] + [a for a in (r["alternative_names"] or "").split("|") if a]:
            n = norm(n)
            if n:
                d = br.setdefault(n, [0.0, 0.0])
                d[0] += f
                d[1] += m

    dados = baixar(URL_US).decode("utf-8")
    for r in csv.DictReader(io.StringIO(dados)):
        n = norm(r["name"])
        if n:
            d = us.setdefault(n, [0.0, 0.0])
            d[0 if r["sex"] == "girl" else 1] += float(r["percent"])

    nomes = {}
    for n in set(br) | set(us):
        ps = [d[0] / (d[0] + d[1]) for d in (br.get(n), us.get(n)) if d and d[0] + d[1] > 0]
        nomes[n] = sum(ps) / len(ps)          # probabilidade de ser mulher

    # terminacoes (1 a 5 letras): cada nome conta 1 (nao a frequencia), para generalizar bem
    # para nomes raros/inventados
    suf = collections.defaultdict(lambda: [0.0, 0.0])
    for n, pf in nomes.items():
        for k in range(1, 6):
            if len(n) > k:
                s = suf[n[-k:]]
                s[0] += pf
                s[1] += 1 - pf

    with open(SAIDA, "w", encoding="utf-8") as arq:
        arq.write("# Gerado por instalador\\gerar_nomes_auto.py (IBGE/brasil.io + SSA EUA). Nao editar a mao:\n")
        arq.write("# para corrigir um nome, ponha em recursos\\nomes.txt (vale mais que este arquivo).\n")
        arq.write("# nome prob_mulher | ~terminacao prob_mulher quantos_nomes\n")
        for n in sorted(nomes):
            arq.write(f"{n} {nomes[n]:.2f}\n")
        for s in sorted(suf):
            f, m = suf[s]
            if f + m >= 15:
                arq.write(f"~{s} {f / (f + m):.2f} {int(f + m)}\n")
    print(f"{len(nomes)} nomes e {sum(1 for f, m in suf.values() if f + m >= 15)} terminacoes -> {SAIDA}")


if __name__ == "__main__":
    sys.exit(main())
