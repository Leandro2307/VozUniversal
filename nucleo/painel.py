# -*- coding: utf-8 -*-
"""Servidor do painel (http://127.0.0.1:8765): pagina + websocket com os eventos ao vivo."""
import asyncio
import json
import os
import threading
import time

from aiohttp import WSMsgType, web

from .util import BASE, log

PAGINA = os.path.join(BASE, "painel", "index.html")


def _minha_janela():
    """A janela do programa (a de titulo "Voz Universal" deste processo)."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    achadas = []
    meu = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == meu and u.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(64)
            u.GetWindowTextW(h, buf, 64)
            if buf.value == "Voz Universal":
                achadas.append(h)
        return True

    u.EnumWindows(cb, 0)
    return achadas[0] if achadas else None


_arrastando = threading.Event()


def _arrastar(h):
    """Segue o mouse enquanto o botao estiver apertado e move a janela com SetWindowPos
    assincrono (SWP_ASYNCWINDOWPOS): nunca espera a janela responder, entao nao trava."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    _arrastando.set()
    try:
        p0, r0 = wintypes.POINT(), wintypes.RECT()
        u.GetCursorPos(ctypes.byref(p0))
        u.GetWindowRect(h, ctypes.byref(r0))
        p = wintypes.POINT()
        fim = time.time() + 120
        ult = None
        while u.GetAsyncKeyState(0x01) & 0x8000 and time.time() < fim:
            u.GetCursorPos(ctypes.byref(p))
            alvo = (r0.left + p.x - p0.x, r0.top + p.y - p0.y)
            if alvo != ult:
                # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
                u.SetWindowPos(h, None, alvo[0], alvo[1], 0, 0, 0x0001 | 0x0004 | 0x0010 | 0x4000)
                ult = alvo
            time.sleep(0.008)
    finally:
        _arrastando.clear()


def controlar_janela(acao):
    """Botoes da barra de titulo. So mensagens ASSINCRONAS do Windows (PostMessage /
    ShowWindowAsync): quem move e redimensiona e o proprio Windows, entao a janela nunca trava."""
    import ctypes
    u = ctypes.windll.user32
    h = _minha_janela()
    if not h:
        return
    if acao == "arrastar":
        if not u.IsZoomed(h) and not _arrastando.is_set():
            threading.Thread(target=_arrastar, args=(h,), daemon=True).start()
    elif acao == "minimizar":
        u.ShowWindowAsync(h, 6)                        # SW_MINIMIZE
    elif acao == "maximizar":
        u.ShowWindowAsync(h, 9 if u.IsZoomed(h) else 3)   # SW_RESTORE / SW_MAXIMIZE
    elif acao == "fechar":
        u.PostMessageW(h, 0x0010, 0, 0)                # WM_CLOSE


