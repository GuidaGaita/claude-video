"""Confere um video renderizado pelo /editar antes de mostrar ao usuario.

Gera em trabalho/<projeto>/conferir/:
  corte_NN.png   1,5 s antes e depois de cada corte (quadros + forma de onda + palavras)
  inicio.png, fim.png
  relatorio.md   duracao, loudness, nivel de cada trecho, som forte nos cortes, quadros pretos

Uso (da raiz do projeto):
  python .claude/skills/conferir/conferir.py <projeto> [--video <arquivo>] [--cortes 2,5] [--sem-imagens]
Sem --video, usa o render mais recente do projeto.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "editar"))
from comum import (QUADRO, caminho, carregar_edl, iniciar, ler_audio, montar_linha, rel, sondar,  # noqa: E402
                   trocas_de_plano, volume_db)
from linha_do_tempo import desenhar  # noqa: E402

ALVO_LUFS, TOLERANCIA_LUFS, MAX_TRUE_PEAK = -14.0, 1.0, -1.0
SALTO_NIVEL_DB = 6.0     # diferenca de nivel de fala entre trechos vizinhos que vira aviso
SOM_NO_CORTE_DB = -30.0  # pico a +-10 ms do corte acima disso = corte em cima de som
PLANO_MIN = 0.7           # plano mais curto que isso no video final = "pisca"
PARADA_MAX = 0.2          # rosto sem mexer por mais que isso = parece que o video travou
ROSTO_PARADO = 0.8        # mudanca media no rosto abaixo disso = parado (fala normal: 5% mais quietos ~1,2)


def escolher_video(pasta: Path, renders: dict, video: str | None) -> Path:
    if video:
        v = caminho(video)
        if not v.is_file():
            v = Path(video).resolve()
        if not v.is_file():
            sys.exit(f"Video nao encontrado: {video}")
        return v
    existentes = [caminho(k) for k in renders if caminho(k).is_file()]
    if not existentes:
        sys.exit(f"Nenhum render de {pasta.name} encontrado. Rode o render.py antes.")
    return max(existentes, key=lambda p: p.stat().st_mtime)


def duracoes_streams(video: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json",
                        str(video)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    saida = {}
    for s in json.loads(r.stdout or "{}").get("streams", []):
        if s.get("duration") not in (None, "N/A"):
            saida.setdefault(s["codec_type"], float(s["duration"]))
    return saida


def loudness(video: Path) -> dict:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn", "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    resumo = r.stderr[r.stderr.rfind("Summary:"):]

    def valor(padrao: str) -> float | None:
        m = re.search(padrao, resumo)
        return float(m.group(1)) if m and m.group(1) != "-inf" else None

    return {"i": valor(r"I:\s+(-?[\d.]+|-inf) LUFS"), "lra": valor(r"LRA:\s+(-?[\d.]+) LU"),
            "tp": valor(r"Peak:\s+(-?[\d.]+|-inf) dBFS")}


def imagem_parada(video: Path, fps: float) -> list[tuple[float, float]]:
    """Trechos em que o ROSTO nao mexe (quadro congelado). Mede a regiao do rosto num video falado vertical: o
    zoom lento continua mexendo a imagem inteira mesmo com a imagem congelada, o rosto parado e que denuncia.
    Mediana de 7 quadros para um pico isolado nao quebrar a deteccao."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf",
                        "crop=iw*0.5:ih*0.3:iw*0.25:ih*0.22,scale=160:96,format=gray", "-f", "rawvideo", "-"],
                       capture_output=True)
    q = np.frombuffer(r.stdout, dtype=np.uint8).reshape(-1, 160 * 96).astype(np.float32)
    if len(q) < 8:
        return []
    d = np.abs(np.diff(q, axis=0)).mean(axis=1)
    mediana = np.array([np.median(d[max(0, i - 3):i + 4]) for i in range(len(d))])
    igual = mediana < ROSTO_PARADO
    paradas, ini = [], None
    for i, v in enumerate(list(igual) + [False]):
        if v and ini is None:
            ini = i
        elif not v and ini is not None:
            if (i - ini) / fps > PARADA_MAX:
                paradas.append((ini / fps, (i + 1) / fps))
            ini = None
    return paradas


def quadros_pretos(video: Path) -> list[tuple[float, float]]:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-an", "-vf",
                        "scale=320:-2,blackdetect=d=0.04:pix_th=0.10", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)]


