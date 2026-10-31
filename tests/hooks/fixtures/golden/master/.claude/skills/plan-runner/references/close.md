# Fechar — conferir e mudar o estado

O plano fecha, não se apaga: fica em `plans/` com `status: done` e `phase: closed`, citável por `PLN-NNN/R2.1`. O que
torna o fechamento confiável é a conferência. O plano não substitui a decisão — a escolha que só existe dentro de um
plano fechado não é achada por quem a procura em `work/decisions/` —, e o que ele prometeu precisa estar na casa dona,
não descrito no Desenho.

## A conferência

Uma rodada só, com os itens do `## Fechamento` do molde, mais o resumo (item 5) e o fecho da Janela (item 6). **Qualquer
um que falte trava o fechamento**, e a resposta é dizer o que falta e onde:

1. **Todo passo `[x]` com evidência, ou `[-]` com motivo.** Passo marcado sem evidência não fecha.
2. **Todo achado com destino que existe.** `a definir` trava. A issue, a decisão, o plano novo — cada um escrito, e não
   apenas nomeado.
3. **O durável saiu para a casa dona** e está em `## Decisões` e `## Documentação`, com o link. Leia o plano procurando
   o que precisa sobreviver a ele:
   - rodada que decide algo além do plano → a nota de `work/decisions/`, `done`, com "decidido em `PLN-NNN/R2.1`" e o
     resumo, gravada com a confirmação de Anderson, que a norma pede para a nota;
   - regra que o código do `brza-studio` precisa cumprir depois → a norma dele (`AGENTS.md`), por uma sessão aberta lá;
   - o que Anderson precisa lembrar de como usar uma ferramenta → um guia em `wiki/guides/`;
   - pendência que ficou para depois → uma issue em `work/issues/`;
   - achado falso → `→ falso` no parecer, com o `**Por que é falso:**` e a evidência; o parecer é a casa dele.
4. **As decisões que o plano respondeu estão `done`**, citando a rodada.
5. **O resumo está redigido** — o pedido, o que foi entregue, os desvios, os achados por destino, o que ficou fora —, um
   em cada commit ou pull request do `brza-studio` que a entrega abriu, com o link do plano. Trabalho só em notas de
   `work/` e `plans/`, que commita sem pull request, não tem resumo a publicar: o plano fechado é o registro. Você
   redige; Anderson publica, com confirmação.
6. **Com Janela, uma linha fecha a seção** — a meta foi cumprida ou não, e a diferença entre `estimate_h` e `executed_h`
   —, inclusive quando a meta não foi cumprida.

## Mudar o estado

Com a conferência limpa, grave num commit só, pela seção "Quem grava" da `SKILL.md`: os itens do `## Fechamento`
marcados, `status: done`, `phase: closed` e `completed` no frontmatter, o `Retomar` reescrito e a linha do
`## Worklogs`. Nada sai de `plans/`.

- **A linha "Último PLN usado" fica como está**: o número não é reusado.
- **`PLN` parado no Desenho** fecha de três jeitos: virou decisão ou norma — foi escrita, e o plano vai a `done` citando
  o que nasceu dele —; virou passos — o plano continua, com `phase: execution` —; ou foi rejeitado — `status: dropped` e
  `phase: dropped`, com a linha do porquê em `## Worklogs`.
- **Plano abandonado** vai a `status: dropped` e `phase: dropped`, com a linha do porquê em `## Worklogs`.
- **Plano de worklog** não tem `status`, porque o worklog não tem: fecha com o registro da execução completo abaixo do
  plano aceito e a mesma conferência. Plano aceito e nunca executado ganha a linha que diz que não foi executado e por
  quê.
- Rode o `notes-checker` depois: o plano `done` ou `dropped` fica como fechou, mas os links dele continuam conferidos.
