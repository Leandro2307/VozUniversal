# -*- coding: utf-8 -*-
"""Escuta a voz ORIGINAL do jogo/video e diz se quem fala e homem, mulher ou crianca.

Captura: so o som do programa do jogo (chrome.exe, GTA6.exe...), pelo "loopback por processo" do
Windows 10 (2004+) / 11. Assim a propria voz do Voz Universal NAO entra na gravacao, nem musica
do Spotify, Discord etc. Se o Windows nao tiver isso, grava o som do PC todo (SoundCard) e joga
fora os trechos em que o narrador estava falando.

Analise: 1) o Silero VAD separa os pedacos com FALA (musica, tiro e motor ficam de fora);
2) um modelo neural (wav2vec2 da audEERING, idade e sexo) ouve so a fala e diz mulher / homem /
crianca + idade. O resultado vem com uma CERTEZA; quem usa decide o que fazer com ela.
(Medir so a altura da voz foi testado e errava muito: homem gritando passa de 200 Hz.)
"""
import collections
import ctypes
import math
import os
import threading
import time
from ctypes import POINTER, Structure, byref, c_int, c_int64, c_uint32, c_uint64, c_ushort, c_ulong, c_void_p, wintypes

import numpy as np

from .diagnostico import diag
from .util import log

SR = 16000

# ======================================================================== captura por processo
_com_pronto = False


def _definir_com():
    global _com_pronto, IAudioClient, IAudioCaptureClient, IActOp, IActHandler, IAgileObject, GUID, COMObject
    if _com_pronto:
        return
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown, COMObject  # noqa

    class WAVEFORMATEX(Structure):
        _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                    ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD),
                    ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]
    globals()["WAVEFORMATEX"] = WAVEFORMATEX

    class IAudioClient(IUnknown):
        _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Initialize", (["in"], wintypes.DWORD, "m"), (["in"], wintypes.DWORD, "f"),
                      (["in"], c_int64, "dur"), (["in"], c_int64, "per"), (["in"], POINTER(WAVEFORMATEX), "fmt"),
                      (["in"], c_void_p, "sess")),
            COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "n")),
            COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(c_int64), "l")),
            COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "p")),
            COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], wintypes.DWORD, "m"),
                      (["in"], POINTER(WAVEFORMATEX), "f"), (["out"], POINTER(c_void_p), "c")),
            COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(c_void_p), "f")),
            COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], POINTER(c_int64), "d"), (["out"], POINTER(c_int64), "m")),
            COMMETHOD([], HRESULT, "Start"),
            COMMETHOD([], HRESULT, "Stop"),
            COMMETHOD([], HRESULT, "Reset"),
            COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "h")),
            COMMETHOD([], HRESULT, "GetService", (["in"], POINTER(GUID), "riid"), (["out"], POINTER(c_void_p), "ppv")),
        ]

    class IAudioCaptureClient(IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetBuffer", (["out"], POINTER(c_void_p), "data"), (["out"], POINTER(c_uint32), "n"),
                      (["out"], POINTER(wintypes.DWORD), "flags"), (["out"], POINTER(c_uint64), "pos"),
                      (["out"], POINTER(c_uint64), "qpc")),
            COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "n")),
            COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "n")),
        ]

    class IActOp(IUnknown):
        _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
        _methods_ = [COMMETHOD([], HRESULT, "GetActivateResult", (["out"], POINTER(c_int), "hr"),
                               (["out"], POINTER(POINTER(IUnknown)), "itf"))]

    class IActHandler(IUnknown):
        _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
        _methods_ = [COMMETHOD([], HRESULT, "ActivateCompleted", (["in"], POINTER(IActOp), "op"))]

    class IAgileObject(IUnknown):
        _iid_ = GUID("{94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90}")
        _methods_ = []

    globals().update(IAudioClient=IAudioClient, IAudioCaptureClient=IAudioCaptureClient, IActOp=IActOp,
                     IActHandler=IActHandler, IAgileObject=IAgileObject, GUID=GUID, COMObject=COMObject)
    _com_pronto = True


class _BLOB(Structure):
    _fields_ = [("cbSize", c_ulong), ("pBlobData", c_void_p)]


class _PROPVARIANT(Structure):
    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort), ("blob", _BLOB)]


class _PARAMS(Structure):
    _fields_ = [("ActivationType", c_int), ("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", c_int)]


