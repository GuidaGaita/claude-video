"""Funcoes compartilhadas por /editar e /conferir: caminhos, ffprobe, audio, EDL."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
HDR = {"smpte2084", "arib-std-b67"}  # PQ (HDR10) e HLG (padrao do iPhone)
FPS_COMUNS = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001),
              Fraction(30), Fraction(50), Fraction(60000, 1001), Fraction(60)]
QUADRO = 0.01  # segundos por medida de volume


def iniciar() -> None:
    """Saida em UTF-8 no console do Windows e confere o FFmpeg no PATH."""
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        sys.exit("FFmpeg/ffprobe nao encontrados no PATH. Reinicie o VS Code depois de instalar (winget install Gyan.FFmpeg).")


def caminho(p: str | Path) -> Path:
    """Caminho do EDL (relativo a raiz do projeto ou absoluto) -> Path absoluto."""
    q = Path(p)
    return q if q.is_absolute() else RAIZ / q


def rel(p: Path) -> str:
    """Path -> texto relativo a raiz com '/', ou absoluto se estiver fora dela."""
    p = Path(p).resolve()
    try:
        return p.relative_to(RAIZ).as_posix()
    except ValueError:
        return p.as_posix()


def rodar(cmd: list, cwd: Path | None = None) -> None:
    """Roda um comando; se falhar, mostra o fim do stderr e encerra."""
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("\n".join(r.stderr.strip().splitlines()[-15:]), file=sys.stderr)
        sys.exit(f"Comando falhou: {' '.join(str(c) for c in cmd[:8])} ...")


def fps_comum(f: Fraction) -> Fraction:
    """Arredonda taxas tortas de celular (VFR) para a taxa padrao mais proxima."""
    perto = min(FPS_COMUNS, key=lambda p: abs(f - p))  # o mais proximo: 30 e 29,97 estao a 0,1% um do outro
    if abs(f - perto) / perto < Fraction(1, 100):
        return perto
    return f.limit_denominator(1001)


def sondar(video: Path) -> dict:
    """duracao, largura/altura ja com rotacao, fps, vertical, hdr, tem_audio, tem_video."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate,color_transfer"
         ":stream_side_data=rotation", "-of", "json", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"ffprobe falhou em {video}: {r.stderr.strip()[-300:]}")
    dados = json.loads(r.stdout)
    streams = dados.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    info = {
        "duracao": float((dados.get("format") or {}).get("duration") or 0),
        "tem_audio": any(s.get("codec_type") == "audio" for s in streams),
        "tem_video": v is not None,
        "largura": 0, "altura": 0, "vertical": False, "hdr": False, "fps": Fraction(30),
    }
    if v:
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        rotacao = next((sd["rotation"] for sd in v.get("side_data_list") or [] if sd.get("rotation") is not None), 0)
        if int(round(float(rotacao))) % 180:
            w, h = h, w  # o ffmpeg gira o video antes dos filtros
        for campo in ("avg_frame_rate", "r_frame_rate"):
            valor = v.get(campo)
            if valor and valor != "0/0":
                try:
                    f = Fraction(valor)
                except (ValueError, ZeroDivisionError):
                    continue
                if f > 0:
                    info["fps"] = fps_comum(f)
                    break
        info.update(largura=w, altura=h, vertical=h > w, hdr=v.get("color_transfer") in HDR)
    return info


