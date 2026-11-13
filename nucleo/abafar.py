# -*- coding: utf-8 -*-
"""Abafa o som do jogo/navegador enquanto a voz em portugues fala ("ducking").

Usa o mixer do Windows (volume de cada programa, igual ao "Mixer de volume"): enquanto o
narrador fala, o programa de onde vem o texto (chrome.exe, RDR2.exe...) desce para `abafar_nivel`
(ex.: 25%); quando a fala termina, volta devagar para o volume que estava.

Seguranca: o volume original de cada programa fica gravado em abafar_estado.json enquanto esta
abafado. Se o Voz Universal fechar no meio (ou travar), na proxima vez que abrir ele devolve.
"""
import json
import os
import threading
import time

from .diagnostico import diag
from .util import BASE, log

ESTADO = os.path.join(BASE, "abafar_estado.json")


class Abafador:
    def __init__(self, cfg, narrador, alvo):
        """alvo(): -> nome do processo de onde vem o som do jogo ("chrome.exe") ou ""."""
        self.cfg = cfg
        self.narr = narrador
        self.alvo = alvo
        self.fator = 1.0                 # 1.0 = volume normal; nivel = abafado
        self.originais = {}              # (pid, exe) -> volume que o programa tinha
        self.sessoes = []
        self.ult_busca = 0.0
        self.ult_fala = 0.0
        self.exe = ""
        self.ok = True
        self.parar = False
        self.thread = threading.Thread(target=self._laco, daemon=True, name="abafar")
        self.thread.start()

    def fechar(self):
        """Ao sair: a propria thread do mixer devolve o volume (COM so funciona nela)."""
        self.parar = True
        self.thread.join(1.5)

    # ------------------------------------------------------------- mixer do Windows
    def _buscar_sessoes(self, exe):
        from pycaw.pycaw import AudioUtilities
        achadas = []
        for s in AudioUtilities.GetAllSessions():
            try:
                if s.Process and s.Process.name().lower() == exe:
                    achadas.append((s.Process.pid, s.SimpleAudioVolume))
            except Exception:
                pass
        return achadas

    def _aplicar(self):
        for pid, vol in self.sessoes:
            chave = f"{pid}:{self.exe}"
            try:
                if chave not in self.originais:
                    self.originais[chave] = vol.GetMasterVolume()
                vol.SetMasterVolume(max(0.0, min(1.0, self.originais[chave] * self.fator)), None)
            except Exception:
                pass

    def _gravar_estado(self):
        try:
            if self.originais:
                with open(ESTADO, "w", encoding="utf-8") as f:
                    json.dump({"exe": self.exe, "originais": self.originais}, f)
            elif os.path.exists(ESTADO):
                os.remove(ESTADO)
        except Exception:
            pass

    def restaurar(self):
        """Devolve o volume que cada programa tinha (ao terminar a fala e ao fechar)."""
        for pid, vol in self.sessoes:
            v = self.originais.get(f"{pid}:{self.exe}")
            if v is not None:
                try:
                    vol.SetMasterVolume(v, None)
                except Exception:
                    pass
        self.originais = {}
        self.fator = 1.0
        self._gravar_estado()

    def _restaurar_de_antes(self):
        """O programa fechou abafado da ultima vez: devolve o volume salvo."""
        if not os.path.exists(ESTADO):
            return
        try:
            with open(ESTADO, encoding="utf-8") as f:
                d = json.load(f)
            sess = dict(self._buscar_sessoes(d.get("exe", "")))
            for chave, v in d.get("originais", {}).items():
                pid = int(chave.split(":")[0])
                if pid in sess:
                    sess[pid].SetMasterVolume(float(v), None)
            log(f"🔈 Volume de {d.get('exe')} devolvido (tinha ficado abafado).")
        except Exception:
            pass
        try:
            os.remove(ESTADO)
        except Exception:
            pass

    # ------------------------------------------------------------- laco
    def _laco(self):
        try:
            import comtypes
            comtypes.CoInitialize()
            self._restaurar_de_antes()
        except Exception as e:
            self.ok = False
            log("Abafar o jogo indisponível (mixer do Windows):", repr(e), tipo="aviso")
            return
        while not self.parar:
            time.sleep(0.04)
            try:
                self._passo()
            except Exception as e:
                diag.ev("abafar_erro", erro=repr(e)[:120])
                time.sleep(1)
        self.restaurar()

    def _passo(self):
        agora = time.time()
        # ja na fila (ainda gerando) conta: o jogo abaixa antes e a voz comeca limpa
        falando = self.narr.falando or not self.narr.audio.empty() or not self.narr.fila.empty()
        if falando:
            self.ult_fala = agora
        ligado = bool(self.cfg.get("abafar_jogo", True))
        exe = (self.alvo() or "").lower() if ligado else ""
        if exe != self.exe:                      # trocou de jogo/janela: devolve o anterior
            self.restaurar()
            self.exe, self.sessoes, self.ult_busca = exe, [], 0.0
            diag.ev("abafar_alvo", exe=exe)
        if not exe:
            return
        # abafado enquanto fala e mais 0,5 s (entre uma frase e outra nao fica sobe-desce)
        nivel = min(1.0, max(0.0, float(self.cfg.get("abafar_nivel", 0.25))))
        alvo = nivel if agora - self.ult_fala < 0.5 else 1.0
        if alvo == self.fator:
            return
        if alvo < 1.0 and (not self.sessoes or agora - self.ult_busca > 3):
            self.sessoes, self.ult_busca = self._buscar_sessoes(exe), agora
        # desce rapido (~0,15 s) para nao cobrir o comeco da fala; sobe devagar (~0,6 s)
        passo = 0.25 if alvo < self.fator else 0.06
        self.fator = max(alvo, self.fator - passo) if alvo < self.fator else min(alvo, self.fator + passo)
        if self.fator >= 1.0:
            self.restaurar()
            diag.ev("abafar", nivel=1.0, exe=exe)
            return
        self._aplicar()
        if alvo < 1.0 and self.fator == alvo:
            diag.ev("abafar", nivel=alvo, exe=exe, sessoes=len(self.sessoes))
        self._gravar_estado()