def iniciar(nucleo, porta):
    clientes = set()
    laco = asyncio.new_event_loop()

    def para_todos(msg):
        dados = json.dumps(msg, ensure_ascii=False, default=str)

        async def enviar():
            for ws in list(clientes):
                try:
                    await ws.send_str(dados)
                except Exception:
                    clientes.discard(ws)

        try:
            asyncio.run_coroutine_threadsafe(enviar(), laco)
        except Exception:
            pass

    nucleo.ouvintes.append(para_todos)

    async def pagina(_):
        return web.FileResponse(PAGINA, headers={"Cache-Control": "no-store"})

    async def ws_handler(req):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(req)
        clientes.add(ws)
        await ws.send_str(json.dumps({"tipo": "estado", "dados": nucleo.estado()}, ensure_ascii=False, default=str))
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
        clientes.discard(ws)
        return ws

    async def estado(_):
        return web.json_response(nucleo.estado(), dumps=lambda d: json.dumps(d, ensure_ascii=False, default=str))

    async def quadro(_):
        jpg = await asyncio.get_running_loop().run_in_executor(None, nucleo.quadro_jpeg)
        if not jpg:
            return web.Response(status=404, text="sem jogo")
        return web.Response(body=jpg, content_type="image/jpeg", headers={"Cache-Control": "no-store"})

    async def acao(req):
        nome = req.match_info["nome"]
        try:
            d = await req.json()
        except Exception:
            d = {}
        n = nucleo
        if nome == "regiao":
            def f():
                if not n.perfil:
                    return
                if d.get("auto"):
                    n.perfil.dados["regiao_manual"] = False
                    n.perfil.regiao = None
                    n.descobridor.zerar()
                    log("Procurando a caixa de diálogo de novo...")
                else:
                    r = [round(min(1, max(0, float(v))), 4) for v in d["regiao"]]
                    n.perfil.dados["regiao_manual"] = True
                    n.perfil.regiao = r
                    log("📍 Caixa de diálogo marcada no painel.", tipo="ok")
                n.leitor.__init__(n.cfg)
                n.avisar_estado()
            n.pedir(f)
        elif nome == "zona_nome":
            def f():
                if n.perfil:
                    n.perfil.dados["zona_nome"] = d.get("zona")
                    n.perfil.salvar()
                    log("Zona do nome " + ("marcada." if d.get("zona") else "removida (volta a detectar sozinho)."))
                    n.avisar_estado()
            n.pedir(f)
        elif nome == "personagem":
            def f():
                if not n.elenco:
                    return
                if d.get("esquecer"):
                    n.elenco.esquecer(d["nome"])
                    log(f"Ficha de {d['nome']} apagada (vai ser descoberta de novo).")
                else:
                    ficha = n.elenco.corrigir(d["nome"], d.get("categoria"), d.get("voz"), d.get("tom"), d.get("vel"))
                    if ficha and d.get("testar", True):
                        n.testar_voz(ficha["voz"], tom=ficha["tom"], vel=ficha["vel"], quem=d["nome"])
                n.emitir("personagens", n.personagens())
            n.pedir(f)
        elif nome == "testar":
            n.testar_voz(d.get("voz", "narrador"), d.get("texto"), float(d.get("tom", 1)), float(d.get("vel", 1)))
        elif nome == "config":
            for k, v in d.items():
                if k in ("volume", "velocidade", "motor", "visao", "auto_regiao", "jogo_fixo",
                         "interromper_ao_avancar", "acelerar_se_atrasado", "abafar_jogo", "abafar_nivel"):
                    n.mudar_config(k, v)
        elif nome == "fonte":
            n.pedir(lambda: n.mudar_fonte(d.get("tipo", "auto"), d.get("hwnd", 0), d.get("monitor", 0)))
        elif nome == "janela":
            controlar_janela(d.get("acao"))
        elif nome == "pausar":
            n.alternar_pausa()
        elif nome == "repetir":
            n.pedir(n.repetir)
        elif nome == "calar":
            n.narr.calar()
        elif nome == "fixar_jogo":
            n.mudar_config("jogo_fixo", n.jan.exe if d.get("fixar") else "")
            n.avisar_estado()
        elif nome == "voz":
            v = n.banco.editar(d["id"], nome=d.get("nome"), descricao=d.get("descricao"),
                               tom=d.get("tom"), velocidade=d.get("velocidade"))
            n.avisar_estado()
            return web.json_response({"ok": bool(v)})
        else:
            return web.json_response({"ok": False, "erro": "ação desconhecida"}, status=404)
        return web.json_response({"ok": True})

    async def janelas(_):
        from .tela import listar_janelas, monitores
        return web.json_response({"janelas": listar_janelas(), "monitores": len(monitores())})

    async def amostra_get(req):
        vid = req.match_info["vid"]
        ws = nucleo.banco.amostras(vid)
        if not ws:
            return web.Response(status=404)
        return web.FileResponse(ws[0])

    async def amostra_post(req):
        vid = req.match_info["vid"]
        if vid not in nucleo.banco.vozes:
            return web.json_response({"ok": False}, status=404)
        dados = await req.read()
        try:
            seg = nucleo.banco.trocar_amostra(vid, dados)
        except Exception as e:
            return web.json_response({"ok": False, "erro": f"não é um WAV válido ({e})"}, status=400)
        if nucleo.narr.clonada:
            nucleo.narr.clonada.lat.pop(vid, None)
        log(f"🎙 Amostra nova na voz {nucleo.banco.vozes[vid].get('nome', vid)} ({seg:.0f}s).", tipo="ok")
        nucleo.avisar_estado()
        return web.json_response({"ok": True, "segundos": seg})

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_get("/", pagina)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/estado", estado)
    app.router.add_get("/api/quadro", quadro)
    app.router.add_get("/api/janelas", janelas)
    app.router.add_get("/api/amostra/{vid}", amostra_get)
    app.router.add_post("/api/amostra/{vid}", amostra_post)
    app.router.add_post("/api/{nome}", acao)

    def rodar():
        asyncio.set_event_loop(laco)
        runner = web.AppRunner(app, access_log=None)
        laco.run_until_complete(runner.setup())
        laco.run_until_complete(web.TCPSite(runner, "127.0.0.1", porta).start())
        laco.run_forever()

    threading.Thread(target=rodar, daemon=True).start()
