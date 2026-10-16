---
name: legendar
description: Legenda animada com a identidade da marca (GuidaGaita - cores e fontes do guidagaita.com) feita em HyperFrames e aplicada por cima do corte do /editar - palavra falada acende, palavras de enfase em outra fonte, "cartazes" de faroeste nos momentos fortes. Tambem o modo cinema (HyperFrames embedded-captions, palavra principal atras da pessoa) para Reels curtos. Use para legendar, legenda animada, legenda estilo Reels/TikTok, legenda da marca, legenda atras da pessoa.
---

# Legendar (HyperFrames)

Dois modos. Rode da raiz do projeto, depois de ter o `edl.json` do `/editar` com os trechos aprovados.

| Modo | Quando | Custo na CPU |
|---|---|---|
| **marca** (padrao) | Todo video. Cores e fontes da marca, qualquer enquadramento, varios takes. | ~3x a duracao em rascunho (60 s de video ~ 3 min) |
| **cinema** | Reel curto, um falante, plano continuo, pedido de algo "cinematografico". | Recorte da pessoa ~2-3 min a cada 10 s + render |

Formato do canal: **so celular, vertical 9:16** (anuncios e Reels). O formato sai do video: em vertical os padroes
sao `tamanho` 72, ate 5 palavras / 26 caracteres por legenda, legenda no quinto de baixo da tela, logo abaixo da
linha de 1/5 (`margem` 0.14: base a 14% da altura, abaixo das maos e da gaita - pedido do Gui; mais alto ela
cobria a gaita) e
os elementos na faixa livre do topo (a partir de 12%), respeitando a area segura de anuncio do Reels
(14% de cima e ~30% de baixo ficam livres para a interface, o texto do anuncio e o botao "Saiba mais").
Em horizontal: `tamanho` 92, 4 palavras / 24 caracteres, `margem` 0.074.
`animacao`: `suave` (padrao, pedido do Gui: a frase so aparece com fade curto e a palavra falada muda de cor,
sem pular nem crescer) ou `viva` (sobe e cresce; mais energia, mais distracao).

## Modo marca

1. No `edl.json`:
   ```json
   "legenda": {
     "motor": "hyperframes",
     "marca": "guidagaita",
     "estilo": "couro",
     "enfase": ["primeira musica", "gaita"],
     "cartazes": [{"texto": "DO ZERO", "palavra": "zero", "posicao": "direita"}]
   }
   ```
   Opcionais: `tamanho` (px num quadro de lado menor 1080), `margem` (fracao da altura, da base ate a legenda),
   `max_palavras`, `max_caracteres` (padroes acima, por formato), `caixa_alta` false, `pontuacao` false.

   Elementos de anuncio (faixa do topo; um por vez - se encavalarem, o anterior sai quando o proximo entra):
   ```json
   "titulo":   {"texto": "A PERGUNTA QUE MAIS ME FAZEM", "em": 0, "ate_palavra": "tambem"},
   "pergunta": {"texto": "Como tocar uma casa so na gaita?", "palavra": "Gui", "ate_palavra": "errado",
                "cabecalho": "PERGUNTA DE UM SEGUIDOR"},
   "passos":   {"itens": [{"texto": "SOPRO", "palavra": "sopro"}, {"texto": "EMBOCADURA", "palavra": "depois a embocadura"}],
                "ate_palavra": "musicas"},
   "chamada":  {"texto": "TOQUE EM SAIBA MAIS", "palavra": "clica"}
   ```
   Tempo de cada um: `em` (segundos do video cortado) ou `palavra` (entra quando e dita; expressao de varias palavras
   desambigua; `depois_de` pula ocorrencias) + `ate`, `ate_palavra` ou `duracao`. A `chamada` vai ate o fim por padrao,
   com seta quicando para o botao. **A `pergunta` so com pergunta real de seguidor** (anuncio nao inventa depoimento);
   sem print, ela vira cartao da marca, sem imitar a tela do Instagram. Cartaz em vertical vai no `topo` por padrao.
2. `python .claude/skills/legendar/legendar.py <projeto> --rascunho` -> `trabalho/<projeto>/legenda/legenda.webm`
   (camada transparente; `--so-gerar` so escreve o `index.html`).
