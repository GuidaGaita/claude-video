"""Linha do tempo de um trecho de video em PNG: quadros + forma de onda (dBFS) + palavras
+ silencios. E o "olho" do editor nos pontos de decisao: pausa ambigua, "~som" do takes.md,
comparar takes, conferir um corte. Nao use para varrer o video inteiro.

Adaptado de browser-use/video-use (MIT — ver .claude/skills/editar/LICENSE-video-use.txt).

Uso (da raiz do projeto):
  python .claude/skills/conferir/linha_do_tempo.py <video> <inicio> <fim> [-o saida.png]
         [--quadros 8] [--transcricao transcript.json] [--marcas 12.3,15.0]
Sem -o, salva em trabalho/linha_do_tempo/. Sem --transcricao, procura em
transcricoes/<nome>/ e na pasta do video.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "editar"))
from comum import RAIZ, carregar_palavras, iniciar, ler_audio, rel  # noqa: E402

FUNDO = (18, 18, 22)
TEXTO = (235, 235, 235)
APAGADO = (120, 120, 130)
DESTAQUE = (255, 140, 60)
SILENCIO = (50, 80, 120, 110)
ONDA = (140, 180, 255)
PISO_DB = -60.0
FONTES = ["C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/arial.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]


def fonte(tamanho: int) -> ImageFont.ImageFont:
    for f in FONTES:
        if Path(f).exists():
            return ImageFont.truetype(f, tamanho)
    return ImageFont.load_default()


def extrair_quadros(video: Path, tempos: list[float], pasta: Path) -> list[Path]:
    quadros = []
    for i, t in enumerate(tempos):
        q = pasta / f"q_{i:03d}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                        "-q:v", "4", "-vf", "scale=-2:240", str(q)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        quadros.append(q)
    return quadros


def envelope(video: Path, inicio: float, fim: float, n: int) -> np.ndarray:
    """Volume RMS em n colunas, em escala dB de PISO_DB a 0 dBFS mapeada para 0..1."""
    audio = ler_audio(video, inicio, fim - inicio)
    if audio.size < n:
        return np.zeros(n)
    janela = audio.size // n
    rms = np.sqrt(np.mean(audio[: janela * n].reshape(n, janela) ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    return np.clip((db - PISO_DB) / -PISO_DB, 0, 1)


def achar_transcricao(video: Path) -> Path | None:
    for c in (RAIZ / "transcricoes" / video.stem / "transcript.json", video.parent / "transcript.json"):
        if c.is_file():
            return c
    return None


def silencios(palavras: list[dict], inicio: float, fim: float, minimo: float = 0.4) -> list[tuple[float, float]]:
    lacunas, anterior = [], inicio
    for w in palavras:
        if w["start"] - anterior >= minimo:
            lacunas.append((anterior, w["start"]))
        anterior = max(anterior, w["end"])
    if fim - anterior >= minimo:
        lacunas.append((anterior, fim))
    return lacunas


def desenhar(video: Path, inicio: float, fim: float, saida: Path, tempos: list[float] | None = None,
             n_quadros: int = 8, transcricao: Path | None = None, marcas: tuple[float, ...] = ()) -> Path:
    if tempos is None:
        passo = (fim - inicio) / max(1, n_quadros - 1)
        tempos = [inicio + i * passo for i in range(n_quadros)] if n_quadros > 1 else [(inicio + fim) / 2]
        tempos[-1] = min(tempos[-1], fim - 0.04)  # o ultimo quadro exato pode nao existir

    with tempfile.TemporaryDirectory() as tmp:
        imgs = []
        for q in extrair_quadros(video, tempos, Path(tmp)):
            imgs.append(Image.open(q).convert("RGB") if q.exists() else Image.new("RGB", (320, 180), (60, 0, 0)))
            if imgs[-1].height != 180:
                imgs[-1] = imgs[-1].resize((max(1, round(imgs[-1].width * 180 / imgs[-1].height)), 180), Image.LANCZOS)

    margem, espaco = 50, 6
    largura_tira = sum(i.width for i in imgs) + espaco * (len(imgs) - 1)
    largura = max(1400, largura_tira + 2 * margem)
    y_tira, y_onda, alt_onda = 50, 50 + 180 + 34 + 46, 200
    altura = y_onda + alt_onda + 56
    tela = Image.new("RGB", (largura, altura), FUNDO)
    d = ImageDraw.Draw(tela, "RGBA")
    f_titulo, f_rotulo, f_pequena = fonte(22), fonte(15), fonte(14)

    d.text((margem, 12), f"{video.name}   {inicio:.2f}s -> {fim:.2f}s   ({fim - inicio:.2f}s)", fill=TEXTO, font=f_titulo)

    # Quadros, cada um com o seu tempo embaixo; borda laranja nos quadros depois da primeira marca.
    x = margem + (largura - 2 * margem - largura_tira) // 2
    for img, t in zip(imgs, tempos):
        tela.paste(img, (x, y_tira))
        if marcas and t >= marcas[0]:
            d.rectangle((x - 2, y_tira - 2, x + img.width + 1, y_tira + 181), outline=DESTAQUE, width=2)
        d.text((x + 4, y_tira + 186), f"{t:.2f}s", fill=APAGADO, font=f_rotulo)
        x += img.width + espaco

    x0, x1 = margem, largura - margem

    def em_x(t: float) -> int:
        return int(x0 + (t - inicio) / max(1e-6, fim - inicio) * (x1 - x0))

    palavras = [w for w in carregar_palavras(transcricao) if w["end"] > inicio and w["start"] < fim]
    d.rectangle((x0, y_onda, x1, y_onda + alt_onda), fill=(28, 28, 34))
    for a, b in silencios(palavras, inicio, fim) if palavras else []:
        d.rectangle((em_x(a), y_onda, em_x(b), y_onda + alt_onda), fill=SILENCIO)
    for db in (-12, -24, -36, -48):
        y = y_onda + alt_onda // 2 - int((db - PISO_DB) / -PISO_DB * (alt_onda // 2 - 6))
        d.line((x0, y, x1, y), fill=(45, 45, 55))
    env = envelope(video, inicio, fim, max(200, x1 - x0))
    meio, amp = y_onda + alt_onda // 2, alt_onda // 2 - 6
    topo = [(x0 + int(i * (x1 - x0) / max(1, len(env) - 1)), meio - int(v * amp)) for i, v in enumerate(env)]
    base = [(px, 2 * meio - py) for px, py in topo]
    if topo:
        d.polygon(topo + base[::-1], fill=(*ONDA, 70))
        d.line(topo, fill=ONDA, width=1)
        d.line(base, fill=ONDA, width=1)

    # Palavras em duas fileiras acima da onda para nao encavalar.
    fim_fileira = [-9999, -9999]
    for w in palavras:
        px = em_x(max(w["start"], inicio))
        larg = int(d.textlength(w["text"], font=f_pequena))
        fileira = 0 if px > fim_fileira[0] + 4 else 1 if px > fim_fileira[1] + 4 else None
        if fileira is None:
            continue
        y = y_onda - 40 + fileira * 18
        d.text((px, y), w["text"], fill=TEXTO, font=f_pequena)
        d.line((px, y_onda - 4, px, y_onda), fill=APAGADO)
        fim_fileira[fileira] = px + larg

    for t in marcas:
        if inicio <= t <= fim:
            px = em_x(t)
            d.line((px, y_onda - 4, px, y_onda + alt_onda), fill=DESTAQUE, width=2)
            d.text((px + 4, y_onda + 4), f"corte {t:.2f}s", fill=DESTAQUE, font=f_rotulo)

    for i in range(7):
        t = inicio + i * (fim - inicio) / 6
        px = em_x(t)
        d.line((px, y_onda + alt_onda + 2, px, y_onda + alt_onda + 8), fill=APAGADO)
        d.text((px - 22, y_onda + alt_onda + 10), f"{t:.2f}s", fill=APAGADO, font=f_rotulo)
    legenda = "onda em dBFS (linhas: -12/-24/-36/-48)"
    if palavras:
        legenda += "   faixas azuis = silencio >= 0,4 s entre palavras"
    d.text((x0, y_onda + alt_onda + 32), legenda, fill=APAGADO, font=f_rotulo)

    saida.parent.mkdir(parents=True, exist_ok=True)
    tela.save(saida, "PNG", optimize=True)
    return saida


def main() -> None:
    iniciar()
    ap = argparse.ArgumentParser(description="Quadros + forma de onda + palavras de um trecho do video")
    ap.add_argument("video", type=Path)
    ap.add_argument("inicio", type=float)
    ap.add_argument("fim", type=float)
    ap.add_argument("-o", "--saida", type=Path)
    ap.add_argument("--quadros", type=int, default=8)
    ap.add_argument("--transcricao", type=Path, help="transcript.json no tempo deste video")
    ap.add_argument("--marcas", default="", help="tempos para destacar, separados por virgula")
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.is_file():
        sys.exit(f"Video nao encontrado: {video}")
    if args.fim <= args.inicio:
        sys.exit("fim precisa ser maior que inicio")
    marcas = tuple(float(m) for m in args.marcas.split(",") if m.strip())
    saida = args.saida or RAIZ / "trabalho" / "linha_do_tempo" / f"{video.stem}_{args.inicio:.2f}-{args.fim:.2f}.png"
    transcricao = args.transcricao or achar_transcricao(video)
    desenhar(video, args.inicio, args.fim, saida, n_quadros=args.quadros, transcricao=transcricao, marcas=marcas)
    print(f"Salvo: {rel(saida)}")


if __name__ == "__main__":
    main()