def main() -> None:
    iniciar()
    ap = argparse.ArgumentParser(description="Confere um render do /editar")
    ap.add_argument("projeto")
    ap.add_argument("--video", help="video a conferir (padrao: render mais recente do projeto)")
    ap.add_argument("--cortes", default="", help="so estes cortes nas imagens (1 = entre o trecho 1 e o 2)")
    ap.add_argument("--sem-imagens", action="store_true")
    args = ap.parse_args()

    pasta, edl = carregar_edl(args.projeto)
    arq_renders = pasta / "renders.json"
    renders = json.loads(arq_renders.read_text(encoding="utf-8")) if arq_renders.exists() else {}
    video = escolher_video(pasta, renders, args.video)
    info = sondar(video)
    registro = renders.get(rel(video))
    if registro:
        trechos, esperado, modo = registro["trechos"], registro["duracao"], registro["modo"]
    else:
        print("(render sem registro em renders.json; recalculando a linha do tempo pelo edl.json)")
        trechos = montar_linha(edl, info["fps"])
        esperado, modo = trechos[-1]["saida_fim"], "desconhecido"
    cortes = [t["saida_inicio"] for t in trechos[1:]]
    total = info["duracao"]

    avisos: list[str] = []
    linhas = [f"# Conferencia — {rel(video)}", "",
              f"Modo: {modo} · {info['largura']}x{info['altura']} @ {float(info['fps']):.3g} fps · "
              f"{len(trechos)} trechos · {len(cortes)} cortes", ""]

    # Duracao e sincronia
    fps = Fraction(info["fps"])
    tolerancia = max(0.1, float(2 / fps))
    linhas.append(f"- Duracao: {total:.2f}s (esperado {esperado:.2f}s)")
    if abs(total - esperado) > tolerancia:
        avisos.append(f"duracao {total:.2f}s difere da esperada {esperado:.2f}s")
    streams = duracoes_streams(video)
    if "video" in streams and "audio" in streams:
        dif = abs(streams["video"] - streams["audio"])
        linhas.append(f"- Video {streams['video']:.2f}s / audio {streams['audio']:.2f}s")
        if dif > 0.1:
            avisos.append(f"video e audio com duracoes diferentes ({dif:.2f}s): risco de dessincronia")
    if not info["tem_audio"]:
        avisos.append("o video nao tem audio")

    # Loudness
    if info["tem_audio"]:
        ld = loudness(video)
        linhas.append(f"- Loudness: {ld['i']} LUFS integrado, true peak {ld['tp']} dBFS, LRA {ld['lra']} LU "
                      f"(alvo {ALVO_LUFS:.0f} LUFS, pico <= {MAX_TRUE_PEAK:.0f})")
        if ld["i"] is None or abs(ld["i"] - ALVO_LUFS) > TOLERANCIA_LUFS:
            avisos.append(f"loudness {ld['i']} LUFS fora de {ALVO_LUFS:.0f} +-{TOLERANCIA_LUFS:.0f} "
                          "(esperado se o render foi com --sem-loudnorm)")
        if ld["tp"] is not None and ld["tp"] > MAX_TRUE_PEAK:
            avisos.append(f"true peak {ld['tp']} dBFS acima de {MAX_TRUE_PEAK:.0f} (pode distorcer no YouTube/Instagram)")

        # Nivel de fala por trecho (percentil 90 do volume) e saltos entre vizinhos
        db = volume_db(ler_audio(video))
        niveis = []
        for t in trechos:
            seg = db[int(t["saida_inicio"] / QUADRO):int(t["saida_fim"] / QUADRO)]
            niveis.append(float(np.percentile(seg, 90)) if seg.size else float("nan"))
        linhas.append("- Nivel de fala por trecho (dBFS): " + ", ".join(f"{i + 1}: {n:.0f}" for i, n in enumerate(niveis)))
        for i in range(1, len(niveis)):
            if abs(niveis[i] - niveis[i - 1]) > SALTO_NIVEL_DB:
                avisos.append(f"salto de volume de {abs(niveis[i] - niveis[i - 1]):.0f} dB no corte {i} "
                              f"(trecho {i} -> {i + 1}, em {cortes[i - 1]:.2f}s)")
        for i, n in enumerate(niveis):
            if n < -45:
                avisos.append(f"trecho {i + 1} quase mudo ({n:.0f} dBFS)")

        # Som forte em cima do corte = corte no meio de fala ou estalo. Medido na voz sozinha (base do render,
        # antes da trilha): a musica tocando embaixo do corte e normal e nao estala.
        base = pasta / f"base_{modo}.mkv"
        fonte_pop = base if base.exists() and abs(sondar(base)["duracao"] - total) < 0.2 else video
        for k, b in enumerate(cortes, 1):
            a = ler_audio(fonte_pop, max(0.0, b - 0.01), 0.02, taxa=48000)
            pico = 20 * np.log10(max(float(np.abs(a).max()) if a.size else 0.0, 1e-6))
            if pico > SOM_NO_CORTE_DB:
                avisos.append(f"corte {k} ({b:.2f}s): som forte no ponto de corte ({pico:.0f} dBFS) — "
                              "cortou em cima de fala ou pode estalar")

        # Bordas do video inteiro: comecar ou terminar com som cortado soa como clique.
        for nome, inicio_janela in (("comeca", 0.0), ("termina", max(0.0, total - 0.02))):
            a = ler_audio(video, inicio_janela, 0.02, taxa=48000)
            pico = 20 * np.log10(max(float(np.abs(a).max()) if a.size else 0.0, 1e-6))
            if pico > SOM_NO_CORTE_DB:
                avisos.append(f"o video {nome} com som forte ({pico:.0f} dBFS nos 20 ms da ponta): soa cortado")

    # Teste das bordas da imagem (como testar o limite de um if): toda troca de imagem tem que cair num corte
    # planejado, e nenhum plano pode durar menos que PLANO_MIN. Pega o "pisca": uns quadros de outro plano
    # da fonte (troca de zoom do celular, emenda) colados na ponta de um trecho, ou imagem esticada do J-cut.
    trocas = trocas_de_plano(video, cache=False)
    planejados = [t["saida_inicio"] + float(t.get("j_imagem") or 0) for t in trechos[1:]]
    tolerancia = 2.5 / float(fps)
    inesperadas = [c for c in trocas if all(abs(c - p) > tolerancia for p in planejados)]
    linhas.append(f"- Trocas de imagem: {len(trocas)} detectadas, {len(planejados)} cortes planejados, "
                  f"{len(inesperadas)} fora dos cortes" + (" (" + ", ".join(f"{c:.2f}s" for c in inesperadas) + ")"
                                                           if inesperadas else ""))
    marcos = sorted(set([0.0, total] + planejados + inesperadas))
    for a, b in zip(marcos, marcos[1:]):
        if b - a < PLANO_MIN:
            avisos.append(f"pisca: plano de {round((b - a) * float(fps))} quadros entre {a:.2f}s e {b:.2f}s "
                          f"(menos de {PLANO_MIN}s; confira se e outro plano da fonte colado no corte)")

    # Taxa de quadros: tem que ser a da fonte (converter 30 -> 29,97 descarta quadros = tranco no movimento).
    fontes_edl = edl.get("fontes") or {}
    if trechos and trechos[0].get("fonte") in fontes_edl:
        fps_fonte = sondar(caminho(fontes_edl[trechos[0]["fonte"]]["video"]))["fps"]
        if abs(float(fps) - float(fps_fonte)) > 0.005:
            avisos.append(f"taxa de quadros {float(fps):.3f} diferente da fonte ({float(fps_fonte):.3f}): "
                          "quadros descartados ou repetidos viram trancos")
    # Imagem parada (todos os quadros iguais) por mais de PARADA_MAX: no celular parece que o video travou.
    for a, b in imagem_parada(video, float(fps)):
        avisos.append(f"imagem parada de {a:.2f}s a {b:.2f}s ({b - a:.2f}s): o rosto nao mexe, parece travamento")
    for a, b in (registro or {}).get("congelamentos", []):
        if b - a > PARADA_MAX:
            avisos.append(f"o render congelou a imagem de {a:.2f}s a {b:.2f}s ({b - a:.2f}s) para fugir de uma troca "
                          "de plano da fonte: parece travamento - mude o corte")

    # Quadros pretos
    pretos = quadros_pretos(video)
    linhas.append(f"- Quadros pretos: {len(pretos)}")
    for a, b in pretos:
        avisos.append(f"quadro preto de {a:.2f}s a {b:.2f}s")

    # Imagens
    imagens = []
    if not args.sem_imagens:
        pasta_img = pasta / "conferir"
        pasta_img.mkdir(exist_ok=True)
        for velha in pasta_img.glob("*.png"):
            velha.unlink()
        transcricao = pasta / "transcript.json"
        escolhidos = {int(c) for c in args.cortes.split(",") if c.strip()} or set(range(1, len(cortes) + 1))
        print(f"Gerando imagens de {len(escolhidos & set(range(1, len(cortes) + 1)))} cortes...", flush=True)
        limite = max(0.0, total - 0.05)
        for k, b in enumerate(cortes, 1):
            if k not in escolhidos:
                continue
            bv = b + float(trechos[k].get("j_imagem") or 0)  # J-cut: a imagem troca depois do som
            tempos = [min(max(0.0, bv + d), limite) for d in (-1.0, -0.3, -0.07, 0.02, 0.3, 1.0)]
            imagens.append(desenhar(video, max(0.0, b - 1.5), min(total, b + 1.5), pasta_img / f"corte_{k:02d}.png",
                                    tempos=tempos, transcricao=transcricao,
                                    marcas=(bv, b) if bv != b else (b,)))
        imagens.append(desenhar(video, 0.0, min(3.0, total), pasta_img / "inicio.png", n_quadros=6,
                                transcricao=transcricao))
        imagens.append(desenhar(video, max(0.0, total - 3.0), total, pasta_img / "fim.png", n_quadros=6,
                                transcricao=transcricao))

    linhas += ["", "## Avisos", ""] + ([f"- {a}" for a in avisos] or ["- nenhum"])
    if imagens:
        linhas += ["", "## Imagens para olhar", ""] + [f"- {rel(i)}" for i in imagens]
        linhas += ["", "Em cada corte: salto estranho de imagem ou flash, palavra comida (a onda some no meio "
                   "de uma palavra), legenda legivel e nao escondida, sobreposicao no lugar e no tempo certos."]
    relatorio = pasta / "conferir" / "relatorio.md"
    relatorio.parent.mkdir(exist_ok=True)
    relatorio.write_text("\n".join(linhas) + "\n", encoding="utf-8")

    print("\n".join(linhas))
    print(f"\nResultado: {'OK' if not avisos else f'{len(avisos)} aviso(s)'} — relatorio em {rel(relatorio)}")


if __name__ == "__main__":
    main()
