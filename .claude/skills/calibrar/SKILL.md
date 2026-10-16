---
name: calibrar
description: Entrevista o usuario ("grill me") para calibrar o gosto de edicao antes de cada video novo - uma pergunta por vez, com recomendacao, desafiando respostas vagas e traduzindo cada resposta em um valor concreto do edl.json (ritmo, J-cut, zoom, cor, transicoes, legenda, trilha, chamada). Na primeira vez monta o perfil de gosto (gosto.md); nos videos seguintes faz uma rodada curta so do que muda (briefing.md do projeto). Use no comeco de todo video novo, quando for a primeira vez usando as skills, quando o usuario disser "me entrevista", "grill me", "calibra", "meu estilo", ou quando um feedback mostrar que o gosto mudou.
---

# Calibrar (grill me do gosto de edicao)

As outras skills vem com os padroes do canal GuidaGaita (vertical 9:16, J-cut, zoom sutil, legenda calma no
quinto de baixo). Esta skill descobre o gosto de QUEM esta editando e de CADA video, e grava em arquivo para
nao perguntar de novo. Rode da raiz do projeto.

| Arquivo | O que guarda | Quando muda |
|---|---|---|
| `gosto.md` (raiz) | Perfil fixo: canal, publico, formato, ritmo, imagem, legenda, som, regras, o que odeia | Primeira vez; depois so quando um feedback contradiz |
| `trabalho/<projeto>/briefing.md` | Este video: objetivo, duracao, gancho, mensagem, chamada, excecoes ao perfil | Todo video novo |

`gosto.md` e pessoal (fica fora do git). Se nao existe, os padroes das skills valem ate a calibragem.

## Qual rodada fazer

- **Sem `gosto.md`** -> rodada completa (perfil + este video). Avise: "sao umas 12-18 perguntas, uma por vez;
  pode responder 'tanto faz' que eu uso o padrao".
- **Com `gosto.md`** -> rodada curta (3-6 perguntas so deste video) + uma pergunta final: "algo no seu gosto
  mudou desde o ultimo video?". Resuma o perfil em 2 linhas antes de comecar.
- **Depois de um rascunho** -> rodada de ajuste: pergunte sobre o que ele viu (ritmo, legenda, zoom), nao sobre
  teoria. Feedback que vale para todo video vai para `gosto.md` com data; o que vale so para este, no briefing.

## Como entrevistar

1. **Leia antes de perguntar.** `gosto.md`, `trabalho/<projeto>/takes.md` e `projeto.md` (se o `/editar` ja
   preparou), `ffprobe` do bruto (formato, duracao, fps), `marcas/` do `/legendar`. Nao pergunte o que o
   material responde: afirme e peca confirmacao ("o bruto e vertical 4K, entao saida 9:16 - certo?").
2. **Uma pergunta por vez**, de preferencia com `AskUserQuestion` (2-4 opcoes concretas). A primeira opcao e a
   sua recomendacao, com o porque em uma frase. Siga a ordem da arvore abaixo: decisoes de cima mudam as de baixo.
3. **Traduza adjetivo em numero e confirme.** "Dinamico", "clean", "profissional", "estilo Reels" nao servem
   para renderizar. Ex.: "dinamico = pausa maxima 0,4 s, J-cut 0,13 s e zoom [1.14, 1.20] em todo trecho. E isso?"
4. **Desafie contradicoes e riscos**, com o motivo: legenda grande + anuncio de Reels (bate na area segura);
   anuncio de 60 s (passa do que segura atencao); "corta tudo" + aula (perde a respiracao); musica alta + fala
   rapida; depoimento inventado em anuncio (proibido). Nao aceite a primeira resposta se ela quebra outra.
5. **Mostre quando a pergunta e visual.** Para enquadramento, zoom e lado do cartaz, gere um quadro do bruto
   (`python .claude/skills/conferir/linha_do_tempo.py <video> <inicio> <fim> --quadros 4`) e pergunte em cima
   dele. Para tamanho e posicao da legenda, o melhor e calibrar no primeiro rascunho.
6. **"Tanto faz" e resposta valida**: anote "padrao" e siga. Nao insista mais de uma vez na mesma pergunta.
7. **Pare quando der para escrever o `edl.json` sem chutar.** O objetivo e decidir, nao esgotar a lista.

## Arvore de perguntas

### Perfil (so na rodada completa)

