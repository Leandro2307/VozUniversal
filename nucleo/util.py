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
    "escuta": True,                # ouvir a voz original do jogo (homem/mulher/crianca), ate em legenda sem nome
    "escuta_espera_s": 0.5,        # quanto esperar (desde a legenda aparecer) ouvindo a voz antes de falar
    "escuta_certeza": 0.35,        # certeza minima para trocar o narrador por voz de homem/mulher
    "narrador_sem_nome": True,     # texto sem nome de quem fala: voz do narrador
    "auto_regiao": True,           # descobrir sozinho onde fica a caixa de dialogo
    "intervalo_ms": 110,
    "estabilidade_ms": 200,
    "sem_pontuacao_ms": 700,
    "sumiu_ms": 450,
    "memoria_s": 8,
    "interromper_ao_avancar": False,   # False: a fala anterior acelera para alcancar (nao corta)
    "acelerar_ate": 1.35,
    "legenda_altura_min": 0.62,    # so le legenda com o meio abaixo desta altura do video (embaixo, no centro)
    "ler_texto_tela": False,       # texto escrito na cena (celular, carta): desligado, so legendas
    "pular_atual_apos": 3,         # legendas atras para cortar a que toca (3 encavaladas: pula so a do meio)
    "pular_atrasada_s": 3.0,       # fala que ainda nao comecou, com legenda mais nova esperando, so e pulada
                                   # se ja estiver este tanto atrasada (0: nunca pula por atraso)
    "tolerancia_fim_s": 1.0,
    "acelerar_se_atrasado": True,
    "xtts_pedacos": [8, 12, 20, 32],   # tamanho dos pedacos gerados: comeca pequeno (rapido) e cresce (sem buracos)
    "modo_legenda": True,
    "auto_pedacos": True,
    "xtts_grafo": True,            # passo do GPT da voz clonada gravado como CUDA graph (bem mais rapido)
    "pre_buffer_ms": 120,
    "ler_dicas": False,            # "Aperte X para..." (instrucao de controle do jogo) nao e lida          # espera ate isso pelo 2o pedaco antes de comecar a falar (sem buraco)          # aumenta/diminui o 1o pedaco da voz conforme os buracos medidos          # video (tela/janela): acha a legenda sozinho a cada leitura
    "abafar_jogo": True,           # abaixa o som do jogo/video enquanto a voz em portugues fala
    "abafar_nivel": 0.25,          # volume do jogo enquanto abafado (0,25 = 25%)
    "manter_audio_ligado": True,   # silencio continuo: o fone/placa nao dorme e nao corta o comeco
    "falar_enquanto_aparece": True,  # comeca a falar antes da frase terminar de aparecer
    "palavras_por_pedaco": 5,
    "porta": 8765,
    "jogo_fixo": "",
    "voz_unica": "",               # botoes "Voz da Lucia"/"Voz do Jason": todos falam com essa voz ("" = desligado)
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


def tom_hz(x, sr):
    """Altura media da voz (Hz, mediana do F0 nos trechos com fala) ou None. Homem fica por volta de
    85-160 Hz, mulher 165-260 Hz. Usado para dar a cada personagem a voz do banco com o tom mais
    parecido com o da voz original do jogo (e para medir as amostras do banco)."""
    import numpy as np
    x = np.asarray(x, np.float32).reshape(-1)
    if sr != 16000:
        x = np.interp(np.arange(0, len(x), sr / 16000), np.arange(len(x)), x).astype(np.float32)
    if len(x) < 16000 * 0.4:
        return None
    try:
        import librosa
        f0 = librosa.yin(x, fmin=65, fmax=450, sr=16000, frame_length=1024, hop_length=256)
        rms = librosa.feature.rms(y=x, frame_length=1024, hop_length=256)[0]
    except Exception:
        return None
    n = min(len(f0), len(rms))
    f0, rms = f0[:n], rms[:n]
    if not n or rms.max() <= 0:
        return None
    ok = (rms > np.percentile(rms, 40)) & (f0 > 66) & (f0 < 440)    # so os quadros com voz forte
    vals = f0[ok]
    return round(float(np.median(vals)), 1) if len(vals) >= 8 else None


# A escuta, a visao e a voz clonada importam o `transformers` em threads diferentes; ao mesmo tempo,
# a importacao preguicosa dele falhava (ImportError "cannot import name 'Wav2Vec2Config'", 20:25).
TRAVA_IMPORTACAO = threading.RLock()


