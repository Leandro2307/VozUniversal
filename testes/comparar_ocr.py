# -*- coding: utf-8 -*-
"""
Compara jeitos de ler os recortes de legenda guardados (diagnostico\\quadros\\<sessao>): conta quantas
palavras estranhas (fora do dicionario) cada jeito produz. Serve para testar melhorias do OCR sem
precisar rodar video.

  python testes\\comparar_ocr.py [pasta_da_sessao]
"""
import glob
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from PIL import Image                                                           # noqa: E402
from nucleo.tela import (Dicionario, em_ordem, juntar_mesma_linha, limpar, ocr_linhas,  # noqa: E402
                         separar_nome, suavizar)

dic = Dicionario()


def ler(img):
    ls = [l for l in ocr_linhas(img) if len(l[0].strip()) >= 2]
    if not ls:
        return ""
    ls = em_ordem(juntar_mesma_linha(ls, img.width))
    _, corpo = separar_nome(ls)
    return limpar(" ".join(l[0] for l in corpo))


def estranhas(t):
    return dic.desconhecidas(dic.consertar(t)) if t else 0


def main():
    pasta = sys.argv[1] if len(sys.argv) > 1 else max(glob.glob(os.path.join(os.path.dirname(AQUI), "diagnostico", "quadros", "*")),
                                                     key=os.path.getmtime)
    arqs = sorted(glob.glob(os.path.join(pasta, "*.jpg")))[:150]
    tot = {"normal": 0, "segunda leitura": 0, "melhor das duas": 0}
    palavras = 0
    exemplos = []
    for a in arqs:
        img = Image.open(a).convert("RGB")
        t1 = ler(img)
        t2 = ler(suavizar(img))
        e1, e2 = estranhas(t1), estranhas(t2)
        fundido = dic.fundir(t1, t2) if t1 and t2 else (t1 or t2)
        e3 = estranhas(fundido)
        tot["normal"] += e1
        tot["segunda leitura"] += e2
        tot["melhor das duas"] += e3
        palavras += len(t1.split())
        if e3 < e1 and len(exemplos) < 6:
            exemplos.append((t1, fundido))
    print(f"{len(arqs)} recortes, {palavras} palavras lidas")
    for k, v in tot.items():
        print(f"  {k:16s}: {v} palavras estranhas")
    for a, b in exemplos:
        print(f"  antes : {a}\n  depois: {dic.consertar(b)}\n")


if __name__ == "__main__":
    main()
