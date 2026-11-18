# -*- coding: utf-8 -*-
"""Fonte "sala": o video e o som do PS5 chegam direto da Sala 3D (PS5 direto), sem capturar a tela.

A pagina da sala (Compartilhar tela, public/js/voz-universal.js) liga num WebSocket em
ws://127.0.0.1:<porta>/sala?t=<token>. A porta e o token ficam em %LOCALAPPDATA%\\Sala3d\\voz-universal.json
(o ajudante do Windows da sala le esse arquivo e, se o Voz Universal estiver fechado, abre ele).

Pagina -> Voz Universal (binario, 8 bytes de cabecalho; byte 0 = tipo):
    1  quadro do PS5 em JPEG (a imagem inteira do jogo, ~8 por segundo)
    2  som do PS5: float32 mono 16 kHz (so o jogo: a voz dos amigos e a do narrador ficam de fora)
Voz Universal -> pagina:
    binario tipo 1: a voz em portugues (bytes 4-7 = taxa, uint32 LE; depois float32 mono), para a sala
                    misturar no som do PS5 que vai para quem joga
    texto {"tipo": "abafar", "nivel": 0..1}: o PS5 abaixa NESTE PC enquanto a voz fala (no lugar do
                    mixer do Windows, que abaixaria o Chrome inteiro, com a voz dos amigos junto)
    texto {"tipo": "estado", ...}: o que o painel da sala mostra
"""
import io
import json
import os
import secrets
import struct
import threading
import time

import numpy as np
from PIL import Image

from .diagnostico import diag
from .util import log

QUADRO, SOM = 1, 2
CAB = 8
PASTA = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Sala3d")
ARQUIVO = os.path.join(PASTA, "voz-universal.json")


class SctDaSala:
    """Faz o papel do mss: capturar(sct, reg) recorta do ultimo quadro do PS5 (area = 0, 0, L, A)."""

    def __init__(self, sala):
        self.sala = sala

    def recortar(self, reg):
        img = self.sala.imagem()
        if img is None:
            return Image.new("RGB", (max(1, int(reg["width"])), max(1, int(reg["height"]))))
        x, y = int(reg["left"]), int(reg["top"])
        # fora da imagem o PIL completa com preto (o mss daria erro)
        return img.crop((x, y, x + int(reg["width"]), y + int(reg["height"])))


class Sala:
    def __init__(self, nucleo):
        self.n = nucleo
        self.token = secrets.token_hex(18)
        self.sct = SctDaSala(self)
        self.trava = threading.Lock()
        self.jpeg = None             # o ultimo quadro, ainda em JPEG (so decodifica quando alguem le)
        self.jpeg_t = 0.0
        self.jpeg_n = 0
        self.img = None
        self.img_n = -1
        self.tam = None              # (largura, altura) do ultimo quadro decodificado
        self.enviar = None           # fn(bytes | str) -> manda para a pagina (o painel define)
        self.conectada = False
        self.quadros = 0
        self.ult_abafar = None

    # ------------------------------------------------------------- arquivo para o ajudante da sala
    def publicar(self, porta):
        try:
            os.makedirs(PASTA, exist_ok=True)
            tmp = ARQUIVO + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"porta": int(porta), "token": self.token, "pid": os.getpid()}, f)
            os.replace(tmp, ARQUIVO)
        except Exception as e:
            log("Não deu para avisar a Sala 3D (voz-universal.json):", repr(e), tipo="aviso")

    @staticmethod
    def despublicar():
        try:
            with open(ARQUIVO, encoding="utf-8") as f:
                if json.load(f).get("pid") != os.getpid():
                    return                # outro Voz Universal ja escreveu o dele
            os.remove(ARQUIVO)
        except Exception:
            pass

    # ------------------------------------------------------------- conexao
    def ligou(self, enviar):
        self.enviar = enviar
        self.conectada = True
        self.quadros = 0
        self.ult_abafar = None
        n = self.n
        n.narr.espelho = self.voz
        n.abafador.fator_sala = 1.0
        n.abafador.sala_cb = self.abafar
        n.pedir(n.entrar_sala)
        log("🎮 Sala 3D ligada: lendo o vídeo do PS5 direto da sala (não precisa da tela à vista).", tipo="ok")
        diag.ev("sala_ligou")

    def desligou(self, enviar):
        if self.enviar is not enviar:
            return                        # ja tem outra pagina no lugar desta
        self.enviar = None
        self.conectada = False
        n = self.n
        n.narr.espelho = None
        n.abafador.sala_cb = None
        with self.trava:
            self.jpeg, self.img, self.tam = None, None, None
        n.pedir(n.sair_sala)
        log("Sala 3D desligada: voltando a ler a tela.")
        diag.ev("sala_desligou", quadros=self.quadros)

    def recebeu(self, dados):
        if len(dados) < CAB:
            return
        tipo = dados[0]
        if tipo == QUADRO:
            with self.trava:
                self.jpeg = bytes(dados[CAB:])
                self.jpeg_t = time.time()
                self.jpeg_n += 1
            self.quadros += 1
        elif tipo == SOM:
            corpo = dados[CAB:]
            corpo = corpo[:len(corpo) - len(corpo) % 4]
            e = self.n.escuta
            if e and corpo:
                e.grav.empurrar(np.frombuffer(corpo, np.float32).copy())

    # ------------------------------------------------------------- a imagem
    def imagem(self):
        """O ultimo quadro (PIL RGB), decodificado uma vez so."""
        with self.trava:
            jpeg, num = self.jpeg, self.jpeg_n
            if jpeg is None:
                return None
            if num == self.img_n and self.img is not None:
                return self.img
        try:
            img = Image.open(io.BytesIO(jpeg))
            img = img.convert("RGB")
        except Exception as e:
            diag.ev("sala_quadro_ruim", erro=repr(e)[:100])
            return self.img
        with self.trava:
            self.img, self.img_n, self.tam = img, num, img.size
        return img

    def area(self):
        """(0, 0, largura, altura) do video do PS5, ou None sem quadro novo ha 3 s."""
        if not self.conectada or time.time() - self.jpeg_t > 3.0:
            return None
        if self.tam is None and self.imagem() is None:
            return None
        w, h = self.tam
        return (0, 0, w, h)

    # ------------------------------------------------------------- de volta para a pagina
    def _mandar(self, msg):
        f = self.enviar
        if f:
            try:
                f(msg)
            except Exception:
                pass

    def voz(self, bloco, sr):
        """Cada pedaco de voz que vai para o alto-falante vai tambem para a sala (quem joga ouve)."""
        if not self.enviar or not len(bloco) or not np.any(bloco):
            return
        x = np.ascontiguousarray(bloco, np.float32).reshape(-1)
        self._mandar(struct.pack("<BxxxI", 1, int(sr)) + x.tobytes())

    def abafar(self, nivel):
        nivel = round(float(nivel), 2)
        if nivel != self.ult_abafar:
            self.ult_abafar = nivel
            self._mandar(json.dumps({"tipo": "abafar", "nivel": nivel}))

    def avisar_estado(self):
        if not self.enviar:
            return
        n = self.n
        try:
            m = n.estado_motor()
            self._mandar(json.dumps({"tipo": "estado", "status": n.status, "pausado": n.pausado,
                                     "motor": m.get("usando"), "clonada": m.get("clonada"),
                                     "escuta": m.get("escuta")}, ensure_ascii=False))
        except Exception:
            pass

    def fala(self, info):
        if self.enviar and info:
            self._mandar(json.dumps({"tipo": "fala", "quem": info.get("quem", ""),
                                     "texto": info.get("texto", "")}, ensure_ascii=False))
