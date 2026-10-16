---
name: editar
description: Edita video falado a partir de uma lista de cortes (edl.json). Prepara o projeto (transcreve com cache, gera takes.md com frases, pausas e "~som" que o Parakeet omitiu), valida os cortes contra as palavras e renderiza rascunho ou final com fade de 30 ms em cada corte, cor, sobreposicoes, legenda queimada por ultimo, correcoes de nomes e loudness -14 LUFS. Use para cortar pausas, erros e takes repetidos, juntar varios takes, montar e legendar um video.
---

# Editar (lista de cortes -> video)

Adaptado de browser-use/video-use (MIT, ver `LICENSE-video-use.txt`). Rode tudo da raiz do projeto.
Saidas em `trabalho/<projeto>/` e o final em `final/<projeto>.mp4`. Nunca escreva em `brutos/`.

## Passo a passo

1. **Preparar**
   ```bash
   python .claude/skills/editar/preparar.py <projeto> brutos/<video> [brutos/<take2> ...]
   ```
   Cria `takes.md`, `edl.json` (fontes A, B, ...), `sons.json` e `projeto.md`. Transcricoes ja feitas saem do cache.
   Se `projeto.md` ja tem sessoes, resuma a ultima em uma frase e pergunte se continua dali.
2. **Ler `takes.md` inteiro.** Anote tropecos, frases repetidas (takes refeitos: fica o ultimo, ou o melhor),
   pausas longas e os `~som`. Na duvida, olhe o trecho:
   `python .claude/skills/conferir/linha_do_tempo.py <video> <inicio> <fim>` e leia o PNG.
3. **Conversar com `/calibrar`.** Descreva o material em poucas linhas e rode o `/calibrar`: sem `gosto.md`
   na raiz (primeira vez) faz a entrevista completa; com ele, so a rodada curta deste video (formato, duracao
   alvo, ritmo, o que nao pode sair, legenda, cor, animacoes). Saida em `gosto.md` e
   `trabalho/<projeto>/briefing.md`; os valores deles valem por cima dos padroes desta skill.
4. **Propor a estrategia** (4 a 8 frases) + tabela de cortes (`fonte inicio-fim | bloco | fala | motivo`).
   **Esperar aprovacao.**
5. **Escrever os trechos no `edl.json`** e validar:
   `python .claude/skills/editar/render.py <projeto> --validar` -> corrigir todos os AVISOS que nao forem intencionais.
6. **Rascunho:** `python .claude/skills/editar/render.py <projeto> --rascunho` (720p, segundos).
7. **Conferir** com `/conferir` antes de mostrar (ate 3 rodadas de correcao).
8. **Final:** `python .claude/skills/editar/render.py <projeto>` -> `final/<projeto>.mp4` (1080p, loudness 2 passadas).
   Se ainda vai entrar trilha: `--sem-loudnorm -o trabalho/<projeto>/montagem.mp4` e faca a loudness depois da mixagem.
   Conferir de novo.
9. **Anotar a sessao** em `projeto.md` (estrategia, decisoes e por que, pendencias).

Iteracao: o usuario pede ajuste -> edite o `edl.json` -> rascunho de novo. Trechos que nao mudaram saem do cache.
Nunca re-transcreva: o render escreve `trabalho/<projeto>/transcript.json` no tempo do video cortado (use no HyperFrames).

## Regras de corte (correcao, nao gosto)

- Corte sempre em fronteira de palavra, nunca no meio. O validador avisa.
- Margem em cada borda: 30-200 ms. Padrao: 50 ms antes da primeira palavra, 80 ms depois da ultima.
  Mais justo para ritmo rapido, mais folgado para tom calmo. O Parakeet tem resolucao de ~80 ms.
- Prefira cortar em silencios >= 0,4 s. Entre 0,15 e 0,4 s so com a linha do tempo na frente. < 0,15 s nao.
- Audio e video cortam juntos; nunca raciocine um sem o outro.
- Preserve os picos: risada, punchline, enfase. Estenda depois da piada para pegar a reacao.
- `~som` dentro de um trecho mantido costuma ser "e..."/"hum": corte, a nao ser que seja riso ou efeito intencional.
- **J-cut sempre** (padrao do render, `"j_cut": 0.13` no EDL; `"j"` por trecho; 0 desliga): em cada corte o som
  do trecho seguinte entra ~4 quadros antes da troca de imagem. So a imagem se desloca: o som segue a lista de
  cortes, entao nunca ha duas falas por cima uma da outra (nada embolado). Deixa a fala corrida mais dinamica.
