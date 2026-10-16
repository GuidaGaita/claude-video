"""Renderiza o video a partir de trabalho/<projeto>/edl.json.

Ordem (adaptada de browser-use/video-use, MIT — ver LICENSE-video-use.txt):
  1. Valida os cortes contra as palavras (corte dentro de palavra, margem curta, silencio sobrando).
  2. Extrai cada trecho com fade de 30 ms nas bordas do audio, cor e escala. Trechos que
     nao mudaram saem do cache.
  3. Junta os trechos sem recodificar.
  4. Escreve transcript.json no tempo do video cortado (para HyperFrames) e legenda.srt.
  5. Passada final: sobreposicoes deslocadas para o tempo certo -> legenda POR ULTIMO ->
     loudness -14 LUFS (2 passadas no final, 1 no rascunho).

Uso (da raiz do projeto):
  python .claude/skills/editar/render.py <projeto> --rascunho   720p rapido -> trabalho/<projeto>/rascunho.mp4
  python .claude/skills/editar/render.py <projeto>              final 1080p -> final/<projeto>.mp4
  python .claude/skills/editar/render.py <projeto> --validar    so confere os cortes
Opcoes: -o <saida>  --sem-legenda  --sem-loudnorm  --fps 30  --resolucao-original
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import numpy as np

from comum import (QUADRO, RAIZ, caminho, carregar_edl, carregar_palavras, fps_comum, iniciar, ler_audio,
                   montar_linha, rel, rodar, sondar, trocas_de_plano, volume_db)

FADE = 0.03
LUFS, TRUE_PEAK, LRA = -14.0, -1.5, 11.0
J_PADRAO = 0.13  # J-cut padrao (s): o som do proximo trecho entra ~4 quadros antes da imagem
# Troca de plano da fonte (zoom do celular, emenda) a menos disto da ponta de um trecho: a imagem congela no
# ultimo quadro bom em vez de mostrar uns quadros do outro plano (o "pisca").
BORDA_PLANO = 0.6
# A boca comeca a se mexer ~0,1 s antes do som da palavra. A imagem de um trecho (com o J-cut) tem que acabar
# antes disso quando a fala seguinte do bruto nao entra na edicao, senao aparece a boca "comecando a falar".
BOCA_ANTES = 0.12
# p, b e m comecam com os labios FECHADOS antes do som: parece fim de frase, nao "comecando a falar".
BILABIAIS = ("p", "b", "m")
# Transicoes sutis (so imagem; o som segue os cortes). Nada de flash/zoom/whip: so fusao.
TRANSICOES = {"dissolver": "fade", "escurecer": "fadeblack"}
TRANSICAO_PADRAO = 0.3  # s
# Vozes por trecho: "personagem" = outra pessoa falando (tom acima + banda de mensagem de voz).
VOZES = {
    "personagem": "rubberband=pitch=1.06,highpass=f=140,lowpass=f=7500",  # ~1 semitom: outra pessoa, sem caricatura
    "personagem_forte": "rubberband=pitch=1.16,highpass=f=180,lowpass=f=6500",
    "grave": "rubberband=pitch=0.86",
    "telefone": "highpass=f=300,lowpass=f=3400,acompressor=threshold=0.1:ratio=4",
}
CORES = {
    "nenhuma": "",
    "sutil": "eq=contrast=1.03:saturation=0.98",
    "neutra": "eq=contrast=1.06:saturation=1.0,curves=master='0/0 0.25/0.23 0.75/0.77 1/1'",
    # Cena de outro personagem (ex.: o aluno perguntando): fria, dessaturada, com vinheta.
    "personagem": ("eq=saturation=0.45:contrast=1.06:brightness=-0.015,"
                   "colorbalance=rs=-0.05:bs=0.06:rm=-0.03:bm=0.04:rh=-0.02:bh=0.03,vignette=PI/4"),
    "cinema_quente": ("eq=contrast=1.12:brightness=-0.02:saturation=0.88,"
                      "colorbalance=rs=0.02:gs=0.0:bs=-0.03:rm=0.04:gm=0.01:bm=-0.02:rh=0.08:gh=0.02:bh=-0.05,"
                      "curves=master='0/0 0.25/0.22 0.75/0.78 1/1'"),
}
# HDR (HLG/PQ) -> SDR Rec.709. Sem isso, video de iPhone sai estourado/saturado.
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
# Legenda: (tamanho, margem vertical) em unidades do libass (tela de 288 de altura; 16 ~ 60 px em 1080p).
# Em 9:16 a margem ~90 deixa a legenda a ~30% da base, acima da interface do Reels/TikTok/Shorts.
LEGENDAS = {
    ("frase", False): (16, 22), ("frase", True): (10, 90),
    ("reels", False): (18, 40), ("reels", True): (13, 90),
}


# -------- Fontes e validacao -------------------------------------------------


def carregar_fontes(edl: dict) -> tuple[dict, dict]:
    """ffprobe e palavras de cada fonte usada nos trechos: (infos, palavras) por chave de fonte."""
    fontes, trechos = edl.get("fontes") or {}, edl.get("trechos") or []
    if not trechos:
        sys.exit("O edl.json ainda nao tem trechos.")
    infos, palavras = {}, {}
    for k in dict.fromkeys(t["fonte"] for t in trechos):
        if k not in fontes:
            sys.exit(f"Fonte '{k}' nao existe em 'fontes' do edl.json.")
        video = caminho(fontes[k]["video"])
        if not video.is_file():
            sys.exit(f"Video da fonte {k} nao encontrado: {fontes[k]['video']}")
        infos[k] = sondar(video)
        if not infos[k]["tem_video"]:
            sys.exit(f"A fonte {k} nao tem video.")
        transcricao = fontes[k].get("transcricao")
        palavras[k] = carregar_palavras(caminho(transcricao) if transcricao else None)
        if not palavras[k]:
            print(f"AVISO: fonte {k} sem transcricao; nao da para validar cortes nem legendar esse trecho.")
    return infos, palavras


def validar(linha: list[dict], palavras: dict, infos: dict, sons: dict, volumes: dict | None = None,
            planos: dict | None = None) -> list[str]:
    avisos = []
    for tr in linha:
        n, info = tr["i"] + 1, infos[tr["fonte"]]
        if tr["inicio"] < 0 or tr["fim"] > info["duracao"] + 0.05:
            sys.exit(f"Trecho {n}: {tr['inicio']:.2f}-{tr['fim']:.2f} esta fora do video {tr['fonte']} "
                     f"(0-{info['duracao']:.2f}).")
        for a, b in sons.get(tr["fonte"], []):
            if a < tr["fim"] and b > tr["inicio"]:
                avisos.append(f"trecho {n}: mantem o ~som {a:.2f}-{b:.2f} (provavel \"e...\"/hum; confira se e intencional)")
        ws = palavras[tr["fonte"]]
        if not ws or tr.get("legenda") is False:
            continue  # sem fala de verdade (ex.: gaita tocando)
        for borda, t in (("inicio", tr["inicio"]), ("fim", tr["fim"])):
            for w in ws:
                if w["start"] + 0.02 < t < w["end"] - 0.02:
                    avisos.append(f"trecho {n}: o {borda} ({t:.2f}) corta a palavra '{w['text']}' "
                                  f"({w['start']:.2f}-{w['end']:.2f})")
        dentro = [w for w in ws if w["start"] >= tr["inicio"] - 0.02 and w["end"] <= tr["fim"] + 0.02]
        if not dentro:
            avisos.append(f"trecho {n}: nenhuma palavra inteira dentro do trecho")
            continue
        antes, depois = dentro[0]["start"] - tr["inicio"], tr["fim"] - dentro[-1]["end"]
        if antes < 0.03:
            avisos.append(f"trecho {n}: margem curta antes de '{dentro[0]['text']}' ({antes * 1000:.0f} ms; use 30-200)")
        elif antes > 0.7:
            avisos.append(f"trecho {n}: {antes:.1f}s de silencio no comeco")
        if depois < 0.03:
            avisos.append(f"trecho {n}: margem curta depois de '{dentro[-1]['text']}' ({depois * 1000:.0f} ms; use 30-200)")
        elif depois > 0.7:
            avisos.append(f"trecho {n}: {depois:.1f}s de silencio no fim")
        for a, b in zip(dentro, dentro[1:]):
            if b["start"] - a["end"] > 0.7:
                avisos.append(f"trecho {n}: pausa de {b['start'] - a['end']:.1f}s em {a['end']:.2f} "
                              f"(entre '{a['text']}' e '{b['text']}')")
        for c in (planos or {}).get(tr["fonte"], []):
            if tr["fim"] - BORDA_PLANO < c < tr["fim"] and tr["fim"] - c > 0.25:
                avisos.append(f"trecho {n}: o plano da fonte muda em {c:.2f}, {tr['fim'] - c:.2f}s antes do fim: a imagem "
                              f"congela esse tempo (se incomodar, corte antes de {c:.2f})")
            elif tr["inicio"] < c < tr["inicio"] + BORDA_PLANO and c - tr["inicio"] > 0.25:
                avisos.append(f"trecho {n}: o plano da fonte muda em {c:.2f}, {c - tr['inicio']:.2f}s depois do inicio: "
                              f"a imagem congela esse tempo (se incomodar, comece em {c:.2f})")
        # A boca comeca a fala seguinte do bruto antes do som: o trecho tem que acabar antes disso.
        seguinte = proxima_fala(palavras, tr["fonte"], tr["fim"], volumes)
        ultima_entra = tr["i"] + 1 < len(linha) and linha[tr["i"] + 1]["fonte"] == tr["fonte"] \
            and abs(linha[tr["i"] + 1]["inicio"] - tr["fim"]) < 0.05  # continua no proximo trecho: ok
        antes = antecedencia_da_boca(palavras, tr["fonte"], seguinte) if seguinte is not None else BOCA_ANTES
        if seguinte is not None and not ultima_entra and tr["fim"] > seguinte - antes + 0.01:
            sugestao = seguinte - antes
            if volumes is not None and tr["fonte"] in volumes:
                db_f, limiar_f, _ = volumes[tr["fonte"]]
                alto_f = db_f[int(tr["inicio"] / QUADRO):int(min(tr["fim"], seguinte) / QUADRO)] >= limiar_f
                if alto_f.any():
                    fim_voz = tr["inicio"] + (len(alto_f) - int(np.argmax(alto_f[::-1]))) * QUADRO
                    sugestao = min(sugestao, fim_voz + 0.08)
            avisos.append(f"trecho {n}: termina em {tr['fim']:.2f}, mas a fala seguinte do bruto comeca em "
                          f"{seguinte:.2f} e a boca ja mexe antes do som: aparece voce comecando a falar "
                          f"(sugestao: fim {sugestao:.2f})")
        # O tempo das palavras pode vir adiantado; o volume do audio nao mente.
        if volumes is not None and tr["fonte"] in volumes:
            db, limiar, _ = volumes[tr["fonte"]]
            alto = db[int(tr["inicio"] / QUADRO):int(tr["fim"] / QUADRO)] >= limiar
            if alto.any():
                mudo_antes = int(np.argmax(alto)) * QUADRO
                mudo_depois = int(np.argmax(alto[::-1])) * QUADRO
                if mudo_antes > 0.15:
                    avisos.append(f"trecho {n}: {mudo_antes:.2f}s de silencio real antes da fala "
                                  f"(sugestao: inicio {tr['inicio'] + mudo_antes - 0.05:.2f})")
                if mudo_depois > 0.25:
                    avisos.append(f"trecho {n}: {mudo_depois:.2f}s de silencio real depois da fala "
                                  f"(sugestao: fim {tr['fim'] - mudo_depois + 0.08:.2f})")
    for a in linha:
        for b in linha[a["i"] + 1:]:
            if a["fonte"] == b["fonte"] and a["inicio"] < b["fim"] and b["inicio"] < a["fim"]:
                avisos.append(f"trechos {a['i'] + 1} e {b['i'] + 1} se sobrepoem na fonte {a['fonte']}")
    return avisos


def volumes_das_fontes(edl: dict, infos: dict) -> dict:
    """Volume (dBFS a cada 10 ms) e limiar de fala de cada fonte usada."""
    saida = {}
    for k, info in infos.items():
        if info["tem_audio"]:
            db = volume_db(ler_audio(caminho(edl["fontes"][k]["video"])))
            if db.size:
                piso, fala = np.percentile(db, 10), np.percentile(db, 90)
                # limiar de som (o mesmo do /transcrever) e limiar de voz clara (o ruido da sala nao passa)
                saida[k] = (db, piso + 0.15 * (fala - piso), piso + 0.3 * (fala - piso))
    return saida


def mostrar_plano(linha: list[dict]) -> None:
    print(" #  fonte  origem              dur     saida    bloco / fala")
    for tr in linha:
        fala = (tr.get("fala") or "")[:60]
        bloco = f"{tr['bloco']}  " if tr.get("bloco") else ""
        print(f"{tr['i'] + 1:2d}  {tr['fonte']:5s}  {tr['inicio']:7.2f}-{tr['fim']:7.2f}  {tr['dur']:6.2f}s"
              f"  {tr['saida_inicio']:7.2f}  {bloco}{fala}")
    print(f"Total: {linha[-1]['saida_fim']:.2f}s em {len(linha)} trechos")


# -------- Extracao e juncao --------------------------------------------------


def tamanho_saida(info: dict, lado_max: int | None) -> tuple[int, int]:
    w, h = info["largura"], info["altura"]
    fator = min(1.0, lado_max / max(w, h)) if lado_max else 1.0
    return int(round(w * fator / 2)) * 2, int(round(h * fator / 2)) * 2


def resolver_cor(nome: str | None) -> str:
    """Preset de CORES ou filtro FFmpeg cru."""
    nome = nome or "nenhuma"
    if nome in CORES:
        return CORES[nome]
    if "=" in nome:
        return nome
    sys.exit(f"Cor desconhecida: {nome}. Use {', '.join(CORES)} ou um filtro FFmpeg.")


def resolver_voz(nome: str | None) -> str:
    if not nome:
        return ""
    if nome in VOZES:
        return VOZES[nome]
    if "=" in nome:
        return nome
    sys.exit(f"Voz desconhecida: {nome}. Use {', '.join(VOZES)} ou um filtro FFmpeg.")


def antecedencia_da_boca(palavras: dict, fonte: str, inicio_fala: float) -> float:
    """Quanto antes do som a boca ja mostra a palavra: ~0,12 s; quase nada se ela comeca com p/b/m."""
    # a palavra dona desse som: a ultima que comeca ate ele (o Parakeet as vezes marca um pouco antes)
    perto = [w for w in palavras.get(fonte, []) if inicio_fala - 0.3 <= w["start"] <= inicio_fala + 0.1]
    if perto and perto[-1]["text"][:1].lower() in BILABIAIS:
        return 0.02
    return BOCA_ANTES


def proxima_fala(palavras: dict, fonte: str, depois_de: float, volumes: dict | None = None) -> float | None:
    """Inicio da fala que vem depois de um instante no bruto: a proxima palavra marcada ou, se vier antes, o
    primeiro som de voz clara no audio (o Parakeet as vezes marca a palavra antes do corte e ela "some")."""
    candidatos = [w["start"] for w in palavras.get(fonte, []) if w["start"] >= depois_de - 0.02][:1]
    if volumes and fonte in volumes:
        db, _, limiar_voz = volumes[fonte]
        k0 = int(depois_de / QUADRO) + 1
        for k in range(k0, min(k0 + 80, db.size)):
            if db[k] >= limiar_voz:
                candidatos.append(k * QUADRO)
                break
    return min(candidatos) if candidatos else None


def cortes_j(linha: list[dict], edl: dict, infos: dict, fps: Fraction, planos: dict,
             palavras: dict | None = None, volumes: dict | None = None) -> list[float]:
    """J-cut em cada corte: o som do trecho que entra comeca antes da troca de imagem (a imagem do anterior
    segura alguns quadros). So a IMAGEM se desloca; o som segue a lista de cortes, sem nada sobreposto.
    Devolve J[n] = atraso da troca de imagem no inicio do trecho n (J[0] = J[N] = 0)."""
    padrao = float(edl.get("j_cut", J_PADRAO))
    atrasos = [0.0]
    for n in range(1, len(linha)):
        ant, tr = linha[n - 1], linha[n]
        j = float(tr.get("j", padrao))
        sobra_fonte = infos[ant["fonte"]]["duracao"] - ant["fim"] - 0.05  # imagem que existe depois do corte
        j = max(0.0, min(j, tr["dur"] / 3, ant["dur"] / 3, sobra_fonte))
        # A imagem esticada depois do corte nao pode cair em outro plano da fonte, nem somar com um congelamento.
        if any(ant["fim"] - BORDA_PLANO < c <= ant["fim"] + j + 0.04 for c in planos.get(ant["fonte"], [])):
            j = 0.0
        # A imagem esticada mostraria a boca comecando a fala seguinte do bruto (que foi cortada).
        seguinte = proxima_fala(palavras or {}, ant["fonte"], ant["fim"], volumes)
        if seguinte is not None:
            antes = antecedencia_da_boca(palavras or {}, ant["fonte"], seguinte)
            j = min(j, max(0.0, seguinte - antes - ant["fim"]))
        atrasos.append(float(Fraction(int(j * fps)) / fps))  # quadros inteiros
    return atrasos + [0.0]


def bordas_de_plano(tr: dict, v_ini: float, v_dur: float, fps: Fraction, planos: list[float]) -> tuple[float, float]:
    """(inicio, fim) da imagem que pode ser usada: corta pedacos de outro plano da fonte nas pontas."""
    v_fim, meio = v_ini + v_dur, (tr["inicio"] + tr["fim"]) / 2
    ini, fim = v_ini, v_fim
    for c in planos:
        if v_ini < c < v_fim:
            if c > meio and v_fim - c <= BORDA_PLANO:
                fim = min(fim, c - 0.5 / float(fps))  # o quadro em c ja e do outro plano
            elif c <= meio and c - v_ini <= BORDA_PLANO:
                ini = max(ini, c)
    return ini, fim


def extrair_video(tr: dict, video: Path, info: dict, w: int, h: int, fps: Fraction, cor: str,
                  preset: str, crf: str, pasta: Path, v_ini: float, v_dur: float,
                  planos: list[float], pad_ini: float = 0.0,
                  pad_fim: float = 0.0) -> tuple[Path, bool, str, tuple[float, float]]:
    """So a imagem do trecho, de v_ini por v_dur (J-cut ja aplicado). Pedaco de outro plano da fonte na ponta
    vira quadro congelado (o zoom continua por cima). Devolve (arquivo, veio_do_cache, nota, (congela_ini, congela_fim))."""
    ini_real, fim_real = bordas_de_plano(tr, v_ini, v_dur, fps, planos)
    congela_ini, congela_fim = ini_real - v_ini, v_ini + v_dur - fim_real
    vf = [TONEMAP] if info["hdr"] else []
    # pad_ini/pad_fim: quadros a mais nas pontas para a transicao se fundir (escondidos pela fusao).
    if congela_ini + pad_ini > 0.001 or congela_fim + pad_fim > 0.001:
        vf.append(f"tpad=start_mode=clone:start_duration={congela_ini + pad_ini:.4f}"
                  f":stop_mode=clone:stop_duration={congela_fim + pad_fim:.4f}")
    dur_saida = v_dur + pad_ini + pad_fim
    nota = ""
    if congela_ini > 0.001:
        nota += f" congela {congela_ini:.2f}s no inicio (troca de plano da fonte em {ini_real:.2f})"
    if congela_fim > 0.001:
        nota += f" congela {congela_fim:.2f}s no fim (troca de plano da fonte em {fim_real:.2f})"
    # zoom: numero (fixo) ou [inicio, fim] (vai mudando devagar ao longo do trecho).
    zoom = tr.get("zoom") or 1.0
    z0, z1 = (float(zoom[0]), float(zoom[1])) if isinstance(zoom, list) else (float(zoom), float(zoom))
    fx, fy = tr.get("foco") or (0.5, 0.42)  # centro do recorte em fracao do quadro; padrao: o rosto
    mesmo_formato = abs(info["largura"] / info["altura"] - w / h) < 0.01
    if z0 != z1 and mesmo_formato:
        # zoompan recalcula o recorte a cada quadro; com fonte 4K o passo de 1 px some na saida 1080p.
        quadros = max(1, round(dur_saida * fps) - 1)
        vf += [f"zoompan=z='{z0}+({z1 - z0:.5f})*on/{quadros}'"
               f":x='max(0,min(iw-iw/zoom,iw*{fx}-iw/zoom/2))':y='max(0,min(ih-ih/zoom,ih*{fy}-ih/zoom/2))'"
               f":d=1:s={w}x{h}:fps={fps}", "setsar=1"]
    else:
        z = max(z0, z1)
        if z > 1:
            cw, ch = int(info["largura"] / z) // 2 * 2, int(info["altura"] / z) // 2 * 2
            cx = min(max(round(fx * info["largura"] - cw / 2), 0), info["largura"] - cw)
            cy = min(max(round(fy * info["altura"] - ch / 2), 0), info["altura"] - ch)
            vf.append(f"crop={cw}:{ch}:{cx}:{cy}")
        vf.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    cor_trecho = resolver_cor(tr["cor"]) if tr.get("cor") else cor
    if cor_trecho:
        vf.append(cor_trecho)
    vf = ",".join(vf)
    chave = json.dumps([rel(video), video.stat().st_mtime, round(v_ini, 4), round(dur_saida, 4), round(ini_real, 4),
                        round(fim_real, 4), vf, preset, crf, str(fps)])
    saida = pasta / f"v_{tr['fonte']}_{v_ini:08.2f}_{hashlib.sha1(chave.encode()).hexdigest()[:10]}.mkv"
    if saida.exists():
        return saida, True, nota, (congela_ini, congela_fim)
    temporario = saida.with_suffix(".tmp.mkv")
    rodar(["ffmpeg", "-y", "-v", "error", "-ss", f"{ini_real:.4f}", "-t", f"{fim_real - ini_real:.4f}", "-i", video,
           "-t", f"{dur_saida:.4f}", "-map", "0:v:0",
           "-vf", vf, "-r", str(fps), "-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p",
           temporario])
    temporario.replace(saida)
    return saida, False, nota, (congela_ini, congela_fim)


def extrair_audio(tr: dict, video: Path, info: dict, pasta: Path) -> tuple[Path, bool]:
    """So o som do trecho (PCM), com fade de 30 ms nas bordas, ganho e efeito de voz."""
    dur = tr["dur"]
    voz = resolver_voz(tr.get("voz"))
    ganho = f"volume={float(tr['ganho_db'])}dB," if tr.get("ganho_db") else ""
    saida_fade = float(tr.get("fade_saida") or FADE)  # ex.: nota de gaita que precisa morrer devagar
    af = (f"{voz + ',' if voz else ''}{ganho}afade=t=in:st=0:d={FADE},"
          f"afade=t=out:st={max(0.0, dur - saida_fade):.4f}:d={saida_fade}")
    chave = json.dumps([rel(video), video.stat().st_mtime, round(tr["inicio"], 4), round(dur, 4), af])
    saida = pasta / f"a_{tr['fonte']}_{tr['inicio']:08.2f}_{hashlib.sha1(chave.encode()).hexdigest()[:10]}.wav"
    if saida.exists():
        return saida, True
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{tr['inicio']:.4f}", "-i", video]
    if not info["tem_audio"]:
        cmd += ["-f", "lavfi", "-t", f"{dur:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
    temporario = saida.with_suffix(".tmp.wav")
    rodar(cmd + ["-t", f"{dur:.4f}", "-map", "0:a:0" if info["tem_audio"] else "1:a:0", "-af", af,
                 "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", temporario])
    temporario.replace(saida)
    return saida, False


def juntar(videos: list[Path], audios: list[Path], base: Path, transicoes: list | None = None,
           duracoes: list[float] | None = None, fps: Fraction = Fraction(30), preset: str = "medium",
           crf: str = "18") -> None:
    """Concatena imagem e som separadamente e junta os dois em base. Sem transicao, a imagem e colada sem
    recodificar; com transicao, monta com xfade (cada trecho ja vem com quadros extras nas pontas da fusao).
    transicoes[k] = (filtro xfade, duracao) na entrada do video k, ou None para corte seco."""
    pasta = videos[0].parent
    for nome, lista in (("lista_v.txt", videos), ("lista_a.txt", audios)):
        (pasta / nome).write_text("".join(f"file '{s.name}'\n" for s in lista), encoding="utf-8")
    so_video, so_audio = pasta / "_video.mkv", pasta / "_audio.wav"
    if transicoes and any(transicoes):
        entradas, partes = [], []
        for k, v in enumerate(videos):
            entradas += ["-i", v]
            # fps antes do settb: o fps muda a base de tempo, e o xfade exige a mesma nas duas entradas
            partes.append(f"[{k}:v]setpts=PTS-STARTPTS,fps={fps},settb=AVTB[e{k}]")
        atual, dur_atual = "[e0]", duracoes[0]
        for k in range(1, len(videos)):
            if transicoes[k]:
                filtro, d = transicoes[k]
                partes.append(f"{atual}[e{k}]xfade=transition={filtro}:duration={d:.4f}:offset={dur_atual - d:.4f}[x{k}]")
                dur_atual += duracoes[k] - d
            else:
                partes.append(f"{atual}[e{k}]concat=n=2:v=1:a=0[x{k}]")
                dur_atual += duracoes[k]
            atual = f"[x{k}]"
        roteiro = pasta / "_transicoes.txt"
        roteiro.write_text(";\n".join(partes), encoding="utf-8")
        rodar(["ffmpeg", "-y", "-v", "error", *entradas, "-/filter_complex", roteiro, "-map", atual,
               "-c:v", "libx264", "-preset", preset, "-crf", str(max(12, int(crf) - 2)), "-pix_fmt", "yuv420p",
               "-r", str(fps), so_video])
    else:
        rodar(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", pasta / "lista_v.txt", "-c", "copy",
               so_video])
    rodar(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", pasta / "lista_a.txt", "-c", "copy", so_audio])
    rodar(["ffmpeg", "-y", "-v", "error", "-i", so_video, "-i", so_audio, "-map", "0:v", "-map", "1:a", "-c", "copy", base])
    so_video.unlink(missing_ok=True)
    so_audio.unlink(missing_ok=True)


# -------- Transcricao e legenda no tempo de saida ----------------------------


def palavras_saida(linha: list[dict], palavras: dict) -> list[dict]:
    """Palavras de cada trecho levadas para o tempo do video cortado: start - inicio_trecho + offset."""
    saida = []
    for tr in linha:
        if tr.get("legenda") is False:
            continue  # ex.: gaita tocando, o Parakeet "ouve" palavras que nao existem
        for w in palavras[tr["fonte"]]:
            dentro = min(w["end"], tr["fim"]) - max(w["start"], tr["inicio"])
            if dentro <= 0 or dentro < 0.5 * (w["end"] - w["start"]):
                continue
            s = max(w["start"], tr["inicio"]) - tr["inicio"] + tr["saida_inicio"]
            e = min(w["end"], tr["fim"]) - tr["inicio"] + tr["saida_inicio"]
            saida.append({"text": w["text"], "start": round(s, 3), "end": round(e, 3)})
    return saida


def aplicar_correcoes(ws: list[dict], correcoes: dict) -> list[dict]:
    """Corrige erros da transcricao (nomes, marcas): {"clawed code": "Claude Code"}. Ignora maiusculas e pontuacao."""
    def nucleo(t: str) -> str:
        return re.sub(r"\W", "", t).lower()

    for errado, certo in (correcoes or {}).items():
        alvo, novo = [nucleo(p) for p in errado.split()], certo.split()
        i = 0
        while alvo and i + len(alvo) <= len(ws):
            bloco = ws[i:i + len(alvo)]
            if [nucleo(w["text"]) for w in bloco] != alvo:
                i += 1
                continue
            pontuacao = re.search(r"\W*$", bloco[-1]["text"]).group(0)
            if len(novo) == len(alvo):
                trocadas = [{**w, "text": n} for w, n in zip(bloco, novo)]
            else:
                trocadas = [{"text": certo, "start": bloco[0]["start"], "end": bloco[-1]["end"]}]
            trocadas[-1]["text"] += pontuacao
            ws = ws[:i] + trocadas + ws[i + len(alvo):]
            i += len(trocadas)
    return ws


def blocos_reels(ws: list[dict]) -> list[list[dict]]:
    """~2 palavras por legenda; cresce ate 3 em vez de piscar por menos de 0,35 s (video-use)."""
    blocos, atual = [], []
    for i, w in enumerate(ws):
        atual.append(w)
        prox = ws[i + 1] if i + 1 < len(ws) else None
        if (prox is None or w["text"][-1] in ".,!?;:" or prox["start"] - w["end"] >= 0.3 or len(atual) >= 3
                or (len(atual) >= 2 and w["end"] - atual[0]["start"] >= 0.35)):
            blocos.append(atual)
            atual = []
    return blocos


def blocos_frase(ws: list[dict], max_chars: int) -> list[list[dict]]:
    blocos, atual = [], []
    for w in ws:
        if atual and (w["start"] - atual[-1]["end"] > 0.6 or atual[-1]["text"][-1:] in ".?!"
                      or len(" ".join(x["text"] for x in atual + [w])) > max_chars):
            blocos.append(atual)
            atual = []
        atual.append(w)
    if atual:
        blocos.append(atual)
    return blocos


def duas_linhas(texto: str, linha: int) -> str:
    if len(texto) <= linha or " " not in texto:
        return texto
    meio = len(texto) // 2
    corte = min((i for i, c in enumerate(texto) if c == " "), key=lambda i: abs(i - meio))
    return texto[:corte] + "\n" + texto[corte + 1:]


def tempo_srt(s: float) -> str:
    ms = int(round(s * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    seg, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{seg:02d},{ms:03d}"


def legenda(cfg, ws: list[dict], pasta: Path, vertical: bool, total: float) -> tuple[Path, str] | None:
    """Escreve legenda.srt (ou usa o arquivo do EDL) e devolve (arquivo, force_style)."""
    if not cfg:
        return None
    if isinstance(cfg, str):
        cfg = {"estilo": cfg}
    estilo = cfg.get("estilo", "frase")
    if (estilo, vertical) not in LEGENDAS:
        sys.exit(f"Estilo de legenda desconhecido: {estilo} (use frase ou reels)")
    tamanho, margem = LEGENDAS[(estilo, vertical)]
    forca = (f"FontName={cfg.get('fonte', 'Arial')},FontSize={cfg.get('tamanho', tamanho)},"
             f"Bold={1 if estilo == 'reels' else 0},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
             f"BackColour=&H80000000,BorderStyle=1,Outline={2 if estilo == 'reels' else 1.5},Shadow=0,"
             f"Alignment=2,MarginV={cfg.get('margem', margem)}")
    if cfg.get("arquivo"):
        arquivo = caminho(cfg["arquivo"])
        if not arquivo.exists():
            sys.exit(f"Legenda do EDL nao encontrada: {cfg['arquivo']}")
        return arquivo, forca

    if estilo == "reels":
        blocos = blocos_reels(ws)
        textos = [" ".join(w["text"] for w in b).rstrip(",;:").upper() for b in blocos]
        extra = 0.1
    else:
        max_chars, linha = (40, 20) if vertical else (64, 32)
        blocos = blocos_frase(ws, max_chars)
        textos = [duas_linhas(" ".join(w["text"] for w in b), linha) for b in blocos]
        extra = 0.3
    # Segura cada legenda um pouco depois da ultima palavra, sem invadir a proxima.
    cues = []
    for i, (b, texto) in enumerate(zip(blocos, textos)):
        ini = b[0]["start"]
        fim = min(max(b[-1]["end"] + extra, ini + 0.5), total)
        if i + 1 < len(blocos):
            fim = min(fim, blocos[i + 1][0]["start"])
        cues.append((ini, max(fim, ini + 0.1), texto))
    arquivo = pasta / "legenda.srt"
    arquivo.write_text("\n".join(f"{i}\n{tempo_srt(a)} --> {tempo_srt(b)}\n{t}\n"
                                 for i, (a, b, t) in enumerate(cues, 1)), encoding="utf-8")
    return arquivo, forca


def camada_hyperframes(pasta: Path, ws: list[dict], cfg: dict, fps: Fraction) -> dict:
    """Camada transparente feita pelo /legendar. Avisa se foi gerada para outros cortes ou outra configuracao."""
    camada, usado = pasta / "legenda" / "legenda.webm", pasta / "legenda" / "gerado_com.json"
    if not camada.exists():
        sys.exit(f"Falta a camada de legenda. Rode antes: python .claude/skills/legendar/legendar.py {pasta.name}")
    gerado = json.loads(usado.read_text(encoding="utf-8")) if usado.exists() else {}
    if gerado != {"palavras": ws, "legenda": cfg}:
        print(f"AVISO: a legenda e de outra versao dos cortes/correcoes/estilo. "
              f"Rode de novo: python .claude/skills/legendar/legendar.py {pasta.name}")
    return {"arquivo": rel(camada), "inicio": 0, "tela_cheia": True, "fps": str(fps)}


# -------- Passada final ------------------------------------------------------


def medir_loudness(base: Path) -> dict | None:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(base), "-vn", "-af",
                        f"loudnorm=I={LUFS}:TP={TRUE_PEAK}:LRA={LRA}:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    achado = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not achado:
        return None
    dados = json.loads(achado.group(0))
    try:
        valores = {k: float(dados[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (KeyError, ValueError):
        return None
    return valores if all(math.isfinite(v) for v in valores.values()) else None


def filtro_loudnorm(base: Path, duas_passadas: bool) -> str:
    alvo = f"loudnorm=I={LUFS}:TP={TRUE_PEAK}:LRA={LRA}"
    if not duas_passadas:
        return alvo
    m = medir_loudness(base)
    if not m:
        print("  (nao consegui medir a loudness; usando 1 passada)")
        return alvo
    print(f"  loudness medida: {m['input_i']:.1f} LUFS, pico {m['input_tp']:.1f} dBTP -> alvo {LUFS:.0f} LUFS")
    return (f"{alvo}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")


def mixar_trilha(base: Path, cfg: dict, linha: list[dict], pasta: Path) -> Path:
    """Voz + musica com ducking -> mix.wav. A musica fica `relativo_db` abaixo da voz (medido, nao chutado)
    e sai em fade ate `ate` (segundos do video) ou ate o inicio do trecho `ate_trecho` (1 = primeiro)."""
    musica = caminho(cfg["arquivo"])
    if not musica.is_file():
        sys.exit(f"Trilha nao encontrada: {cfg['arquivo']}")
    total = linha[-1]["saida_fim"]
    if cfg.get("ate_trecho"):
        ate = linha[int(cfg["ate_trecho"]) - 1]["saida_inicio"] + 0.3
    else:
        ate = float(cfg.get("ate", total))
    fade = float(cfg.get("fade", 1.5))
    inicio = float(cfg.get("inicio_na_musica", 0))
    trecho_musica = pasta / "_musica.wav"
    rodar(["ffmpeg", "-y", "-v", "error", "-ss", f"{inicio:.3f}", "-i", musica, "-t", f"{ate:.3f}",
           "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", trecho_musica])
    voz, mus = medir_loudness(base), medir_loudness(trecho_musica)
    if not voz or not mus:
        sys.exit("Nao consegui medir a loudness da voz ou da musica.")
    ganho = voz["input_i"] + float(cfg.get("relativo_db", -18)) - mus["input_i"]
    print(f"  trilha: voz {voz['input_i']:.1f} LUFS, musica {mus['input_i']:.1f} LUFS -> ganho {ganho:+.1f} dB, "
          f"sai em {ate:.1f}s")
    mix = pasta / "mix.wav"
    grafo = (f"[1:a]volume={ganho:.2f}dB,afade=t=in:d=0.4,afade=t=out:st={max(0.0, ate - fade):.3f}:d={fade},"
             f"apad[m];[0:a]asplit=2[voz][chave];"
             f"[m][chave]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck];"
             f"[voz][duck]amix=inputs=2:duration=first:normalize=0[a]")
    rodar(["ffmpeg", "-y", "-v", "error", "-i", base, "-i", trecho_musica, "-filter_complex", grafo,
           "-map", "[a]", "-c:a", "pcm_s16le", "-ar", "48000", mix])
    trecho_musica.unlink(missing_ok=True)
    return mix


def compor(base: Path, sobreposicoes: list[dict], srt: tuple[Path, str] | None, saida: Path, total: float,
           w: int, h: int, preset: str, crf: str, loudnorm: str | None, audio: Path | None = None,
           fps: Fraction = Fraction(30)) -> None:
    entradas: list = ["-i", base]
    if audio:
        entradas += ["-i", audio]
    primeira_ov = 2 if audio else 1
    partes: list[str] = []
    atual = "[0:v]"
    for i, ov in enumerate(sobreposicoes, primeira_ov):
        arq = caminho(ov["arquivo"])
        if not arq.exists():
            sys.exit(f"Sobreposicao nao encontrada: {ov['arquivo']}")
        ini = float(ov["inicio"])
        imagem = arq.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        dur = float(ov.get("duracao") or (0 if imagem else sondar(arq)["duracao"]))
        if dur <= 0:
            sys.exit(f"Sobreposicao {arq.name}: informe 'duracao'.")
        if imagem:
            entradas += ["-loop", "1", "-t", f"{dur:.3f}"]
        elif arq.suffix.lower() == ".webm":
            entradas += ["-c:v", "libvpx-vp9"]  # o decodificador nativo descarta o canal alfa
        entradas += ["-i", arq]
        escala = f"scale={w}:{h}," if ov.get("tela_cheia", True) else ""
        # Rule 4 do video-use: desloca o quadro 0 da sobreposicao para o inicio da janela.
        # Camada gerada na taxa do projeto (ex.: legenda do HyperFrames): tempo pelo numero do quadro, nao pelo
        # carimbo do arquivo (que pode vir 29,97 com conteudo a 30 e atrasar a legenda ao longo do video).
        tempo = f"N/({ov['fps']}*TB)" if ov.get("fps") else "PTS-STARTPTS"
        partes.append(f"[{i}:v]{escala}setpts={tempo}+{ini:.3f}/TB[s{i}]")
        partes.append(f"{atual}[s{i}]overlay=x={ov.get('x', '(W-w)/2')}:y={ov.get('y', '(H-h)/2')}"
                      f":eof_action=pass:enable='between(t,{ini:.3f},{ini + dur:.3f})'[v{i}]")
        atual = f"[v{i}]"
    if srt:
        # Legenda POR ULTIMO, senao as sobreposicoes cobrem. Caminho relativo: o FFmpeg roda na raiz.
        partes.append(f"{atual}subtitles=filename='{rel(srt[0])}':force_style='{srt[1]}'[vout]")
    elif partes:
        partes.append(f"{atual}null[vout]")

    cmd: list = ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-stats", *entradas]
    if partes:
        cmd += ["-filter_complex", ";".join(partes), "-map", "[vout]",
                "-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-map", "0:v", "-c:v", "copy"]
    cmd += ["-map", "1:a" if audio else "0:a"]
    if loudnorm:
        cmd += ["-af", loudnorm]
    cmd += ["-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{total:.3f}",
            "-movflags", "+faststart", saida]
    saida.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(c) for c in cmd], cwd=RAIZ)
    if r.returncode != 0:
        sys.exit("A passada final do FFmpeg falhou (veja a mensagem acima).")


def registrar(pasta: Path, saida: Path, modo: str, fps: Fraction, linha: list[dict], atrasos: list[float],
              congelamentos: list[list[float]]) -> None:
    """renders.json: linha do tempo de cada render, usada pelo /conferir."""
    arquivo = pasta / "renders.json"
    dados = json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}
    dados[rel(saida)] = {
        "modo": modo, "fps": str(fps), "duracao": round(linha[-1]["saida_fim"], 4),
        "quando": datetime.now().isoformat(timespec="seconds"), "congelamentos": congelamentos,
        "trechos": [{**{k: tr.get(k) for k in ("fonte", "inicio", "fim", "saida_inicio", "saida_fim", "bloco")},
                     "j_imagem": round(atrasos[tr["i"]], 4)} for tr in linha],
    }
    arquivo.write_text(json.dumps(dados, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# -------- Principal ----------------------------------------------------------


def main() -> None:
    iniciar()
    ap = argparse.ArgumentParser(description="Renderiza o video a partir do edl.json")
    ap.add_argument("projeto", help="nome do projeto (trabalho/<projeto>/edl.json)")
    ap.add_argument("-o", "--saida", type=Path, help="arquivo de saida")
    ap.add_argument("--rascunho", action="store_true", help="720p, ultrafast, CRF 28: para conferir cortes")
    ap.add_argument("--validar", action="store_true", help="so valida os cortes, nao renderiza")
    ap.add_argument("--sem-legenda", action="store_true")
    ap.add_argument("--sem-loudnorm", action="store_true", help="use se ainda vai entrar trilha/mixagem")
    ap.add_argument("--sem-trilha", action="store_true", help="ignora a trilha do edl.json")
    ap.add_argument("--fps", help="taxa de saida (padrao: a da primeira fonte), ex.: 30 ou 30000/1001")
    ap.add_argument("--resolucao-original", action="store_true", help="nao limita o final a 1080p")
    args = ap.parse_args()

    pasta, edl = carregar_edl(args.projeto)
    fontes = edl.get("fontes") or {}
    infos, palavras = carregar_fontes(edl)
    primeira = infos[edl["trechos"][0]["fonte"]]
    fps = fps_comum(Fraction(args.fps)) if args.fps else primeira["fps"]
    linha = montar_linha(edl, fps)
    mostrar_plano(linha)
    arq_sons = pasta / "sons.json"
    sons = json.loads(arq_sons.read_text(encoding="utf-8")) if arq_sons.exists() else {}
    planos_fontes = {k: trocas_de_plano(caminho(fontes[k]["video"])) for k in infos}
    volumes = volumes_das_fontes(edl, infos)
    avisos = validar(linha, palavras, infos, sons, volumes, planos_fontes)
    for a in avisos:
        print(f"AVISO: {a}")
    if args.validar:
        print("Cortes ok." if not avisos else f"{len(avisos)} aviso(s).")
        return

    cor = resolver_cor(edl.get("cor"))

    modo = "rascunho" if args.rascunho else "final"
    lado = 1280 if args.rascunho else (None if args.resolucao_original else 1920)
    w, h = tamanho_saida(primeira, lado)
    preset, crf = ("ultrafast", "28") if args.rascunho else ("medium", "18")
    saida = (args.saida.resolve() if args.saida
             else pasta / "rascunho.mp4" if args.rascunho else RAIZ / "final" / f"{pasta.name}.mp4")

    pasta_trechos = pasta / f"trechos_{modo}"
    pasta_trechos.mkdir(parents=True, exist_ok=True)
    planos = {k: trocas_de_plano(caminho(fontes[k]["video"])) for k in infos}
    atrasos = cortes_j(linha, edl, infos, fps, planos, palavras, volumes)
    transicoes = [None] * len(linha)  # transicoes[n] = (filtro xfade, duracao) na entrada do trecho n
    for tr in linha[1:]:
        t = tr.get("transicao")
        if not t:
            continue
        t = {"tipo": t} if isinstance(t, str) else t
        if t.get("tipo", "dissolver") not in TRANSICOES:
            sys.exit(f"Transicao desconhecida: {t.get('tipo')}. Use {', '.join(TRANSICOES)}.")
        meia = float(Fraction(round(float(t.get("duracao", TRANSICAO_PADRAO)) / 2 * fps)) / fps)  # quadros inteiros
        transicoes[tr["i"]] = (TRANSICOES[t.get("tipo", "dissolver")], 2 * meia)
        atrasos[tr["i"]] = 0.0  # a fusao ja suaviza o corte: sem J-cut aqui
    print(f"\nExtraindo {len(linha)} trechos ({w}x{h} @ {float(fps):.3g} fps, {modo}; J-cut ate "
          f"{max(atrasos) * 1000:.0f} ms)...", flush=True)
    videos, audios, do_cache, congelamentos, duracoes_video = [], [], 0, [], []
    for tr in linha:
        fonte, n = tr["fonte"], tr["i"]
        arquivo = caminho(fontes[fonte]["video"])
        v_ini, v_dur = tr["inicio"] + atrasos[n], tr["dur"] - atrasos[n] + atrasos[n + 1]
        pad_ini = transicoes[n][1] / 2 if transicoes[n] else 0.0
        pad_fim = transicoes[n + 1][1] / 2 if n + 1 < len(linha) and transicoes[n + 1] else 0.0
        v, cache_v, nota, (ci, cf) = extrair_video(tr, arquivo, infos[fonte], w, h, fps, cor, preset, crf,
                                                   pasta_trechos, v_ini, v_dur, planos[fonte], pad_ini, pad_fim)
        duracoes_video.append(v_dur + pad_ini + pad_fim)
        if transicoes[n]:
            nota += f" entra com transicao ({transicoes[n][1]:.2f}s)"
        inicio_imagem, fim_imagem = tr["saida_inicio"] + atrasos[n], tr["saida_fim"] + atrasos[n + 1]
        if ci > 0.001:
            congelamentos.append([round(inicio_imagem, 3), round(inicio_imagem + ci, 3)])
        if cf > 0.001:
            congelamentos.append([round(fim_imagem - cf, 3), round(fim_imagem, 3)])
        a, cache_a = extrair_audio(tr, arquivo, infos[fonte], pasta_trechos)
        videos.append(v)
        audios.append(a)
        do_cache += cache_v and cache_a
        print(f"  {n + 1:2d}/{len(linha)} {fonte} {tr['inicio']:.2f}-{tr['fim']:.2f}"
              f"{'  (cache)' if cache_v and cache_a else ''}{nota}", flush=True)
    for velho in list(pasta_trechos.glob("*.mkv")) + list(pasta_trechos.glob("*.wav")):
        if velho not in videos and velho not in audios:
            velho.unlink()

    base = pasta / f"base_{modo}.mkv"
    juntar(videos, audios, base, transicoes, duracoes_video, fps, preset, crf)
    total = linha[-1]["saida_fim"]

    ws = aplicar_correcoes(palavras_saida(linha, palavras), edl.get("correcoes"))
    (pasta / "transcript.json").write_text(json.dumps(ws, ensure_ascii=False, indent=1), encoding="utf-8")
    sobreposicoes = list(edl.get("sobreposicoes") or [])
    cfg_legenda, srt, tipo_legenda = edl.get("legenda"), None, "nao"
    if not args.sem_legenda and isinstance(cfg_legenda, dict) and cfg_legenda.get("motor") == "hyperframes":
        sobreposicoes.append(camada_hyperframes(pasta, ws, cfg_legenda, fps))  # por ultimo: fica por cima de tudo
        tipo_legenda = "hyperframes"
    elif not args.sem_legenda:
        srt = legenda(cfg_legenda, ws, pasta, vertical=h > w, total=total)
        tipo_legenda = "srt" if srt else "nao"

    audio = None
    if edl.get("trilha") and not args.sem_trilha:
        audio = mixar_trilha(base, edl["trilha"], linha, pasta)
    print(f"Passada final ({len(sobreposicoes)} sobreposicoes, legenda: {tipo_legenda}, "
          f"trilha: {'sim' if audio else 'nao'})...", flush=True)
    loudnorm = None if args.sem_loudnorm else filtro_loudnorm(audio or base, duas_passadas=not args.rascunho)
    compor(base, sobreposicoes, srt, saida, total, w, h, preset, crf, loudnorm, audio, fps)
    registrar(pasta, saida, modo, fps, linha, atrasos, congelamentos)

    mb = saida.stat().st_size / 1024 / 1024
    print(f"\nPronto: {rel(saida)} ({mb:.1f} MB, {total:.2f}s, {len(linha)} trechos, {do_cache} do cache)")
    if avisos:
        print(f"{len(avisos)} aviso(s) de corte acima.")
    print(f"Conferir: python .claude/skills/conferir/conferir.py {pasta.name} --video {rel(saida)}")


if __name__ == "__main__":
    main()
