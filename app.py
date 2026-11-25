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


def evento_janela():
    """Aviso entre processos: "abra a janela do painel"."""
    k = ctypes.windll.kernel32
    k.CreateEventW.restype = ctypes.c_void_p
    return k.CreateEventW(None, False, False, "VozUniversal_AbrirJanela")


def esperar_pedido_de_janela():
    """Aberto pela Sala 3D ou numa troca de versao: fica SEM janela ate alguem abrir o programa pelo atalho.
    Qualquer janela (minimizada, escondida, sem foco) tomava a frente ao nascer, e a Sala sem foco para de
    receber o controle de quem joga (23/11: "toda vez que voce melhora desconecta o meu controle")."""
    k = ctypes.windll.kernel32
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    h = evento_janela()
    while k.WaitForSingleObject(h, 1000) != 0:
        pass


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


def encerrar(nucleo):
    try:
        nucleo.abafador.fechar()      # devolve o volume do jogo antes de sair
    except Exception:
        pass
    from nucleo.sala import Sala
    Sala.despublicar()                # a Sala 3D nao tenta mais falar com este
    os._exit(0)


def main():
    cfg = carregar_config()
    porta = int(cfg.get("porta", 8765))
    troca = "--troca" in sys.argv      # abre ao lado da versao antiga e assume o lugar dela (nucleo/ponte.py)
    if not instancia_unica() and not troca:
        if "--minimizada" not in sys.argv:      # aberto pela Sala 3D: o que ja esta aberto serve
            k = ctypes.windll.kernel32
            k.SetEvent.argtypes = [ctypes.c_void_p]
            k.SetEvent(evento_janela())          # a que esta aberta (sem janela) mostra o painel
            h = ctypes.windll.user32.FindWindowW(None, "Voz Universal")
            if h:                                # ja tem janela: traz para a frente
                ctypes.windll.user32.ShowWindow(h, 9)
                ctypes.windll.user32.SetForegroundWindow(h)
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
    from nucleo import ponte
    viva = ponte.ponte_viva()
    nucleo.em_espera = troca and viva is not None      # so le e fala quando a ponte mandar assumir
    viva = viva or ponte.garantir_ponte()
    nucleo.usa_ponte = viva is not None
    painel.iniciar(nucleo, porta)
    if viva:
        nucleo.sala.ponte = ponte.ClientePonte(nucleo, lambda: encerrar(nucleo))
        nucleo.sala.ponte.iniciar(*viva)
    else:
        log("Ponte da Sala 3D não abriu: ligando direto.", tipo="aviso")
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
    fim = time.time() + 5
    while not hasattr(nucleo, "porta_painel") and time.time() < fim:
        time.sleep(0.05)
    time.sleep(0.4)          # da tempo do servidor do painel subir
    if "--minimizada" in sys.argv or troca:
        esperar_pedido_de_janela()
    abrir_janela(getattr(nucleo, "porta_painel", porta))      # fica aqui ate a janela fechar
    log("Janela fechada: encerrando.")
    encerrar(nucleo)


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
