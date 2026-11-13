# -*- coding: utf-8 -*-
"""
Teste ao vivo com um video no navegador (sem precisar de ninguem na frente do PC):
reinicia o Voz Universal, espera a voz clonada, minimiza o painel, traz o navegador para a
frente, da play, roda X segundos em janela normal e Y segundos em tela cheia, pausa e mostra o
resumo da sessao (testes\\analisar.py).

  python testes\\ao_vivo.py [segundos_janela] [segundos_tela_cheia] [trecho-do-titulo-da-janela]
"""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(AQUI)
sys.path.insert(0, BASE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
URL = "http://127.0.0.1:8765"
u = ctypes.windll.user32


def api(caminho, dados=None, tempo=3):
    req = urllib.request.Request(URL + caminho, data=None if dados is None else json.dumps(dados).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=tempo).read())


def estado():
    try:
        return api("/api/estado")
    except Exception:
        return None


def focar(h):
    u.keybd_event(0x12, 0, 0, 0)      # ALT: o Windows deixa trocar a janela da frente
    u.keybd_event(0x12, 0, 2, 0)
    u.ShowWindow(h, 9)
    u.SetForegroundWindow(h)
    time.sleep(0.6)


def tecla(vk):
    u.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    u.keybd_event(vk, 0, 2, 0)
    time.sleep(0.3)


def foto(nome):
    """Foto da tela (testes\resultados\telas) para conferir depois o que estava aparecendo."""
    try:
        import mss
        from PIL import Image
        pasta = os.path.join(AQUI, "resultados", "telas")
        os.makedirs(pasta, exist_ok=True)
        with mss.MSS() as s:
            im = s.grab(s.monitors[1])
            img = Image.frombytes("RGB", im.size, im.bgra, "raw", "BGRX")
            img.thumbnail((1280, 720))
            arq = os.path.join(pasta, time.strftime("%H%M%S_") + nome + ".jpg")
            img.save(arq, quality=80)
            print("foto:", arq, flush=True)
    except Exception as e:
        print("foto falhou:", e)


def duplo_clique_no_video(cliques=2):
    """Duplo clique no meio do video (achado pelo Voz Universal, ou o meio da janela)."""
    try:
        e = estado()
        x, y, w, h = e["jogo"]["area"]
        r = e.get("video") or None
    except Exception:
        r = None
    if not r:
        import win32gui
        from nucleo.tela import listar_janelas
        nav = [j for j in listar_janelas() if "youtube" in j["titulo"].lower()]
        l, t, rr, b = win32gui.GetWindowRect(nav[0]["hwnd"])
        cx, cy = l + (rr - l) // 2, t + int((b - t) * 0.4)
    else:
        cx, cy = int(x + (r[0] + r[2]) / 2 * w), int(y + (r[1] + r[3]) / 2 * h)
    u.SetCursorPos(cx, cy)
    time.sleep(0.2)
    for _ in range(cliques):
        u.mouse_event(0x0002, 0, 0, 0, 0)
        u.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.08)
    time.sleep(1.0)
    u.SetCursorPos(5, 5)                # tira o mouse de cima (senao os controles do player ficam a vista)


def mexendo(h, espera=1.5):
    """O video esta tocando? Compara duas fotos da janela do navegador com `espera` s de intervalo."""
    import mss
    import numpy as np
    import win32gui
    l, t, r, b = win32gui.GetWindowRect(h)
    caixa = {"left": max(0, l), "top": max(0, t), "width": max(50, r - l), "height": max(50, b - t)}
    with mss.MSS() as s:
        a = np.asarray(s.grab(caixa))[:, :, :3].astype(np.int16)
        time.sleep(espera)
        c = np.asarray(s.grab(caixa))[:, :, :3].astype(np.int16)
    return float(np.abs(a - c).mean()) > 0.4


def garantir_tocando(h):
    for _ in range(2):
        if mexendo(h):
            return True
        print("  (vídeo parado: clicando no vídeo para dar play)", flush=True)
        focar(h)
        duplo_clique_no_video(cliques=1)    # a tecla "k" nem sempre chega no player
        time.sleep(1.5)
    return mexendo(h)


