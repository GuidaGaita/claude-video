"""Legenda com a identidade da marca, feita em HyperFrames, aplicada por cima do video do /editar.

1. Le o edl.json: trechos, correcoes e "legenda": {"motor": "hyperframes", "marca": ..., ...}.
2. Leva as palavras para o tempo do video cortado (mesma conta do render.py).
3. Agrupa em frases curtas (sem terminar em "de", "o", "que"...), marca enfases e cartazes.
4. Monta trabalho/<projeto>/legenda/index.html com modelo.html + marcas/<marca>/marca.json.
5. Renderiza com a CLI do HyperFrames para legenda.webm (fundo transparente).
O render.py aplica essa camada por ultimo, por cima de todas as sobreposicoes.

Uso (da raiz do projeto):
  python .claude/skills/legendar/legendar.py <projeto>              qualidade final
  python .claude/skills/legendar/legendar.py <projeto> --rascunho   rapido, para conferir
  python .claude/skills/legendar/legendar.py <projeto> --so-gerar   so o index.html, sem renderizar
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

PASTA = Path(__file__).resolve().parent
sys.path.insert(0, str(PASTA.parent / "editar"))
from comum import RAIZ, carregar_edl, fps_comum, iniciar, montar_linha, rel, sondar  # noqa: E402
from render import aplicar_correcoes, carregar_fontes, palavras_saida, tamanho_saida  # noqa: E402

LANCADOR = Path("skills/hyperframes/scripts/plugin-cli.mjs")
# Palavras que nao devem fechar uma legenda: a frase fica pendurada ("vou te mostrar a").
FRACAS = {"a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos", "em", "na", "no",
          "nas", "nos", "e", "que", "com", "por", "pra", "pro", "para", "se", "ao", "aos", "mas", "ou", "sem",
          "te", "me", "lhe", "seu", "sua", "meu", "minha", "nosso", "nossa"}
PADRAO = {"marca": "guidagaita", "estilo": "couro", "animacao": "suave", "caixa_alta": False, "pontuacao": False, "enfase": [],
          "cartazes": [], "titulo": None, "pergunta": None, "passos": None, "chamada": None}
# Tamanhos em px num quadro de lado menor 1080 (escalam com o video). margem/topo: fracao da altura.
# Vertical segue a area segura de anuncio do Reels: nada nos 14% de cima nem nos ~30% de baixo.
FORMATO = {
    False: {"tamanho": 92, "margem": 0.074, "max_palavras": 4, "max_caracteres": 24, "topo": 0.07,
            "titulo": 84, "pergunta": 52, "passos": 60, "cartaz": 150, "chamada": 64},
    True: {"tamanho": 72, "margem": 0.14, "max_palavras": 5, "max_caracteres": 26, "topo": 0.12,
           "titulo": 76, "pergunta": 64, "passos": 50, "cartaz": 120, "chamada": 66},
}


def nucleo(t: str) -> str:
    return re.sub(r"\W", "", t).lower()


def achar(ws: list[dict], expressao: str, depois: float = -1.0) -> list[int]:
    """Indices de inicio de cada ocorrencia da expressao (ignora maiusculas e pontuacao)."""
    alvo = [nucleo(p) for p in expressao.split()]
    return [i for i in range(len(ws) - len(alvo) + 1)
            if ws[i]["start"] >= depois and [nucleo(w["text"]) for w in ws[i:i + len(alvo)]] == alvo]


def agrupar(ws: list[dict], max_palavras: int, max_caracteres: int, pausa: float = 0.25) -> list[list[int]]:
    grupos, atual = [], []
    for i, w in enumerate(ws):
        atual.append(i)
        prox = ws[i + 1] if i + 1 < len(ws) else None
        if prox is None:
            break
        texto = " ".join(ws[j]["text"] for j in atual)
        respiro = prox["start"] - w["end"]
        fecha = (w["text"][-1] in ".,!?;:" or respiro >= pausa or len(atual) >= max_palavras
                 or len(texto) + 1 + len(prox["text"]) > max_caracteres)
        if not fecha:
            continue
        if len(atual) > 1 and nucleo(w["text"]) in FRACAS and w["text"][-1].isalnum() and respiro < pausa:
            atual.pop()  # a palavra fraca abre a proxima legenda
            grupos.append(atual)
            atual = [i]
        else:
            grupos.append(atual)
            atual = []
    if atual:
        grupos.append(atual)
    return grupos


def exibir(t: str, caixa_alta: bool, pontuacao: bool) -> str:
    if not pontuacao:
        t = re.sub(r"[,.;:…]+$", "", t)
    return t.upper() if caixa_alta else t


def montar_dados(ws: list[dict], cfg: dict, total: float) -> tuple[list[dict], list[dict]]:
    enfase = {i + k for e in cfg["enfase"] for i in achar(ws, e) for k in range(len(e.split()))}
    grupos = agrupar(ws, cfg["max_palavras"], cfg["max_caracteres"])
    inicios = [max(0.0, ws[g[0]]["start"] - 0.06) for g in grupos]
    saida = []
    for n, g in enumerate(grupos):
        ini = max(inicios[n], saida[-1]["fim"] if saida else 0.0)
        limite = inicios[n + 1] if n + 1 < len(grupos) else total
        fim = min(max(ws[g[-1]]["end"] + 0.45, ini + 0.5), limite, total)
        saida.append({"ini": round(ini, 3), "fim": round(max(fim, ini + 0.1), 3), "palavras": [
            {"t": exibir(ws[i]["text"], cfg["caixa_alta"], cfg["pontuacao"]), "s": ws[i]["start"], "e": ws[i]["end"],
             "enfase": i in enfase} for i in g]})

    vertical = cfg["_vertical"]

    def inicio(c: dict, nome: str) -> float:
        """'em' (segundos do video cortado) ou 'palavra' (pousa quando e dita; 'depois_de' pula ocorrencias)."""
        if "em" in c:
            return float(c["em"])
        alvo = c.get("palavra") or c.get("texto")
        achados = achar(ws, alvo, float(c.get("depois_de", -1)))
        if not achados:
            sys.exit(f"{nome}: nao achei '{alvo}' na fala. Use 'em' (segundos do video cortado).")
        return max(0.0, ws[achados[0]]["start"] - 0.12)

    def fim(c: dict, ini: float, padrao: float) -> float:
        """'ate' (segundos), 'ate_palavra' (some pouco depois dela) ou 'duracao'."""
        if "ate" in c:
            return min(total, float(c["ate"]))
        if "ate_palavra" in c:
            achados = achar(ws, c["ate_palavra"], ini)
            if not achados:
                sys.exit(f"Nao achei '{c['ate_palavra']}' depois de {ini:.2f}s.")
            return min(total, ws[achados[0] + len(c["ate_palavra"].split()) - 1]["end"] + 0.35)
        return min(total, ini + float(c.get("duracao", padrao)))

    elementos = []
    for c in cfg["cartazes"]:
        posicao = c.get("posicao", "topo" if vertical else "direita")
        if posicao not in ("esquerda", "direita", "centro", "topo"):
            sys.exit(f"Cartaz '{c['texto']}': posicao deve ser topo, esquerda, direita ou centro.")
        ini = inicio(c, f"Cartaz '{c['texto']}'")
        elementos.append({"tipo": "cartaz", "texto": c["texto"], "posicao": posicao, "ini": ini, "fim": fim(c, ini, 1.6)})
    if cfg["titulo"]:
        c = cfg["titulo"]
        ini = float(c.get("em", 0.0)) if "palavra" not in c else inicio(c, "Titulo")
        elementos.append({"tipo": "titulo", "texto": c["texto"], "posicao": "topo", "ini": ini, "fim": fim(c, ini, 3.0)})
    if cfg["pergunta"]:
        c = cfg["pergunta"]
        ini = inicio(c, "Pergunta")
        elementos.append({"tipo": "pergunta", "texto": c["texto"], "cabecalho": c.get("cabecalho", "PERGUNTA DE UM SEGUIDOR"),
                          "posicao": "topo", "ini": ini, "fim": fim(c, ini, 4.0)})
    if cfg["passos"]:
        c = cfg["passos"]
        itens = [{"texto": p["texto"], "em": round(inicio(p, f"Passo '{p['texto']}'"), 3)} for p in c["itens"]]
        ini = itens[0]["em"]
        elementos.append({"tipo": "passos", "itens": itens, "posicao": "topo", "ini": ini,
                          "fim": fim(c, ini, itens[-1]["em"] - ini + 1.5)})
    if cfg["chamada"]:
        c = cfg["chamada"]
        ini = inicio(c, "Chamada")
        elementos.append({"tipo": "chamada", "texto": c.get("texto", "TOQUE EM SAIBA MAIS"), "posicao": "topo",
                          "ini": ini, "fim": fim(c, ini, total - ini)})
    for e in elementos:
        e["ini"], e["fim"] = round(e["ini"], 3), round(max(e["fim"], e["ini"] + 0.5), 3)
    no_topo = sorted((e for e in elementos if e["posicao"] == "topo"), key=lambda e: e["ini"])
    for a, b in zip(no_topo, no_topo[1:]):
        if b["ini"] < a["fim"]:
            print(f"AVISO: {a['tipo']} ({a['ini']:.2f}-{a['fim']:.2f}s) e {b['tipo']} ({b['ini']:.2f}s) se "
                  f"encavalam no topo; o primeiro sai em {b['ini']:.2f}s.")
            a["fim"] = b["ini"]
    return saida, elementos


def montar_html(cfg: dict, marca: dict, grupos: list, elementos: list,
                w: int, h: int, fps: Fraction, total: float) -> str:
    if cfg["estilo"] not in marca["estilos"]:
        sys.exit(f"Estilo '{cfg['estilo']}' nao existe na marca. Opcoes: {', '.join(marca['estilos'])}")
    estilo, cores, fontes = marca["estilos"][cfg["estilo"]], marca["cores"], marca["fontes"]
    s = min(w, h) / 1080
    fmt = FORMATO[h > w]
    faces, vistos = [], set()
    for f in fontes.values():
        if f["arquivo"] in vistos:
            continue
        vistos.add(f["arquivo"])
        faces.append(f'      @font-face {{ font-family: "{f["familia"]}"; src: url("fontes/{f["arquivo"]}"); '
                     f'font-weight: {f.get("pesos", f["peso"])}; font-display: block; }}')
    variaveis = {**{k: v for k, v in cores.items()},
                 "texto": cores[estilo["texto"]], "ativa": cores[estilo["ativa"]], "enfase": cores[estilo["enfase"]],
                 "tamanho": f"{cfg['tamanho'] * s:.1f}px", "margem": f"{cfg['margem'] * h:.1f}px",
                 "largura-max": f"{w * 0.9:.0f}px", "topo": f"{fmt['topo'] * 100:.1f}%",
                 **{f"tamanho-{k}": f"{fmt[k] * s:.1f}px" for k in ("titulo", "pergunta", "passos", "cartaz", "chamada")}}
    dados = {
        "w": w, "h": h, "s": s, "tamanho": cfg["tamanho"], "animacao": cfg["animacao"], "tamanhoCartaz": fmt["cartaz"],
        "fontes": {k: {"familia": fo["familia"], "peso": fo["peso"]} for k, fo in fontes.items()},
        "cor": {"texto": variaveis["texto"], "ativa": variaveis["ativa"], "enfase": variaveis["enfase"]},
        "grupos": grupos, "elementos": elementos,
    }
    trocas = {
        "__W__": str(w), "__H__": str(h), "__MARCA__": marca["nome"],
        "__DURACAO__": f"{total:.3f}", "__FPS__": f"{float(fps):.3f}".rstrip("0").rstrip("."),
        "__FONTFACE__": "\n".join(faces),
        "__VARS__": "\n".join(f"        --{k}: {v};" for k, v in variaveis.items()),
        "__CORPO__": fontes["corpo"]["familia"], "__PESO_CORPO__": str(fontes["corpo"]["peso"]),
        "__ENFASE__": fontes["enfase"]["familia"], "__CARTAZ__": fontes["cartaz"]["familia"],
        "__CLASSE_ESTILO__": "com-placa" if estilo["placa"] else "sem-placa",
        "__DADOS__": json.dumps(dados, ensure_ascii=False).replace("</", "<\\/"),
    }
    html = (PASTA / "modelo.html").read_text(encoding="utf-8")
    for chave, valor in trocas.items():
        html = html.replace(chave, valor)
    return html


def raiz_plugin() -> Path | None:
    """Pasta da versao mais nova do plugin hyperframes instalado no Claude Code."""
    base = Path.home() / ".claude" / "plugins" / "cache" / "hyperframes" / "hyperframes"
    versoes = [d for d in base.glob("*") if (d / LANCADOR).is_file()]
    if not versoes:
        return None
    return max(versoes, key=lambda d: tuple(int(x) if x.isdigit() else 0 for x in d.name.split(".")))


def cli_hyperframes() -> list[str]:
    """CLI do plugin (versao fixada pelo plugin); senao, npx."""
    plugin = raiz_plugin()
    if plugin:
        return ["node", str(plugin / LANCADOR)]
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", "hyperframes"]
    sys.exit("HyperFrames nao encontrado (nem o plugin nem o npx). Instale o plugin hyperframes no Claude Code.")


def main() -> None:
    iniciar()
    ap = argparse.ArgumentParser(description="Legenda da marca em HyperFrames (camada transparente)")
    ap.add_argument("projeto")
    ap.add_argument("--rascunho", action="store_true", help="qualidade draft (mais rapido)")
    ap.add_argument("--so-gerar", action="store_true", help="so escreve o index.html, nao renderiza")
    ap.add_argument("--fps", help="mesmo --fps usado no render.py, se usou")
    args = ap.parse_args()

    pasta, edl = carregar_edl(args.projeto)
    cfg_edl = edl.get("legenda")
    if not isinstance(cfg_edl, dict) or cfg_edl.get("motor") != "hyperframes":
        sys.exit('No edl.json, use "legenda": {"motor": "hyperframes", "marca": "guidagaita", "estilo": "couro"}.')
    pasta_marca = PASTA / "marcas" / cfg_edl.get("marca", PADRAO["marca"])
    if not (pasta_marca / "marca.json").is_file():
        sys.exit(f"Marca '{cfg['marca']}' nao encontrada em {rel(PASTA / 'marcas')}.")
    marca = json.loads((pasta_marca / "marca.json").read_text(encoding="utf-8"))

    infos, palavras = carregar_fontes(edl)
    primeira = infos[edl["trechos"][0]["fonte"]]
    fps = fps_comum(Fraction(args.fps)) if args.fps else primeira["fps"]
    linha = montar_linha(edl, fps)
    total = linha[-1]["saida_fim"]
    ws = aplicar_correcoes(palavras_saida(linha, palavras), edl.get("correcoes"))
    if not ws:
        sys.exit("Nao ha palavras para legendar (transcricao vazia?).")
    w, h = tamanho_saida(primeira, 1920)
    padroes = {k: v for k, v in FORMATO[h > w].items() if k in ("tamanho", "margem", "max_palavras", "max_caracteres")}
    cfg = {**PADRAO, **padroes, **cfg_edl, "_vertical": h > w}

    grupos, elementos = montar_dados(ws, cfg, total)
    html = montar_html(cfg, marca, grupos, elementos, w, h, fps, total)
    destino = pasta / "legenda"
    (destino / "fontes").mkdir(parents=True, exist_ok=True)
    for f in {f["arquivo"] for f in marca["fontes"].values()}:
        shutil.copy2(pasta_marca / "fontes" / f, destino / "fontes" / f)
    (destino / "index.html").write_text(html, encoding="utf-8")
    print(f"{len(grupos)} legendas, {sum(p['enfase'] for g in grupos for p in g['palavras'])} palavras em enfase, "
          f"{len(elementos)} elementos ({', '.join(e["tipo"] for e in elementos) or 'nenhum'}) -> {rel(destino / 'index.html')}")
    if args.so_gerar:
        return

    qualidade = "draft" if args.rascunho else "looks"
    camada = destino / "legenda.webm"
    marca_render = hashlib.sha1((html + qualidade + str(fps)).encode()).hexdigest()
    arq_marca = destino / ".render"
    if camada.exists() and arq_marca.exists() and arq_marca.read_text() == marca_render:
        print(f"Camada sem mudancas (cache): {rel(camada)}")
    else:
        print(f"Renderizando a legenda no HyperFrames ({total:.1f}s, {w}x{h}, {qualidade})...", flush=True)
        t0 = time.time()
        cmd = [*cli_hyperframes(), "render", str(destino), "--format", "webm", "-o", str(camada),
               "--quality", qualidade, "--fps", str(fps), "--quiet"]
        if subprocess.run(cmd, cwd=RAIZ).returncode != 0 or not camada.exists():
            sys.exit("O render do HyperFrames falhou (veja acima).")
        arq_marca.write_text(marca_render)
        print(f"  pronto em {time.time() - t0:.0f}s")
    dur = sondar(camada)["duracao"]
    if abs(dur - total) > 0.15:
        print(f"AVISO: a camada tem {dur:.2f}s e o video cortado {total:.2f}s.")
    (destino / "gerado_com.json").write_text(
        json.dumps({"palavras": ws, "legenda": cfg_edl}, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Camada: {rel(camada)}. Agora: python .claude/skills/editar/render.py {pasta.name} --rascunho")


if __name__ == "__main__":
    main()