1. **Canal e publico**: sobre o que e, quem assiste, tom (professor proximo, energico, tecnico). -> `gosto.md`
2. **Onde sai**: Reels/TikTok/Shorts, anuncio pago, YouTube horizontal, varios. -> formato 9:16 ou 16:9;
   anuncio pago liga a area segura (nada importante nos 14% de cima nem nos ~30% de baixo).
3. **Marca**: tem cores e fontes? Se e outra marca que nao a GuidaGaita, ofereca criar `marcas/<nome>/` no
   `/legendar` (pegar cores do site com o usuario). -> `"marca"` da legenda.
4. **Ritmo**: enxuto (anuncio) ou respirado (aula). -> pausa maxima mantida (padrao 0,7 s; enxuto 0,3-0,4 s),
   margens de corte (padrao 50/80 ms), cortar vicios ("ne", "tipo", "e...") sempre / so quando atrapalham.
5. **J-cut**: som do proximo trecho entrando antes da imagem. -> `"j_cut"` (padrao 0.13; 0 desliga).
6. **Zoom**: parado, sutil dinamico (padrao), marcado. -> `zoom` por trecho (`[1.14, 1.18]` alternando com
   `[1.24, 1.20]`; marcado ~`[1.10, 1.30]`), `foco` (padrao [0.5, 0.56] corta o teto).
7. **Cor**: `nenhuma`, `sutil`, `neutra`, `cinema_quente`. Mostre um quadro antes de algo forte.
8. **Transicoes**: nenhuma (so corte seco), dissolver so em viradas de cena (padrao), mais frequentes.
   -> `"transicao"` nos trechos de virada. Flash/whip/zoom brusco: so se pedirem, avise que destoa.
9. **Legenda - motor**: marca animada (`/legendar`, padrao), simples (`"estilo": "frase"` ou `"reels"`),
   cinema (palavra atras da pessoa), nenhuma.
10. **Legenda - visual** (se marca): `animacao` `suave` (so a palavra falada muda de cor) ou `viva`;
    `estilo` `couro` ou `limpo`; `tamanho` (padrao 72 em vertical); `max_palavras` (padrao 5); posicao
    `margem` (padrao 0.14 = quinto de baixo); caixa alta sim/nao.
11. **Enfase e cartazes**: quantos. -> `enfase` (~1 a cada 2-3 legendas), `cartazes` (no maximo 1 a cada 20-30 s
    ou nenhum).
12. **Som**: trilha sim/nao, estilo, volume. -> `trilha.relativo_db` (padrao -18; -12 a -15 mais presente),
    efeitos so ligados a algo visivel ou nenhum.
13. **Regras de conteudo**: chamada padrao (ex.: "toque em Saiba mais"), o que nunca falar (preco), nada de
    depoimento inventado, personagem (`"cor"`/`"voz": "personagem"`) quando ele interpreta outra pessoa.
14. **O que odeia**: peca 1-3 exemplos de edicao que irrita (pulo de legenda, emoji, zoom brusco, musica alta).
    E a lista mais util do perfil: grave literal.

### Este video (toda rodada)

1. **Objetivo**: anuncio (leva para onde?), aula, reel organico, outro. Muda ritmo e chamada.
2. **Duracao alvo** (anuncio: 15-45 s; reel: 20-60 s; aula: o que o conteudo pedir).
3. **Mensagem unica**: se a pessoa lembrar de uma frase, qual? Vira candidata a cartaz/enfase.
4. **Gancho**: o que entra nos primeiros 3 s. Proponha 1-2 frases do `takes.md`.
5. **O que nao pode sair** e o que pode sair sem do.
6. **Chamada final** e para onde leva.
7. **Excecoes ao perfil** neste video (outra musica, sem legenda, cena de personagem...).

## Ao terminar

1. Mostre uma tabela `pergunta -> resposta -> ajuste no edl.json` e peca ok.
2. Grave `gosto.md` (rodada completa) e `trabalho/<projeto>/briefing.md` (sempre) com o formato abaixo.
3. Siga para o plano de cortes do `/editar` (passo 4), usando o briefing. O plano cita o briefing quando uma
   decisao vier dele ("cortei X porque o alvo e 30 s").

```markdown
# Gosto de edicao - <nome>          (ou: # Briefing - <projeto>)
Atualizado: AAAA-MM-DD

## <secao da arvore>
- <decisao em palavras> -> `<chave do edl.json>`: <valor>   (por que, se veio de feedback)

## Odeia
- ...

## Historico
- AAAA-MM-DD: <o que mudou e por que> (ex.: "pausa 0,7 -> 0,4 s: achou o anuncio-02 lento")
```
