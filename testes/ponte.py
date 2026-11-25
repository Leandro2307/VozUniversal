# -*- coding: utf-8 -*-
"""Troca de versao sem derrubar a pagina da Sala: pagina e dois Voz Universal de mentira na ponte de verdade
(pasta separada: nao mexe no voz-universal.json em uso).   python testes\ponte.py"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8")


async def main():
    import aiohttp
    tmp = tempfile.mkdtemp()
    proc = subprocess.Popen([sys.executable, "-m", "nucleo.ponte"], cwd=BASE, env={**os.environ, "LOCALAPPDATA": tmp})
    arq = os.path.join(tmp, "Sala3d", "voz-universal.json")
    for _ in range(100):
        if os.path.exists(arq):
            break
        await asyncio.sleep(0.1)
    d = json.load(open(arq, encoding="utf-8"))
    u = f"ws://127.0.0.1:{d['porta']}"
    async with aiohttp.ClientSession() as s:
        t = "?t=" + d["token"]

        async def prox(ws, seg=3):
            m = await ws.receive(timeout=seg)
            return json.loads(m.data) if m.type == aiohttp.WSMsgType.TEXT else m.data
        try:
            await s.ws_connect(u + "/sala?t=errado")
            assert False, "entrou sem token"
        except aiohttp.WSServerHandshakeError:
            pass
        a = await s.ws_connect(u + "/nucleo" + t + "&pid=1")
        assert (await prox(a)) == {"_ponte": "assumiu"}
        await a.send_str('{"_ponte": "pronto"}')
        pag = await s.ws_connect(u + "/sala" + t)
        assert (await prox(a)) == {"_ponte": "pagina", "ligada": True}
        await pag.send_bytes(b"quadro1")
        assert (await prox(a)) == b"quadro1"
        await a.send_bytes(b"voz A")
        assert (await prox(pag)) == b"voz A"
        b = await s.ws_connect(u + "/nucleo" + t + "&pid=2")
        await pag.send_bytes(b"quadro2")
        assert (await prox(a)) == b"quadro2"                 # carregando: a antiga continua
        await b.send_str('{"_ponte": "pronto"}')
        assert (await prox(a)) == {"_ponte": "sair"}
        await a.send_bytes(b"fim da fala")                   # a antiga termina a fala
        assert (await prox(pag)) == b"fim da fala"
        await a.close()
        assert (await prox(b)) == {"_ponte": "assumiu"}
        assert (await prox(b)) == {"_ponte": "pagina", "ligada": True}
        await pag.send_bytes(b"quadro3")
        assert (await prox(b)) == b"quadro3"
        await b.send_bytes(b"voz B")
        assert (await prox(pag)) == b"voz B"                 # a MESMA pagina, sem cair
        await b.close()
        m = await pag.receive(timeout=12)
        assert m.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING)
    proc.wait(10)
    assert not os.path.exists(arq)
    print("TUDO CERTO: a página não caiu na troca")


asyncio.run(main())