def _abrir_loopback_processo(pid, incluir):
    """-> (IAudioClient, IAudioCaptureClient, evento, canais) gravando so o processo `pid` (e filhos)
    ou tudo MENOS ele (incluir=False). Formato pedido: 16 kHz, 16 bits."""
    _definir_com()

    class Handler(COMObject):
        _com_interfaces_ = [IActHandler, IAgileObject]

        def __init__(self):
            super().__init__()
            self.pronto = threading.Event()

        def ActivateCompleted(self, op):
            self.pronto.set()
            return 0

    params = _PARAMS(1, pid, 0 if incluir else 1)
    pv = _PROPVARIANT()
    pv.vt = 65                       # VT_BLOB
    pv.blob.cbSize = ctypes.sizeof(params)
    pv.blob.pBlobData = ctypes.cast(ctypes.pointer(params), c_void_p)
    h = Handler()
    ph = h.QueryInterface(IActHandler)
    op = POINTER(IActOp)()
    iid = IAudioClient._iid_
    fn = ctypes.windll.mmdevapi.ActivateAudioInterfaceAsync
    fn.restype = c_int
    hr = fn(ctypes.c_wchar_p("VAD\\Process_Loopback"), byref(iid), byref(pv), ph, byref(op))
    if hr != 0:
        raise OSError(f"ActivateAudioInterfaceAsync 0x{hr & 0xFFFFFFFF:08X}")
    if not h.pronto.wait(3):
        raise OSError("ativação do áudio não respondeu")
    hr2, itf = op.GetActivateResult()
    if hr2 != 0:
        raise OSError(f"GetActivateResult 0x{hr2 & 0xFFFFFFFF:08X}")
    cli = itf.QueryInterface(IAudioClient)
    erro = None
    for canais in (1, 2):
        fmt = WAVEFORMATEX(1, canais, SR, SR * 2 * canais, 2 * canais, 16, 0)
        try:
            # LOOPBACK | EVENTCALLBACK | AUTOCONVERTPCM | SRC_DEFAULT_QUALITY ; buffer de 200 ms
            cli.Initialize(0, 0x00020000 | 0x00040000 | 0x80000000 | 0x08000000, 2_000_000, 0, byref(fmt), None)
            break
        except Exception as e:
            erro = e
    else:
        raise OSError(f"Initialize falhou: {erro!r}")
    evento = ctypes.windll.kernel32.CreateEventW(None, False, False, None)
    cli.SetEventHandle(evento)
    p = cli.GetService(byref(IAudioCaptureClient._iid_))
    cap = ctypes.cast(p, POINTER(IAudioCaptureClient))
    cli.Start()
    return cli, cap, evento, canais, h


