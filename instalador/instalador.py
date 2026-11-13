# -*- coding: utf-8 -*-
"""
Instalador do Voz Universal (vira "Instalar Voz Universal.exe" com o PyInstaller).

Faz tudo sozinho na outra maquina, sem precisar de Python instalado:
  1. baixa o "uv" (instalador de Python ultrarrapido) e, com ele, o Python 3.11
  2. instala as dependencias (torch com CUDA se tiver placa NVIDIA; senao, versao para CPU)
  3. copia o programa (vai embutido dentro do .exe) para %LOCALAPPDATA%\\VozUniversal
  4. baixa as vozes Piper e o modelo da voz clonada (XTTS v2, ~1,8 GB)
  5. cria o atalho "Voz Universal" na Area de Trabalho e no Menu Iniciar
"""
import io
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import zipfile

NOME = "Voz Universal"
DESTINO_PADRAO = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "VozUniversal")
UV_URL = "https://github.com/astral-sh/uv/releases/download/0.5.11/uv-x86_64-pc-windows-msvc.zip"
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/pt/pt_BR/{v}/medium/pt_BR-{v}-medium.onnx"
PIPER_VOZES = ["faber", "cadu", "jeff"]
SEM_JANELA = 0x08000000


def recurso(nome):
    """Arquivo embutido no .exe (PyInstaller) ou ao lado deste script."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nome)


class Instalacao:
    def __init__(self, destino, avisar):
        self.destino = destino
        self.avisar = avisar            # avisar(texto, progresso 0-100 ou None)
        self.uv = os.path.join(destino, "ferramentas", "uv.exe")
        self.venv = os.path.join(destino, ".venv")
        self.py = os.path.join(self.venv, "Scripts", "python.exe")
        self.pyw = os.path.join(self.venv, "Scripts", "pythonw.exe")

    # ------------------------------------------------------------- utilidades
    def baixar(self, url, arq, rotulo, p0, p1):
        os.makedirs(os.path.dirname(arq), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "VozUniversal-instalador"})
        with urllib.request.urlopen(req, timeout=60) as r, open(arq + ".parte", "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            feito = 0
            while True:
                bloco = r.read(1 << 20)
                if not bloco:
                    break
                f.write(bloco)
                feito += len(bloco)
                if total:
                    self.avisar(f"{rotulo} ({feito >> 20} de {total >> 20} MB)", p0 + (p1 - p0) * feito / total)
        os.replace(arq + ".parte", arq)

    def rodar(self, args, rotulo, p0, p1, env=None):
        """Roda um comando mostrando a ultima linha da saida no instalador."""
        self.avisar(rotulo, p0)
        e = dict(os.environ, **(env or {}))
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", creationflags=SEM_JANELA, env=e)
        linhas = []
        for linha in proc.stdout:
            linha = linha.strip()
            if linha:
                linhas.append(linha)
                self.avisar(f"{rotulo}\n{linha[:110]}", None)
        if proc.wait() != 0:
            raise RuntimeError(f"Falhou: {rotulo}\n" + "\n".join(linhas[-15:]))
        self.avisar(rotulo, p1)

    @staticmethod
    def tem_nvidia():
        try:
            r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=10, creationflags=SEM_JANELA)
            return r.returncode == 0 and "GPU" in r.stdout
        except Exception:
            return False

    # ------------------------------------------------------------- etapas
    def tudo(self):
        os.makedirs(self.destino, exist_ok=True)
        log = os.path.join(self.destino, "instalacao.log")
        sys.stdout = open(log, "a", encoding="utf-8")
        print(time.strftime("\n==== %Y-%m-%d %H:%M:%S ===="), flush=True)

        # 1) programa
        self.avisar("Copiando o programa...", 2)
        with zipfile.ZipFile(recurso("programa.zip")) as z:
            for item in z.namelist():
                alvo = os.path.join(self.destino, item)
                # nunca apaga o que o usuario ja tem (config, perfis, vozes trocadas)
                if os.path.exists(alvo) and item.replace("\\", "/").split("/")[0] in ("config.json", "perfis"):
                    continue
                if item.endswith("/"):
                    os.makedirs(alvo, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(alvo), exist_ok=True)
                    with z.open(item) as src, open(alvo, "wb") as dst:
                        shutil.copyfileobj(src, dst)

        # 2) uv + Python 3.11
        if not os.path.exists(self.uv):
            z = os.path.join(self.destino, "ferramentas", "uv.zip")
            self.baixar(UV_URL, z, "Baixando o instalador de Python (uv)", 4, 8)
            with zipfile.ZipFile(z) as zz:
                for n in zz.namelist():
                    if n.endswith("uv.exe"):
                        with zz.open(n) as src, open(self.uv, "wb") as dst:
                            shutil.copyfileobj(src, dst)
            os.remove(z)
        env_uv = {"UV_PYTHON_INSTALL_DIR": os.path.join(self.destino, "ferramentas", "python"),
                  "UV_CACHE_DIR": os.path.join(self.destino, "ferramentas", "cache")}
        if not os.path.exists(self.py):
            self.rodar([self.uv, "venv", "--python", "3.11", self.venv], "Instalando o Python 3.11...", 8, 14, env_uv)

        # 3) dependencias
        gpu = self.tem_nvidia()
        indice = "https://download.pytorch.org/whl/cu121" if gpu else "https://download.pytorch.org/whl/cpu"
        self.avisar("Placa de vídeo NVIDIA encontrada: voz clonada rápida." if gpu else
                    "Sem placa NVIDIA: a voz clonada vai ficar lenta (a voz rápida funciona bem).", 14)
        req = os.path.join(self.destino, "requirements.txt")
        # O antivirus do Windows as vezes trava os milhares de arquivos do torch enquanto sao
        # extraidos ("Acesso negado, os error 5"). O que ja baixou fica no cache: tenta de novo.
        env_uv["UV_LINK_MODE"] = "copy"
        for tentativa in range(1, 5):
            try:
                self.rodar([self.uv, "pip", "install", "--python", self.py, "-r", req,
                            "--extra-index-url", indice, "--index-strategy", "unsafe-best-match"],
                           "Instalando os componentes (pode levar 10-20 min, são ~4 GB)..." +
                           (f" (tentativa {tentativa})" if tentativa > 1 else ""), 15, 70, env_uv)
                break
            except RuntimeError as e:
                if tentativa == 4 or "os error 5" not in str(e).lower() and "acesso negado" not in str(e).lower():
                    raise
                self.avisar("O antivírus segurou alguns arquivos; tentando de novo em 15 s...", None)
                time.sleep(15)

        # 4) vozes Piper
        pasta_piper = os.path.join(self.destino, "recursos", "piper_vozes")
        for i, v in enumerate(PIPER_VOZES):
            arq = os.path.join(pasta_piper, f"pt_BR-{v}-medium.onnx")
            for ext in ("", ".json"):
                if not os.path.exists(arq + ext):
                    self.baixar(PIPER.format(v=v) + ext, arq + ext, f"Baixando voz rápida {v}", 70 + i * 3, 73 + i * 3)

        # 5) modelo da voz clonada (baixa uma vez agora, para a 1a abertura ser rapida)
        self.rodar([self.py, "-c",
                    "import os; os.environ['COQUI_TOS_AGREED']='1'\n"
                    "from TTS.utils.manage import ModelManager\n"
                    "ModelManager().download_model('tts_models/multilingual/multi-dataset/xtts_v2')\n"
                    "print('modelo pronto')"],
                   "Baixando o modelo da voz clonada (XTTS v2, ~1,8 GB)...", 80, 95)

        # 6) iniciar + atalhos
        bat = os.path.join(self.destino, "Iniciar Voz Universal.bat")
        with open(bat, "w", encoding="utf-8") as f:
            f.write('@echo off\r\ncd /d "%~dp0"\r\nstart "" ".venv\\Scripts\\pythonw.exe" app.py\r\n')
        if "--sem-atalhos" not in sys.argv:
            self.atalhos()
        self.verificar_ocr()
        self.avisar("Pronto! O Voz Universal está instalado.", 100)

    def atalhos(self):
        icone = os.path.join(self.destino, "recursos", "icone.ico")
        for pasta in (os.path.join(os.path.expanduser("~"), "Desktop"),
                      os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")):
            if not os.path.isdir(pasta):
                continue
            lnk = os.path.join(pasta, f"{NOME}.lnk")
            ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
                  f"$s.TargetPath='{self.pyw}';$s.Arguments='app.py';$s.WorkingDirectory='{self.destino}';"
                  + (f"$s.IconLocation='{icone}';" if os.path.exists(icone) else "") + "$s.Save()")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=SEM_JANELA)

    def verificar_ocr(self):
        """O programa le a tela com o OCR do Windows em portugues: avisa se faltar."""
        try:
            r = subprocess.run([self.py, "-c", "import winocr,sys;from PIL import Image;"
                                "winocr.recognize_pil_sync(Image.new('RGB',(200,60),'white'),'pt-BR');print('ok')"],
                               capture_output=True, text=True, timeout=60, creationflags=SEM_JANELA)
            if "ok" not in r.stdout:
                self.avisar("Atenção: falta o pacote de leitura de tela (OCR) em português no Windows.\n"
                            "Configurações > Hora e idioma > Idioma > Português (Brasil) > Opções > instalar "
                            "\"Reconhecimento óptico de caracteres\".", None)
                time.sleep(6)
        except Exception:
            pass


# ----------------------------------------------------------------- janela do instalador
def janela():
    import tkinter as tk
    from tkinter import ttk, filedialog

    raiz = tk.Tk()
    raiz.title(f"Instalar {NOME}")
    raiz.geometry("640x330")
    raiz.configure(bg="#0b1230")
    raiz.resizable(False, False)
    estilo = ttk.Style()
    estilo.theme_use("clam")
    estilo.configure("Azul.Horizontal.TProgressbar", troughcolor="#16204a", background="#7c8cff", bordercolor="#0b1230")
    tk.Label(raiz, text=NOME, font=("Segoe UI", 22, "bold"), fg="white", bg="#0b1230").pack(pady=(26, 0))
    tk.Label(raiz, text="vozes para qualquer jogo, geradas na hora", font=("Segoe UI", 10), fg="#9fb2ff", bg="#0b1230").pack()
    destino = tk.StringVar(value=DESTINO_PADRAO)
    linha = tk.Frame(raiz, bg="#0b1230")
    linha.pack(pady=(18, 4), fill="x", padx=30)
    tk.Label(linha, text="Instalar em:", fg="#cfd6ff", bg="#0b1230", font=("Segoe UI", 9)).pack(side="left")
    tk.Entry(linha, textvariable=destino, width=52, bg="#16204a", fg="white", insertbackground="white",
             relief="flat").pack(side="left", padx=6)
    tk.Button(linha, text="...", relief="flat", bg="#1d2858", fg="white",
              command=lambda: destino.set(filedialog.askdirectory() or destino.get())).pack(side="left")
    texto = tk.Label(raiz, text="Precisa de internet e ~8 GB livres. Pode demorar uns 20 minutos.",
                     fg="#cfd6ff", bg="#0b1230", font=("Segoe UI", 9), justify="left", wraplength=580)
    texto.pack(pady=8, padx=30, anchor="w")
    barra = ttk.Progressbar(raiz, style="Azul.Horizontal.TProgressbar", length=580, mode="determinate")
    barra.pack(pady=4)
    botao = tk.Button(raiz, text="Instalar", font=("Segoe UI", 11, "bold"), bg="#7c8cff", fg="white",
                      relief="flat", padx=26, pady=6)
    botao.pack(pady=14)
    fila = queue.Queue()

    def avisar(t, p):
        fila.put((t, p))

    def atualizar():
        try:
            while True:
                t, p = fila.get_nowait()
                if t == "__fim__":
                    botao.configure(text="Abrir o Voz Universal", state="normal", command=abrir)
                    continue
                if t == "__erro__":
                    botao.configure(text="Fechar", state="normal", command=raiz.destroy)
                    continue
                texto.configure(text=t)
                if p is not None:
                    barra["value"] = p
        except queue.Empty:
            pass
        raiz.after(150, atualizar)

    def abrir():
        d = destino.get()
        subprocess.Popen([os.path.join(d, ".venv", "Scripts", "pythonw.exe"), "app.py"], cwd=d)
        raiz.destroy()

    def trabalhar():
        try:
            Instalacao(destino.get(), avisar).tudo()
            fila.put(("__fim__", None))
        except Exception as e:
            avisar(f"Erro: {e}\n(detalhes em {os.path.join(destino.get(), 'instalacao.log')})", None)
            try:
                print(traceback.format_exc(), flush=True)
            except Exception:
                pass
            fila.put(("__erro__", None))

    def comecar():
        botao.configure(state="disabled", text="Instalando...")
        threading.Thread(target=trabalhar, daemon=True).start()

    botao.configure(command=comecar)
    raiz.after(150, atualizar)
    raiz.mainloop()


if __name__ == "__main__":
    if "--sem-janela" in sys.argv:          # instalacao por linha de comando (testes)
        d = sys.argv[sys.argv.index("--sem-janela") + 1] if len(sys.argv) > sys.argv.index("--sem-janela") + 1 else DESTINO_PADRAO
        try:
            Instalacao(d, lambda t, p: print(f"[{'' if p is None else int(p)}%] {t}", flush=True)).tudo()
            print("INSTALACAO_OK", flush=True)
            os._exit(0)
        except Exception:
            print("INSTALACAO_ERRO\n" + traceback.format_exc(), flush=True)
            os._exit(1)
    else:
        janela()
