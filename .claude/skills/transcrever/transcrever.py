"""Transcreve um video/audio com Parakeet TDT 0.6B v3 (local, CPU) e gera:

  transcript.json  lista de palavras [{text, start, end}] (formato aceito pelo HyperFrames)
  segments.json    frases separadas por pausa [{start, end, text}]
  transcript.txt   texto legivel com tempos, para decidir cortes
  transcript.srt   legenda pronta (blocos curtos)

Uso:
  python transcrever.py <arquivo> [-o pasta_saida] [--qualidade alta]
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

MODELO = "nemo-parakeet-tdt-0.6b-v3"
FIM_MAX_PALAVRA = 0.4  # segundos: evita que uma pausa seja absorvida pela palavra
QUADRO = 0.01  # segundos por medida de volume
# Volume baixo por este tempo marca o fim da palavra. Menos que isso pode ser oclusiva (p, t, k) ou a
# entrada fraca de um "s" no meio da palavra ("i|sso"): cortar ali come o final.
SILENCIO_MIN = 0.15
INICIO_MAX_ATRASO = 0.4  # quanto o inicio pode andar para frente ate o som comecar
FRASE_PAUSA = 0.3  # lacuna entre palavras que quebra a linha no transcript.txt
LEGENDA_MAX_CHARS = 64  # por bloco; acima de LEGENDA_LINHA vira 2 linhas
LEGENDA_LINHA = 32
LEGENDA_PAUSA = 0.6


def extrair_audio(entrada: Path, wav: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("FFmpeg nao encontrado no PATH. Reinicie o terminal/VS Code depois de instalar (winget install Gyan.FFmpeg).")
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(entrada), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
        check=True,
    )


def tokens_para_palavras(seg) -> list[dict]:
    """Agrupa os tokens de um segmento em palavras com tempo absoluto (o fim sai de definir_fins)."""
    palavras: list[dict] = []
    for token, ts in zip(seg.tokens or [], seg.timestamps or []):
        inicio = seg.start + ts
        if token.startswith(" ") or not palavras:
            palavras.append({"text": token.strip(), "start": inicio, "ultimo": inicio, "limite": seg.end})
        else:
            palavras[-1]["text"] += token
            # Pontuacao sai com tempo atrasado (ate segundos depois da fala) e esticaria a palavra.
            if any(c.isalnum() for c in token):
                palavras[-1]["ultimo"] = inicio
    return [p for p in palavras if p["text"]]


def volume_db(wav: Path) -> np.ndarray:
    """Volume RMS em dBFS a cada QUADRO segundos (wav mono 16 kHz)."""
    with wave.open(str(wav), "rb") as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    n = int(16000 * QUADRO)
    q = len(audio) // n
    if q == 0:
        return np.zeros(0)
    rms = np.sqrt(np.mean(audio[: q * n].reshape(q, n) ** 2, axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-5))


def ajustar_tempos(palavras: list[dict], db: np.ndarray) -> None:
    """Inicio depois de silencio = onde o volume sobe (o Parakeet chega a marcar 0,2-0,3 s antes da fala).
    Fim = onde o volume cai depois do ultimo token, sem passar de FIM_MAX_PALAVRA nem do inicio da proxima."""
    limiar = None
    if db.size:
        piso, fala = np.percentile(db, 10), np.percentile(db, 90)
        # Baixo para consoante fraca (f, s, h) contar como som: na duvida a palavra fica longa, nunca cortada.
        limiar = piso + 0.15 * (fala - piso)
    for i, p in enumerate(palavras):
        prox = palavras[i + 1]["start"] if i + 1 < len(palavras) else p["limite"]
        # O inicio vem antes do fim: com o inicio adiantado no silencio, o fim tambem "achava" silencio
        # logo de cara e a palavra encolhia para dentro da pausa.
        comeca_mudo = limiar is not None and bool(db.size) and all(
            db[k] < limiar for k in range(int(p["start"] / QUADRO), min(int((p["start"] + 0.06) / QUADRO), db.size)))
        if limiar is not None and (p["start"] - (palavras[i - 1]["end"] if i else 0.0) >= 0.1 or comeca_mudo):
            fim_busca = min(p["start"] + INICIO_MAX_ATRASO, prox - 0.02)
            achou = False
            for k in range(int(p["start"] / QUADRO), min(int(fim_busca / QUADRO), db.size)):
                if db[k] >= limiar:
                    p["start"] = max(p["start"], k * QUADRO)
                    p["ultimo"] = max(p["ultimo"], p["start"])
                    achou = True
                    break
            if not achou and i + 1 < len(palavras):
                # A palavra caiu inteira no silencio (ex.: "Mas" 0,25 s antes de "Mas deixa"): ela comeca junto
                # com o som e fica com uma fatia curta antes da proxima. Senao a legenda perde a palavra.
                prox_p = palavras[i + 1]
                for k in range(int(p["start"] / QUADRO), min(int((prox + 0.3) / QUADRO), db.size)):
                    if db[k] >= limiar:
                        inicio_som = k * QUADRO
                        if inicio_som >= prox - 0.05:
                            fatia = min(0.12, max(0.04, (prox_p["ultimo"] + 0.1 - inicio_som) / 3))
                            p["start"] = inicio_som
                            p["ultimo"] = max(p["ultimo"], inicio_som)
                            prox_p["start"] = max(prox_p["start"], inicio_som + fatia)
                            prox_p["ultimo"] = max(prox_p["ultimo"], prox_p["start"])
                            prox = prox_p["start"]
                        break
        teto = max(min(p["ultimo"] + FIM_MAX_PALAVRA, p["limite"], prox), p["start"] + 0.02)
        fim = teto
        if limiar is not None:
            quietos = 0
            for k in range(int((p["ultimo"] + 0.04) / QUADRO), min(int(teto / QUADRO), db.size)):
                quietos = quietos + 1 if db[k] < limiar else 0
                if quietos * QUADRO >= SILENCIO_MIN:
                    fim = (k - quietos + 1) * QUADRO
                    break
        p["end"] = max(fim, p["start"] + 0.02)
        del p["ultimo"], p["limite"]


def tempo(seg: float, sep: str = ".") -> str:
    h, ms = divmod(int(round(seg * 1000)), 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def agrupar(palavras: list[dict], pausa_max: float, max_chars: int | None = None) -> list[dict]:
    """Junta palavras em blocos, quebrando em pausas, fim de frase ou limite de caracteres."""
    blocos: list[list[dict]] = []
    atual: list[dict] = []
    for p in palavras:
        if atual:
            pausa = p["start"] - atual[-1]["end"]
            fim_frase = atual[-1]["text"][-1:] in ".?!"
            longo = max_chars is not None and len(" ".join(w["text"] for w in atual + [p])) > max_chars
            if pausa > pausa_max or fim_frase or longo:
                blocos.append(atual)
                atual = []
        atual.append(p)
    if atual:
        blocos.append(atual)
    return [{"start": b[0]["start"], "end": b[-1]["end"], "text": " ".join(w["text"] for w in b)} for b in blocos]


def duas_linhas(texto: str) -> str:
    if len(texto) <= LEGENDA_LINHA or " " not in texto:
        return texto
    meio = len(texto) // 2
    corte = min((i for i, c in enumerate(texto) if c == " "), key=lambda i: abs(i - meio))
    return texto[:corte] + "\n" + texto[corte + 1:]


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcricao local com Parakeet v3")
    ap.add_argument("arquivo", type=Path)
    ap.add_argument("-o", "--saida", type=Path, help="pasta de saida (padrao: transcricoes/<nome>)")
    ap.add_argument("--qualidade", choices=["rapida", "alta"], default="rapida",
                    help="rapida = modelo int8 (~650 MB RAM); alta = modelo completo (mais lento, ~2.5 GB RAM)")
    ap.add_argument("--forcar", action="store_true", help="transcreve de novo mesmo se ja houver transcricao")
    args = ap.parse_args()

    entrada = args.arquivo.resolve()
    if not entrada.exists():
        sys.exit(f"Arquivo nao encontrado: {entrada}")
    saida = (args.saida or Path("transcricoes") / entrada.stem).resolve()
    pronto = saida / "transcript.json"
    if not args.forcar and pronto.exists() and pronto.stat().st_mtime >= entrada.stat().st_mtime:
        print(f"Ja transcrito (cache): {saida}. Use --forcar para refazer.")
        return
    saida.mkdir(parents=True, exist_ok=True)

    import onnx_asr

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        print(f"[1/3] Extraindo audio de {entrada.name}...", flush=True)
        extrair_audio(entrada, wav)

        print("[2/3] Carregando Parakeet v3...", flush=True)
        quant = "int8" if args.qualidade == "rapida" else None
        modelo = onnx_asr.load_model(MODELO, quantization=quant)
        vad = onnx_asr.load_vad("silero")
        # Trechos longos dao contexto ao modelo (pontuacao melhor); as pausas saem dos tempos das palavras.
        asr = modelo.with_vad(vad, max_speech_duration_s=20, min_silence_duration_ms=1000).with_timestamps()

        print("[3/3] Transcrevendo...", flush=True)
        palavras = [p for seg in asr.recognize(str(wav)) for p in tokens_para_palavras(seg)]
        ajustar_tempos(palavras, volume_db(wav))

    for p in palavras:
        p["start"], p["end"] = round(p["start"], 3), round(p["end"], 3)
    frases = agrupar(palavras, FRASE_PAUSA)

    (saida / "transcript.json").write_text(json.dumps(palavras, ensure_ascii=False, indent=1), encoding="utf-8")
    (saida / "segments.json").write_text(json.dumps(frases, ensure_ascii=False, indent=1), encoding="utf-8")
    (saida / "transcript.txt").write_text(
        "\n".join(f"[{tempo(f['start'])} -> {tempo(f['end'])}] {f['text']}" for f in frases) + "\n", encoding="utf-8"
    )
    (saida / "transcript.srt").write_text(
        "\n".join(
            f"{i}\n{tempo(b['start'], ',')} --> {tempo(b['end'], ',')}\n{duas_linhas(b['text'])}\n"
            for i, b in enumerate(agrupar(palavras, LEGENDA_PAUSA, LEGENDA_MAX_CHARS), 1)
        ),
        encoding="utf-8",
    )

    duracao = frases[-1]["end"] if frases else 0
    print(f"Pronto em {time.time() - t0:.0f}s: {len(palavras)} palavras, {len(frases)} frases, {duracao:.0f}s de fala.")
    print(f"Saida: {saida}")


if __name__ == "__main__":
    main()