class Gravador:
    """Guarda os ultimos `memoria_s` segundos do som do jogo, com o horario (time.time) de cada pedaco.

    alvo(): -> pid do programa do jogo (0 = qualquer um menos o Voz Universal)
    falando(): -> True enquanto o narrador fala (so usado no modo de reserva, sem loopback por processo)."""

    def __init__(self, alvo=lambda: 0, falando=lambda: False, memoria_s=20):
        self.alvo = alvo
        self.falando = falando
        self.memoria_s = memoria_s
        self.pedacos = collections.deque()       # (t_fim, np.float32 mono 16 kHz, valido)
        self.trava = threading.Lock()
        self.modo = ""                            # "processo", "pc" ou "" (sem captura)
        self.pid = None
        self.erro = ""
        self.parar = False
        self.thread = threading.Thread(target=self._laco, daemon=True, name="escuta")
        self.thread.start()

    def fechar(self):
        self.parar = True

    def _guardar(self, x, valido=True):
        agora = time.time()
        with self.trava:
            self.pedacos.append((agora, x, valido))
            while self.pedacos and agora - self.pedacos[0][0] > self.memoria_s:
                self.pedacos.popleft()

    def trecho(self, t0, t1):
        """Audio entre t0 e t1 (horarios time.time). Trechos invalidos (narrador falando, no modo
        de reserva) e buracos (programa em silencio) viram zeros."""
        n = int(max(0.0, t1 - t0) * SR)
        out = np.zeros(n, np.float32)
        if n == 0:
            return out
        with self.trava:
            pedacos = list(self.pedacos)
        for fim, x, valido in pedacos:
            ini = fim - len(x) / SR
            if fim <= t0 or ini >= t1 or not valido:
                continue
            a = int(round((ini - t0) * SR))
            ia, ib = max(0, -a), min(len(x), n - a)
            if ib > ia:
                out[a + ia:a + ib] = x[ia:ib]
        return out

    # ---------------------------------------------------------------- laco
    def _laco(self):
        try:
            import comtypes
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:
            pass
        while not self.parar:
            pid = int(self.alvo() or 0)
            try:
                self._gravar_processo(pid)
            except Exception as e:
                if self.modo != "pc":
                    self.erro = repr(e)
                    diag.ev("escuta_erro", erro=repr(e)[:160], pid=pid)
                    log("Escuta por programa indisponível, usando o som do PC todo:", repr(e)[:120], tipo="aviso")
                try:
                    self._gravar_pc()
                except Exception as e2:
                    self.modo = ""
                    self.erro = repr(e2)
                    diag.ev("escuta_erro", erro=repr(e2)[:160], modo="pc")
                    time.sleep(10)

    def _gravar_processo(self, pid):
        incluir = pid > 0
        cli, cap, evento, canais, h = _abrir_loopback_processo(pid if incluir else os.getpid(), incluir)
        self.modo, self.pid = "processo", pid
        diag.ev("escuta", modo="processo", pid=pid, canais=canais)
        espera = ctypes.windll.kernel32.WaitForSingleObject
        try:
            ult_alvo = time.time()
            while not self.parar:
                espera(evento, 100)
                while True:
                    n = cap.GetNextPacketSize()
                    if not n:
                        break
                    dados, quadros, flags, _, _ = cap.GetBuffer()
                    if quadros:
                        if flags & 0x2 or not dados:        # AUDCLNT_BUFFERFLAGS_SILENT
                            x = np.zeros(quadros, np.float32)
                        else:
                            buf = (ctypes.c_int16 * (quadros * canais)).from_address(dados)
                            x = np.frombuffer(buf, np.int16).astype(np.float32) / 32768.0
                            if canais == 2:
                                x = x.reshape(-1, 2).mean(1)
                        self._guardar(x.copy())
                    cap.ReleaseBuffer(quadros)
                if time.time() - ult_alvo > 1.0:          # trocou de jogo: reabre no programa novo
                    ult_alvo = time.time()
                    if int(self.alvo() or 0) != pid:
                        diag.ev("escuta_troca", de=pid, para=int(self.alvo() or 0))
                        return
        finally:
            try:
                cli.Stop()
            except Exception:
                pass
            ctypes.windll.kernel32.CloseHandle(evento)

    def _gravar_pc(self):
        """Reserva: som do PC todo (alto-falante padrao), descartando quando o narrador fala."""
        import soundcard as sc
        alto = sc.default_speaker()
        mic = sc.get_microphone(id=str(alto.name), include_loopback=True)
        self.modo = "pc"
        diag.ev("escuta", modo="pc", dispositivo=alto.name)
        ate = time.time() + 30
        with mic.recorder(samplerate=SR, channels=1, blocksize=800) as rec:
            while not self.parar and time.time() < ate:
                x = rec.record(numframes=800)[:, 0].astype(np.float32)
                self._guardar(x, valido=not self.falando())


