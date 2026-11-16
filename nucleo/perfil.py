# -*- coding: utf-8 -*-
"""Perfil de cada jogo (perfis\\<programa>.json): onde fica a caixa de dialogo e a ficha de
cada personagem que ja apareceu (sexo/idade descobertos e a voz que ganhou).

No modo "tela inteira"/janela (videos), os personagens ficam separados por GRUPO: o titulo da
janela (o nome do video ou do jogo). Assim o "Arthur" do Red Dead nao se mistura com outro."""
import os
import re
import threading
import time

from .util import PERFIS, gravar_json, ler_json, nome_de_arquivo


def _chave_titulo(t):
    """ "Red Dead Redemption 2 - O Filme (Legendado) 4K" -> "reddeadredemption2ofilmelegendado4k" """
    import unicodedata
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in t if not unicodedata.combining(c)))


def grupo_do_titulo(titulo):
    """ "(257) Red Dead Redemption 2 - O Filme (Legendado) 4K - YouTube - Google Chrome"
    -> "Red Dead Redemption 2 - O Filme (Legendado) 4K" """
    t = re.sub(r"^\(\d+\)\s*", "", titulo or "").strip()
    t = re.split(r"\s+-\s+(YouTube|Google Chrome|Microsoft​? Edge|Mozilla Firefox|Twitch)\b", t)[0]
    t = t.strip()
    # titulo provisorio enquanto a pagina carrega ("youtube.com/watch?v=...", "YouTube", "Nova guia")
    if (not t or re.search(r"(https?:|www\.|\.com/|watch\?v=)", t, re.I)
            or t.lower() in ("youtube", "nova guia", "new tab", "google chrome", "carregando...")):
        return ""
    return t[:80]


class Perfil:
    def __init__(self, exe, titulo=""):
        self.exe = exe
        self.caminho = os.path.join(PERFIS, nome_de_arquivo(exe) + ".json")
        self.trava = threading.RLock()
        self.grupo = ""                       # "" = personagens do perfil (jogos); senao, do video
        d = ler_json(self.caminho, {}) or {}
        self.dados = {
            "jogo": exe,
            "titulo": d.get("titulo") or titulo or exe,
            "regiao": d.get("regiao"),          # [x0, y0, x1, y1] em fracoes da tela do jogo
            "zona_nome": d.get("zona_nome"),    # [x0, y0, x1, y1] em fracoes da regiao (opcional)
            "regiao_manual": bool(d.get("regiao_manual")),   # a caixa foi marcada a mao no painel
            "personagens": d.get("personagens") or {},
            "grupos": d.get("grupos") or {},    # titulo do video/jogo -> personagens dele
            "criado": d.get("criado") or time.strftime("%Y-%m-%d %H:%M"),
        }
        if titulo and not d.get("titulo"):
            self.dados["titulo"] = titulo
        # personagens descobertos sozinhos que falaram 1 vez so sao quase sempre leitura ruim do
        # OCR ("Afflnr"): somem ao abrir (os corrigidos no painel ficam sempre)
        for pers in [self.dados["personagens"]] + list(self.dados["grupos"].values()):
            # ...e palavra comum que virou "nome" ("Até", "Ei", "Com": 15/11, roubava a voz das falas)
            from .tela import palavra_comum
            lixo = [n for n, f in pers.items() if not f.get("fixo")
                    and (f.get("falas", 0) <= 1 or (not f.get("anonimo") and (palavra_comum(n) or len(n.strip()) <= 2)))]
            for n in lixo:
                pers.pop(n, None)

        if self.exe and not self.exe.startswith("tela_inteira"):
            self.herdar_personagens(self.dados["titulo"])

    def herdar_personagens(self, titulo):
        """Traz os personagens ja conhecidos de outros perfis/grupos com o MESMO jogo no titulo:
        "Red Dead Redemption 2 - O Filme (Legendado) 4K" (YouTube) -> janela "Red Dead Redemption 2".
        Antes, ao abrir o jogo, o perfil comecava vazio ("jogo novo") e os 40 personagens do RDR2 ja
        descobertos (Arthur, Dutch, Hosea...) ficavam esquecidos no grupo do video (14/11).
        Nao sobrescreve quem ja existe; nao traz as fichas "(pela voz)". -> quantos trouxe."""
        chave = _chave_titulo(grupo_do_titulo(titulo) or titulo)
        if len(chave) < 6:
            return 0
        destino = self.personagens
        trouxe, de = 0, set()
        try:
            arquivos = [os.path.join(PERFIS, a) for a in os.listdir(PERFIS) if a.endswith(".json")]
        except Exception:
            arquivos = []
        for arq in arquivos:
            d = self.dados if os.path.abspath(arq) == os.path.abspath(self.caminho) else (ler_json(arq, {}) or {})
            fontes = [(d.get("titulo") or "", d.get("personagens") or {})] + list((d.get("grupos") or {}).items())
            for tit, pers in fontes:
                if pers is destino or not pers or chave not in _chave_titulo(tit):
                    continue
                for nome, f in pers.items():
                    if nome in destino or f.get("anonimo") or "(pela voz)" in nome:
                        continue
                    if not f.get("fixo") and f.get("falas", 0) <= 1:
                        continue
                    destino[nome] = dict(f, herdado_de=tit[:60])
                    trouxe += 1
                    de.add(tit[:60])
        if trouxe:
            from .util import log
            log(f"👥 Personagens já conhecidos carregados: {trouxe} (de: {', '.join(sorted(de))})", tipo="ok")
            self.salvar()
        return trouxe

    @property
    def regiao(self):
        return self.dados.get("regiao")

    @regiao.setter
    def regiao(self, r):
        with self.trava:
            self.dados["regiao"] = r
            self.salvar()

    @property
    def personagens(self):
        if self.grupo:
            return self.dados["grupos"].setdefault(self.grupo, {})
        return self.dados["personagens"]

    def usar_grupo(self, titulo):
        """Troca o conjunto de personagens conforme o video/jogo na frente. -> True se mudou."""
        g = grupo_do_titulo(titulo)
        if g and g != self.grupo:
            self.grupo = g
            self.herdar_personagens(g)
            return True
        return False

    def salvar(self):
        with self.trava:
            gravar_json(self.caminho, self.dados)
