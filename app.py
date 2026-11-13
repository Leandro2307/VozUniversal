# -*- coding: utf-8 -*-
"""
Voz Universal - da voz aos dialogos de qualquer jogo, na hora.

Le o texto da tela (OCR do Windows), descobre quem fala (menino, menina, homem, mulher,
idoso ou idosa) pelo nome, pelo jeito de falar e pela imagem, e fala com uma das 2 vozes
daquele tipo, com uma variacao propria para cada personagem.

Painel: abre sozinho (http://127.0.0.1:8765)
Teclas: F10 pausa/retoma | F11 repete a ultima fala
"""
import ctypes
import os
import sys
import threading
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
# Nunca disputar o PC com o jogo: poucos nucleos para as bibliotecas e prioridade baixa
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "2")
try:
    import psutil
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
except Exception:
    pass
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from nucleo.util import carregar_config, log  # noqa: E402


def instancia_unica():
    k = ctypes.windll.kernel32
    h = k.CreateMutexW(None, False, "VozUniversal_Narrador")
    return None if k.GetLastError() == 183 else h


def abrir_janela(porta):
    """Janela do programa (como um app de verdade, sem navegador). Volta quando ela fecha."""
    # o navegador embutido "dorme" quando a janela e minimizada e, ao voltar, ficava uns segundos
    # em branco; assim ele continua acordado (e o painel segue recebendo as atualizacoes)
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                          "--disable-background-timer-throttling --disable-renderer-backgrounding "
                          "--disable-backgrounding-occluded-windows")
    import webview
    webview.create_window(
        "Voz Universal", f"http://127.0.0.1:{porta}/", width=1280, height=860,
        min_size=(900, 620), frameless=True, easy_drag=False, background_color="#0b1230")
    webview.start(gui="edgechromium", private_mode=False,
                  storage_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "recursos", "janela"))


def aviso(texto):
    ctypes.windll.user32.MessageBoxW(None, texto, "Voz Universal", 0x40)


def main():
    cfg = carregar_config()
    porta = int(cfg.get("porta", 8765))
    if not instancia_unica():
        aviso("A Voz Universal já está aberta.")
        return
    print("=" * 60)
    print("  VOZ UNIVERSAL   |   F10 pausa   |   F11 repete")
    print("=" * 60, flush=True)
    log("Iniciando...")
    from nucleo.nucleo import Nucleo
    from nucleo.diagnostico import diag
    if cfg.get("diagnostico", True):
        log("Diagnóstico ligado:", diag.abrir())
    from nucleo import painel
    nucleo = Nucleo(cfg)
    painel.iniciar(nucleo, porta)
    try:
        import keyboard
        keyboard.add_hotkey("f10", nucleo.alternar_pausa)
        keyboard.add_hotkey("f11", lambda: nucleo.pedir(nucleo.repetir))
    except Exception as e:
        log("Teclas F10/F11 indisponíveis:", repr(e), tipo="aviso")
    threading.Thread(target=nucleo.rodar, daemon=True).start()
    log("Pronto — abra o jogo e jogue.", tipo="ok")
    if "--sem-janela" in sys.argv:
        while True:
            time.sleep(3600)
    time.sleep(0.4)          # da tempo do servidor do painel subir
    abrir_janela(porta)      # fica aqui ate a janela fechar
    log("Janela fechada: encerrando.")
    try:
        nucleo.abafador.fechar()      # devolve o volume do jogo antes de sair
    except Exception:
        pass
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            log("ERRO FATAL:", traceback.format_exc(), tipo="erro")
            aviso("Deu um erro e o programa vai fechar.\nOs detalhes estão em log.txt, na pasta do programa.")
        except Exception:
            pass
        os._exit(1)
