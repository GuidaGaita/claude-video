---
name: transcrever
description: Transcreve video ou audio localmente com NVIDIA Parakeet v3 (roda na CPU, aceita portugues) e gera transcript.json com tempo por palavra, frases, .txt com tempos e legenda .srt. Use sempre que precisar transcrever, legendar, achar trechos para cortar, remover silencios/erros ou preparar legendas para o HyperFrames.
---

# Transcrever (Parakeet v3)

Rode a partir da raiz do projeto:

```bash
python .claude/skills/transcrever/transcrever.py "<arquivo>" [-o <pasta>] [--qualidade alta]
```

- Padrao de saida: `transcricoes/<nome-do-arquivo>/`
- Se ja existe transcricao mais nova que o arquivo, sai do cache na hora. `--forcar` refaz.
- `--qualidade alta` usa o modelo completo (mais lento e ~2.5 GB de RAM). Use so se a versao rapida errar muito (com `--forcar`).
- Para editar, prefira `/editar` (o `preparar.py` chama esta skill e monta o `takes.md`).
- O modelo (~650 MB) ja esta no cache do Hugging Face; ~5 s so para carregar. Para videos longos, rode em background.
- Os tempos das palavras sao acertados pelo volume do audio: o Parakeet marca o inicio ate 0,3 s antes da voz
  (as vezes a palavra inteira cai no silencio) e a pontuacao sai atrasada. O script move o inicio ate o som,
  so encerra a palavra depois de >= 0,15 s de silencio (oclusiva e "s" fraco no meio da palavra nao contam) e,
  na duvida, deixa a palavra longa, nunca cortada. Mudou a regra? Retranscreva com `--forcar`.
- O HyperFrames tem o proprio Parakeet instalado (`npx hyperframes transcribe <arquivo>`), usado pelos fluxos dele. Esta skill e para editar: as pausas ficam visiveis (o HyperFrames estica o fim da palavra ate a proxima) e gera .txt/.srt.

## Arquivos gerados

| Arquivo | Para que serve |
|---|---|
| `transcript.txt` | Leia este primeiro. Uma frase por linha com `[inicio -> fim]`. Lacunas entre linhas sao pausas. |
| `segments.json` | Mesmas frases em JSON, para calcular cortes. |
| `transcript.json` | Palavras `[{text, start, end}]` em segundos. Formato "normalized word array" do HyperFrames: copie para o projeto HyperFrames como `transcript.json` ou passe para `npx hyperframes transcribe <arquivo>` em vez de usar Whisper. |
| `transcript.srt` | Legenda pronta: quebra em fim de frase/pausa, ate 2 linhas de ~32 caracteres. |

## Depois de transcrever

1. Leia `transcript.txt` e confira a qualidade (palavras sem sentido = audio ruim; tente `--qualidade alta --forcar`).
2. Para cortes: use `/editar` (lista de cortes em `edl.json` + `render.py`). Nao transcreva o video cortado: o `render.py` ja gera o `transcript.json` no tempo novo.
3. Sempre mostre ao usuario a lista de cortes proposta antes de renderizar.
4. O Parakeet costuma omitir "e...", "hum" e risos: uma lacuna sem palavras pode ter som. O `takes.md` do `/editar` marca isso como `~som`.
