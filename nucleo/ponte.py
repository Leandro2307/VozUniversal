# -*- coding: utf-8 -*-
"""Ponte entre a Sala 3D e o Voz Universal: segura a conexao da pagina enquanto o programa troca de versao.

A pagina da Sala nao reconecta sozinha: quando o Voz Universal fechava, a dublagem caia ate alguem clicar em
"Tentar de novo". Este programinha (um processo a parte, sem voz nem OCR) fica com a porta que a Sala usa e
repassa tudo para o Voz Universal ATIVO. Uma versao nova conecta, carrega as vozes e avisa "pronto"; a antiga
termina a fala, sai, e a nova assume sem a pagina perceber.

  pagina  -> ws /sala?t=<token>        (igual a nucleo/sala.py)
  nucleo  -> ws /nucleo?t=<token>&pid=N
      nucleo manda: texto {"_ponte": "pronto"} e, para a pagina, binario (voz) / texto (estado, fala, abafar)
      ponte manda:  texto {"_ponte": "assumiu" | "pagina" (ligada: bool) | "sair"} e o binario da pagina
Sem nenhum Voz Universal por 6 s, fecha a pagina, apaga o voz-universal.json e termina.
"""
import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time

PASTA = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Sala3d")
ARQUIVO = os.path.join(PASTA, "voz-universal.json")      # o que o ajudante da sala le
PORTAS = range(8776, 8786)
SEM_NUCLEO_S = 6.0
SAIR_MAX_S = 40.0


