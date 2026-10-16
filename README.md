# Estúdio de vídeo com Claude Code

Skills do [Claude Code](https://claude.com/claude-code) para editar vídeo falado (talking head) conversando:
você joga o bruto na pasta, o Claude transcreve, propõe os cortes, monta, legenda com a sua marca, confere o
resultado quadro a quadro e exporta. Feito para Reels e anúncios verticais (9:16), funciona em 16:9.

Tudo roda local, na CPU (sem GPU): transcrição com NVIDIA Parakeet v3, montagem com FFmpeg, legenda animada
com HyperFrames.

## As skills

| Skill | O que faz |
|---|---|
| `/calibrar` | **Comece por aqui.** Entrevista você ("grill me"), uma pergunta por vez, sobre o seu gosto de edição e sobre o vídeo da vez. Traduz "quero algo dinâmico" em números (pausa máxima, zoom, J-cut, tamanho da legenda…). Primeira vez: monta o perfil `gosto.md`. Vídeos seguintes: rodada curta em `trabalho/<projeto>/briefing.md`. |
| `/transcrever` | Transcreve vídeo/áudio com Parakeet v3 (português, local). Gera palavras com tempo, `.txt`, `.srt` e `transcript.json`. Com cache. |
| `/editar` | Prepara o projeto (`takes.md` com frases, pausas e sons sem palavra), recebe a lista de cortes (`edl.json`) e renderiza rascunho (720p) ou final (1080p) com J-cut, zoom dinâmico, transições, cor, trilha com ducking, legenda e loudness -14 LUFS. |
| `/conferir` | Revisa todo render antes de você ver: imagens de cada corte (quadros + forma de onda + palavras), duração, loudness, saltos de volume, quadros pretos, "piscas" e congelamentos. Também mostra qualquer trecho para decidir um corte. |
| `/legendar` | Legenda animada com a identidade da marca (cores e fontes em `marcas/<nome>/`), ênfases, "cartazes" e elementos de anúncio. Ou o modo cinema (palavra atrás da pessoa). |

Os padrões das skills são os do canal GuidaGaita (ensino de gaita, estética de faroeste): vertical 9:16, sem
pausa morta, J-cut em todo corte, zoom sutil, legenda calma no quinto de baixo da tela, área segura de anúncio
do Reels. O `/calibrar` grava o seu gosto e ele passa por cima desses padrões.

## Instalação

Requisitos (testado no Windows 11 com Python 3.13, FFmpeg 9 e Node 24):

1. **Claude Code** instalado e logado.
2. **Python 3.11+** com as bibliotecas:
   ```bash
   pip install onnx-asr[cpu,hub] numpy pillow
   ```
   Na primeira transcrição o modelo Parakeet (~650 MB) é baixado do Hugging Face.
3. **FFmpeg** (com `ffprobe`) no PATH.
4. **Node.js 20+** e o plugin **HyperFrames** no Claude Code (usado pelo `/legendar`; sem o plugin ele tenta
   `npx hyperframes`).
5. Opcional: MCP do **Epidemic Sound** para trilha e efeitos (baixar música exige assinatura ativa).

Clone o repositório e abra o Claude Code na pasta dele. As skills ficam em `.claude/skills/` e são carregadas
sozinhas.

```bash
git clone <url-deste-repo> estudio-video
cd estudio-video
mkdir brutos trabalho final transcricoes audio videos
claude
```

## Pastas

```
brutos/        material original - nunca é alterado nem apagado
transcricoes/  saída do /transcrever (cache, uma pasta por arquivo)
trabalho/      um projeto por pasta: takes.md, edl.json, briefing.md, projeto.md, rascunhos
final/         exportações finais
audio/         música e efeitos
videos/        projetos HyperFrames (modo cinema, animações)
gosto.md       seu perfil de gosto (criado pelo /calibrar)
```

Nenhuma dessas pastas vai para o git: o repositório guarda só as skills.

## Como usar

### Primeira vez

Coloque o bruto em `brutos/` e peça:

> Quero editar `brutos/meu-video.mp4` como anúncio para Reels. É a primeira vez, me entrevista antes.

O Claude prepara o projeto e roda o `/calibrar` completo (12 a 18 perguntas, uma por vez, cada uma com uma
recomendação). Pode responder "tanto faz": fica o padrão. No fim ele mostra uma tabela
`pergunta → resposta → ajuste` e grava `gosto.md`.

### Cada vídeo novo

> Edita `brutos/aula-07.mp4`, projeto `aula-07`.

1. **Preparar**: transcrição (com cache) + `trabalho/aula-07/takes.md`.
2. **Calibrar**: rodada curta, só deste vídeo (objetivo, duração alvo, gancho, mensagem, o que não pode sair,
   chamada final) + "algo mudou no seu gosto?".
3. **Plano de cortes**: estratégia + tabela `fonte início-fim | bloco | fala | motivo`. **Nada é renderizado
   antes de você aprovar.**
4. **Rascunho** em 720p → `/conferir` automático (até 3 rodadas de correção) → você assiste.
5. **Legenda** com a marca, ênfases e cartazes (também passam pela sua aprovação).
6. **Trilha** (opcional), **final** em `final/aula-07.mp4` e `/conferir` de novo. O Claude informa os números
   medidos (duração, LUFS, pico): ele não ouve o áudio.

Feedback do tipo "ficou lento" ou "legenda grande demais" vira ajuste no `edl.json` e, se valer para todo
vídeo, uma linha datada no `gosto.md`.

### Comandos diretos

Rode da raiz do repositório:

```bash
python .claude/skills/transcrever/transcrever.py brutos/video.mp4
python .claude/skills/editar/preparar.py <projeto> brutos/video.mp4 [brutos/take2.mp4 ...]
python .claude/skills/editar/render.py <projeto> --validar
python .claude/skills/editar/render.py <projeto> --rascunho
python .claude/skills/legendar/legendar.py <projeto> --rascunho
python .claude/skills/conferir/conferir.py <projeto>
python .claude/skills/conferir/linha_do_tempo.py <video> <inicio> <fim>
python .claude/skills/editar/render.py <projeto>          # final em final/<projeto>.mp4
```

O formato do `edl.json` está em `.claude/skills/editar/SKILL.md`; as opções de legenda em
`.claude/skills/legendar/SKILL.md`.

## Sua marca

Copie `.claude/skills/legendar/marcas/guidagaita/` para `marcas/<sua-marca>/`, troque cores, fontes e estilos
no `marca.json` e as fontes em `fontes/` (com a licença). O `/calibrar` pergunta pela marca na primeira vez e
pode montar isso com você.

## Créditos e licenças

- `/editar` adaptado de [browser-use/video-use](https://github.com/browser-use/video-use) (MIT, ver
  `.claude/skills/editar/LICENSE-video-use.txt`).
- Fontes Outfit, Alfa Slab One e Rye sob a SIL Open Font License (arquivos `OFL-*.txt` junto das fontes).
- Transcrição: [NVIDIA Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) via
  [onnx-asr](https://github.com/istupakov/onnx-asr).
