# -*- coding: utf-8 -*-
"""O laco principal: acha o jogo, le a caixa de dialogo, descobre quem fala e manda falar."""
import collections
import difflib
import io
import os
import re
import threading
import time

import mss
import numpy as np
from PIL import Image

from .diagnostico import diag
from .identidade import Elenco, ROTULOS, Visao
from .leitor import Leitor
from .perfil import Perfil
from .tela import (Descobridor, DetectorVideo, Dicionario, eh_relogio,Janela, achar_legenda, capturar, em_ordem, limpar, ocr_linhas, regiao_em_tela,
                   separar_nome, nome_estavel, suavizar, isolar_contorno)
from .util import FIM, gravar_json, CONFIG, historico_log, log, normalizar, ouvir_log, semelhanca
from .vozes import Banco, Narrador



NAVEGADORES = {"chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "opera_gx.exe", "brave.exe", "vivaldi.exe"}


def _ret_visivel(h):
    """Retangulo VISIVEL da janela. GetWindowRect inclui uma borda invisivel de ~8 px: maximizada,
    a janela "ia" ate 1040 px, mas a barra de tarefas comeca em 1032 (a area da legenda caia nela)."""
    import ctypes
    from ctypes import wintypes
    try:
        r = wintypes.RECT()
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(h), 9, ctypes.byref(r), ctypes.sizeof(r)) == 0:
            return r.left, r.top, r.right, r.bottom          # DWMWA_EXTENDED_FRAME_BOUNDS
    except Exception:
        pass
    import win32gui
    return win32gui.GetWindowRect(h)

