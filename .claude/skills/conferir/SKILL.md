---
name: conferir
description: Confere um video renderizado pelo /editar antes de mostrar ao usuario - imagens de cada corte (quadros + forma de onda + palavras), duracao, sincronia, loudness, salto de volume entre trechos, som forte no ponto de corte e quadros pretos. Tambem gera a linha do tempo de qualquer trecho de video para decidir cortes. Use depois de todo render e quando precisar "ver" um trecho.
---

# Conferir (autoconferencia antes de mostrar)

Rode da raiz do projeto, depois de cada render do `/editar`:

```bash
python .claude/skills/conferir/conferir.py <projeto> [--video <arquivo>] [--cortes 2,5] [--sem-imagens]
```

Sem `--video`, confere o render mais recente. Saida em `trabalho/<projeto>/conferir/`: `relatorio.md`,
`corte_NN.png` (1,5 s antes e depois de cada corte), `inicio.png` e `fim.png`.

## Teste das bordas (sempre)

Como testar o limite de um `if`: os erros de edicao moram nas pontas de cada trecho. O relatorio testa sozinho:
- **Toda troca de imagem tem que cair num corte planejado** (descontado o J-cut). Troca fora do corte = plano
  da fonte (zoom do celular, emenda) dentro do trecho.
- **Nenhum plano com menos de 0,7 s** ("pisca"): pedaco de outro plano colado na ponta de um trecho ou imagem
  esticada do J-cut caindo em outro plano. Foi o caso do anuncio-01: 13 quadros de um close no fim de um trecho.
- **Pontas do video**: o primeiro e o ultimo 20 ms nao podem ter som forte (soa cortado).
- **Imagem parada**: rosto sem mexer por mais de 0,2 s (medido na regiao do rosto: o zoom lento disfarca o
  congelamento na imagem inteira) e congelamentos que o proprio render criou. No celular parece que travou.
- **Taxa de quadros igual a da fonte**: converter 30 -> 29,97 descarta quadros de tempos em tempos (tranco no
  movimento). Foi um bug real do render (arredondava 30 para 29,97).
- **Cortes medidos na voz** (sem a trilha): som forte no corte = corte em cima de fala ou estalo.
Ao corrigir um problema que o teste nao pegou, primeiro faca o teste reprovar o video ruim, depois corrija e
veja passar (assim o teste fica valendo para os proximos videos).

## O que fazer

1. Leia o relatorio. Os numeros sao medidos: duracao contra o EDL, video e audio do mesmo tamanho, loudness
   (alvo -14 LUFS, true peak <= -1), nivel de fala por trecho, som forte no ponto de corte, quadros pretos.
2. Abra os PNGs com Read. Em cada corte procure:
   - salto estranho de imagem ou flash (os quadros com borda laranja sao os de depois do corte);
   - palavra comida: a onda some no meio de uma palavra ou a primeira silaba nao aparece;
   - pausa longa ou curta demais para o ritmo combinado;
   - legenda legivel, inteira e por cima de tudo; sobreposicao no lugar e no tempo certos.
   Tambem confira `inicio.png` e `fim.png` (comeca e termina limpo?).
3. Achou problema: corrija o `edl.json` -> renderize -> confira de novo. **No maximo 3 rodadas**; se ainda sobrar
   algo, mostre ao usuario dizendo o que ficou.
4. So mostre o video depois que passar. Diga os numeros (duracao, LUFS, pico). Voce nao consegue ouvir:
   nao diga que "soa bem", diga o que mediu.
5. Para algo que vai ser publicado (lancamento, anuncio), ofereca uma revisao critica: veredito, problemas com
   minutagem e evidencia (quadros, niveis) e as 5 correcoes mais importantes.

## Linha do tempo de qualquer trecho

```bash
python .claude/skills/conferir/linha_do_tempo.py <video> <inicio> <fim> [--marcas 12.3,15] [--quadros 8] [-o x.png]
```

Quadros, forma de onda em dBFS, palavras e silencios (faixas azuis). Use nos pontos de decisao: `~som` do
`takes.md`, pausa ambigua, comparar dois takes, conferir uma margem. Nao use para varrer o video inteiro.
Sem `-o`, salva em `trabalho/linha_do_tempo/`.