class Ponte:
    def __init__(self):
        self.token = secrets.token_hex(18)
        self.pagina = None
        self.nucleos = []                 # [{"ws", "pid", "pronto", "saindo"}] na ordem em que chegaram
        self.ativo = None
        self.sem_nucleo_desde = time.time()
        self.teve = False
        self.porta = 0

    # --------------------------------------------------------------- arquivo da sala
    def publicar(self):
        os.makedirs(PASTA, exist_ok=True)
        tmp = ARQUIVO + ".ponte.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"porta": self.porta, "token": self.token, "pid": os.getpid(), "ponte": True}, f)
        os.replace(tmp, ARQUIVO)

    @staticmethod
    def despublicar():
        try:
            with open(ARQUIVO, encoding="utf-8") as f:
                if json.load(f).get("pid") != os.getpid():
                    return
            os.remove(ARQUIVO)
        except Exception:
            pass

    # --------------------------------------------------------------- quem esta ativo
    async def ativar(self, n):
        self.ativo = n
        if n is None:
            return
        await self.para_nucleo(n, {"_ponte": "assumiu"})
        if self.pagina is not None:
            await self.para_nucleo(n, {"_ponte": "pagina", "ligada": True})

    async def para_nucleo(self, n, msg):
        try:
            await n["ws"].send_str(json.dumps(msg))
        except Exception:
            pass

    async def trocar(self):
        """A versao nova esta pronta: pede para a ativa terminar a fala e sair."""
        a = self.ativo
        if a is None or a["saindo"]:
            return
        nova = [n for n in self.nucleos if n is not a and n["pronto"]]
        if nova:
            a["saindo"] = time.time()
            await self.para_nucleo(a, {"_ponte": "sair"})

    async def vigiar(self, laco_fim):
        while True:
            await asyncio.sleep(0.5)
            a = self.ativo
            if a and a["saindo"] and time.time() - a["saindo"] > SAIR_MAX_S:
                # a antiga nao saiu sozinha: a nova assume e a conexao da antiga cai (ela encerra)
                await self.tirar(a)
                try:
                    await a["ws"].close()
                except Exception:
                    pass
            if self.nucleos:
                self.sem_nucleo_desde = time.time()
            elif time.time() - self.sem_nucleo_desde > (SEM_NUCLEO_S if self.teve else 60.0):
                self.despublicar()
                if self.pagina is not None:
                    try:
                        await self.pagina.close()
                    except Exception:
                        pass
                laco_fim.set()
                return

    async def tirar(self, n):
        if n in self.nucleos:
            self.nucleos.remove(n)
        if self.ativo is n:
            prontos = [x for x in self.nucleos if x["pronto"]] or self.nucleos
            await self.ativar(prontos[-1] if prontos else None)

    # --------------------------------------------------------------- conexoes
    def app(self):
        from aiohttp import WSMsgType, web

        async def sala(req):
            if req.query.get("t") != self.token:
                return web.Response(status=403, text="token")
            ws = web.WebSocketResponse(heartbeat=20, max_msg_size=8 * 1024 * 1024)
            await ws.prepare(req)
            antiga, self.pagina = self.pagina, ws
            if antiga is not None and not antiga.closed:
                await antiga.close()             # uma pagina por vez: a mais nova fica
            if self.ativo:
                await self.para_nucleo(self.ativo, {"_ponte": "pagina", "ligada": True})
            try:
                async for m in ws:
                    if m.type == WSMsgType.BINARY:
                        a = self.ativo
                        if a:
                            try:
                                await a["ws"].send_bytes(m.data)
                            except Exception:
                                pass
                    elif m.type == WSMsgType.ERROR:
                        break
            finally:
                if self.pagina is ws:
                    self.pagina = None
                    if self.ativo:
                        await self.para_nucleo(self.ativo, {"_ponte": "pagina", "ligada": False})
            return ws

        async def nucleo(req):
            if req.query.get("t") != self.token:
                return web.Response(status=403, text="token")
            ws = web.WebSocketResponse(heartbeat=20, max_msg_size=8 * 1024 * 1024)
            await ws.prepare(req)
            n = {"ws": ws, "pid": int(req.query.get("pid") or 0), "pronto": False, "saindo": 0}
            self.nucleos.append(n)
            self.teve = True
            if self.ativo is None:
                await self.ativar(n)
            try:
                async for m in ws:
                    if m.type == WSMsgType.TEXT:
                        if m.data.startswith('{"_ponte"'):
                            if json.loads(m.data).get("_ponte") == "pronto":
                                n["pronto"] = True
                                await self.trocar()
                        elif n is self.ativo and self.pagina is not None:
                            await self._pagina(m.data)
                    elif m.type == WSMsgType.BINARY:
                        if n is self.ativo and self.pagina is not None:
                            await self._pagina(m.data)
                    elif m.type == WSMsgType.ERROR:
                        break
            finally:
                await self.tirar(n)
            return ws

        async def estado(req):
            if req.query.get("t") != self.token:
                return web.Response(status=403, text="token")
            return web.json_response({
                "pagina": self.pagina is not None, "ativo": (self.ativo or {}).get("pid"),
                "nucleos": [{k: n[k] for k in ("pid", "pronto", "saindo")} for n in self.nucleos]})

        app = web.Application()
        app.router.add_get("/sala", sala)
        app.router.add_get("/nucleo", nucleo)
        app.router.add_get("/estado", estado)
        return app

    async def _pagina(self, dados):
        try:
            if isinstance(dados, str):
                await self.pagina.send_str(dados)
            else:
                await self.pagina.send_bytes(dados)
        except Exception:
            pass

    async def rodar(self):
        from aiohttp import web
        runner = web.AppRunner(self.app(), access_log=None)
        await runner.setup()
        for p in PORTAS:
            try:
                await web.TCPSite(runner, "127.0.0.1", p).start()
                self.porta = p
                break
            except OSError:
                continue
        if not self.porta:
            return
        self.publicar()
        fim = asyncio.Event()
        await self.vigiar(fim)
        await runner.cleanup()


# ------------------------------------------------------------------- lado do Voz Universal
def ponte_viva():
    """(porta, token) da ponte aberta, ou None."""
    try:
        with open(ARQUIVO, encoding="utf-8") as f:
            d = json.load(f)
        if not d.get("ponte"):
            return None
        with socket.create_connection(("127.0.0.1", int(d["porta"])), timeout=1.0):
            return int(d["porta"]), d["token"]
    except Exception:
        return None


def garantir_ponte(prazo=15.0):
    """A ponte aberta, ou abre uma (processo a parte, que continua depois deste fechar)."""
    v = ponte_viva()
    if v:
        return v
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.Popen([sys.executable, "-m", "nucleo.ponte"], cwd=base, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=0x00000008 | 0x00000200 | 0x08000000)   # solta, sem janela
    fim = time.time() + prazo
    while time.time() < fim:
        time.sleep(0.2)
        v = ponte_viva()
        if v:
            return v
    return None