- **Trocas de plano dentro do bruto** (o celular troca de zoom/lente, ou o arquivo ja vem emendado): o
  `takes.md` mostra `| troca de plano X`. Nao termine nem comece trecho a menos de 0,6 s de uma (aparece um
  "pisca" de outro plano). Se nao der, o render congela o ultimo quadro bom nesse pedaco (o zoom lento continua
  por cima) e o `--validar` avisa quanto tempo congela - mas **congelar mais de ~0,2 s parece travamento** (o Gui
  notou 0,35 s): mude o corte. Sem pausa entre as palavras, procure um silencio natural com
  `linha_do_tempo.py` + o volume a cada 10 ms: o fechamento da boca antes de "p", "t", "b" (ex.: "por tras|para"
  caiu a -47 dB). O J-cut nunca atravessa uma troca de plano (ali vira corte seco).
- **A boca mexe ~0,1 s antes do som.** Quando a fala seguinte do bruto nao entra na edicao, o trecho tem que
  acabar antes disso, senao aparece voce "comecando a falar" e so entao corta (o Gui pegou isso depois de
  "...o jeito de fazer embocadura"). Termine logo depois do fim da VOZ (medido no audio, nao no fim da palavra
  marcada), com a boca fechada; o `--validar` avisa e sugere o ponto, e o J-cut nao estica a imagem por cima
  dessa preparacao. Excecao: palavra seguinte com p/b/m comeca de labios fechados (parece fim de frase).
- O `--validar` mede o **silencio real no audio** no comeco e no fim de cada trecho (o Parakeet as vezes marca a
  palavra ate 0,3 s antes da voz) e sugere o ponto certo. Aplique as sugestoes; corte de anuncio nao tem pausa morta.
- O render ja faz: fade de 30 ms em cada borda, legenda por ultimo, quadros inteiros, HDR -> SDR, loudness.

## Estruturas comuns (escolha, adapte ou invente)

- Tutorial: INTRO -> PREPARO -> PASSOS -> PEGADINHAS -> RESUMO
- Lancamento/demo: GANCHO -> PROBLEMA -> SOLUCAO -> BENEFICIO -> EXEMPLO -> CHAMADA
- Entrevista: (PERGUNTA -> RESPOSTA -> SEGUIMENTO) repetido
- Varios takes: escolha o melhor take de cada bloco e monte na ordem dos blocos, nao na ordem dos arquivos.
  Passou da duracao alvo: corte um bloco inteiro ou apare as pontas, e confira o total de novo.

## edl.json

```json
{
  "versao": 1,
  "projeto": "aula-01",
  "fontes": {"A": {"video": "brutos/aula.mp4", "transcricao": "transcricoes/aula/transcript.json"}},
  "trechos": [
    {"fonte": "A", "inicio": 2.42, "fim": 6.85, "bloco": "GANCHO", "fala": "...", "motivo": "take mais limpo"}
  ],
  "cor": "nenhuma",
  "legenda": {"estilo": "frase"},
  "correcoes": {"clawed code": "Claude Code"},
  "sobreposicoes": [{"arquivo": "videos/x/render.webm", "inicio": 12.0}]
}
```

- Tempos de `trechos` em segundos **da fonte**. `bloco`, `fala` e `motivo` sao para leitura (aparecem no plano).
- Por trecho, opcionais: `zoom` (numero fixo, ou `[inicio, fim]` que muda devagar ao longo do trecho; recorte do
  bruto, sem perda com fonte 4K -> saida 1080x1920), `foco` ([x, y] em fracao do quadro, centro do recorte;
  padrao [0.5, 0.42]), `ganho_db`, `cor` (preset ou filtro so neste trecho), `voz` (efeito so neste trecho),
  `j` (J-cut na entrada deste trecho), `legenda: false` (sem fala de verdade, ex.: gaita tocando - o Parakeet
  inventa palavras em som de gaita).