def em_tela_cheia(h):
    import win32api
    import win32gui
    l, t, r, b = win32gui.GetWindowRect(h)
    mon = win32api.GetMonitorInfo(win32api.MonitorFromWindow(h))["Monitor"]
    return (l, t, r, b) == tuple(mon)


def abrir_link(h, link):
    import keyboard
    focar(h)
    keyboard.press_and_release("ctrl+l")
    time.sleep(0.4)
    keyboard.write(link, delay=0.01)
    keyboard.press_and_release("enter")
    time.sleep(6)


def main():
    a = sys.argv[1:]
    seg_janela = float(a[0]) if len(a) > 0 else 60
    seg_cheia = float(a[1]) if len(a) > 1 else 60
    trecho = a[2] if len(a) > 2 else "YouTube"
    link = a[3] if len(a) > 3 else ""
    from nucleo.tela import listar_janelas

    # 1) reinicia o Voz Universal com o codigo novo
    if estado():
        try:
            api("/api/janela", {"acao": "fechar"})
        except Exception:
            pass
        for _ in range(40):
            time.sleep(0.5)
            if not estado():
                break
    # garante que nao sobrou nenhum outro Voz Universal aberto (dois narradores = teste invalido)
    from fechar_todos import fechar_todos
    sobrou = fechar_todos()
    if sobrou:
        print("encerrados Voz Universal que ainda estavam abertos:", sobrou, flush=True)
        time.sleep(1.5)
    subprocess.Popen([PYW, os.path.join(BASE, "app.py")], cwd=BASE)
    print("Voz Universal reiniciado; esperando a voz clonada...", flush=True)
    t0 = time.time()
    while time.time() - t0 < 120:
        e = estado()
        if e and e["motor"]["clonada"] in ("pronta", "erro", "sem_memoria"):
            break
        time.sleep(1)
    e = estado()
    print("motor:", e and e["motor"], flush=True)
    time.sleep(1)
    api("/api/janela", {"acao": "minimizar"})

    # 2) navegador na frente e play
    nav = [j for j in listar_janelas() if trecho.lower() in j["titulo"].lower() or trecho.lower() in j["exe"].lower()]
    if not nav:
        sys.exit(f"Nenhuma janela com '{trecho}' no título.")
    h = nav[0]["hwnd"]
    print("navegador:", nav[0]["titulo"], flush=True)
    if link:
        abrir_link(h, link)             # o YouTube ja comeca tocando sozinho
        if "--cc" in sys.argv:
            focar(h)
            tecla(0x43)                 # "c" = liga as legendas do proprio YouTube
    else:
        focar(h)
        tecla(0x4B)                     # "k" = play/pause no YouTube
    garantir_tocando(h)
    print(f"▶ janela normal por {seg_janela:.0f}s", flush=True)
    time.sleep(min(12, seg_janela))
    foto("janela")
    time.sleep(max(0, seg_janela - 12))
    if seg_cheia > 0:
        focar(h)
        duplo_clique_no_video()         # duplo clique no video = tela cheia (a tecla "f" falhava)
        if not em_tela_cheia(h):
            print("  (não entrou em tela cheia: tentando de novo)", flush=True)
            focar(h)
            duplo_clique_no_video()
        print(f"▶ tela cheia por {seg_cheia:.0f}s (tela cheia: {em_tela_cheia(h)})", flush=True)
        # o duplo clique pode deixar o video pausado (2 cliques = pausa + play, as vezes so 1 conta)
        garantir_tocando(h)
        time.sleep(min(12, seg_cheia))
        foto("tela_cheia")
        fim = time.time() + max(0, seg_cheia - 12)
        while time.time() < fim:            # a cada minuto confere se o video nao parou
            time.sleep(min(60, max(0, fim - time.time())))
            if time.time() < fim - 5 and not mexendo(h):
                print("  (vídeo parou no meio do teste)", flush=True)
                garantir_tocando(h)
        focar(h)
        tecla(0x1B)                     # ESC sai da tela cheia
        time.sleep(3)                   # a animacao de sair da tela cheia parecia "video tocando"
    if mexendo(h, 2.0):
        focar(h)
        duplo_clique_no_video(cliques=1)    # pausa (so se estiver tocando: senao daria play)
    time.sleep(2)
    print("teste encerrado; vídeo pausado.", flush=True)


if __name__ == "__main__":
    main()