class Nucleo:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ouvintes = []                 # funcoes do painel que recebem os eventos
        self.banco = Banco()
        try:
            from .util import medidor_placa
            medidor_placa()                    # comeca a medir o uso da placa de video ja na abertura
        except Exception:
            pass
        # "sala" so vale com a pagina da Sala 3D ligada: fechado no meio, abre na fonte de antes
        if cfg.get("fonte") == "sala":
            cfg["fonte"] = cfg.get("fonte_antes_sala") or "auto"
        self.narr = Narrador(cfg, self.banco, aviso=self.emitir)
        from .abafar import Abafador
        from .sala import Sala
        self.exe_frente = ""               # programa da janela da frente (modo tela/video)
        self.jan = Janela(cfg)            # antes do abafador: a linha dele ja usa self.jan
        self.sala = Sala(self)             # o PS5 direto da Sala 3D (video e som pela rede)
        self.jan.sala = self.sala
        self.abafador = Abafador(cfg, self.narr, self._exe_do_som)
        self.pid_frente = 0                # processo da janela da frente (de onde vem o som)
        # ouve a voz ORIGINAL do jogo para saber se quem fala e homem, mulher ou crianca
        self.escuta = None
        self._voz_pag = {}                 # legenda -> voz decidida (os pedacos da mesma legenda nao trocam)
        self._oposto = {}                  # legenda da 2a pessoa ("- A. - B.") -> legenda da 1a
        if cfg.get("escuta", True):
            try:
                from .escuta import Escuta
                self.escuta = Escuta(cfg, self._pid_do_som, lambda: self.narr.falando)
                self.narr.resolver_voz = self._resolver_voz
                self.narr.candidatas_voz = self._candidatas_voz
            except Exception as e:
                log("Escuta da voz original indisponível:", repr(e), tipo="aviso")
        self.dic = Dicionario()
        self.visao = Visao() if cfg.get("visao", True) else None
        self.perfil = None
        self.elenco = None
        self.leitor = Leitor(cfg)
        self.descobridor = Descobridor()
        self.pausado = False
        self.status = "aguardando"
        self.area = None
        self.ultimo_falando = None
        self.ult_nomes = collections.deque(maxlen=8)
        self.ult_nomes_t = collections.deque(maxlen=8)    # quando cada nome foi lido
        self.ult_nomes_txt = collections.deque(maxlen=8)  # o texto lido junto com cada nome
        self.nome_pagina = ""
        self.ficha_pagina = None
        self.sem_texto = 0
        self.lido = ""
        self.legenda_frac = None
        self.video = DetectorVideo()
        self.ult_video = 0.0
        self.ult_quadro = ""
        self.stats = {"ticks": 0, "ocr_ms": 0.0, "desde": time.time()}
        self.pedidos = []                  # acoes vindas do painel, executadas no laco
        self.trava = threading.Lock()
        self.palavras_vistas = {}          # palavra com maiuscula vista no meio das falas -> quando
        ouvir_log(lambda item: self.emitir("log", item))
        if cfg.get("motor") == "clonada":
            # so depois de tudo pronto (antes dava erro e nao carregava). A visao carrega JUNTO, em
            # paralelo: antes a clonada esperava a rapida aquecer e a visao terminar (39 s parada
            # ao ligar o PC). Medido 16/11: juntas 16-20 s contra 19-26 s em serie, mesmo pico de
            # RAM (4,9 GB) - a carga direta na placa acabou com os +3 GB de antes.
            self.narr.ligar_clonada()
            if self.visao:
                self.visao.preparar()

    # ------------------------------------------------------------- eventos para o painel
    def emitir(self, tipo, dados=None):
        if tipo == "falando":
            self.ultimo_falando = dict(dados, quando=time.time())
        sala = getattr(self, "sala", None)
        if tipo == "motor":
            dados = self.estado_motor()
        if sala and sala.enviar:
            if tipo == "falando":
                sala.fala(dados)
            elif tipo in ("motor", "status"):
                sala.avisar_estado()
        for fn in list(self.ouvintes):
            try:
                fn({"tipo": tipo, "dados": dados})
            except Exception:
                pass

    def estado_motor(self):
        return {"escolhido": self.cfg.get("motor"), "clonada": self.narr.estado_clonada,
                "usando": ("local" if self.narr.motor_atual() is self.narr.rapida and self.cfg.get("motor") == "local"
                           else self.narr.motor_atual().nome),
                "visao": ("pronta" if self.visao and self.visao.pronta else
                          "erro" if self.visao and self.visao.erro else
                          "carregando" if self.visao and self.visao.carregando else
                          "espera" if self.visao and self.cfg.get("visao", True) else "desligada"),
                "escuta": self._estado_escuta()}

    def _estado_escuta(self):
        e = self.escuta
        if not e or not self.cfg.get("escuta", True):
            return "desligada"
        if e.ouvido.erro or (not e.grav.modo and e.grav.erro):
            return "erro"
        if e.ativa:
            return "pronta"
        return "carregando"

    def personagens(self):
        if not self.perfil:
            return []
        out = []
        for nome, f in self.perfil.personagens.items():
            v = self.banco.vozes.get(f.get("voz"), {})
            out.append({"nome": nome, "categoria": f.get("categoria"), "rotulo": ROTULOS.get(f.get("categoria"), "?"),
                        "voz": f.get("voz"), "voz_nome": v.get("nome", f.get("voz")), "tom": f.get("tom", 1.0),
                        "vel": f.get("vel", 1.0), "confianca": f.get("confianca", 0), "fixo": f.get("fixo", False),
                        "pistas": f.get("pistas", []), "falas": f.get("falas", 0),
                        "ultima_vez": f.get("ultima_vez", 0)})
        out.sort(key=lambda p: -p["ultima_vez"])
        return out

    def estado(self):
        cfg = self.cfg
        return {
            "status": self.status,
            "pausado": self.pausado,
            "jogo": {"exe": self.jan.exe, "titulo": self.jan.titulo, "area": self.area} if self.jan.raiz else None,
            "regiao": self.perfil.regiao if self.perfil else None,
            "zona_nome": self.perfil.dados.get("zona_nome") if self.perfil else None,
            "modo_legenda": self._modo_legenda() if self.perfil else False,
            "video": self.video.rect,
            "legenda_lida": self.legenda_frac if self._modo_legenda() else None,
            "motor": self.estado_motor(),
            "config": {k: cfg.get(k) for k in ("volume", "velocidade", "motor", "visao", "escuta", "auto_regiao",
                                                "jogo_fixo", "interromper_ao_avancar", "acelerar_se_atrasado",
                                                "fonte", "fonte_hwnd", "fonte_monitor", "voz_unica")},
            "banco": self.banco.resumo(),
            "personagens": self.personagens(),
            "falando": self.ultimo_falando,
            "lido": self.lido,
            "log": historico_log()[-80:],
        }

    def avisar_estado(self):
        self.emitir("estado", self.estado())

    # ------------------------------------------------------------- acoes do painel
    def pedir(self, fn):
        with self.trava:
            self.pedidos.append(fn)

    def salvar_config(self):
        gravar_json(CONFIG, self.cfg)

    def mudar_config(self, chave, valor):
        self.cfg[chave] = valor
        if chave == "voz_unica":
            self.cfg.pop("so_lucia", None)          # opcao antiga (so a Lucia)
            if valor and self.cfg.get("motor") != "clonada":
                # a voz clonada (Lucia, Jason) so existe na clonada (na rapida/local seria outra voz)
                self.cfg["motor"] = "clonada"
                chave, valor = "motor", "clonada"
                log("🎙 Voz única ligada: mudando para a voz Clonada.")
        self.salvar_config()
        if chave == "motor" and valor == "clonada":
            self.narr.ligar_clonada()
        elif chave == "motor":
            self.narr.rapida.precarregar()          # rapida/local: aquece as vozes do PC agora
        if chave == "escuta" and valor and not self.escuta:
            try:
                from .escuta import Escuta
                self.escuta = Escuta(self.cfg, self._pid_do_som, lambda: self.narr.falando)
                self.narr.resolver_voz = self._resolver_voz
                self.narr.candidatas_voz = self._candidatas_voz
            except Exception as e:
                log("Escuta da voz original indisponível:", repr(e), tipo="aviso")
        if chave == "visao" and valor and not self.visao:
            self.visao = Visao()
        if chave == "jogo_fixo":
            self.jan.raiz = None
        self.emitir("motor")

    def mudar_fonte(self, tipo, hwnd=0, monitor=0):
        """De onde ler: 'auto' (jogo), 'tela' (monitor inteiro) ou 'janela' (ex.: YouTube)."""
        from .tela import listar_janelas
        self.cfg["fonte"] = tipo if tipo in ("auto", "tela", "janela") else "auto"
        if tipo == "janela":
            j = next((x for x in listar_janelas() if x["hwnd"] == int(hwnd)), None)
            self.cfg["fonte_hwnd"] = int(hwnd)
            self.cfg["fonte_exe"] = (j or {}).get("exe", "")
        if tipo == "tela":
            self.cfg["fonte_monitor"] = int(monitor)
        self.salvar_config()
        self.jan.raiz, self.jan.exe, self.jan.titulo = None, "", ""
        log({"auto": "Lendo: o jogo que estiver aberto (automático).",
             "tela": f"Lendo: a tela inteira do monitor {int(monitor) + 1}.",
             "janela": "Lendo: a janela " + (f"“{j['titulo']}”" if tipo == 'janela' and j else 'escolhida') + "."}
            [self.cfg["fonte"]], tipo="ok")

    # ------------------------------------------------------------- Sala 3D (PS5 direto)
    def na_sala(self):
        return self.cfg.get("fonte") == "sala"

    def entrar_sala(self):
        """A pagina da Sala 3D ligou: le o video do PS5 que chega por ela (nucleo/sala.py)."""
        if not self.na_sala():
            self.cfg["fonte_antes_sala"] = self.cfg.get("fonte") or "auto"
        self.cfg["fonte"] = "sala"
        self.frente_bloqueada = False
        self.exe_frente, self.pid_frente = "", 0
        self.salvar_config()
        self.sala.avisar_estado()

    def sair_sala(self):
        if not self.na_sala():
            return
        self.cfg["fonte"] = self.cfg.get("fonte_antes_sala") or "auto"
        self.salvar_config()

    def testar_voz(self, vid, texto=None, tom=1.0, vel=1.0, quem=""):
        d = self.banco.vozes.get(vid, {})
        texto = texto or {
            "menino": "Ei! Você viu isso? Vamos lá, aposto que eu chego primeiro!",
            "menina": "Oi! Que bom que você veio. Eu estava te esperando faz um tempão!",
            "homem": "Bem-vindo, viajante. A estrada até a cidade é perigosa a esta hora.",
            "mulher": "Fique atento. Nem tudo nesta floresta é o que parece.",
            "idoso": "Ah, meu jovem... Nos meus tempos, as coisas eram bem diferentes.",
            "idosa": "Ai, ai... Venha cá, minha criança, deixe a vovó te contar uma história.",
            "narrador": "Você encontrou um item raro! Ele foi guardado na sua bolsa.",
        }.get(d.get("categoria"), "Olá! Esta é a minha voz.")
        self.narr.falar(texto, vid, tom, vel, quem=quem or f"Teste · {d.get('nome', vid)}",
                        categoria=d.get("categoria", ""), forcar=True)

    def quadro_jpeg(self):
        """Foto da tela do jogo agora (para marcar a caixa de dialogo no painel)."""
        area = self.jan.area() if self.jan.raiz else None
        if not area:
            return None
        x, y, w, h = area
        if self.na_sala():
            img = self.sala.imagem()
            if img is None:
                return None
        else:
            with (getattr(mss, "MSS", None) or mss.mss)() as sct:
                img = capturar(sct, {"left": x, "top": y, "width": w, "height": h})
        if img.width > 1280:
            img = img.resize((1280, int(img.height * 1280 / img.width)))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82)
        return buf.getvalue()

    # ------------------------------------------------------------- laco
    def _trocou_jogo(self):
        self.video.zerar()
        diag.ev("fonte", jogo=self.jan.exe, titulo=self.jan.titulo,
                regiao=(Perfil(self.jan.exe).regiao if self.jan.raiz else None))
        if self.jan.raiz:
            self.perfil = Perfil(self.jan.exe, self.jan.titulo)
            self.perfil.salvar()
            self.elenco = Elenco(self.perfil, self.banco)
            n = len(self.perfil.personagens)
            log(f"🎮 Jogo: {self.jan.titulo} ({self.jan.exe})"
                + (f" — {n} personagens já conhecidos" if n else " — jogo novo"), tipo="ok")
            if not self.perfil.regiao:
                log("Procurando a caixa de diálogo sozinho... (ou marque no painel)")
        else:
            log("Jogo fechado. Esperando outro...")
        self.leitor = Leitor(self.cfg)
        self.descobridor.zerar()
        self.ult_nomes.clear()
        self.ult_nomes_t.clear()
        self.ult_nomes_txt.clear()
        self.nome_pagina = ""
        self.avisar_estado()

    def _nome_canonico(self, bruto):
        """Nome lido (com erro de OCR) -> nome de personagem ja conhecido, se for parecido."""
        b = re.sub(r"[^\wÀ-ÿ' .\-]", "", bruto or "").strip(" .-")
        if len(normalizar(b)) < 2:
            return ""
        if b.isupper() and len(b) > 3:
            b = b.title()
        # "Pessoa desconhecida 1" e "... 2" sao pessoas DIFERENTES: o numero nunca some nem se
        # aproxima de outro; so a parte de texto e consertada ("V)essoa"/"Qessoa" -> "Pessoa")
        m_pd = re.fullmatch(r"\W*\w{0,2}\W*essoa\s+desconhecid[ao](?:\s+(\d{1,2}))?", b, re.I)
        if m_pd:
            return "Pessoa desconhecida" + (f" {m_pd.group(1)}" if m_pd.group(1) else "")
        m_num = re.match(r"^(.*\D)\s+(\d{1,2})$", b)
        if m_num:
            return f"{m_num.group(1).strip()} {m_num.group(2)}"
        conhecidos = {normalizar(n): n for n in (self.perfil.personagens if self.perfil else {})}
        n = normalizar(b)
        if n not in conhecidos and n in getattr(self, "palavras_vistas", {}) and b[:1].isupper():
            return b                       # escrito igual no meio de uma fala: e nome de verdade
        from .identidade import nomes_proprios
        lista_nomes = nomes_proprios()
        if n in conhecidos and (n in lista_nomes or len(n) < 4):
            return conhecidos[n]
        if n in conhecidos:
            # personagem que ficou salvo com o nome estragado ("Allhur"): se parece muito com um
            # nome proprio da lista, usa o certo daqui pra frente
            lista = [x for x in lista_nomes if len(x) >= 3 and x[:1] == n[:1] and abs(len(x) - len(n)) <= 1]
            perto = sorted(((difflib.SequenceMatcher(None, n, x).ratio(), x) for x in lista), reverse=True)
            if perto and perto[0][0] >= 0.66 and (len(perto) == 1 or perto[0][0] - perto[1][0] >= 0.1):
                return perto[0][1].title()
            return conhecidos[n]
        if n not in conhecidos and n in lista_nomes and len(n) >= 4:
            return b[:1].upper() + b[1:]   # nome proprio certo ("Lenny") nao vira outro parecido ("Leon")
        # erro de OCR no nome ("Dulch", "Ljutch" -> Dutch; "Anhur" -> Arthur): 1o os personagens
        # que ja apareceram, depois a lista de nomes proprios
        perto = difflib.get_close_matches(n, list(conhecidos), n=1, cutoff=0.72)
        if perto:
            return conhecidos[perto[0]]
        def inicial(x):
            return "l" if x[:1] in "il1" else x[:1]

        def forma(x):
            # letras que o OCR confunde viram a mesma ("Aflhnr" ~ "Arthur": t/f/l/i -> l, u/n -> n)
            return x.translate(str.maketrans({"t": "l", "f": "l", "i": "l", "1": "l", "j": "l",
                                              "u": "n", "h": "n", "r": "n", "c": "e", "o": "e", "v": "y"}))
        parecidos = [k for k in conhecidos if inicial(k) == inicial(n) and abs(len(k) - len(n)) <= 1
                     and (difflib.SequenceMatcher(None, k[1:], n[1:]).ratio() >= 0.5
                          or difflib.SequenceMatcher(None, forma(k), forma(n)).ratio() >= 0.8)]
        if len(parecidos) > 1:
            # varios parecidos ("Ionnv": Lenny ou Leon?): fica o de forma mais parecida, se bem na frente
            notas = sorted(((difflib.SequenceMatcher(None, forma(inicial(k) + k[1:]), forma(inicial(n) + n[1:])).ratio(), k)
                            for k in parecidos), reverse=True)
            if notas[0][0] >= 0.75 and notas[0][0] - notas[1][0] >= 0.15:
                parecidos = [notas[0][1]]
        if len(parecidos) == 1:
            diag.ev("nome_corrigido", lido=b, virou=conhecidos[parecidos[0]])
            return conhecidos[parecidos[0]]
        if len(n) >= 4:
            from .identidade import nomes_proprios
            lista = [x for x in nomes_proprios() if len(x) >= 3]
            perto = difflib.get_close_matches(n, lista, n=1, cutoff=0.72)
            if not perto and n not in lista:
                # muito estragado ("Allhur"): mesma 1a letra, tamanho quase igual e um so candidato bom
                cands = sorted(((difflib.SequenceMatcher(None, n, x).ratio(), x) for x in lista
                                if x[:1] == n[:1] and abs(len(x) - len(n)) <= 1), reverse=True)
                folga = cands[0][0] - cands[1][0] if len(cands) > 1 else 1.0
                if cands and ((cands[0][0] >= 0.62 and folga >= 0.1) or (cands[0][0] >= 0.7 and folga >= 0.07)):
                    perto = [cands[0][1]]
            if perto and perto[0] != n:
                certo = perto[0].title()
                diag.ev("nome_corrigido", lido=b, virou=certo)
                return certo
        return b

    def _olhar(self, sct, area, reg_frac):
        """Recortes da tela para o CLIP: a cena acima da caixa e o retrato ao lado do texto."""
        x, y, w, h = area
        imgs = []
        try:
            cena_alt = int(reg_frac[1] * h) if reg_frac else h
            if cena_alt > 80:
                imgs.append(capturar(sct, {"left": x, "top": y, "width": w, "height": cena_alt}))
            if reg_frac:
                rx0, ry0, ry1 = reg_frac[0] * w, reg_frac[1] * h, reg_frac[3] * h
                lado = int(min(rx0, (ry1 - ry0) * 1.6))
                if lado > 48:
                    imgs.append(capturar(sct, {"left": int(x + rx0 - lado), "top": int(y + max(0, ry0 - 0.4 * (ry1 - ry0))),
                                               "width": lado, "height": int(min(h - ry0, (ry1 - ry0) * 1.4))}))
        except Exception:
            pass
        return imgs

    def _modo_legenda(self):
        """Video (tela inteira ou janela do navegador) sem caixa marcada a mao: procura a legenda
        a cada leitura, em vez de uma regiao fixa (cada video poe a legenda numa altura)."""
        return (self.cfg.get("fonte") in ("tela", "janela", "sala") and self.cfg.get("modo_legenda", True)
                and not (self.perfil and self.perfil.dados.get("regiao_manual")))

    @staticmethod
    def _janelas_proprias(area):
        """Janelas do proprio Voz Universal POR CIMA do navegador (fracoes da area): o painel aberto
        na frente mexe (foto, textos) e esse movimento nao pode virar "video". As que estao ATRAS
        do navegador nao contam (painel maximizado atras excluia a tela toda: "sem video")."""
        out = []
        try:
            import win32gui
            import win32process
            x, y, w, h = area
            meu = os.getpid()
            frente = win32gui.GetForegroundWindow()
            if frente and win32process.GetWindowThreadProcessId(frente)[1] == meu:
                frente = None          # o painel e a janela da frente: vale ate achar o navegador

            def cada(hw, _):
                try:
                    if hw == frente:
                        return False   # daqui para baixo, tudo esta atras da janela da frente
                    if not win32gui.IsWindowVisible(hw) or win32gui.IsIconic(hw):
                        return True
                    if win32process.GetWindowThreadProcessId(hw)[1] != meu:
                        return True
                    l, t, r, b = win32gui.GetWindowRect(hw)
                    if r - l >= 200 and b - t >= 150:
                        out.append([(l - x) / w, (t - y) / h, (r - x) / w, (b - y) / h])
                except Exception:
                    pass
                return True
            win32gui.EnumWindows(cada, None)
        except Exception:
            pass
        return out

    def _tela_cheia(self):
        """Navegador em TELA CHEIA (a janela cobre o monitor todo): o video e a tela inteira, sem
        depender do movimento (cena calma ou escura nao encolhe a area da legenda)."""
        lim = self.video.limite
        return bool(lim) and lim[0] <= 0.005 and lim[1] <= 0.005 and lim[2] >= 0.995 and lim[3] >= 0.995

    def _limite_video(self, area):
        """O video esta dentro da janela da frente (fora a do proprio programa). Se ela mudar
        (tela cheia, moveu, outra janela), o detector de video recomeca."""
        exc = self._janelas_proprias(area)
        if exc != self.video.excluir:
            diag.ev("excluir_janelas", areas=[[round(v, 3) for v in e] for e in exc])
        self.video.excluir = exc
        try:
            import win32gui
            import win32process
            h = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(h)
            if pid == os.getpid():
                return
            # programa comum na frente (VS Code, Discord, WhatsApp, Explorador...): nao e jogo nem
            # video -> nao le nada ate voltar (senao le o chat/terminal que estiver aberto)
            try:
                import psutil
                exe = psutil.Process(pid).name().lower()
            except Exception:
                exe = ""
            navegadores = NAVEGADORES
            bloquear = exe in [p.lower() for p in self.cfg.get("ignorar_processos", [])] and exe not in navegadores
            bloquear = bloquear or exe.startswith("instalar voz universal")    # o proprio instalador
            if exe and not bloquear:
                self.exe_frente = exe          # de onde vem o som do jogo/video (para abafar)
                self.pid_frente = pid          # (e para ouvir a voz original)
            if bloquear != getattr(self, "frente_bloqueada", False):
                self.frente_bloqueada = bloquear
                diag.ev("frente_programa_comum" if bloquear else "frente_liberada", exe=exe)
            if bloquear:
                return
            x, y, w, hh = area
            l, t, r, b = _ret_visivel(h)
            lim = [max(0.0, (l - x) / w), max(0.0, (t - y) / hh), min(1.0, (r - x) / w), min(1.0, (b - y) / hh)]
            if lim[2] - lim[0] < 0.1 or lim[3] - lim[1] < 0.1:
                return
            novo_titulo = win32gui.GetWindowText(h) or ""
            sem_contador = re.sub(r"^\(\d+\)\s*", "", novo_titulo)       # "(257) " = notificacoes
            if sem_contador != re.sub(r"^\(\d+\)\s*", "", getattr(self, "titulo_frente", "")):
                # video novo: o YouTube mostra o NOME do video em cima da imagem por uns segundos
                self.silencio_ate = time.time() + 2.5
                diag.ev("titulo_mudou", titulo=sem_contador[:60])
                # o que foi lido logo ANTES do titulo mudar era a pagina trocando (titulo do video
                # novo por cima da imagem, dica da aba): para de falar (medido as 04:52, Zelda)
                ult = getattr(self, "_ult_fala", None)
                if getattr(self, "titulo_frente", "") and ult and time.time() - ult[2] < 2.5:
                    diag.ev("calei_troca_de_pagina", texto=ult[1][:50])
                    self.narr.calar()
                # cada video/jogo tem a sua lista de personagens (o "Arthur" de um nao e o do outro)
                if self.perfil and self._modo_legenda() and self.perfil.usar_grupo(novo_titulo):
                    log(f"🎬 Personagens de: {self.perfil.grupo}")
                    self.emitir("personagens", self.personagens())
            self.titulo_frente = novo_titulo
            if not hasattr(self, "titulos_recentes"):
                self.titulos_recentes = collections.deque(maxlen=40)
            if self.titulo_frente and (not self.titulos_recentes or self.titulos_recentes[-1][1] != self.titulo_frente):
                self.titulos_recentes.append((time.time(), self.titulo_frente))
            elif self.titulos_recentes:
                self.titulos_recentes[-1] = (time.time(), self.titulo_frente)
            if self.video.definir_limite((h, l, t, r, b), lim):
                diag.ev("janela_frente", titulo=win32gui.GetWindowText(h)[:60], limite=[round(v, 3) for v in lim])
        except Exception:
            pass

    def _onde_video(self, sct, area, agora):
        """Retangulo do video na tela (fracoes), atualizado 4x por segundo pelo movimento."""
        if self.na_sala():
            return [0.0, 0.0, 1.0, 1.0]        # o quadro do PS5 e todo video (nao tem pagina em volta)
        if agora - self.ult_video >= 0.25:
            self.ult_video = agora
            x, y, w, h = area
            self._limite_video(area)
            # JOGO (ou qualquer programa que nao e navegador) na frente: o "video" e a janela
            # inteira. Procurar o que se mexe so serve para achar o player dentro da pagina do
            # navegador; no RDR2 ele achou o meio da cena (41%-77% da altura) e a legenda, que fica
            # mais embaixo, ficou 20 s sem ser lida (14/11 09:35).
            if self._jogo_na_frente() and self.video.limite:
                if self.video.rect is not None:
                    self.video.rect = None
                    diag.ev("video", rect=None, motivo="jogo: janela inteira")
                return self.video.limite
            # navegador: acha o player pelo movimento. (Ficou depois de um "return" por engano e
            # nao rodava: lia a janela inteira do navegador, com menus e paginas junto.)
            antes = self.video.rect
            r = self.video.olhar(capturar(sct, {"left": x, "top": y, "width": w, "height": h}))
            if r and (antes is None or max(abs(a - b) for a, b in zip(r, antes)) > 0.04):
                diag.ev("video", rect=[round(v, 3) for v in r], confianca=self.video.confianca)
        if (self._jogo_na_frente() or self._tela_cheia()) and self.video.limite:
            return self.video.limite
        if self.video.rect:
            return self.video.rect
        # tela parada (jogo com a caixa de dialogo aberta, video pausado desde o comeco): depois de
        # 3 s sem movimento, le dentro da janela da frente inteira
        if self.video.limite and agora - self.video.desde > 3.0:
            return self.video.limite
        return None

    def _jogo_na_frente(self):
        if self.na_sala():
            return True
        exe = (getattr(self, "exe_frente", "") or "").lower()
        return bool(exe) and exe not in NAVEGADORES and not getattr(self, "frente_bloqueada", False)

    def _ler_legenda(self, sct, area):
        """-> (texto, nome) da legenda: dentro do video (achado pelo movimento), na parte de baixo."""
        x, y, w, h = area
        t_ini = time.time()
        v = self._onde_video(sct, area, t_ini)
        if getattr(self, "frente_bloqueada", False):
            self._nao_li("programa comum na frente")
            return "", ""              # programa comum na frente (chat, terminal): espera voltar
        if self._pagina_sem_video():
            self._nao_li("pagina inicial/lista do YouTube (nao e um video aberto)")
            return "", ""          # previas das miniaturas tocam sozinhas e os titulos eram lidos (15/11)
        if t_ini < getattr(self, "silencio_ate", 0):
            self._nao_li("video acabou de trocar")
            return "", ""              # acabou de abrir outro video: o nome dele esta em cima da imagem
        if not v:
            self._nao_li("sem video")
            # sem video na tela (nada se mexendo): nao le nada. Senao le o que estiver aberto
            # (terminal, pagina, menus) e fala coisa que nao e legenda.
            if self.status != "sem_video":
                diag.ev("sem_video")
            return "", ""
        vx0, vy0, vx1, vy1 = int(v[0] * w), int(v[1] * h), int(v[2] * w), int(v[3] * h)
        topo = vy0 + int((vy1 - vy0) * 0.45)
        base = min(h, vy1 + int((vy1 - vy0) * 0.15))
        lim = self.video.limite
        if lim:
            # nunca passa da janela do navegador: com ele MAXIMIZADO (sem tela cheia) a margem de
            # baixo caia na barra de tarefas do Windows, a area parecia "coberta por outra janela"
            # e nada era lido (15/11: "a legenda so pega em tela cheia")
            base = min(base, int(lim[3] * h))
        reg = {"left": x + vx0, "top": y + topo, "width": max(40, vx1 - vx0), "height": max(20, base - topo)}
        if not self.jan.a_vista(reg):
            self._nao_li("janela do programa por cima da legenda")
            return None, None
        if not self._da_janela_da_frente(reg):
            self._nao_li("outra janela por cima da legenda")
            return "", ""              # na troca (saindo da tela cheia) a area ja mostra outra janela
        self._nao_li(None)
        img = capturar(sct, reg)
        # video pequeno (janela): a legenda fica miuda e o OCR erra ("Dulch"): amplia antes de ler
        esc = 1.0
        if img.width < 1100:
            from PIL import ImageOps
            esc = min(3.0, 1300 / img.width)
            # ampliar com LANCZOS + cinza + contraste: o OCR separa melhor as palavras miudas
            # ("TÔ escorregando!" em vez de "Tôescorregando!") — medido num video em janela
            img = img.resize((int(img.width * esc), int(img.height * esc)), Image.LANCZOS)
            img = ImageOps.autocontrast(ImageOps.grayscale(img)).convert("RGB")
        motivos = []
        bloco = achar_legenda(img, dic=self.dic, motivos=motivos)
        alt_txt = None
        if bloco:
            bloco, alt_txt = self._segunda_leitura(img, bloco)
        ms = (time.time() - t_ini) * 1000
        self.stats["ticks"] += 1
        self.stats["ocr_ms"] += ms
        if motivos and motivos != getattr(self, "_ult_motivos", None):
            self._ult_motivos = motivos
            diag.ev("descartei", blocos=motivos[:6])
        if not bloco:
            return "", ""
        x0, y0 = min(l[1] for l in bloco), min(l[2] for l in bloco)
        x1, y1 = max(l[3] for l in bloco), max(l[4] for l in bloco)
        self.legenda_frac = [(vx0 + x0 / esc) / w, (topo + y0 / esc) / h, (vx0 + x1 / esc) / w, (topo + y1 / esc) / h]
        self._quadro = (img, (x0, y0, x1, y1))
        nome, corpo = separar_nome(bloco)          # "Swanson: A Abigail disse..." -> Swanson
        texto = limpar(" ".join(l[0] for l in corpo))
        if nome and " " in nome and self.perfil:
            # nome partido pelo OCR ("Du tch" = Dutch, 15/11): junto, e alguem conhecido?
            junto = "".join(nome.split())
            for conhecido in self.perfil.personagens:
                if normalizar(conhecido) == normalizar(junto):
                    nome = conhecido
                    break
            else:
                c = self._nome_canonico(junto)          # "I on nv" -> "Ionnv" -> Lenny
                if c in self.perfil.personagens:
                    nome = c
        # ...a nao ser que seja quem acabou de falar ("Tavish:" de novo na legenda seguinte, 15/11)
        ant = getattr(self, "_ult_fala", None)
        recem = bool(nome and len(nome) > 2 and ant and ant[0] and time.time() - ant[2] <= 6
                     and ant[0] in (nome, self._nome_canonico(nome)))
        if nome and not recem and len(nome) > 3:
            # funcao/papel ("Guarda: não fiquei sabendo...") ou comeco de alguem conhecido ("Guarda 3")
            from .identidade import _NEUTRAS, _NOME, nomes_proprios
            p0 = normalizar(nome.split()[0])
            recem = (p0 in _NEUTRAS or nome.split()[0].lower() in _NOME
                     or nome.split()[0].lower() in nomes_proprios()      # "Lenny: tão vendendo..." (15/11)
                     or any(normalizar(k).startswith(normalizar(nome) + " ") for k in (self.perfil.personagens if self.perfil else {})))
        if nome and texto[:1].islower() and not recem and (len(nome) <= 2 or not (self.perfil and nome in self.perfil.personagens)):
            # "nome" NOVO seguido de texto em minuscula: palavra partida pelo OCR, nao quem fala
            # ("Alé" + "de ex-colega de cela do Brian." era "Além de ex-colega..."; "Bo" + "o Brian era..."
            # era "Bom, o Brian era...": nome de 2 letras vale mesmo se ja estiver na lista, 15/11)
            diag.ev("nome_descartado", nome=nome, texto=texto[:40])
            texto, nome = limpar(nome + " " + texto), ""
        if not nome:
            # nome e fala vieram como linhas separadas (alturas um pouco diferentes): procura o
            # "Nome:" de novo no texto ja montado ("Jimmy: Oh, concordo...")
            n2, c2 = separar_nome([(texto, 0, 0, 1, 1)])
            if n2:
                nome, texto = n2, limpar(" ".join(l[0] for l in c2))
        if not nome and self.perfil and self.perfil.personagens:
            # "flosea: Todos os dias." (H lido como "fl", sem maiuscula): vale se for parecido com
            # um personagem que JA apareceu
            m3 = re.match(r"^\s*([a-zà-ÿ][\wÀ-ÿ]{2,15})\s*:\s+(.+)$", texto)
            if m3 and not (self.dic.ok and self.dic.conhece(m3.group(1))):
                cand = self._nome_canonico(m3.group(1).title())
                if cand in self.perfil.personagens:
                    nome, texto = cand, m3.group(2)
                    diag.ev("nome_corrigido", lido=m3.group(1), virou=cand)
        if not nome:
            nome, texto = self._nome_sem_dois_pontos(texto)
        # o titulo do video (aparece na barra da janela do navegador) nao e legenda
        tudo = normalizar((nome + " " if nome else "") + texto)

        palavras = [normalizar(p) for p in ((nome + " ") if nome else "").split() + texto.split()]
        palavras = [p for p in palavras if len(p) >= 3]

        def eh_titulo(tit_bruto):
            tit = normalizar(tit_bruto)
            if not tit or len(tudo) < 8:
                return False
            if tudo in tit or semelhanca(tudo, tit[:len(tudo) + 5]) >= 0.8 or difflib.SequenceMatcher(
                    None, tudo, tit).find_longest_match(0, len(tudo), 0, len(tit)).size >= 0.8 * len(tudo):
                return True
            # pedacos cortados pelo OCR ("...em m comentarios PTBR"): 70% das palavras estao no titulo
            if len(palavras) >= 3:
                pt = [normalizar(p) for p in tit_bruto.split() if len(normalizar(p)) >= 3]
                achou = sum(1 for p in palavras if p in pt or any(semelhanca(p, q) >= 0.8 for q in pt))
                return achou / len(palavras) >= 0.7
            return False

        # compara com os titulos dos ultimos 30 s (enquanto a pagina carrega, o titulo da janela
        # ainda e provisorio e o nome do video ja aparece em cima da imagem)
        agora_t = time.time()
        if any(eh_titulo(tt) for t0, tt in getattr(self, "titulos_recentes", []) if agora_t - t0 <= 30):
            if not getattr(self, "_avisou_titulo", False):
                self._avisou_titulo = True
                diag.ev("ignorado_titulo", texto=texto[:60])
            return "", ""
        if not self.cfg.get("ler_dicas", False):
            from .tela import DICA_CONTROLE, eh_barra_de_comandos
            if DICA_CONTROLE.match(texto.lstrip("-–—•'\" ")) or eh_barra_de_comandos(texto):
                self._dica_ate = time.time() + 3.0
                return "", ""
            # 2a linha de uma dica que acabou de ser barrada ("Segure X" / "para remover a sela.")
            # (ou um pedaco curto dela que o OCR leu sozinho: "una busca")
            if (texto[:1].islower() or len(texto.split()) <= 3) and time.time() < getattr(self, "_dica_ate", 0):
                return "", ""
        if self.dic.ok and texto and alt_txt:
            # 2a leitura so com o texto claro isolado do fundo: fica, em cada trecho, com a leitura
            # que faz mais sentido ("sando knscaras" -> "usando mascaras" no GTA V, 14/11).
            # 3a leitura (branco com contorno escuro): entra como candidata inteira e tambem fundida;
            # fica a versao com mais palavras de verdade, se for a MESMA frase (15/11: na cena do
            # espelho, 17 de 22 leituras perfeitas contra 1; em 120 recortes variados, 18 melhores e 0 piores)
            try:
                s_txt, c_txt = alt_txt if isinstance(alt_txt, tuple) else (alt_txt, None)
                def sem_nome(t):
                    if not t:
                        return ""
                    _, c = separar_nome([(t, 0, 0, 1, 1)])
                    return limpar(" ".join(l[0] for l in c))
                s_txt, c_txt = sem_nome(s_txt), sem_nome(c_txt)
                cands = [self.dic.fundir(texto, s_txt) if s_txt else texto]
                if c_txt:
                    cands += [self.dic.fundir(texto, c_txt), c_txt]
                base = normalizar(texto)
                cands = [x for x in cands if semelhanca(normalizar(x), base) >= 0.6] or cands[:1]
                from .leitor import Leitor
                texto = max(cands, key=Leitor._nota)
            except Exception as e:
                diag.ev("fusao_erro", erro=repr(e)[:120])
        if self.dic.ok and texto:
            texto = self.dic.consertar(texto)
        if not texto or (len(texto.split()) < 2 and (texto[-1:] not in FIM or len(re.findall(r"[^\W\d_]", texto)) < 3)):
            return "", nome
        return texto, nome

    def _segunda_leitura(self, img, bloco):
        """Le de novo so o recorte da legenda, com o texto claro em preto sobre branco (sem o fundo
        do video) e margem larga dos lados (o OCR perdia a 1a letra: "osso" por "Nosso").
        -> (bloco sem as linhas que nao sao texto claro, texto da 2a leitura).
        Linha que so aparece na leitura colorida (bilhete rosa "ALARME" do cenario, colado embaixo
        da legenda) sai. Guarda o resultado enquanto a leitura colorida nao muda (~10-15 ms)."""
        chave = tuple(l[0] for l in bloco)
        cache = getattr(self, "_cache_2a", None)
        if cache and cache[0] == chave:
            return cache[1], cache[2]
        try:
            x0, y0 = min(l[1] for l in bloco), min(l[2] for l in bloco)
            x1, y1 = max(l[3] for l in bloco), max(l[4] for l in bloco)
            alt_lin = max(8, max(l[4] - l[2] for l in bloco))
            mx, my = int(2 * alt_lin), int(0.5 * alt_lin)
            recorte = img.crop((max(0, int(x0) - mx), max(0, int(y0) - my),
                                min(img.width, int(x1) + mx), min(img.height, int(y1) + my)))
            rec = suavizar(recorte)
            alt_l = em_ordem([l for l in ocr_linhas(rec) if len(l[0].strip()) >= 2])
            alt_txt = limpar(" ".join(l[0] for l in alt_l))
            # 3a leitura: so o branco COM contorno escuro (tira a luz do cenario atras da legenda)
            cont_txt = limpar(" ".join(l[0] for l in em_ordem(
                [l for l in ocr_linhas(isolar_contorno(recorte)) if len(l[0].strip()) >= 2])))
            novo = list(bloco)
            if alt_l and len(bloco) >= 2:
                def tem_par(l):
                    a = normalizar(l[0])
                    return any(semelhanca(a, normalizar(k[0])) >= 0.5 or (a and a in normalizar(k[0]))
                               for k in alt_l)
                pares = [tem_par(l) for l in bloco]
                # so tira linha curta (1-2 palavras) e so se a 2a leitura achou a legenda de verdade
                # (legenda amarela some na 2a leitura: ai nao tira nada)
                if any(pares):
                    novo = [l for l, ok in zip(bloco, pares) if ok or len(l[0].split()) > 2]
                    if len(novo) < len(bloco):
                        diag.ev("linha_nao_legenda", tiradas=[l[0][:30] for l in bloco if l not in novo])
            res = (novo or list(bloco), (alt_txt or None, cont_txt or None))
        except Exception as e:
            diag.ev("segunda_leitura_erro", erro=repr(e)[:120])
            res = (bloco, None)
        self._cache_2a = (chave, res[0], res[1])
        return res

    @staticmethod
    def _mesmo_texto(fala, lido):
        """A fala saiu desta leitura? (lido=None: sem registro, aceita)"""
        if lido is None:
            return True
        a, b = normalizar(fala), normalizar(lido)
        if not a or not b:
            return False
        return a[:14] in b or b[:14] in a or difflib.SequenceMatcher(None, a[:40], b[:40]).ratio() >= 0.6

    def _video_com_nomes(self):
        """Quase toda fala deste video vem com "Nome:" (filme do RDR2)."""
        recentes = list(getattr(self, "_com_nome", None) or [])
        if len(recentes) >= 3:
            return sum(recentes) >= 0.6 * len(recentes)
        pers = (self.perfil.personagens if getattr(self, "perfil", None) else {}) or {}
        return sum(1 for f in pers.values() if f.get("falas", 0) >= 3 and not f.get("anonimo")) >= 5

    def _lixo_sem_nome(self, fala):
        """Legenda sem nome, num video com nomes, com metade das palavras inexistentes: objetivo lido
        num fundo claro ("rogu Idnr de recompensas.", 15/11). Nao e fala de ninguem."""
        if not self._video_com_nomes():
            return False
        from .leitor import Leitor
        pal = [w for w in re.findall(r"[^\W\d_]+", fala) if len(w) >= 3]
        if len(pal) < 2:
            return False
        ruins = sum(1 for w in pal if not Leitor._conhecida(w) and not w[:1].isupper())
        ruins += sum(1 for w in pal if w[:1].isupper() and not Leitor._conhecida(w) and w != pal[0] and len(w) <= 4)
        return ruins >= 0.5 * len(pal)

    def _eh_objetivo(self, fala):
        """Video em que quase toda fala vem com "Nome:" (filme do RDR2): uma legenda SEM nome que
        comeca com verbo de objetivo ("Ajude o Charles e o Bill", "Fuja pelo buraco...", "Entre em
        cobertura perto do Tio") e o objetivo da missao, nao fala (15/11)."""
        from .tela import _VERBOS_OBJETIVO
        recentes = list(getattr(self, "_com_nome", None) or [])   # (objetivos nao entram nela)
        if not recentes or len(recentes) < 3:
            # logo ao abrir o programa ainda nao ha historico: vale o perfil do video (muitos
            # personagens com nome que ja falaram bastante = legenda com "Nome:")
            pers = (self.perfil.personagens if getattr(self, "perfil", None) else {}) or {}
            com_nome = sum(1 for f in pers.values() if f.get("falas", 0) >= 3 and not f.get("anonimo"))
            if com_nome < 5 or (recentes and sum(recentes) < len(recentes)):
                return False
        elif sum(recentes) < 0.6 * len(recentes):
            return False
        p = re.sub(r"[^\wÀ-ÿ-]", "", (fala.split() or [""])[0]).lower()
        # "Aproxime- do ladrão" (o OCR comeu o "se"): vale o verbo antes do hifen
        return ((p in _VERBOS_OBJETIVO or p.split("-")[0] in _VERBOS_OBJETIVO)
                and not fala.rstrip().endswith(("?", "!")))

    def _pagina_sem_video(self):
        """Navegador na frente mostrando a pagina inicial/lista do YouTube: o titulo da janela e so
        "YouTube" (num video aberto e "<titulo do video> - YouTube")."""
        if self.na_sala() or (getattr(self, "exe_frente", "") or "").lower() not in NAVEGADORES:
            return False
        t = re.sub(r"^\(\d+\)\s*", "", getattr(self, "titulo_frente", "") or "").strip()
        t = re.sub(r"\s+-\s+(Google Chrome|Microsoft.? Edge|Mozilla Firefox|Opera|Brave)$", "", t)
        return t in ("YouTube", "Inscrições - YouTube", "Assinaturas - YouTube", "Histórico - YouTube")

    def _nao_li(self, motivo):
        """Registra POR QUE nao leu a legenda (so quando o motivo muda): sem isso, "0 leituras"
        nao dizia nada (15/11: ficou minutos sem ler e o registro so dizia "lendo")."""
        if motivo != getattr(self, "_ult_nao_li", None):
            self._ult_nao_li = motivo
            diag.ev("nao_li" if motivo else "voltou_a_ler", motivo=motivo)

    def _da_janela_da_frente(self, reg):
        """O que aparece no meio e nos cantos de baixo da area e mesmo da janela da frente?"""
        if self.na_sala():
            return True
        try:
            import win32gui
            ass = self.video.assinatura
            if not ass:
                return True
            hfrente = ass[0]
            x0, y0 = reg["left"] + 4, reg["top"] + 4
            x1, y1 = reg["left"] + reg["width"] - 5, reg["top"] + reg["height"] - 5
            for px, py in (((x0 + x1) // 2, (y0 + y1) // 2), (x0, y1), (x1, y1)):
                if win32gui.GetAncestor(win32gui.WindowFromPoint((int(px), int(py))), 2) != hfrente:
                    return False
            return True
        except Exception:
            return True

    def _nome_na_fala(self, fala):
        """Ultima rede antes de falar: nome de personagem CONHECIDO que sobrou no comeco da fala
        (video pequeno): "susan; eu vou...", "lilly: Obrigada", "A: thur: Tá, vamos lá.",
        "Arthur agente matou..." (este so se o Arthur acabou de ser lido como quem fala).
        -> (nome, fala sem o nome) ou ("", fala)"""
        if not self.perfil or not self.perfil.personagens:
            return "", fala
        conhecidos = self.perfil.personagens

        def canonico(bruto):
            from .tela import consertar_prefixo
            b = consertar_prefixo((bruto.strip() + ": x",))[0].split(":")[0].strip()
            b = b[:1].upper() + b[1:]
            if re.match(r"^[lI|]", bruto.strip()) and len(b) >= 4:
                b2 = "T" + b[1:]                      # "lilly" -> "Tilly"
                if b2 in conhecidos:
                    return b2
            c = self._nome_canonico(b)
            # ...ou o nome que acabou de ser lido (personagem novo ainda sem ficha: "Freguês 6")
            return c if (c in conhecidos or c in self.ult_nomes) else ""

        # sujeira antes do nome: "B: Freguês 6: São os Saqueadores..." (15/11)
        fala = re.sub(r"^\W{0,3}\S{1,3}:\s+(?=[A-ZÀ-Ý][^:]{2,25}:\s)", "", fala)

        m = re.match(r"^\W{0,3}(\S{1,3}:\s?\S{2,12}|[^\s:;]{3,20}(?:\s(?:[A-ZÀ-Ý][^\s:;]{1,15}|\d{1,2}))?)\s*[:;]\s+(\S.*)$", fala)
        if m:
            nome = canonico(m.group(1).replace(": ", "").replace(":", ""))
            if nome:
                diag.ev("nome_na_fala", lido=m.group(1), nome=nome)
                return nome, m.group(2)
        m = re.match(r"^([A-ZÀ-Ý][a-zà-ÿ]{2,15})\s+([a-zà-ÿ].*)$", fala)
        if m and m.group(1) in conhecidos:
            agora = time.time()
            if any(n == m.group(1) and agora - tt <= 1.5 for n, tt in zip(self.ult_nomes, self.ult_nomes_t)):
                diag.ev("nome_na_fala", lido=m.group(1), nome=m.group(1))
                return m.group(1), m.group(2)
        return "", fala

    def _exe_do_som(self):
        """Programa cujo som e abafado: o jogo escolhido ou, no modo tela/video, a janela da frente."""
        if self.na_sala():
            return ""          # quem abaixa o PS5 e a propria sala (Abafador.sala_cb)
        e = (self.jan.exe or "").lower()
        if e.endswith(".exe"):
            return e
        return self.exe_frente

    def _pid_do_som(self):
        """Processo cujo som e ouvido (voz original): a janela do jogo ou, no modo tela/video, a da frente."""
        if self.na_sala():
            return -1          # o som do PS5 chega pela sala (Gravador.empurrar)
        try:
            e = (self.jan.exe or "").lower()
            if e.endswith(".exe") and self.jan.raiz:
                import win32process
                return win32process.GetWindowThreadProcessId(self.jan.raiz)[1]
        except Exception:
            pass
        return self.pid_frente

    def _previa(self, texto, nome):
        """1a leitura de uma legenda nova: ja pede a voz online das frases completas, nas vozes que
        ela pode ter (o leitor ainda vai esperar ~0,2 s para confirmar o texto). Quando a fala sair
        igual, o som ja esta chegando (~0,2 s a menos de atraso)."""
        if not texto or not self.elenco:
            return
        try:
            from .leitor import dividir_frases, hesitacao
            completas = [f for f in dividir_frases(texto)
                         if f.rstrip("\"')")[-1:] in ".!?…" and len(normalizar(f)) >= 3]
            if not completas:
                return
            quem = self._nome_canonico(nome) if nome else ""
            f = self.elenco.ficha(quem) if quem else None
            voz, tom, vel, cat = ((f["voz"], f.get("tom", 1.0), f.get("vel", 1.0), f.get("categoria") or "")
                                  if f and f.get("voz") else ("narrador", 1.0, 1.0, "narrador" if not quem else ""))
            for fr in completas[:3]:
                self.narr.previa(fr, voz, tom, vel, quem if f else "", cat)
        except Exception as e:
            diag.ev("previa_erro", erro=repr(e)[:160])

    def _candidatas_voz(self, texto, voz, tom, vel, quem, cat):
        """Vozes que esta fala PODE ter, antes da escuta decidir: a voz da ficha e, se for fala sem
        nome, tambem a do "Homem (pela voz)" e a da "Mulher (pela voz)". O narrador ja as pede
        a voz da Microsoft enquanto a escuta ouve (a espera da escuta deixa de somar no atraso)."""
        if not self.elenco or not self.perfil or not (cat == "narrador" or not quem):
            return [(voz, tom, vel)]
        # fala sem nome: a voz da ULTIMA decisao primeiro, depois a do outro sexo. A do narrador so
        # entra se a ultima foi narrador (menos pedidos: com 3 vozes x 3 tentativas a Microsoft
        # parou de responder por 20 s, 14/11)
        pers = self.perfil.personagens
        ult = getattr(self, "_ult_anonimo", "Homem (pela voz)")
        out = []
        if ult == "narrador":
            out.append((voz, tom, vel))
        for nome in sorted(("Homem (pela voz)", "Mulher (pela voz)"), key=lambda n: n != ult):
            f = pers.get(nome)
            if f and f.get("voz"):
                out.append((f["voz"], f.get("tom", 1.0), f.get("vel", 1.0)))
        return out[:2] or [(voz, tom, vel)]

    def _resolver_voz(self, pag, texto, voz, tom, vel, quem, cat, visto):
        """Chamado pelo narrador logo antes de gerar cada fala: ouve a voz original do jogo.
        - fala SEM nome (legenda): se a voz for claramente de homem/mulher/crianca, usa a ficha
          "Homem (pela voz)" etc. em vez do narrador;
        - personagem com nome ainda novo: soma a voz na ficha antes de falar (escolhe a voz certa ja
          na 1a fala); depois disso so ouve em segundo plano, para confirmar."""
        esc = self.escuta
        if not esc or not esc.ativa or not self.cfg.get("escuta", True) or not self.elenco:
            return None
        if pag in self._voz_pag:
            return self._voz_pag[pag]
        if pag in getattr(self, "_oposto", {}) and (cat == "narrador" or not quem):
            r = self._voz_oposta(pag)
            if r:
                self._voz_pag[pag] = r
                diag.ev("escuta_fala", texto=texto[:60], quem="", decisao=r[3] + " (2a pessoa do dialogo)")
                return r
        if len(self._voz_pag) > 50:
            for k in sorted(self._voz_pag)[:25]:
                self._voz_pag.pop(k, None)
        espera = float(self.cfg.get("escuta_espera_s", 1.0))
        r = None
        if cat == "narrador" or not quem:
            res = esc.ouvir(visto, esperar=max(0.0, visto + espera - time.time()))
            extra = float(self.cfg.get("escuta_extra_s", 0.6))
            if not res["sexo"] and res.get("rms", 0) > 0.005 and res.get("voz_s", 0) < 0.3 and extra > 0:
                # ouviu pouca voz ainda (a fala original comecou depois da legenda): escuta mais um
                # pouco antes de cair na voz do narrador (15/11: 79 falas sem decisao no trailer)
                res2 = esc.ouvir(visto, esperar=max(0.0, visto + espera + extra - time.time()))
                if res2["sexo"]:
                    res2["extra"] = True
                    res = res2
            decisao = None
            if res["sexo"] and (res.get("cedo") or res["certeza"] >= float(self.cfg.get("escuta_certeza", 0.35))):
                from .identidade import categoria_de
                categoria = categoria_de(res["sexo"], "crianca" if res["crianca"] >= 0.6 else "adulto")
                nome, f = self.elenco.anonimo(categoria)
                self._ult_anonimo = nome
                r = (f["voz"], f.get("tom", 1.0), f.get("vel", 1.0), nome, f["categoria"])
                decisao = nome
            if not decisao:
                # a escuta nao decidiu: o TEXTO pode dizer quem fala ("Eu tava ocupada." -> mulher;
                # 15/11 saiu na voz do Jason)
                from .identidade import categoria_de, pistas_texto
                sexos = {p[1] for p in pistas_texto(texto) if p[0] == "s"}
                if len(sexos) == 1:
                    nome, f = self.elenco.anonimo(categoria_de(sexos.pop(), "adulto"))
                    self._ult_anonimo = nome
                    r = (f["voz"], f.get("tom", 1.0), f.get("vel", 1.0), nome, f["categoria"])
                    decisao = nome + " (pelo texto)"
            if (not decisao and res.get("sexo") and res.get("certeza", 0) >= float(self.cfg.get("escuta_palpite", 0.2))
                    and abs(res.get("p_m", 0.5) - 0.5) >= 0.2):
                # a escuta nao teve certeza, mas PENDE claramente para um lado: melhor que a voz
                # padrao (15/11: "Lantejoulas?", "Gustavo, essa é a Lucia." pendiam p/ mulher e
                # saiam na voz do Jason; os palpites de 0,2+ conferidos batiam com quem falava)
                nome, f = self.elenco.anonimo(categoria_de(res["sexo"], "crianca" if res.get("crianca", 0) >= 0.6 else "adulto"))
                self._ult_anonimo = nome
                r = (f["voz"], f.get("tom", 1.0), f.get("vel", 1.0), nome, f["categoria"])
                decisao = nome + " (palpite da voz)"
            if not decisao and self.cfg.get("voz_unica") == "dupla":
                # chamou o OUTRO pelo nome: "Jay, segura aí!" -> quem fala e a Lucia; "..., Lu!" -> Jason
                # (15/11: cena de tiroteio, a escuta nao ouvia a voz no meio do barulho)
                sx = self._sexo_de_quem_e_chamado(texto)
                if sx:
                    nome, f = self.elenco.anonimo(categoria_de("f" if sx == "m" else "m", "adulto"))
                    self._ult_anonimo = nome
                    r = (f["voz"], f.get("tom", 1.0), f.get("vel", 1.0), nome, f["categoria"])
                    decisao = nome + " (chamou o outro pelo nome)"
            if not decisao:
                self._ult_anonimo = "narrador"
            diag.ev("escuta_fala", texto=texto[:60], quem="", decisao=decisao, **res)
        else:
            f = self.elenco.ficha(quem)
            if f and not f.get("fixo") and not f.get("anonimo") and f.get("ouvidas", 0) < 6:
                if f.get("falas", 0) <= 2:
                    res = esc.ouvir(visto, esperar=max(0.0, visto + espera - time.time()))
                    f = self.elenco.ouvir(quem, res) or f
                    r = (f["voz"], f.get("tom", 1.0), f.get("vel", 1.0), quem, f["categoria"])
                    diag.ev("escuta_fala", texto=texto[:60], quem=quem, decisao=f["categoria"], **res)
                else:
                    def depois():
                        time.sleep(max(0.0, visto + 3.0 - time.time()))
                        res2 = esc.ouvir(visto, ate=visto + 3.0)
                        diag.ev("escuta_fala", texto=texto[:60], quem=quem, decisao="(fundo)", **res2)
                        self.elenco.ouvir(quem, res2)
                    threading.Thread(target=depois, daemon=True).start()
        self._voz_pag[pag] = r
        if r:
            self.emitir("personagens", self.personagens())
        return r

    _RE_DIALOGO = re.compile(r"^\s*[-–—]\s*(\S.*?[.!?…]\"?)\s+[-–—]\s*(\S.+)$")

    def _separar_dialogo(self, falas):
        """Legenda com DUAS pessoas ("- Isso. - E você é o motorista?"): vira duas falas, a 2a com a
        voz oposta a da 1a (antes saia tudo numa voz so, 15/11)."""
        for fala, nova, visto in falas:
            m = self._RE_DIALOGO.match(fala)
            if not m or self.nome_pagina and not nova:
                yield fala, nova, visto
                continue
            diag.ev("dialogo_separado", a=m.group(1)[:40], b=m.group(2)[:40])
            self._parte1, self._parte1_pag = True, None
            yield m.group(1), True, visto
            self._parte1 = False
            self._parte2_de = self._parte1_pag
            yield m.group(2), True, visto + 0.3
            self._parte2_de = None

    def _voz_oposta(self, pag):
        """Voz da 2a pessoa de uma legenda "- A. - B.": o sexo oposto ao da 1a, se a 1a foi decidida."""
        r1 = self._voz_pag.get(getattr(self, "_oposto", {}).get(pag))
        if not r1:
            return None
        oposto = {"homem": "mulher", "mulher": "homem", "menino": "menina", "menina": "menino",
                  "idoso": "idosa", "idosa": "idoso"}.get(r1[4])
        if not oposto:
            return None
        nome, f = self.elenco.anonimo(oposto)
        return (f["voz"], f.get("tom", 1.0), f.get("vel", 1.0), nome, f["categoria"])

    @staticmethod
    def _sexo_de_quem_e_chamado(texto):
        """Modo "Lucia + Jason": a fala chama a Lucia ou o Jason pelo nome/apelido ("Jay, ...",
        "..., Lu!", "Você é profissional, Lucia, hein?") -> sexo de quem e CHAMADO, senao None."""
        from .identidade import nomes_proprios
        nomes = nomes_proprios()
        achados = set()
        for m in re.finditer(r"(?:^|[,.!?…]\s*)([A-ZÀ-Ý][a-zà-ÿ]{1,9})\s*(?=[,!?.…]|$)", texto.strip()):
            alvo = m.group(1).lower()
            for vid, sx in (("lucia", "f"), ("jason", "m")):
                if alvo[:2] == vid[:2] and nomes.get(alvo, sx) == sx and (vid.startswith(alvo) or alvo in nomes):
                    achados.add(sx)
        return achados.pop() if len(achados) == 1 else None

    def _nomes_no_texto(self, fala):
        """Nome de personagem conhecido que perdeu a 1a letra no OCR: "Siga o utch." -> "Siga o Dutch."
        So troca palavra que NAO existe e e exatamente o nome sem a inicial (4+ letras)."""
        if not self.perfil or not self.perfil.personagens:
            return fala
        sem_inicial = {}
        for n in self.perfil.personagens:
            if " " not in n and len(n) >= 5:
                sem_inicial.setdefault(n[1:].lower(), set()).add(n)

        def troca(m):
            w = m.group(0)
            cands = sem_inicial.get(w.lower())
            if not cands or len(cands) != 1 or (self.dic.ok and self.dic.conhece(w.lower())):
                return w
            certo = next(iter(cands))
            diag.ev("nome_no_texto", lido=w, virou=certo)
            return certo
        return re.sub(r"(?<![\wÀ-ÿ])[a-zà-ÿ]{4,}(?![\wÀ-ÿ])", troca, fala)

    def _nome_sem_dois_pontos(self, texto):
        """ "Aflhur E eles?": o OCR comeu os ':' e estragou o nome. Se a 1a palavra nao existe no
        portugues, e parecida com um personagem JA CONHECIDO e a frase segue com Maiuscula, e ele."""
        ps = texto.split(maxsplit=1)
        if len(ps) < 2 or not self.perfil or not self.perfil.personagens:
            return "", texto
        bruto, resto = ps[0], ps[1]
        if not resto[:1].isupper():
            return "", texto
        # "Aê! Cracudos!": exclamacao/pergunta no fim da 1a palavra e fala, nao nome sem ':'
        if bruto.rstrip("\"')")[-1:] in ("!", "?", "…") or len(re.sub(r"[^\wÀ-ÿ]", "", bruto)) < 3:
            return "", texto
        # "Arthur. Pois é.": nome CERTO com "." no lugar de ":" -> so se a mesma legenda acabou de
        # ser lida com esse nome (senao e alguem chamando o Arthur: "Arthur. Vem cá.")
        if bruto.endswith((".", ",")) and bruto[:-1] in self.perfil.personagens:
            agora = time.time()
            if any(n == bruto[:-1] and agora - tt <= 1.5 for n, tt in zip(self.ult_nomes, self.ult_nomes_t)):
                diag.ev("nome_com_ponto", nome=bruto[:-1])
                return bruto[:-1], resto
            return "", texto
        # comeco estragado ("l)tltchl O Hosea...": "l)" e "D" e o "l" do fim era o ":")
        from .tela import consertar_prefixo
        p0 = consertar_prefixo((bruto.rstrip(".,;:í(") + ": x",))[0].split(":")[0].strip()
        if len(p0) < 3 or not p0[:1].isupper():
            return "", texto
        tentativas = [p0] + ([p0[:-1]] if len(p0) > 4 and p0[-1] in "lI|1" else [])
        for t in tentativas:
            if self.dic.ok and self.dic.conhece(t.lower()):
                return "", texto
            cand = self._nome_canonico(t)
            if cand in self.perfil.personagens and cand.lower() != t.lower():
                diag.ev("nome_sem_dois_pontos", lido=bruto, virou=cand)
                return cand, resto
        return "", texto

    def _ler(self, sct, area):
        """-> (texto, nome) da caixa de dialogo agora."""
        reg = regiao_em_tela(area, self.perfil.regiao)
        if not self.jan.a_vista(reg):
            return None, None
        img = capturar(sct, reg)
        linhas = [l for l in ocr_linhas(img) if len(re.findall(r"[^\W_]", l[0])) >= 2]
        if not linhas:
            return "", ""
        nome, corpo = separar_nome(em_ordem(linhas), self.perfil.dados.get("zona_nome"), img.size)
        texto = limpar(" ".join(l[0] for l in corpo))
        if not texto:
            return "", nome
        if self.dic.ok and self.dic.desconhecidas(self.dic.consertar(texto)):
            alt_l = [l for l in ocr_linhas(suavizar(img)) if len(re.findall(r"[^\W_]", l[0])) >= 2]
            if alt_l:
                _, corpo2 = separar_nome(em_ordem(alt_l), self.perfil.dados.get("zona_nome"), img.size)
                alt = limpar(" ".join(l[0] for l in corpo2))
                if alt:
                    texto = self.dic.fundir(texto, alt)
        if self.dic.ok:
            texto = self.dic.consertar(texto)
        if len(texto.split()) < 2 and texto[-1:] not in FIM:
            return "", nome        # palavra solta sem pontuacao = botao/menu
        return texto, nome

    def _salvar_quadro(self, texto):
        """Guarda o recorte da legenda lida (diagnostico\\quadros) para conferir o OCR depois."""
        try:
            import os
            from .diagnostico import PASTA
            img, (x0, y0, x1, y1) = self._quadro
            pasta = os.path.join(PASTA, "quadros", os.path.basename(diag.arquivo or "sessao").replace(".jsonl", ""))
            os.makedirs(pasta, exist_ok=True)
            self._n_quadros = getattr(self, "_n_quadros", 0) + 1
            if self._n_quadros % 100 == 0:            # limpa os antigos so de vez em quando
                arqs = sorted(os.listdir(pasta))
                for velho in arqs[:-1500]:
                    os.remove(os.path.join(pasta, velho))
            m = 12
            rec = img.crop((max(0, int(x0) - m), max(0, int(y0) - m), min(img.width, int(x1) + m), min(img.height, int(y1) + m)))
            nome = f"{time.time() - diag.t0:08.2f}.jpg"
            rec.save(os.path.join(pasta, nome), quality=80)
            diag.ev("quadro", arquivo=nome, texto=texto[:80])
        except Exception:
            pass
        self._quadro = None

    @staticmethod
    def _placa_30s():
        try:
            from .util import medidor_placa
            return medidor_placa().resumo(30)
        except Exception:
            return {}

    def _sistema(self):
        """A cada 30 s: memoria, placa de video, processador e o custo de cada leitura."""
        try:
            import psutil
            from .util import memoria_livre
            ram, vram = memoria_livre()
            p = psutil.Process()
            st = self.stats
            diag.ev("sistema", ram_livre_gb=round(ram or 0, 1), vram_livre_gb=round(vram or 0, 1),
                    cpu_total=psutil.cpu_percent(None), programa_mb=round(p.memory_info().rss / 2 ** 20), privada_mb=round(getattr(p.memory_info(), "private", 0) / 2 ** 20),
                    leituras=st["ticks"], ocr_medio_ms=round(st["ocr_ms"] / max(1, st["ticks"])),
                    leituras_por_s=round(st["ticks"] / max(1, time.time() - st["desde"]), 1),
                    pedacos=self.cfg.get("xtts_pedacos"), status=self.status, **self._placa_30s())
            self.stats = {"ticks": 0, "ocr_ms": 0.0, "desde": time.time()}
        except Exception:
            pass
        # RAM quase no fim (jogo pesado): solta a visao, que e o opcional mais pesado (~0,7 GB). Ela
        # volta sozinha quando sobrar memoria (preparar() so carrega com 3 GB livres).
        try:
            limite = float(self.cfg.get("ram_minima_gb", 1.0))
            if self.visao and self.visao.pronta and ram is not None and ram < limite and self.visao.descarregar():
                log(f"🧹 Pouca RAM livre ({ram:.1f} GB): desliguei a visão por enquanto para a voz não engasgar.",
                    tipo="aviso")
                diag.ev("visao_descarregada", ram_livre_gb=round(ram, 2))
                self.narr.aviso("motor", {})
        except Exception:
            pass
        # voz clonada escolhida mas nao carregou por falta de memoria: tenta de novo (o jogo
        # costuma liberar memoria depois de carregar). Antes so tentava ao mexer no painel.
        if self.cfg.get("motor") == "clonada" and self.narr.estado_clonada == "sem_memoria":
            self.narr.ligar_clonada(avisar=False)

    def rodar(self):
        sct = (getattr(mss, "MSS", None) or mss.mss)()
        ult_sistema = time.time()
        ult_procura = 0.0
        ult_estado = 0.0
        ult_motor = 0.0
        self._ult_estado_motor = None
        while True:
            t0 = time.time()
            with self.trava:
                pedidos, self.pedidos = self.pedidos, []
            for fn in pedidos:
                try:
                    fn()
                except Exception as e:
                    log("Erro numa ação do painel:", repr(e), tipo="erro")
            try:
                if self.jan.atualizar():
                    self._trocou_jogo()
                self.area = self.jan.area()
                self._passo(self.sala.sct if self.na_sala() else sct, t0, ult_procura)
                if self.status == "procurando" and t0 - ult_procura >= 1.2:
                    ult_procura = t0
            except Exception as e:
                log("Erro no laço:", repr(e), tipo="erro")
                time.sleep(0.5)
            if t0 - ult_motor > 1:
                # visao/escuta/voz clonada terminam de carregar em segundo plano: atualiza o painel
                # assim que o estado muda (no modo rapido ninguem avisava e ficava "carregando...")
                ult_motor = t0
                try:
                    est = self.estado_motor()
                    if est != self._ult_estado_motor:
                        self._ult_estado_motor = est
                        self.emitir("motor")
                except Exception:
                    pass
            if t0 - ult_estado > 5:
                ult_estado = t0
                self.emitir("personagens", self.personagens())
            if t0 - ult_sistema > 30:
                ult_sistema = t0
                self._sistema()
            self._talvez_carregar_visao()
            time.sleep(max(0.01, self.cfg["intervalo_ms"] / 1000 - (time.time() - t0)))

    def _talvez_carregar_visao(self):
        """Carrega a visao (CLIP) num momento CALMO: sem fala sendo gerada/tocada. Carregar no meio de uma fala travava a placa de video e abria um buraco de
        ~1,5 s (medido as 03:38)."""
        v = self.visao
        if not v or v.pronta or v.carregando or not self.cfg.get("visao", True):
            return
        # ao abrir, a visao ja carrega junto com a voz clonada (no __init__). Fora isso (ex.: foi
        # descarregada por falta de memoria) espera a clonada terminar para nao disputar a memoria
        if self.narr.estado_clonada == "carregando":
            return
        if self.narr.falando or not self.narr.fila.empty() or not self.narr.audio.empty():
            return
        v.preparar()

    def _mudar_status(self, s):
        if s != self.status:
            self.status = s
            self.emitir("status", {"status": s, "pausado": self.pausado})

    def _passo(self, sct, agora, ult_procura):
        if not self.jan.raiz or not self.area:
            self._mudar_status("aguardando")
            self.leitor.tick("", agora)
            time.sleep(0.4)
            return
        if self.pausado:
            self._mudar_status("pausado")
            return
        if not self.perfil.regiao and not self._modo_legenda():
            if not self.cfg.get("auto_regiao", True):
                self._mudar_status("sem_regiao")
                return
            self._mudar_status("procurando")
            if agora - ult_procura < 1.2:
                return
            x, y, w, h = self.area
            img = capturar(sct, {"left": x, "top": y, "width": w, "height": h})
            if img.width > 1600:          # tela grande: le em tamanho menor (bem mais leve)
                img = img.resize((img.width // 2, img.height // 2))
            reg = self.descobridor.olhar(img)
            if reg:
                self.perfil.regiao = reg
                log("📍 Achei a caixa de diálogo! (dá para ajustar no painel)", tipo="ok")
                self.avisar_estado()
            return
        texto, nome = (self._ler_legenda(sct, self.area) if self._modo_legenda()
                       else self._ler(sct, self.area))
        if texto is None:
            self._mudar_status("coberto")
            return
        if self._modo_legenda() and getattr(self, "frente_bloqueada", False):
            self._mudar_status("outra_janela")
        else:
            self._mudar_status("sem_video" if (self._modo_legenda() and not self.video.rect
                                                   and not self._jogo_na_frente()) else "lendo")
        if texto:
            self.sem_texto = 0
            self.ult_nomes.append(self._nome_canonico(nome))
            self.ult_nomes_t.append(agora)
            self.ult_nomes_txt.append(texto)
        else:
            self.sem_texto += 1
            if self.sem_texto >= 3:
                self.ult_nomes.clear()
                self.ult_nomes_t.clear()
                self.ult_nomes_txt.clear()
        if texto == self.lido and nome != getattr(self, "_nome_lido", ""):
            # mesmo texto, nome diferente (OCR piscando no nome): registra para achar de onde veio
            diag.ev("nome_lido", nome=nome, texto=texto[:40])
        self._nome_lido = nome
        if texto != self.lido:
            diag.ev("leitura", texto=texto, nome=nome)
            self.lido = texto
            self._previa(texto, nome)
            # nomes que aparecem ESCRITOS no meio da fala ("O Signor Bronte vai ficar..."): quando
            # alguem com esse nome falar, ele nao vira outro parecido da lista ("Bonnie")
            for w in re.findall(r"(?<=\w )[A-ZÀ-Ý][a-zà-ÿ]{3,}", texto):
                self.palavras_vistas[normalizar(w)] = agora
            if texto and getattr(self, "_quadro", None):
                self._salvar_quadro(texto)
            self.emitir("lido", {"texto": texto, "nome": nome})
        for fala, nova, visto in self._separar_dialogo(self.leitor.tick(texto, agora)):
            if len(re.sub(r"[\W\d_]", "", fala)) < 2:      # "i", "-", "4": sujeira do OCR
                diag.ev("descartei", motivo="curta demais", texto=fala)
                continue
            if eh_relogio(fala):                             # tempo do player do video
                diag.ev("descartei", motivo="relogio", texto=fala)
                continue
            nome_f, fala = self._nome_na_fala(fala)
            fala = self._nomes_no_texto(fala)
            if nome_f:
                nova = nova or nome_f != self.nome_pagina
            if not nova and self.nome_pagina and self._modo_legenda():
                # mesma "pagina" para o leitor, mas a legenda trocou de pessoa: "Catherine: Olá." (o
                # nome antigo piscou) -> "Arthur: Olá, senhora." falava "senhora." como Catherine (15/11)
                txts = getattr(self, "ult_nomes_txt", None) or [None] * len(self.ult_nomes)
                atual = nome_estavel([n for n, tt, tx in zip(self.ult_nomes, self.ult_nomes_t, txts)
                                      if tt >= visto - 0.15 and tx and self._mesmo_texto(fala, tx)])
                if atual and atual != self.nome_pagina:
                    diag.ev("nome_mudou_na_pagina", de=self.nome_pagina, para=atual, texto=fala[:40])
                    nova = True
            if nova or not self.nome_pagina:
                if self._modo_legenda():
                    # legenda: so valem os nomes lidos JUNTO com esta fala (desde que ela apareceu);
                    # senao um objetivo sem nome herdava o nome da legenda anterior
                    # ...e lidos com ESTE texto: objetivo e dialogo alternando na tela davam ao
                    # objetivo o nome do dialogo ("Entregue o moonshine..." como Arthur, 15/11)
                    desde = visto - 0.15
                    txts = getattr(self, "ult_nomes_txt", None) or [None] * len(self.ult_nomes)
                    self.nome_pagina = nome_estavel([n for n, tt, tx in zip(self.ult_nomes, self.ult_nomes_t, txts)
                                                     if tt >= desde and self._mesmo_texto(fala, tx)])
                else:
                    self.nome_pagina = nome_estavel(self.ult_nomes)
                self.ficha_pagina = None
                # legenda sem nome que CONTINUA a anterior ("...do outro lado da cidade," /
                # "a gente tava so esperando."): e o mesmo personagem falando
                ant = getattr(self, "_ult_fala", None)
                inicio = fala.lstrip("\"'“”«»-–— ")
                # (objetivo logo depois de "Extraoficialmente..." nao e continuacao, 15/11)
                continua = (ant and ant[0] and agora - ant[2] < 4.0 and not self._eh_objetivo(fala)
                            and (inicio[:1].islower() or ant[1].rstrip()[-1:] in ",;" or ant[1].rstrip().endswith("...")))
                # nome lido agora e NOVO (ninguem com esse nome falou antes) e a frase continua a
                # anterior: quase sempre e o nome estragado pelo OCR ("Willie" no lugar de Hosea)
                novo_incerto = (self.nome_pagina and self.perfil
                                and self.nome_pagina not in self.perfil.personagens and inicio[:1].islower())
                if continua and (not self.nome_pagina or novo_incerto):
                    if self.nome_pagina:
                        diag.ev("nome_trocado_por_continuacao", lido=self.nome_pagina, virou=ant[0])
                    self.nome_pagina = ant[0]
                    diag.ev("continua", quem=ant[0], texto=fala[:50])
            if nome_f:
                self.nome_pagina = nome_f          # o nome que estava grudado na propria fala vale mais
            if self.nome_pagina and len(fala.split()) >= 3:
                # nome de quem fala colado no comeco, estragado pelo OCR ("Trelawnv ligados a Cole...",
                # quem fala e o Trelawny, 15/11): tira a 1a palavra se parece o nome e o resto segue
                p1, resto = fala.split(None, 1)
                limpo = re.sub(r"[^\wÀ-ÿ]", "", p1)
                proprio = (normalizar(limpo) == normalizar(self.nome_pagina.split()[0])
                           and p1.rstrip()[-1:] in ".:;")     # "Lenny. Talvez te chamem..." dito pelo Lenny
                if proprio or (resto[:1].islower() and len(limpo) >= 4 and limpo[:1].isupper()
                        and difflib.SequenceMatcher(None, normalizar(limpo), normalizar(self.nome_pagina)).ratio() >= 0.7):
                    diag.ev("nome_tirado_da_fala", lido=p1, quem=self.nome_pagina)
                    fala = resto
            self._ult_fala = (self.nome_pagina, fala, agora)
            self._com_nome = getattr(self, "_com_nome", collections.deque(maxlen=12))
            from .tela import _VERBOS_OBJETIVO
            p0 = re.sub(r"[^\wÀ-ÿ-]", "", (fala.split() or [""])[0]).lower()
            # frase sem nome com cara de objetivo NAO entra na conta: cada objetivo que escapava
            # baixava a proporcao e fazia os seguintes escaparem tambem (15/11)
            if self.nome_pagina or (p0 not in _VERBOS_OBJETIVO and p0.split("-")[0] not in _VERBOS_OBJETIVO):
                self._com_nome.append(bool(self.nome_pagina))
            if self.nome_pagina:
                prob = None
                f0 = self.elenco.ficha(self.nome_pagina)
                if (self.visao and self.cfg.get("visao", True) and nova
                        and (not f0 or (f0.get("olhadas", 0) < 3 and not f0.get("fixo")))):
                    # com a voz clonada presa na placa de video, a visao (tambem na placa) travaria
                    # a leitura da tela inteira: espera a placa voltar
                    if self.visao.pronta and not self.narr._clonada_presa():
                        reg_v = self.legenda_frac if self._modo_legenda() else self.perfil.regiao
                        prob = self.visao.classificar(self._olhar(sct, self.area, reg_v))
                    # (se ainda nao esta pronta, _talvez_carregar_visao carrega num momento calmo)
                f = self.elenco.observar(self.nome_pagina, fala, prob)
                self.narr.falar(fala, f["voz"], f.get("tom", 1.0), f.get("vel", 1.0), quem=self.nome_pagina,
                                categoria=f["categoria"], nova=nova, visto=visto)
                if not f0 or nova:
                    self.emitir("personagens", self.personagens())
            elif self._eh_objetivo(fala):
                diag.ev("descartei", motivo="objetivo do jogo", texto=fala[:60])
            elif self._lixo_sem_nome(fala):
                diag.ev("descartei", motivo="sem nome e embaralhada", texto=fala[:60])
            else:
                self.narr.falar(fala, "narrador", quem="", categoria="narrador", nova=nova, visto=visto)
                if getattr(self, "_parte2_de", None) is not None:
                    # 2a pessoa da legenda "- A. - B.": a voz sai oposta a da 1a (ver _resolver_voz)
                    self._oposto = getattr(self, "_oposto", {})
                    self._oposto[self.narr.pag] = self._parte2_de
                    self._parte2_de = None
                elif getattr(self, "_parte1", False):
                    self._parte1_pag = self.narr.pag
                    self._parte1 = False

    # ------------------------------------------------------------- teclas
    def alternar_pausa(self):
        self.pausado = not self.pausado
        self.narr.calar()
        log("⏸ Narração pausada" if self.pausado else "▶ Narração retomada")
        self.emitir("status", {"status": "pausado" if self.pausado else self.status, "pausado": self.pausado})

    def repetir(self):
        if self.leitor.repetir:
            f = self.elenco.ficha(self.nome_pagina) if (self.elenco and self.nome_pagina) else None
            if f:
                self.narr.falar(self.leitor.repetir, f["voz"], f.get("tom", 1), f.get("vel", 1),
                                quem=self.nome_pagina, categoria=f["categoria"], forcar=True)
            else:
                self.narr.falar(self.leitor.repetir, "narrador", categoria="narrador", forcar=True)
