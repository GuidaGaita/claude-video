"""Prepara um projeto de edicao em trabalho/<projeto>/.

1. Transcreve cada fonte com /transcrever (o que ja foi transcrito sai do cache).
2. Gera takes.md: uma linha por frase com [inicio-fim] em segundos, quebrando em
   pausas >= 0,5 s ou fim de frase, e linhas "~som" onde ha som forte sem palavra
   reconhecida (o Parakeet costuma omitir "e...", "hum", risos). E a leitura
   principal para decidir os cortes.
3. Cria o edl.json (ou acrescenta as fontes novas) e o projeto.md.

Uso (da raiz do projeto):
  python .claude/skills/editar/preparar.py <projeto> <video> [<video> ...]
  python .claude/skills/editar/preparar.py <projeto>      refaz o takes.md com as fontes do edl.json
"""

from __future__ import annotations

import argparse
import json
import re
import string
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np

from comum import QUADRO, RAIZ, caminho, carregar_palavras, iniciar, ler_audio, rel, sondar, trocas_de_plano, volume_db

TRANSCREVER = RAIZ / ".claude" / "skills" / "transcrever" / "transcrever.py"
PAUSA_FRASE = 0.5     # lacuna entre palavras que quebra a linha
LACUNA_MIN = 0.3      # lacunas menores nao sao examinadas
SOM_MIN = 0.2         # som continuo minimo para virar "~som"
LIMIAR_FRACAO = 0.6   # limiar entre o piso de ruido (0) e o nivel tipico da fala (1)


def transcrever(video: Path) -> Path:
    destino = RAIZ / "transcricoes" / video.stem / "transcript.json"
    if destino.exists() and destino.stat().st_mtime >= video.stat().st_mtime:
        return destino
    print(f"Transcrevendo {video.name} (na CPU leva alguns minutos para videos longos)...", flush=True)
    r = subprocess.run([sys.executable, str(TRANSCREVER), str(video)], cwd=RAIZ)
    if r.returncode != 0 or not destino.exists():
        sys.exit(f"A transcricao de {video.name} falhou.")
    return destino


def sons_sem_palavra(palavras: list[dict], db: np.ndarray) -> list[tuple[float, float]]:
    """Lacunas entre palavras com som forte continuo: provavel "e...", "hum", riso ou ruido."""
    if db.size == 0 or not palavras:
        return []

    def idx(t: float) -> int:
        return min(db.size, max(0, int(t / QUADRO)))

    fala = np.concatenate([db[idx(w["start"]):idx(w["end"])] for w in palavras] or [np.zeros(0)])
    if fala.size == 0:
        return []
    piso = float(np.percentile(db, 10))
    limiar = piso + LIMIAR_FRACAO * (float(np.median(fala)) - piso)

    achados = []
    for a, b in zip(palavras, palavras[1:]):
        ini, fim = a["end"] + 0.08, b["start"] - 0.05  # folga para o fim/inicio real das palavras
        if b["start"] - a["end"] < LACUNA_MIN or fim - ini < SOM_MIN:
            continue
        alto = db[idx(ini):idx(fim)] > limiar
        melhor = atual = comeco = melhor_comeco = 0
        for k, v in enumerate(alto):
            if v:
                if atual == 0:
                    comeco = k
                atual += 1
                if atual > melhor:
                    melhor, melhor_comeco = atual, comeco
            else:
                atual = 0
        if melhor * QUADRO >= SOM_MIN:
            s = ini + melhor_comeco * QUADRO
            achados.append((s, s + melhor * QUADRO))
    return achados


def frases(palavras: list[dict]) -> list[dict]:
    blocos: list[list[dict]] = []
    atual: list[dict] = []
    for w in palavras:
        if atual and (w["start"] - atual[-1]["end"] >= PAUSA_FRASE or atual[-1]["text"][-1:] in ".?!"):
            blocos.append(atual)
            atual = []
        atual.append(w)
    if atual:
        blocos.append(atual)
    return [{"start": b[0]["start"], "end": b[-1]["end"], "text": " ".join(w["text"] for w in b)} for b in blocos]


def duracao_txt(s: float) -> str:
    return f"{s:.1f}s" if s < 60 else f"{int(s // 60)}m {s % 60:04.1f}s"


def secao(chave: str, video: Path, info: dict, palavras: list[dict], sons: list[tuple[float, float]],
          planos: list[float]) -> str:
    fs = frases(palavras)
    fps = float(info["fps"])
    cab = (f"## {chave} — {video.name}  ({duracao_txt(info['duracao'])} · {info['largura']}x{info['altura']}"
           f" · {fps:.3g} fps{' · HDR' if info['hdr'] else ''} · {len(fs)} frases · {len(sons)} ~som)")
    linhas = [cab]
    if not palavras:
        linhas.append("  (sem fala reconhecida)")
    eventos = [(f["start"], f"  [{f['start']:06.2f}-{f['end']:06.2f}] {f['text']}") for f in fs]
    eventos += [(a, f"      ~som {a:06.2f}-{b:06.2f} ({b - a:.1f}s)") for a, b in sons]
    eventos += [(c, f"      | troca de plano {c:06.2f}") for c in planos]
    linhas += [texto for _, texto in sorted(eventos, key=lambda e: e[0])]
    return "\n".join(linhas)


