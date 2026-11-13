# -*- coding: utf-8 -*-
"""
Resumo de uma sessao gravada em diagnostico\\*.jsonl (a ultima, se nao disser qual).

  python testes\\analisar.py                 resumo da ultima sessao
  python testes\\analisar.py <arquivo.jsonl>  resumo de uma sessao
  python testes\\analisar.py --desde 120     so a partir do segundo 120 da sessao
"""
import collections
import glob
import json
import os
import statistics as st
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def carregar(arq):
    return [json.loads(l) for l in open(arq, encoding="utf-8") if l.strip()]


def resumo(ev, desde=0.0, ate=1e9):
    ev = [e for e in ev if desde <= e.get("t", 0) <= ate]
    por = collections.defaultdict(list)
    for e in ev:
        por[e["ev"]].append(e)
    falas = {e["id"]: e for e in por["fala"]}
    inicio = {e["id"]: e for e in por["tocar_inicio"]}
    bur = collections.defaultdict(list)
    for e in por["buraco"]:
        bur[e["id"]].append(e["ms"])
    acel = collections.defaultdict(float)
    for e in por["acelerou"]:
        acel[e["id"]] = max(acel[e["id"]], e["fator"])
    out = []
    dur = (ev[-1]["t"] - ev[0]["t"]) if ev else 0
    out.append(f"Sessão: {dur:.0f}s de eventos ({len(ev)} eventos)")
    for e in por["fonte"]:
        out.append(f"  fonte: {e.get('titulo')} ({e.get('jogo')})")
    for e in por["video"][-3:]:
        out.append(f"  vídeo achado em {e['rect']} (confiança {e['confianca']}) no t={e['t']:.0f}s")
    out.append("")
    out.append("FALAS (t = quando entrou na fila)")
    for fid, f in sorted(falas.items()):
        ini = inicio.get(fid)
        marca = []
        if bur[fid]:
            marca.append(f"buracos {bur[fid]}")
        if acel.get(fid):
            marca.append(f"acelerou {acel.get(fid)}x")
        if not ini:
            marca.append("NÃO TOCOU")
        quem = f"[{f['quem']}] " if f.get("quem") else ""
        atraso = f"{ini['atraso_ms']:5d}ms" if ini else "   -   "
        out.append(f"  t={f['t']:6.1f} {atraso} {quem}{f['texto'][:75]}" + (f"   <- {', '.join(marca)}" if marca else ""))
    out.append("")
    if por["descartada"]:
        out.append("DESCARTADAS: " + "; ".join(f"#{e['id']} {e['motivo']}" for e in por["descartada"]))
    desc = collections.Counter()
    exemplos = {}
    for e in por["descartei"]:
        for t, m in e.get("blocos", []):
            chave = m.split(" (")[0]
            desc[chave] += 1
            exemplos.setdefault(chave, t)
    if desc:
        out.append("TEXTO DA TELA IGNORADO (motivo: vezes, exemplo)")
        for m, n in desc.most_common():
            out.append(f"  {m}: {n}x   ex.: “{exemplos[m]}”")
    fichas = {}
    for e in por["ficha"]:
        fichas[e["nome"]] = e
    if fichas:
        out.append("PERSONAGENS")
        for n, e in fichas.items():
            out.append(f"  {n}: {e['categoria']} ({e['confianca'] * 100:.0f}%), voz {e['voz']}  pistas: {', '.join(e['pistas'][:3])}")
    for e in por["auto_pedacos"]:
        out.append(f"AUTO-AJUSTE t={e['t']:.0f}s: pedaços {e['de']} -> {e['para']}")
    for e in por["corte"]:
        out.append(f"CORTE XTTS t={e['t']:.0f}s: {e['frase'][:60]}")
    for e in por["pulou"]:
        out.append(f"PULOU t={e['t']:.0f}s fala #{e['id']} (resto {e['resto_s']}s)")
    if por["sistema"]:
        s = por["sistema"][-1]
        out.append(f"SISTEMA (último): RAM livre {s['ram_livre_gb']} GB | VRAM livre {s['vram_livre_gb']} GB | CPU {s['cpu_total']}% | "
                   f"programa {s['programa_mb']} MB | OCR {s['ocr_medio_ms']} ms/leitura, {s['leituras_por_s']} leituras/s")
    at = [e["atraso_ms"] for e in por["tocar_inicio"]]
    todos_bur = [m for l in bur.values() for m in l]
    out.append("")
    out.append("RESUMO")
    out.append(f"  legendas lidas ......... {len([e for e in por['leitura'] if e.get('texto')])}")
    out.append(f"  falas .................. {len(falas)} (tocaram {len(inicio)})")
    if at:
        out.append(f"  atraso até o som ....... mediana {st.median(at):.0f} ms | média {st.mean(at):.0f} ms | pior {max(at)} ms")
    out.append(f"  buracos (picotado) ..... {len(todos_bur)} em {len(bur)} falas, total {sum(todos_bur)} ms")
    out.append(f"  aceleradas ............. {len(acel)}")
    return "\n".join(out)


if __name__ == "__main__":
    a = sys.argv[1:]
    desde = 0.0
    if "--desde" in a:
        i = a.index("--desde")
        desde = float(a[i + 1])
        del a[i:i + 2]
    arq = a[0] if a else max(glob.glob(os.path.join(BASE, "diagnostico", "2*.jsonl")), key=os.path.getmtime)
    print(arq)
    print(resumo(carregar(arq), desde))