- Estilo da casa para video falado (aprovado pelo Gui): zoom **dinamico e sutil** em todo trecho, alternando
  entrar/sair devagar (`[1.14, 1.18]` e `[1.24, 1.20]`), o que da ~6% de diferenca no corte; `foco` [0.5, 0.56]
  ancora o recorte mais baixo e corta o teto branco do estudio. Confira nas imagens se a cabeca nao encosta nos
  elementos do topo.
- **Transicoes** (`"transicao"` no trecho que ENTRA): `"dissolver"` (fusao, padrao 0,3 s) ou `"escurecer"`
  (passa por escuro), ou `{"tipo": "dissolver", "duracao": 0.4}`. So imagem: o som continua com os cortes.
  Estilo do Gui: **sutis e so em algumas passagens de cena** (entrar/sair de um personagem, virada do roteiro,
  entrada do fechamento); nada de flash, zoom, whip ou brilho. Os outros cortes ficam secos com J-cut e zoom.
  Nesse corte o J-cut e desligado (a fusao ja suaviza) e as pontas ganham uns quadros parados escondidos pela fusao.
- Cena de outro personagem (ex.: ele interpretando a pergunta do aluno): `"cor": "personagem"` (fria,
  dessaturada, com vinheta) e `"voz": "personagem"` (~1 semitom acima + banda leve: outra pessoa sem
  caricatura; o Gui achou 2,5 semitons exagerado). Outras vozes: `personagem_forte`, `grave`, `telefone`.
- `trilha` (opcional): `{"arquivo": "audio/x.mp3", "relativo_db": -18, "ate_trecho": 13, "fade": 1.5,
  "inicio_na_musica": 0}`. A musica fica `relativo_db` abaixo da voz (medido), abaixa sozinha quando ha fala
  (ducking) e sai em fade ate o inicio do trecho `ate_trecho` (ou `ate` em segundos). A loudness vem depois da mixagem.
  `--sem-trilha` ignora. So use musica com licenca (Epidemic Sound so baixa com assinatura ativa).
- `cor`: `nenhuma` (padrao), `sutil`, `neutra` (contraste leve), `cinema_quente` (so se pedirem um look), ou um filtro
  FFmpeg cru. Antes de algo forte, confira tons de pele num quadro.
- `legenda`: `null` (sem), `{"estilo": "frase"}` (frases de ate 2 linhas; YouTube, aula) ou `{"estilo": "reels"}`
  (2-3 palavras em MAIUSCULAS; Reels/Shorts/TikTok). Opcionais: `tamanho`, `margem`, `fonte`, ou `arquivo` com um .srt pronto.
  Em 9:16 a margem padrao poe a legenda a ~30% da base, acima da interface dos apps.
- `correcoes`: troca erros de transcricao (nomes, marcas, termos) na legenda e no `transcript.json`.
  Ignora maiusculas e pontuacao. Revise os nomes proprios no `takes.md` e preencha antes de legendar.
- `sobreposicoes`: tempos **do video de saida**. `inicio`, `duracao` (opcional para video; obrigatorio para imagem),
  `tela_cheia` (padrao true: escala para o quadro inteiro, o normal para render transparente do HyperFrames),
  `x`/`y` (expressoes do FFmpeg, padrao centralizado). `.webm` com alfa e `.mov` ProRes 4444 mantem a transparencia.
  Sincronia: comece a animacao `duracao_da_entrada` segundos antes da palavra-chave (veja o tempo no `transcript.json`
  do projeto) para ela pousar na hora em que a palavra e dita.

## Opcoes do render.py

`--rascunho` 720p ultrafast · `--validar` so confere · `-o <saida>` · `--sem-legenda` · `--sem-loudnorm` ·
`--fps 30` (padrao: o da primeira fonte; taxas tortas de celular sao arredondadas) · `--resolucao-original`
(o final limita a 1080p, sem nunca aumentar).

## Arquivos do projeto

| Arquivo | O que e |
|---|---|
| `takes.md` | Leitura principal: frases com `[inicio-fim]`, `~som`. |
| `edl.json` | As decisoes de corte. |
| `projeto.md` | Memoria entre sessoes. |
| `transcript.json` | Palavras no tempo do video cortado (HyperFrames, animacoes sincronizadas). |
| `legenda.srt` | Legenda gerada no ultimo render. |
| `renders.json` | Linha do tempo de cada render (usada pelo /conferir). |
| `trechos_*/`, `base_*.mkv` | Cache e intermediarios; pode apagar. |
