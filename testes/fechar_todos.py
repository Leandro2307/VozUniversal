# -*- coding: utf-8 -*-
"""Fecha qualquer Voz Universal aberto (inclusive os sem janela). Usado antes dos testes."""
import os
import sys

import psutil


def fechar_todos():
    fechados = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        if p.info["pid"] == os.getpid():
            continue
        args = p.info["cmdline"] or []
        if any(os.path.basename(a).lower() == "app.py" for a in args):
            try:
                p.kill()
                fechados.append(p.info["pid"])
            except Exception:
                pass
    return fechados


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("fechados:", fechar_todos())