def ler_audio(video: Path, inicio: float = 0.0, duracao: float | None = None, taxa: int = 16000):
    """Audio mono em float32 (-1..1) via FFmpeg. Vazio se o arquivo nao tiver audio."""
    import numpy as np

    cmd = ["ffmpeg", "-v", "error"]
    if inicio > 0:
        cmd += ["-ss", f"{inicio:.3f}"]
    cmd += ["-i", str(video)]
    if duracao is not None:
        cmd += ["-t", f"{duracao:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(taxa), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def volume_db(audio, taxa: int = 16000):
    """Volume RMS em dBFS a cada QUADRO segundos."""
    import numpy as np

    n = int(taxa * QUADRO)
    q = len(audio) // n
    if q == 0:
        return np.zeros(0)
    blocos = audio[: q * n].reshape(q, n)
    return 20 * np.log10(np.maximum(np.sqrt(np.mean(blocos ** 2, axis=1)), 1e-5))


def notas_de_cena(video: Path) -> list[tuple[float, float]]:
    """(tempo, pontuacao de mudanca de imagem 0..1) de cada quadro, medido numa versao pequena do video."""
    import re

    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-an", "-vf",
                        "scale=160:-2,select='gte(scene,0)',metadata=print:key=lavfi.scene_score", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tempos = [float(t) for t in re.findall(r"pts_time:([\d.]+)", r.stderr)]
    notas = [float(s) for s in re.findall(r"lavfi\.scene_score=([\d.]+)", r.stderr)]
    return list(zip(tempos, notas))


def trocas_de_plano(video: Path, cache: bool = True) -> list[float]:
    """Instantes em que a imagem muda de plano DENTRO do arquivo (troca de zoom/lente do celular, emenda).
    Pico isolado de mudanca: >= 0.12 e 4x maior que os quadros vizinhos (gesto perto da camera nao conta).
    Um zoom do celular no mesmo cenario da ~0.2, abaixo do limiar comum de 0.3: por isso o limiar e baixo."""
    arquivo = RAIZ / "transcricoes" / video.stem / "planos.json"
    if cache and arquivo.exists() and arquivo.stat().st_mtime >= video.stat().st_mtime:
        return json.loads(arquivo.read_text(encoding="utf-8"))
    notas = notas_de_cena(video)
    trocas = []
    for i, (t, s) in enumerate(notas):
        vizinhos = [v for j, (_, v) in enumerate(notas[max(0, i - 3):i + 4], max(0, i - 3)) if j != i]
        if s >= 0.12 and s >= 4 * max(vizinhos or [0.0]):
            trocas.append(round(t, 3))
    if cache:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(trocas) + "\n", encoding="utf-8")
    return trocas


def carregar_palavras(arquivo: Path | None) -> list[dict]:
    """transcript.json do /transcrever: [{text, start, end}] em segundos."""
    if not arquivo or not Path(arquivo).is_file():
        return []
    dados = json.loads(Path(arquivo).read_text(encoding="utf-8"))
    if isinstance(dados, dict):
        dados = dados.get("words", [])
    return [w for w in dados
            if str(w.get("text", "")).strip() and w.get("start") is not None and w.get("end") is not None]


def pasta_projeto(projeto: str) -> Path:
    p = Path(projeto)
    if p.name == "edl.json":
        return caminho(p).parent
    if p.is_absolute() or len(p.parts) > 1:
        return caminho(p)
    return RAIZ / "trabalho" / projeto


def carregar_edl(projeto: str) -> tuple[Path, dict]:
    pasta = pasta_projeto(projeto)
    arquivo = pasta / "edl.json"
    if not arquivo.exists():
        sys.exit(f"Nao achei {rel(arquivo)}. Rode antes: python .claude/skills/editar/preparar.py {projeto} <video>")
    return pasta, json.loads(arquivo.read_text(encoding="utf-8"))


def duracao_trecho(inicio: float, fim: float, fps: Fraction) -> float:
    """Duracao arredondada para um numero inteiro de quadros (audio e video do mesmo tamanho)."""
    return float(max(1, round(Fraction(fim - inicio) * fps)) / fps)


def montar_linha(edl: dict, fps: Fraction) -> list[dict]:
    """Trechos do EDL com posicao no video de saida (saida_inicio/saida_fim)."""
    t = 0.0
    linha = []
    for i, tr in enumerate(edl.get("trechos") or []):
        inicio = float(tr["inicio"])
        dur = duracao_trecho(inicio, float(tr["fim"]), fps)
        linha.append({**tr, "i": i, "inicio": inicio, "fim": inicio + dur, "dur": dur,
                      "saida_inicio": t, "saida_fim": t + dur})
        t += dur
    return linha