def nova_chave(fontes: dict) -> str:
    for c in string.ascii_uppercase:
        if c not in fontes:
            return c
    n = len(fontes) + 1
    while f"F{n}" in fontes:
        n += 1
    return f"F{n}"


def main() -> None:
    iniciar()
    ap = argparse.ArgumentParser(description="Prepara um projeto de edicao (transcricao + takes.md + edl.json)")
    ap.add_argument("projeto", help="nome do projeto (pasta em trabalho/)")
    ap.add_argument("videos", nargs="*", type=Path, help="videos de origem (ex.: brutos/take1.mp4)")
    args = ap.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.projeto):
        sys.exit("Nome do projeto: so letras, numeros, - e _ (sem espacos nem acentos).")
    pasta = RAIZ / "trabalho" / args.projeto
    pasta.mkdir(parents=True, exist_ok=True)
    arq_edl = pasta / "edl.json"
    edl = json.loads(arq_edl.read_text(encoding="utf-8")) if arq_edl.exists() else {
        "versao": 1, "projeto": args.projeto, "fontes": {}, "trechos": [],
        "cor": "nenhuma", "legenda": None, "sobreposicoes": [],
    }
    fontes = edl.setdefault("fontes", {})
    conhecidos = {caminho(f["video"]).resolve(): k for k, f in fontes.items()}
    for v in args.videos:
        v = v.resolve()
        if not v.is_file():
            sys.exit(f"Video nao encontrado: {v}")
        if v not in conhecidos:
            k = nova_chave(fontes)
            fontes[k] = {"video": rel(v), "transcricao": rel(RAIZ / "transcricoes" / v.stem / "transcript.json")}
            conhecidos[v] = k
    if not fontes:
        sys.exit("Passe pelo menos um video.")

    secoes, resumo, todos_sons = [], [], {}
    for k, f in fontes.items():
        video = caminho(f["video"])
        if not video.is_file():
            sys.exit(f"Fonte {k} sumiu: {f['video']}")
        f["transcricao"] = rel(transcrever(video))
        info = sondar(video)
        palavras = carregar_palavras(caminho(f["transcricao"]))
        sons = sons_sem_palavra(palavras, volume_db(ler_audio(video))) if info["tem_audio"] else []
        todos_sons[k] = [[round(a, 2), round(b, 2)] for a, b in sons]
        planos = trocas_de_plano(video) if info["tem_video"] else []
        secoes.append(secao(k, video, info, palavras, sons, planos))
        resumo.append(f"  {k} = {f['video']}  ({duracao_txt(info['duracao'])}, {len(palavras)} palavras, {len(sons)} ~som)")

    takes = pasta / "takes.md"
    takes.write_text(
        f"# Takes — {args.projeto}\n\n"
        "Tempos em segundos do video de origem. Linha nova = pausa >= 0,5 s ou fim de frase.\n"
        "`~som` = som forte sem palavra reconhecida (provavel \"e...\", \"hum\", riso, ruido). "
        "Confira com a linha do tempo antes de decidir.\n"
        "`| troca de plano` = a imagem muda de plano dentro do bruto (zoom do celular, emenda). Nao termine nem "
        "comece um trecho a menos de 0,6 s dela: aparece um \"pisca\" de outro plano.\n\n"
        + "\n\n".join(secoes) + "\n",
        encoding="utf-8")
    arq_edl.write_text(json.dumps(edl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (pasta / "sons.json").write_text(json.dumps(todos_sons) + "\n", encoding="utf-8")  # o render.py avisa

    memoria = pasta / "projeto.md"
    if not memoria.exists():
        memoria.write_text(
            f"# Projeto: {args.projeto}\n\nCriado em {date.today().isoformat()}.\n\n"
            "<!-- Uma secao por sessao:\n## Sessao N — AAAA-MM-DD\n"
            "**Estrategia:** ...\n**Decisoes:** cortes, takes escolhidos, legenda, cor, animacoes e por que\n"
            "**Pendente:** ...\n-->\n", encoding="utf-8")

    print(f"Projeto {args.projeto}:")
    print("\n".join(resumo))
    print(f"Leia: {rel(takes)}   EDL: {rel(arq_edl)}   Memoria: {rel(memoria)}")


if __name__ == "__main__":
    main()