3. `python .claude/skills/editar/render.py <projeto> --rascunho` -> aplica a camada por ultimo, por cima de tudo.
4. `/conferir`: nas imagens, legenda inteira, legivel e no tempo da fala; cartaz sem cobrir o rosto.
5. Final: `legendar.py <projeto>` (sem `--rascunho`) e depois `render.py <projeto>`.

Mudou corte, correcao ou estilo? Rode o `legendar.py` de novo: o `render.py` avisa quando a camada esta velha.
Correcoes de nomes (`correcoes` no `edl.json`) valem para a legenda.

### Estilos da marca (`marcas/guidagaita/marca.json`)

- `couro` (padrao): placa de couro escuro com borda de brasa, como os cartoes do site; texto osso; palavra falada
  em ouro claro; enfase em Alfa Slab One cor de brasa.
- `limpo`: sem placa, texto osso com sombra forte. Para fundo escuro ou quando a placa pesa.

### Enfase e cartazes (decisao editorial: proponha no plano e espere aprovacao)

- `enfase`: palavras ou expressoes que carregam a ideia (nome do metodo, numero, beneficio). Todas as ocorrencias.
  Pouca: ~1 a cada 2-3 legendas. Se tudo e enfase, nada e.
- `cartazes`: o momento mais forte, em letra de faroeste (Rye) com estrela e regua. **No maximo 1 a cada ~20-30 s.**
  `palavra` ancora na fala (pousa quando a palavra e dita; `depois_de` em segundos para pular ocorrencias)
  ou `em` (segundos do video cortado). `duracao` padrao 1,6 s. `posicao`: `esquerda`, `direita` ou `centro` -
  **olhe um quadro** (`linha_do_tempo.py`) e ponha do lado oposto ao rosto.

### Outra marca

Copie `marcas/guidagaita/` para `marcas/<nome>/`, troque `marca.json` (cores, fontes, estilos) e as fontes em
`fontes/` (com a licenca). Fonte nao carregada faz o render falhar de proposito (nao cai em Arial calada).

## Modo cinema (embedded-captions)

Palavra principal composta ATRAS da pessoa (recorte), trilho de legenda na frente. Os estilos sao os 35 do catalogo
do HyperFrames e **nao aceitam cores nem tamanhos proprios** (a pasta do plugin e somente leitura): para a GuidaGaita,
os mais proximos sao `anchor` (creme/caramelo, discreto), `cream` (creme quente) e `stomp`/`loud` para algo mais forte.
Atencao: o trilho do `anchor` e pequeno (46 px em 1080p) para video horizontal visto no celular. Use o modo cinema
quando a palavra atras da pessoa for o ponto do video; para o dia a dia, o modo marca le melhor.

Recuse ou divida se: mais de um falante, cortes de camera no trecho (o recorte e por plano), legenda ja queimada
no video, rosto pouco visivel.

1. `python .claude/skills/legendar/cinema.py <projeto>` (padrao `--idioma pt`): corte sem legenda -> projeto
   `videos/<projeto>-cinema/` com a nossa transcricao (sem WhisperX) -> dependencias -> recorte + zonas seguras.
   Rode em segundo plano.
2. Siga o `SKILL.md` do embedded-captions do plugin (o caminho aparece no fim do passo 1) a partir do passo 3:
   proponha 2-3 identidades do catalogo e deixe o usuario escolher, escreva `theme.json`/`cinematic.json`,
   `preview-frames.cjs`, mostre as previas, renderize.
3. `python .claude/skills/legendar/cinema.py <projeto> --finalizar` -> loudness -14 LUFS -> `final/<projeto>.mp4`.
4. `/conferir` no final.

Comandos (L = `<plugin>/skills/hyperframes/scripts/plugin-cli.mjs`, S = `<plugin>/skills/embedded-captions/scripts`,
P = caminho absoluto de `videos/<projeto>-cinema`; o `cinema.py` imprime os caminhos):
- compilar o tema: `node L --script S/make-theme.cjs P`
- previas (~2 s por quadro, sem render): `node L --script S/preview-frames.cjs P 1.5 5.6 8.0` -> `P/preview/sheet.png`
- render (modo Theme): `bash S/render-theme.sh P` -> `P/final_fx.mp4` (rode em segundo plano)
No `theme.json`, ponha `"width": 1920, "height": 1080` (o padrao do tema e 1280x720) e copie as palavras exatamente
como estao no `transcript.json` do projeto, com a pontuacao.