# ======================================================================== modelo neural (sexo + idade)
class Ouvido:
    """wav2vec2 da audEERING ajustado para idade e sexo (mulher / homem / crianca). Versao de 6
    camadas (~360 MB, baixada sozinha na 1a vez), roda no processador em ~0,1 s por fala.
    Aguenta musica e efeitos bem melhor do que medir so a altura da voz."""

    NOME = "audeering/wav2vec2-large-robust-6-ft-age-gender"

    def __init__(self):
        self.pronto = False
        self.carregando = False
        self.erro = ""
        self.trava = threading.Lock()

    def carregar(self):
        if self.pronto or self.carregando:
            return
        self.carregando = True
        try:
            t0 = time.time()
            import torch
            import torch.nn as nn
            from huggingface_hub import snapshot_download
            from transformers import Wav2Vec2Config, Wav2Vec2Model, Wav2Vec2PreTrainedModel

            class Cabeca(nn.Module):
                def __init__(self, config, n):
                    super().__init__()
                    self.dense = nn.Linear(config.hidden_size, config.hidden_size)
                    self.dropout = nn.Dropout(config.final_dropout)
                    self.out_proj = nn.Linear(config.hidden_size, n)

                def forward(self, x):
                    return self.out_proj(self.dropout(torch.tanh(self.dense(self.dropout(x)))))

            class IdadeSexo(Wav2Vec2PreTrainedModel):
                def __init__(self, config):
                    super().__init__(config)
                    self.wav2vec2 = Wav2Vec2Model(config)
                    self.age = Cabeca(config, 1)
                    self.gender = Cabeca(config, 3)
                    self.init_weights()

                def forward(self, x):
                    h = self.wav2vec2(x)[0].mean(1)
                    return self.age(h), torch.softmax(self.gender(h), dim=1)

            log("Escuta: carregando o modelo de voz (a 1ª vez baixa ~360 MB)...")
            pasta = snapshot_download(self.NOME, allow_patterns=["config.json", "preprocessor_config.json",
                                                                 "model.safetensors"])
            cfg = Wav2Vec2Config.from_pretrained(pasta)
            self.torch = torch
            self.modelo = IdadeSexo.from_pretrained(pasta, config=cfg, use_safetensors=True).eval()
            self.pronto = True
            log(f"Escuta pronta em {time.time() - t0:.1f}s (reconhece homem, mulher e criança pela voz).")
        except Exception as e:
            self.erro = repr(e)
            log("Modelo de voz indisponível (segue só pela altura da voz):", repr(e)[:160], tipo="aviso")
        finally:
            self.carregando = False

    def classificar(self, x):
        """x: voz 16 kHz -> {"mulher": p, "homem": p, "crianca": p, "idade": anos} ou None."""
        if not self.pronto or len(x) < SR * 0.3:
            return None
        torch = self.torch
        x = (x - x.mean()) / (x.std() + 1e-7)
        with self.trava, torch.inference_mode():
            idade, sexo = self.modelo(torch.from_numpy(x.astype(np.float32))[None])
        p = sexo[0].tolist()
        return {"mulher": round(p[0], 3), "homem": round(p[1], 3), "crianca": round(p[2], 3),
                "idade": round(float(idade[0, 0]) * 100, 1)}


def so_voz(x):
    """Recorta so os trechos com som (tira silencio antes/depois e buracos) para o modelo."""
    if len(x) < 400:
        return x
    q = 320
    n = len(x) // q
    e = np.sqrt((x[:n * q].reshape(n, q) ** 2).mean(1))
    if not len(e) or e.max() < 1e-4:
        return x[:0]
    from scipy.ndimage import maximum_filter1d
    ok = (e > 0.08 * maximum_filter1d(e, size=50, mode="nearest")) & (e > 1e-4)
    return np.concatenate([x[i * q:(i + 1) * q] for i in np.nonzero(ok)[0]]) if ok.any() else x[:0]


class Vad:
    """Silero VAD (2 MB, MIT): chance de ter FALA em cada pedaco de 32 ms. Separa fala de musica,
    tiro e motor, para o modelo de sexo/idade so ouvir voz de gente."""

    def __init__(self):
        import onnxruntime as ort
        from .util import RECURSOS
        op = ort.SessionOptions()
        op.inter_op_num_threads = op.intra_op_num_threads = 1
        self.s = ort.InferenceSession(os.path.join(RECURSOS, "modelos", "silero_vad.onnx"), op,
                                      providers=["CPUExecutionProvider"])
        self.trava = threading.Lock()

    def probs(self, x):
        n = len(x) // 512
        estado = np.zeros((2, 1, 128), np.float32)
        ctx = np.zeros(64, np.float32)
        sr = np.array(SR, np.int64)
        out = np.zeros(n, np.float32)
        with self.trava:
            for k in range(n):
                c = x[k * 512:(k + 1) * 512].astype(np.float32)
                p, estado = self.s.run(None, {"input": np.concatenate([ctx, c])[None], "state": estado, "sr": sr})
                ctx = c[-64:]
                out[k] = float(p[0, 0])
        return out


