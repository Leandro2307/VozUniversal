# -*- coding: utf-8 -*-
"""
Gera "dist\\Instalar Voz Universal.exe": empacota o programa (sem os arquivos pessoais e sem os
pesados que o instalador baixa sozinho) dentro do instalador.

  python instalador\\gerar_exe.py
"""
import fnmatch
import os
import subprocess
import sys
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(AQUI)

# o que NAO vai junto: pessoal, gerado na hora, ou baixado pelo instalador
FORA = [
    ".git/*", "*/__pycache__/*", "__pycache__/*", "*.pyc", "config.json", "log.txt", "perfis/*",
    "diagnostico/*", "testes/resultados/*", "recursos/janela/*", "recursos/piper_vozes/*",
    "banco_vozes/_originais/*", "banco_vozes/_antigas/*", "banco_vozes/*/latentes.pt",
    "dist/*", "build/*", "instalador/build/*", "instalador/dist/*", "instalador/programa.zip",
    "*.spec", "Iniciar Voz Universal.bat",
]


def fora(rel):
    rel = rel.replace("\\", "/")
    return any(fnmatch.fnmatch(rel, p) for p in FORA)


def empacotar():
    destino = os.path.join(AQUI, "programa.zip")
    n = 0
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for raiz, pastas, arqs in os.walk(BASE):
            for a in arqs:
                cam = os.path.join(raiz, a)
                rel = os.path.relpath(cam, BASE)
                if fora(rel):
                    continue
                z.write(cam, rel)
                n += 1
    print(f"programa.zip: {n} arquivos, {os.path.getsize(destino) / 2 ** 20:.1f} MB")
    return destino


def main():
    zipado = empacotar()
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
    icone = os.path.join(BASE, "recursos", "icone.ico")
    # python instalador\gerar_exe.py [pasta]   (padrao: dist; outra pasta se o .exe antigo estiver aberto)
    saida = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(BASE, "dist")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
            "--name", "Instalar Voz Universal", "--add-data", f"{zipado};.",
            "--distpath", saida, "--workpath", os.path.join(AQUI, "build"),
            "--specpath", AQUI, os.path.join(AQUI, "instalador.py")]
    if os.path.exists(icone):
        args[4:4] = ["--icon", icone]
    subprocess.check_call(args)
    exe = os.path.join(saida, "Instalar Voz Universal.exe")
    print(f"\nPRONTO: {exe} ({os.path.getsize(exe) / 2 ** 20:.1f} MB)")


if __name__ == "__main__":
    main()
