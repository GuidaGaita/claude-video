"""Modo cinema: legenda do HyperFrames embedded-captions, com a palavra principal ATRAS da pessoa.

Prepara o projeto a partir do corte do /editar, usando a nossa transcricao (Parakeet + correcoes):
  1. Corte sem legenda: trabalho/<projeto>/sem_legenda.mp4 (render.py --sem-legenda --sem-loudnorm).
  2. hyperframes init videos/<projeto>-cinema (skill embedded-captions, sem transcricao propria).
  3. transcript.json no formato do embedded-captions ({"language_code", "words"}): ele pula o WhisperX.
  4. Instala sharp/puppeteer/gsap no projeto (versoes do SKILL.md do plugin) e roda o prepare.sh:
     recorte da pessoa (CPU: ~2-3 min a cada 10 s de video) + zonas seguras.
Depois siga o SKILL.md do embedded-captions do passo 3 em diante (identidade, theme.json, preview, render)
e finalize com --finalizar (loudness -14 LUFS -> final/<projeto>.mp4).

Uso (da raiz do projeto):
  python .claude/skills/legendar/cinema.py <projeto> [--idioma pt]
  python .claude/skills/legendar/cinema.py <projeto> --finalizar
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

from legendar import cli_hyperframes, raiz_plugin
from comum import RAIZ, carregar_edl, fps_comum, iniciar, montar_linha, rel, rodar, sondar
from render import aplicar_correcoes, carregar_fontes, filtro_loudnorm, palavras_saida

RENDER = RAIZ / ".claude" / "skills" / "editar" / "render.py"


def preparar(pasta: Path, edl: dict, idioma: str, fps_arg: str | None) -> None:
    plugin = raiz_plugin()
    if not plugin:
        sys.exit("O modo cinema precisa do plugin hyperframes instalado no Claude Code.")
    skill = plugin / "skills" / "embedded-captions"
    nome = pasta.name
    projeto = RAIZ / "videos" / f"{nome}-cinema"
    infos, palavras = carregar_fontes(edl)
    primeira = infos[edl["trechos"][0]["fonte"]]
    if not fps_arg and primeira["fps"].denominator != 1:
        # O embedded-captions renderiza em fps inteiro: 29,97 viraria 30 e o video escorregaria do audio.
        fps_arg = str(round(float(primeira["fps"])))
        print(f"Fonte a {float(primeira['fps']):.3f} fps: o corte do modo cinema sai a {fps_arg} fps.")

    # 1. corte sem legenda (o embedded-captions entrega o video intocado + legenda)
    corte = pasta / "sem_legenda.mp4"
    edl_arq = pasta / "edl.json"
    if not corte.exists() or corte.stat().st_mtime < edl_arq.stat().st_mtime:
        print("[1/4] Renderizando o corte sem legenda...", flush=True)
        cmd = [sys.executable, RENDER, nome, "--sem-legenda", "--sem-loudnorm", "-o", corte]
        if fps_arg:
            cmd += ["--fps", fps_arg]
        if subprocess.run([str(c) for c in cmd], cwd=RAIZ).returncode != 0:
            sys.exit("O render do corte falhou.")
    else:
        print(f"[1/4] Corte sem legenda em dia: {rel(corte)}")

    # 2. projeto HyperFrames (init converte e copia o video para dentro do projeto)
    if (projeto / "hyperframes.json").exists() and (projeto / ".corte").exists() \
            and (projeto / ".corte").read_text() == str(corte.stat().st_mtime):
        print(f"[2/4] Projeto em dia: {rel(projeto)}")
    else:
        autorais = {}  # o que o editor escreveu a mao sobrevive a recriacao do projeto
        if projeto.exists():
            print(f"[2/4] O corte mudou: recriando {rel(projeto)}...", flush=True)
            autorais = {n: (projeto / n).read_text(encoding="utf-8")
                        for n in ("theme.json", "cinematic.json") if (projeto / n).exists()}
            shutil.rmtree(projeto)
        else:
            print(f"[2/4] Criando {rel(projeto)}...", flush=True)
        env = {**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1"}
        cmd = [*cli_hyperframes(), "init", str(projeto), "--non-interactive", "--video", str(corte),
               "--skip-transcribe", "--skill=embedded-captions"]
        if subprocess.run(cmd, cwd=RAIZ, env=env).returncode != 0:
            sys.exit("hyperframes init falhou.")
        (projeto / ".corte").write_text(str(corte.stat().st_mtime))
        for n, texto in autorais.items():
            (projeto / n).write_text(texto, encoding="utf-8")
            print(f"  {n} mantido: confira se as linhas ainda batem com a fala do corte novo")

    # 3. nossa transcricao, no tempo do corte, no formato que o transcribe.cjs aceita sem rodar o WhisperX
    fps = fps_comum(Fraction(fps_arg)) if fps_arg else primeira["fps"]
    ws = aplicar_correcoes(palavras_saida(montar_linha(edl, fps), palavras), edl.get("correcoes"))
    (projeto / "transcript.json").write_text(
        json.dumps({"language_code": idioma, "words": ws}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[3/4] transcript.json com {len(ws)} palavras (Parakeet + correcoes)")

    # 4. dependencias do projeto (versoes que o SKILL.md do plugin pede) + prepare.sh
    if not (projeto / "node_modules" / "puppeteer").exists():
        pedido = re.search(r"npm install --prefix <project> --save-dev --save-exact ([^\n`]+)",
                           (skill / "SKILL.md").read_text(encoding="utf-8"))
        pacotes = pedido.group(1).split() if pedido else ["sharp", "puppeteer", "gsap"]
        npm = shutil.which("npm")
        if not npm:
            sys.exit("npm nao encontrado no PATH.")
        print(f"[4/4] Instalando {' '.join(pacotes)} no projeto (baixa o Chromium na primeira vez)...", flush=True)
        if subprocess.run([npm, "install", "--prefix", str(projeto), "--save-dev", "--save-exact", *pacotes],
                          cwd=RAIZ).returncode != 0:
            sys.exit("npm install falhou.")
    bash = shutil.which("bash")
    if not bash:
        sys.exit("bash nao encontrado (instale o Git for Windows).")
    duracao = sondar(corte)["duracao"]
    print(f"[4/4] prepare.sh: recorte da pessoa + zonas seguras ({duracao:.0f}s de video; "
          f"na CPU leva ~{max(1, round(duracao / 10 * 2.5))} min)...", flush=True)
    t0 = time.time()
    if subprocess.run([bash, str(skill / "scripts" / "prepare.sh"), str(projeto)], cwd=RAIZ).returncode != 0:
        sys.exit("prepare.sh falhou (veja os logs _prepare_*.log no projeto).")
    print(f"  pronto em {(time.time() - t0) / 60:.1f} min")
    print(f"\nProjeto pronto: {rel(projeto)}\nProximo: siga {skill / 'SKILL.md'} do passo 3 "
          f"(escolher identidade, theme.json/cinematic.json, preview-frames, render).\n"
          f"Scripts do plugin: {skill / 'scripts'}\n"
          f"No fim: python .claude/skills/legendar/cinema.py {nome} --finalizar")


def finalizar(pasta: Path) -> None:
    projeto = RAIZ / "videos" / f"{pasta.name}-cinema"
    candidatos = [projeto / "final_fx.mp4", projeto / "final.mp4"]  # final_fx = modo Theme (com reacao da cena)
    entrada = next((c for c in candidatos if c.exists()), None)
    if not entrada:
        sys.exit(f"Nao achei final_fx.mp4 nem final.mp4 em {rel(projeto)}. Renderize o embedded-captions antes.")
    if not sondar(entrada)["tem_audio"]:
        sys.exit(f"{rel(entrada)} saiu sem audio; confira o render do embedded-captions.")
    saida = RAIZ / "final" / f"{pasta.name}.mp4"
    saida.parent.mkdir(exist_ok=True)
    # O render do embedded-captions sai com bitrate de intermediario (~55 Mbps): recodifica para entregar.
    print(f"Recodificando (CRF 18) + loudness -14 LUFS: {rel(entrada)} -> {rel(saida)}", flush=True)
    rodar(["ffmpeg", "-y", "-v", "error", "-i", entrada, "-map", "0:v", "-map", "0:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-af", filtro_loudnorm(entrada, duas_passadas=True), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", saida])
    print(f"Pronto: {rel(saida)}. Confira: python .claude/skills/conferir/conferir.py {pasta.name} --video {rel(saida)}")


def main() -> None:
    iniciar()
    ap = argparse.ArgumentParser(description="Modo cinema (HyperFrames embedded-captions)")
    ap.add_argument("projeto")
    ap.add_argument("--idioma", default="pt", help="codigo do idioma da fala (padrao: pt)")
    ap.add_argument("--fps", help="mesmo --fps usado no render.py, se usou")
    ap.add_argument("--finalizar", action="store_true", help="loudness no resultado -> final/<projeto>.mp4")
    args = ap.parse_args()
    pasta, edl = carregar_edl(args.projeto)
    if args.finalizar:
        finalizar(pasta)
    else:
        preparar(pasta, edl, args.idioma, args.fps)


if __name__ == "__main__":
    main()
