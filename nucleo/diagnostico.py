# -*- coding: utf-8 -*-
"""Registro detalhado de cada etapa (diagnostico\\<sessao>.jsonl), para achar o que melhorar.

Cada linha e um evento em JSON com o instante "t" (segundos desde o inicio da sessao):
  leitura      o que o OCR leu na caixa (muda de texto)        -> base para REPRODUZIR a sessao
  pronta       o leitor decidiu falar um trecho (parcial ou frase inteira)
  fala         entrou na fila do narrador (id, voz, quem)
  gerar_inicio / pedaco / gerar_fim   geracao da voz (tempo de cada pedaco e quanto audio rendeu)
  tocar_inicio / buraco / acelerou / pulou / tocar_fim   o que saiu no alto-falante
  corte        o XTTS "disparou" e o fim estranho foi cortado

A bateria de testes (testes\\bateria.py) reproduz as "leituras" de uma sessao e mede tudo.
"""
import json
import os
import threading
import time

from .util import BASE

PASTA = os.path.join(BASE, "diagnostico")


class Diagnostico:
    def __init__(self):
        self.ligado = True
        self.t0 = time.time()
        self.trava = threading.Lock()
        self.arquivo = None
        self.ouvinte = None          # a bateria de testes escuta os eventos ao vivo

    @staticmethod
    def limpar_antigos(sessoes=20, com_imagens=5):
        """Guarda so as ultimas `sessoes` sessoes e os recortes de imagem das ultimas `com_imagens`
        (senao a pasta cresce sem parar: ~100 MB por noite de testes)."""
        import glob
        import shutil
        try:
            arqs = sorted(glob.glob(os.path.join(PASTA, "2*.jsonl")), key=os.path.getmtime)
            for a in arqs[:-sessoes]:
                os.remove(a)
            pastas = sorted(glob.glob(os.path.join(PASTA, "quadros", "*")), key=os.path.getmtime)
            for p in pastas[:-com_imagens]:
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass

    def abrir(self, nome=None):
        os.makedirs(PASTA, exist_ok=True)
        if nome is None:
            self.limpar_antigos()
        self.t0 = time.time()
        self.arquivo = os.path.join(PASTA, (nome or time.strftime("%Y-%m-%d_%H-%M-%S")) + ".jsonl")
        self.ev("sessao", inicio=time.strftime("%Y-%m-%d %H:%M:%S"))
        return self.arquivo

    def ev(self, tipo, **campos):
        if not self.ligado:
            return
        item = {"t": round(time.time() - self.t0, 3), "ev": tipo}
        item.update(campos)
        if self.ouvinte:
            try:
                self.ouvinte(item)
            except Exception:
                pass
        if not self.arquivo:
            return
        try:
            with self.trava, open(self.arquivo, "a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception:
            pass


diag = Diagnostico()