class ClientePonte:
    """Liga o Voz Universal na ponte (no lugar de a pagina ligar direto nele)."""

    def __init__(self, nucleo, sair):
        self.n = nucleo
        self.sair = sair                  # fn(): encerra o programa
        self.ws = None
        self.laco = asyncio.new_event_loop()
        self.assumiu = False
        self.avisou_pronto = False
        self.enviar = None

    def iniciar(self, porta, token):
        self.url = f"ws://127.0.0.1:{porta}/nucleo?t={token}&pid={os.getpid()}"
        threading.Thread(target=self._thread, daemon=True, name="ponte").start()

    def _thread(self):
        asyncio.set_event_loop(self.laco)
        try:
            self.laco.run_until_complete(self._rodar())
        except Exception as e:
            from .util import log
            log("Ponte da Sala 3D caiu:", repr(e), tipo="aviso")

    async def _rodar(self):
        import aiohttp
        from .util import log
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(self.url, heartbeat=20, max_msg_size=8 * 1024 * 1024) as ws:
                self.ws = ws
                tarefa = asyncio.ensure_future(self._avisar_pronto())
                async for m in ws:
                    if m.type == aiohttp.WSMsgType.BINARY:
                        self.n.sala.recebeu(m.data)
                    elif m.type == aiohttp.WSMsgType.TEXT:
                        d = json.loads(m.data)
                        t = d.get("_ponte")
                        if t == "assumiu":
                            self.assumiu = True
                            self.n.em_espera = False
                            log("Versão nova no ar: assumi a dublagem." if self.avisou_pronto else "Ponte da Sala 3D ligada.",
                                tipo="ok")
                        elif t == "pagina":
                            self._pagina(bool(d.get("ligada")))
                        elif t == "sair":
                            threading.Thread(target=self._despedir, daemon=True).start()
                tarefa.cancel()
        # a ponte fechou (ou derrubou esta versao): se a nova assumiu, esta encerra
        if getattr(self, "saindo", False):
            self.sair()
        self._pagina(False)

    async def _avisar_pronto(self):
        t0 = time.time()
        while True:
            est = self.n.narr.estado_clonada
            if self.n.cfg.get("motor") != "clonada" or est == "pronta":
                break
            if not self.assumiu and (est in ("sem_memoria", "erro") or time.time() - t0 > 240):
                from .util import log
                log(f"Versão nova não conseguiu carregar a voz ({est}): fica a que já estava aberta.", tipo="aviso")
                self.sair()
                return
            await asyncio.sleep(0.5)
        if not self.assumiu:
            await asyncio.sleep(4.0)          # as vozes "acordam" (latentes) antes de assumir
        self.avisou_pronto = not self.assumiu
        await self.ws.send_str('{"_ponte": "pronto"}')

    def _pagina(self, ligada):
        sala = self.n.sala
        if ligada and not sala.conectada:
            self.enviar = self._mandar
            sala.ligou(self.enviar)
            sala.avisar_estado()
        elif not ligada and self.enviar:
            sala.desligou(self.enviar)
            self.enviar = None

    def _mandar(self, msg):
        ws = self.ws
        if ws is None or ws.closed:
            return

        async def mandar():
            try:
                if isinstance(msg, str):
                    await ws.send_str(msg)
                else:
                    await ws.send_bytes(msg)
            except Exception:
                pass
        asyncio.run_coroutine_threadsafe(mandar(), self.laco)

    def _despedir(self):
        """A nova esta pronta: espera um instante sem legenda e sem fala (no maximo 30 s) e encerra."""
        from .util import log
        self.saindo = True
        n = self.n
        log("Versão nova pronta: termino a fala e fecho esta.")
        fim, quieto = time.time() + 30, 0.0
        while time.time() < fim:
            livre = (not n.narr.falando and n.narr.fila.empty() and n.narr.audio.empty()
                     and not getattr(n, "lido", ""))
            quieto = quieto + 0.1 if livre else 0.0
            if quieto >= 0.8:
                break
            time.sleep(0.1)
        n.em_espera = True
        fim = time.time() + 6
        while time.time() < fim and (n.narr.falando or not n.narr.fila.empty() or not n.narr.audio.empty()):
            time.sleep(0.1)
        n.sala.abafar(1.0)                # o PS5 volta ao volume normal
        time.sleep(0.2)
        self.sair()


if __name__ == "__main__":
    asyncio.run(Ponte().rodar())
