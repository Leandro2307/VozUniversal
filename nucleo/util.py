# -*- coding: utf-8 -*-
"""Coisas pequenas usadas por todo o programa: caminhos, log, config e comparacao de textos."""
import collections
import difflib
import json
import os
import re
import threading
import time
import unicodedata

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECURSOS = os.path.join(BASE, "recursos")
PIPER_DIR = os.path.join(RECURSOS, "piper_vozes")
BANCO = os.path.join(BASE, "banco_vozes")
PERFIS = os.path.join(BASE, "perfis")
CONFIG = os.path.join(BASE, "config.json")
LOG = os.path.join(BASE, "log.txt")
FIM = ".!?…"

os.makedirs(PERFIS, exist_ok=True)

# ----------------------------------------------------------------- log (arquivo + painel)
_ouvintes = []
_historico = collections.deque(maxlen=200)


def ouvir_log(fn):
    _ouvintes.append(fn)


def historico_log():
    return list(_historico)


def log(*a, tipo="info"):
    texto = " ".join(str(x) for x in a)
    linha = time.strftime("[%H:%M:%S] ") + texto
    try:
        print(linha, flush=True)
    except Exception:
        pass
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except Exception:
        pass
    item = {"hora": time.strftime("%H:%M:%S"), "texto": texto, "tipo": tipo}
    _historico.append(item)
    for fn in list(_ouvintes):
        try:
            fn(item)
        except Exception:
            pass


# ----------------------------------------------------------------- json com gravacao segura
def ler_json(caminho, padrao=None):
    for _ in range(5):
        try:
            with open(caminho, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            break
        except Exception:
            time.sleep(0.1)
    return json.loads(json.dumps(padrao)) if padrao is not None else None


_trava_json = threading.Lock()


def gravar_json(caminho, dados):
    """Grava num temporario e troca de uma vez (quem ler no meio nunca ve o arquivo pela metade)."""
    with _trava_json:
        tmp = f"{caminho}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
        for _ in range(10):
            try:
                os.replace(tmp, caminho)
                return
            except PermissionError:
                time.sleep(0.1)


PADRAO = {
    "motor": "clonada",            # "clonada" (XTTS na placa de video) ou "rapida" (Piper/Edge)
    "volume": 0.9,
    "velocidade": 1.05,
    "visao": True,                 # olhar a imagem do personagem para descobrir sexo/idade
    "narrador_sem_nome": True,     # texto sem nome de quem fala: voz do narrador
    "auto_regiao": True,           # descobrir sozinho onde fica a caixa de dialogo
    "intervalo_ms": 110,
    "estabilidade_ms": 200,
    "sem_pontuacao_ms": 700,
    "sumiu_ms": 450,
    "memoria_s": 8,
    "interromper_ao_avancar": False,   # False: a fala anterior acelera para alcancar (nao corta)
    "acelerar_ate": 1.35,
    "tolerancia_fim_s": 1.0,
    "acelerar_se_atrasado": True,
    "xtts_pedacos": [8, 12, 20, 32],   # tamanho dos pedacos gerados: comeca pequeno (rapido) e cresce (sem buracos)
    "modo_legenda": True,
    "auto_pedacos": True,
    "pre_buffer_ms": 120,
    "ler_dicas": False,            # "Aperte X para..." (instrucao de controle do jogo) nao e lida          # espera ate isso pelo 2o pedaco antes de comecar a falar (sem buraco)          # aumenta/diminui o 1o pedaco da voz conforme os buracos medidos          # video (tela/janela): acha a legenda sozinho a cada leitura
    "abafar_jogo": True,           # abaixa o som do jogo/video enquanto a voz em portugues fala
    "abafar_nivel": 0.25,          # volume do jogo enquanto abafado (0,25 = 25%)
    "manter_audio_ligado": True,   # silencio continuo: o fone/placa nao dorme e nao corta o comeco
    "falar_enquanto_aparece": True,  # comeca a falar antes da frase terminar de aparecer
    "palavras_por_pedaco": 5,
    "porta": 8765,
    "jogo_fixo": "",
    "fonte": "auto",               # auto (jogo em 1o plano) | tela (monitor inteiro) | janela (escolhida)
    "fonte_hwnd": 0,
    "fonte_exe": "",
    "fonte_monitor": 0,
    "ignorar_processos": [
        "explorer.exe", "msedge.exe", "msedgewebview2.exe", "chrome.exe", "firefox.exe", "code.exe",
        "windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "python.exe", "pythonw.exe",
        "conhost.exe", "discord.exe", "taskmgr.exe", "searchhost.exe", "shellexperiencehost.exe",
        "startmenuexperiencehost.exe", "applicationframehost.exe", "steamwebhelper.exe", "steam.exe",
        "obs64.exe", "claude.exe", "textinputhost.exe", "lockapp.exe", "systemsettings.exe",
        "epicgameslauncher.exe", "whatsapp.exe", "spotify.exe", "notepad.exe", "capcut.exe",
    ],
}


def carregar_config():
    cfg = json.loads(json.dumps(PADRAO))
    cfg.update(ler_json(CONFIG, {}) or {})
    gravar_json(CONFIG, cfg)
    return cfg


# ----------------------------------------------------------------- textos
def normalizar(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def sem_acento(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def semelhanca(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def memoria_livre():
    """(GB de RAM livre, GB de memoria livre na placa de video ou None)."""
    ram = vram = None
    try:
        import psutil
        ram = psutil.virtual_memory().available / 2 ** 30
    except Exception:
        pass
    try:
        import subprocess
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, creationflags=0x08000000).stdout
        vram = int(out.split()[0]) / 1024
    except Exception:
        pass
    return ram, vram


def cabe(nome, ram_gb, vram_gb):
    """Ha memoria sobrando para carregar `nome` sem travar o PC? Se nao, avisa e diz que nao."""
    ram, vram = memoria_livre()
    falta = []
    if ram is not None and ram < ram_gb:
        falta.append(f"RAM livre {ram:.1f} GB (precisa {ram_gb} GB)")
    if vram is not None and vram < vram_gb:
        falta.append(f"memória da placa de vídeo livre {vram:.1f} GB (precisa {vram_gb} GB)")
    if falta:
        log(f"⛔ Não carreguei {nome} para não travar o PC: " + "; ".join(falta)
            + ". Feche algo (ou o jogo) e tente de novo.", tipo="aviso")
        return False
    return True


def nome_de_arquivo(s):
    return re.sub(r'[\\/:*?"<>|]', "", s or "").strip() or "sem_nome"
