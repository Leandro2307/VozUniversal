# -*- coding: utf-8 -*-
"""
Bateria de testes: reproduz uma sessao (as legendas na ordem e no tempo em que apareceram) com o
motor de voz DE VERDADE, sem tocar som, e mede tudo: atraso, buracos (picotado), aceleracoes,
falas puladas. Grava tambem o audio que voce teria ouvido, para escutar.

  python testes\\bateria.py gta                      roda a sequencia testes\\sequencias\\gta.jsonl
  python testes\\bateria.py gta --set xtts_pedacos=[8,12,20,32]   testa um ajuste sem mudar o config
  python testes\\bateria.py --do-log 23:53:55 23:55:30 gta   cria a sequencia a partir do log.txt
  python testes\\bateria.py --da-sessao diagnostico\\<arquivo>.jsonl nome   cria a partir de uma sessao gravada

Resultados: testes\\resultados\\<sequencia>_<hora>.txt (relatorio), .wav (audio) e .jsonl (eventos).
"""
import json
import os
import re
import sys
import threading
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(AQUI)
sys.path.insert(0, BASE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SEQ = os.path.join(AQUI, "sequencias")
RES = os.path.join(AQUI, "resultados")
os.makedirs(SEQ, exist_ok=True)
os.makedirs(RES, exist_ok=True)


# ----------------------------------------------------------------- criar sequencias
def do_log(ini, fim, nome):
    """Legendas a partir das linhas 🔊 do log.txt: apareceram em (hora do som - atraso)."""
    def seg(h):
        a, b, c = map(int, h.split(":"))
        return a * 3600 + b * 60 + c
    s0, s1 = seg(ini), seg(fim)
    itens = []
    for linha in open(os.path.join(BASE, "log.txt"), encoding="utf-8"):
        m = re.match(r"\[(\d\d:\d\d:\d\d)\] 🔊 \[(.*?) · .*?\] (.*?)\s+\(voz em (\d+) ms", linha)
        if not m or not s0 <= seg(m.group(1)) <= s1:
            continue
        quem = m.group(2)
        quem = "" if quem.startswith(("Narrador", "Teste")) else quem
        itens.append((seg(m.group(1)) - int(m.group(4)) / 1000, m.group(3).strip(), quem))
    if not itens:
        sys.exit("Nenhuma fala encontrada nesse intervalo do log.")
    t0 = itens[0][0] - 0.5
    ev = []
    for i, (t, texto, quem) in enumerate(itens):
        ev.append({"t": round(t - t0, 2), "texto": texto, "nome": quem})
        prox = itens[i + 1][0] - t0 if i + 1 < len(itens) else t - t0 + 3.0
        ev.append({"t": round(max(t - t0 + 0.6, prox - 0.15), 2), "texto": "", "nome": ""})
    salvar_seq(nome, ev)


def da_sessao(arq, nome):
    ev = []
    for linha in open(arq, encoding="utf-8"):
        d = json.loads(linha)
        if d.get("ev") == "leitura":
            ev.append({"t": d["t"], "texto": d.get("texto", ""), "nome": d.get("nome", "")})
    if not ev:
        sys.exit("Essa sessão não tem leituras gravadas.")
    t0 = ev[0]["t"] - 0.5
    for e in ev:
        e["t"] = round(e["t"] - t0, 3)
    salvar_seq(nome, ev)


def salvar_seq(nome, ev):
    caminho = os.path.join(SEQ, nome + ".jsonl")
    with open(caminho, "w", encoding="utf-8") as f:
        for e in ev:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    falas = [e for e in ev if e["texto"]]
    print(f"Sequência '{nome}' criada: {len(falas)} legendas em {ev[-1]['t']:.0f}s → {caminho}")


# ----------------------------------------------------------------- rodar
def rodar(nome, ajustes):
    import numpy as np
    import soundfile as sf
    from nucleo.diagnostico import diag
    from nucleo.leitor import Leitor
    from nucleo.util import carregar_config, log
    from nucleo.vozes import Banco, Narrador

    seq = [json.loads(l) for l in open(os.path.join(SEQ, nome + ".jsonl"), encoding="utf-8")]
    cfg = carregar_config()
    cfg.update(ajustes)
    hora = time.strftime("%Y%m%d_%H%M%S")
    saida = os.path.join(RES, f"{nome}_{hora}")
    diag.abrir(os.path.relpath(saida, os.path.join(BASE, "diagnostico")))
    eventos = []
    diag.ouvinte = eventos.append

    # alto-falante falso: guarda o audio na linha do tempo e espera o tempo real dele
    # (instalado ANTES de criar o narrador: ele ja comeca mandando silencio para o alto-falante)
    linha_tempo, T = [], {"t0": None}

    def alto_falante(bloco, sr):
        if T["t0"] is None or not np.any(bloco):
            time.sleep(len(bloco) / sr)
            return
        linha_tempo.append((time.time() - T["t0"], np.asarray(bloco, np.float32).copy(), sr))
        time.sleep(len(bloco) / sr)

    Narrador._escrever = lambda self, bloco, sr: alto_falante(bloco, sr)
    banco = Banco()
    narr = Narrador(cfg, banco)
    if cfg.get("motor") == "clonada":
        narr.ligar_clonada()
        while narr.estado_clonada in ("desligada", "carregando"):
            time.sleep(0.2)
        if narr.estado_clonada != "pronta":
            sys.exit("A voz clonada não carregou: " + narr.estado_clonada)
    # aquece cada voz usada (a 1a fala de uma voz prepara a amostra: nao e o que se mede)
    for vid in banco.vozes:
        if narr.clonada:
            narr.clonada.latentes(vid)

    leitor = Leitor(cfg)
    print(f"\n▶ Rodando '{nome}' ({len([e for e in seq if e['texto']])} legendas, {seq[-1]['t']:.0f}s) com "
          f"motor {cfg.get('motor')}" + (f", ajustes {ajustes}" if ajustes else "") + "...")
    diag.ev("bateria", sequencia=nome, ajustes=ajustes)
    T["t0"] = time.time()
    diag.t0 = T["t0"]
    i, atual = 0, ""
    fim = seq[-1]["t"] + 1.0
    while True:
        agora = time.time() - T["t0"]
        while i < len(seq) and seq[i]["t"] <= agora:
            atual = seq[i]["texto"]
            if atual:
                diag.ev("leitura", texto=atual, nome=seq[i].get("nome", ""))
            i += 1
        for fala, nova, visto in leitor.tick(atual, time.time()):
            narr.falar(fala, "narrador", nova=nova, visto=visto)
        if agora > fim and narr.fila.empty() and narr.audio.empty() and not narr.falando:
            break
        if agora > fim + 30:
            break
        time.sleep(cfg["intervalo_ms"] / 1000)
    time.sleep(0.6)
    diag.ouvinte = None

    # ---- audio ouvido
    if linha_tempo:
        sr = 24000
        total = int((max(t for t, _, _ in linha_tempo) + 5) * sr)
        mix = np.zeros(total, np.float32)
        for t, b, s in linha_tempo:
            if s != sr:
                b = np.interp(np.arange(0, len(b), s / sr), np.arange(len(b)), b).astype(np.float32)
            a = int(t * sr)
            mix[a:a + len(b)] += b[:max(0, total - a)]
        sf.write(saida + ".wav", np.clip(mix, -1, 1), sr)

    rel = relatorio(eventos, seq)
    with open(saida + ".txt", "w", encoding="utf-8") as f:
        f.write(f"Sequência: {nome}   ajustes: {ajustes}   motor: {cfg.get('motor')}\n\n" + rel)
    print(rel)
    print(f"\nArquivos: {saida}.txt  |  {saida}.wav (escute!)  |  {saida}.jsonl")
    sys.stdout.flush()
    os._exit(0)


def relatorio(ev, seq):
    import statistics as st
    falas = {}
    leituras = [e for e in ev if e["ev"] == "leitura"]
    for e in ev:
        fid = e.get("id")
        if fid is None:
            continue
        f = falas.setdefault(fid, {"buracos": [], "acelerou": None, "pulou": None, "pedacos": []})
        if e["ev"] == "fala":
            f.update(texto=e["texto"], t_fala=e["t"], espera_leitor=e["espera_ms"])
        elif e["ev"] == "tocar_inicio":
            f["t_som"] = e["t"]
            f["atraso"] = e["atraso_ms"]
        elif e["ev"] == "buraco":
            f["buracos"].append(e["ms"])
        elif e["ev"] == "acelerou":
            f["acelerou"] = max(f["acelerou"] or 1.0, e["fator"])
        elif e["ev"] == "pulou":
            f["pulou"] = e["resto_s"]
        elif e["ev"] == "pedaco":
            f["pedacos"].append((e["pronto_ms"], e["audio_s"]))
        elif e["ev"] == "gerar_fim":
            f["vel_geracao"] = e["velocidade"]
        elif e["ev"] == "descartada":
            f["descartada"] = e["motivo"]
        elif e["ev"] == "corte":
            f["corte"] = True
    linhas = [f"{'#':>3} {'atraso':>7} {'buracos':>13} {'acel':>5} {'gera':>5}  texto"]
    for fid in sorted(falas):
        f = falas[fid]
        if "texto" not in f:
            continue
        bur = f"{len(f['buracos'])}× {sum(f['buracos']):4d}ms" if f["buracos"] else "-"
        ac = f"{f['acelerou']}x" if f["acelerou"] else "-"
        extra = ""
        if f.get("pulou"):
            extra += f"  [PULOU {f['pulou']}s]"
        if f.get("descartada"):
            extra += f"  [DESCARTADA: {f['descartada']}]"
        if f.get("corte"):
            extra += "  [CORTE XTTS]"
        if "t_som" not in f and not f.get("descartada"):
            extra += "  [NÃO TOCOU]"
        linhas.append(f"{fid:3d} {str(f.get('atraso', '-')) + 'ms':>7} {bur:>13} {ac:>5} "
                      f"{str(f.get('vel_geracao', '-')) + 'x':>5}  {f['texto'][:70]}{extra}")
    tocadas = [f for f in falas.values() if "t_som" in f]
    atrasos = [f["atraso"] for f in tocadas]
    bur_total = [b for f in falas.values() for b in f["buracos"]]
    com_bur = sum(1 for f in tocadas if f["buracos"])
    linhas.append("")
    linhas.append("RESUMO")
    linhas.append(f"  legendas na tela ........ {len(leituras)}")
    linhas.append(f"  trechos falados ......... {len(tocadas)} de {len(falas)}")
    if atrasos:
        linhas.append(f"  atraso até o som ........ média {st.mean(atrasos):.0f} ms | mediana {st.median(atrasos):.0f} ms | pior {max(atrasos)} ms")
    linhas.append(f"  PICOTADO (buracos) ...... {len(bur_total)} buracos em {com_bur} falas, total {sum(bur_total)} ms"
                  + (f", maior {max(bur_total)} ms" if bur_total else ""))
    linhas.append(f"  aceleradas .............. {sum(1 for f in tocadas if f['acelerou'])}")
    linhas.append(f"  puladas (resto cortado) . {sum(1 for f in falas.values() if f.get('pulou'))}")
    linhas.append(f"  descartadas ............. {sum(1 for f in falas.values() if f.get('descartada'))}")
    linhas.append(f"  cortes do XTTS .......... {sum(1 for f in falas.values() if f.get('corte'))}")
    prim = [f["pedacos"][0][0] for f in falas.values() if f["pedacos"]]
    if prim:
        linhas.append(f"  1º pedaço pronto em ..... média {st.mean(prim):.0f} ms")
    nota = 100 - min(60, sum(bur_total) / 50) - min(25, (st.mean(atrasos) - 300) / 20 if atrasos else 25) \
        - 5 * sum(1 for f in falas.values() if f.get("pulou")) - 3 * sum(1 for f in falas.values() if f.get("corte"))
    linhas.append(f"  NOTA (0-100) ............ {max(0, nota):.0f}   (perde ponto por buraco, atraso, pulo e corte)")
    return "\n".join(linhas)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--do-log":
        do_log(a[1], a[2], a[3])
    elif a and a[0] == "--da-sessao":
        da_sessao(a[1], a[2])
    elif a:
        ajustes = {}
        if "--set" in a:
            for kv in a[a.index("--set") + 1:]:
                k, v = kv.split("=", 1)
                try:
                    ajustes[k] = json.loads(v)
                except Exception:
                    ajustes[k] = v
        rodar(a[0], ajustes)
    else:
        print(__doc__)