def analisar(x, ouvido, vad, limiar=0.25):
    """-> dict: sexo ('m'/'f'/None), p_m (chance de homem), crianca (0..1), idade (anos),
    voz_s (segundos de fala achados) e certeza (0..1)."""
    res = {"sexo": None, "p_m": 0.5, "crianca": 0.0, "idade": None, "voz_s": 0.0, "certeza": 0.0,
           "rms": round(float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0, 4)}
    if len(x) < SR * 0.3 or res["rms"] < 1e-4 or not ouvido or not ouvido.pronto:
        return res
    # o jogo fica abafado (20%) enquanto o narrador fala: o detector de fala e sensivel ao volume,
    # entao leva tudo a um volume normal antes (pelos 5% mais altos, para um tiro nao mandar)
    q = 320
    n = len(x) // q
    e = np.sqrt((x[:n * q].reshape(n, q) ** 2).mean(1))
    x = np.clip(x * min(60.0, 0.1 / max(1e-5, float(np.percentile(e, 95)))), -1.0, 1.0).astype(np.float32)
    p = vad.probs(x)
    ok = p > limiar
    # um pedaco de cada lado junto (o comeco/fim das silabas)
    ok = ok | np.r_[ok[1:], False] | np.r_[False, ok[:-1]]
    res["vad"] = round(float(p.mean()), 2)
    if not ok.any():
        return res
    fala = np.concatenate([x[k * 512:(k + 1) * 512] for k in np.nonzero(ok)[0]])
    res["voz_s"] = round(len(fala) / SR, 2)
    if res["voz_s"] < 0.3:
        return res
    r = ouvido.classificar(fala)
    if not r:
        return res
    h, m, c = r["homem"], r["mulher"], r["crianca"]
    p_m = h / max(1e-6, h + m)
    res.update(p_m=round(p_m, 3), crianca=round(c, 3), idade=r["idade"], sexo="m" if p_m >= 0.5 else "f",
               probs=r)
    quanto = min(1.0, res["voz_s"] / 0.8)
    longe = min(1.0, abs(p_m - 0.5) * 2.0 * (1 - c * 0.5))
    res["convicto"] = round(longe, 3)
    res["certeza"] = round(quanto * longe, 2)
    return res


class Escuta:
    """Junta o gravador e a analise. Usada pelo nucleo:
       ouvir(visto, ate_s, minimo) -> analise da voz que comecou quando a legenda apareceu."""

    def __init__(self, cfg, alvo, falando):
        self.cfg = cfg
        self.grav = Gravador(alvo=alvo, falando=falando)
        self.trava = threading.Lock()
        self.ouvido = Ouvido()
        self.vad = None
        threading.Thread(target=self._preparar, daemon=True, name="escuta_modelo").start()

    def _preparar(self):
        try:
            self.vad = Vad()
        except Exception as e:
            log("Detector de fala indisponível:", repr(e)[:160], tipo="aviso")
            return
        from .util import cabe
        for _ in range(60):                       # espera ter memoria (a voz clonada carrega junto)
            if cabe("a escuta", ram_gb=1.2, vram_gb=0):
                break
            time.sleep(10)
        self.ouvido.carregar()

    @property
    def ativa(self):
        return bool(self.grav.modo) and self.ouvido.pronto and self.vad is not None

    def ouvir(self, visto, ate=None, esperar=0.0, minimo=0.3, convicto=0.95):
        """Analisa o som desde a legenda aparecer (visto - 0,1 s) ate `ate` (ou agora). Se
        `esperar` > 0, vai ouvindo e para ASSIM que tiver `minimo` s de fala com o modelo
        `convicto` (res["cedo"] = True), ou quando acabar o tempo.
        Medido no trailer do GTA 6 (120 falas com gabarito): decide em ~0,5 s (mediana),
        104 certas, 4 discordancias, 12 ficam no narrador."""
        fim_espera = time.time() + esperar
        res = None
        while True:
            t1 = min(time.time(), ate) if ate else time.time()
            x = self.grav.trecho(visto - 0.1, t1)
            with self.trava:
                res = analisar(x, self.ouvido, self.vad)
            if res["sexo"] and res["voz_s"] >= minimo and res.get("convicto", 0) >= convicto:
                res["cedo"] = True
                break
            if time.time() >= fim_espera:
                break
            time.sleep(0.1)
        res["janela_s"] = round(len(x) / SR, 2)
        if self.cfg.get("escuta_gravar"):          # para conferir/ajustar depois (testes)
            res["arquivo"] = time.strftime("%H%M%S_") + f"{int(visto * 1000) % 1000:03d}"
            threading.Thread(target=self._gravar_teste, args=(res["arquivo"], visto, x), daemon=True).start()
        return res

    def _gravar_teste(self, nome, visto, x):
        """Guarda o trecho ouvido e, 3 s depois, um trecho maior (1 s antes ate 3 s depois da legenda)."""
        try:
            import soundfile as sf
            from .util import BASE
            pasta = os.path.join(BASE, "diagnostico", "escuta")
            os.makedirs(pasta, exist_ok=True)
            sf.write(os.path.join(pasta, nome + ".wav"), x, SR)
            time.sleep(max(0.0, visto + 3.2 - time.time()))
            sf.write(os.path.join(pasta, nome + "_longo.wav"), self.grav.trecho(visto - 1.0, visto + 3.0), SR)
        except Exception:
            pass

    def fechar(self):
        self.grav.fechar()