class MedidorPlaca:
    """Uso da placa de video (% de calculo e GB de memoria) lido a cada segundo pelo NVML, a
    biblioteca que vem com o driver da NVIDIA (nvml.dll). Serve para saber, no registro, se a voz
    clonada atrasou porque o jogo estava usando a placa toda. Sem placa NVIDIA: fica desligado."""

    def __init__(self, intervalo=1.0, guardar=60):
        self.ok = False
        self.amostras = collections.deque(maxlen=guardar)    # (hora, % calculo, GB usados, GB total)
        try:
            import ctypes

            class _Uso(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            class _Mem(ctypes.Structure):
                _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]

            self._nvml = ctypes.WinDLL(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll"))
            if self._nvml.nvmlInit_v2() != 0:
                return
            self._h = ctypes.c_void_p()
            if self._nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self._h)) != 0:
                return
            self._uso, self._mem, self._byref = _Uso(), _Mem(), ctypes.byref
            self.ok = self._ler() is not None
        except Exception:
            return
        if self.ok:
            threading.Thread(target=self._laco, args=(intervalo,), daemon=True, name="placa").start()

    def _ler(self):
        n = self._nvml
        if n.nvmlDeviceGetUtilizationRates(self._h, self._byref(self._uso)) != 0:
            return None
        if n.nvmlDeviceGetMemoryInfo(self._h, self._byref(self._mem)) != 0:
            return None
        return (time.time(), int(self._uso.gpu), self._mem.used / 2 ** 30, self._mem.total / 2 ** 30)

    def _laco(self, intervalo):
        while True:
            try:
                a = self._ler()
                if a:
                    self.amostras.append(a)
            except Exception:
                pass
            time.sleep(intervalo)

    def agora(self):
        """{"placa_pct": % de calculo agora, "placa_livre_gb": memoria livre} ou {}."""
        if not self.amostras:
            return {}
        _, pct, usado, total = self.amostras[-1]
        return {"placa_pct": pct, "placa_livre_gb": round(total - usado, 2)}

    def resumo(self, segundos=30):
        """Media e maximo do uso de calculo nos ultimos `segundos`."""
        lim = time.time() - segundos
        v = [a[1] for a in list(self.amostras) if a[0] >= lim]
        if not v:
            return {}
        return {"placa_pct_media": round(sum(v) / len(v)), "placa_pct_max": max(v),
                "placa_pct_90mais": round(100 * sum(1 for x in v if x >= 90) / len(v))}


MEDIDOR_PLACA = None


def medidor_placa():
    global MEDIDOR_PLACA
    if MEDIDOR_PLACA is None:
        MEDIDOR_PLACA = MedidorPlaca()
    return MEDIDOR_PLACA


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


def _paginacao_livre():
    """GB livres no arquivo de paginacao (memoria virtual em disco) do Windows."""
    try:
        import psutil
        return psutil.swap_memory().free / 2 ** 30
    except Exception:
        return 0.0


def cabe(nome, ram_gb, vram_gb, contar_paginacao=False, avisar=True):
    """Ha memoria sobrando para carregar `nome` sem travar o PC? Se nao, avisa e diz que nao.

    contar_paginacao: sem placa NVIDIA o modelo fica na RAM; o arquivo do modelo e lido mapeado
    do disco e o Windows usa a paginacao para o resto. Entao conta a RAM livre + a paginacao livre
    (PC de 8 GB com o navegador aberto tinha so ~1 GB de RAM livre e nunca carregava)."""
    ram, vram = memoria_livre()
    if contar_paginacao and vram is None and ram is not None:
        ram += _paginacao_livre()
    falta = []
    if ram is not None and ram < ram_gb:
        falta.append(f"RAM livre {ram:.1f} GB (precisa {ram_gb} GB)")
    if vram is not None and vram < vram_gb:
        falta.append(f"memória da placa de vídeo livre {vram:.1f} GB (precisa {vram_gb} GB)")
    if falta:
        if not avisar:
            return False
        log(f"⛔ Não carreguei {nome} para não travar o PC: " + "; ".join(falta)
            + ". Feche algo (ou o jogo) e tente de novo.", tipo="aviso")
        return False
    return True


def nome_de_arquivo(s):
    return re.sub(r'[\\/:*?"<>|]', "", s or "").strip() or "sem_nome"
