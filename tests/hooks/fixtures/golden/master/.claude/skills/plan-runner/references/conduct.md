# Conduzir — executar, retomar e pedir a revisão

Conduzir é executar e registrar no mesmo movimento. O checkbox marcado sem prova e a fase que não casa com os passos
nascem de executar e registrar serem atos separados. O passo só está feito quando a evidência chegou ao plano, em
`plans/`, gravada pela sessão que o executou.

## Retomar

"Retomar o PLN-NNN" começa sempre igual, antes de tocar em qualquer arquivo:

1. **Leia o frontmatter** — `phase`, `ready`, as horas —, o `Retomar` e as últimas linhas do `## Worklogs`, em
   `plans/PLN-NNN-assunto/PLN-NNN-plan.md`. Ler `plans/` é direto, e gravar segue "Quem grava", na `SKILL.md`.
2. **Rode o checador** (`../notes-checker/scripts/check_notes.py`) e traga às seções de ponteiro as notas que ele avisa
   (`backlink-missing`): o worklog, o parecer, a decisão ou a issue que linkam o plano e não estão nele.
3. **Confira os pré-requisitos de novo.** Assinatura que existia na sessão passada pode ter mudado; faltando, pare e
   diga o que falta.
4. **Ache o primeiro passo sem `[x]`** e confira que o anterior tem evidência. Passo marcado sem evidência volta a ser
   pendente: diga isso a Anderson, e grave a correção no plano.
5. **Diga em uma linha onde o trabalho está** e qual passo vem, antes de executá-lo.

Retomar um plano de worklog numa sessão nova é o sinal de que ele atravessa sessões: promova-o a `PLN` antes de seguir:
a seção datada é copiada, com os mesmos IDs, e o worklog ganha a linha "promovido a `PLN-NNN`".

## Executar um passo

**O passo só executa a pedido explícito de Anderson, passo a passo**: plano aceito não autoriza execução.

1. **Pré-requisito do passo, antes de começar.** Se não existe, é gatilho de parada: não improvise o que falta.
2. **Pelo executor que o passo nomeia:**
   - **a sessão que conduz** — a rodada, a nota, o registro; se o passo entrevista, as rodadas acontecem aqui;
   - **a sessão do `brza-studio`** — o passo de código, numa sessão nova aberta em `~/Projects/brza-studio`, num worktree
     em `.worktrees/`, na branch `<tipo>/pln-NNN-<slug>`, e nunca na sessão em que Anderson conduz o plano. O pedido
     leva o passo, os arquivos, o `D` e o que as rodadas decidiram, citado como foi respondido, e vai como arquivo, no
     `Retomar` do plano ou numa issue. A sessão de lá grava a evidência no passo, o `Retomar` e o worklog dela; a
     devolução fica no commit ou no PR e no plano, e o aviso à sessão que pediu só vai quando o pedido diz "me avise";
   - **um subagente** — leitura, levantamento e rascunho, pelo laço da `SKILL.md`; ele devolve os arquivos tocados, os
     comandos com o resultado e cada desvio local como `X` proposto, e desvio que muda o contrato do `D` o faz parar e
     devolver, sem aplicar;
   - **Anderson** — o que só se prova no aparelho dele (o Windows, o instalador): o plano escreve os `C` e ele grava o
     Observado e o veredito.
3. **Registre a evidência** na linha do passo, e só então marque `[x]`. O `Executado` do passo vai junto, e `executed_h`
   e `remaining_h` são reescritos no lugar: o executado soma o do passo fechado, e o restante deixa de contar o estimado
   dele.
4. **Registre o desvio** como `X`, curto, e cite-o no passo. Desvio que muda um `D` vira rodada antes de seguir: no
   `PLN`, o `D` é reescrito no lugar, com a origem nova; no worklog, o plano aceito não muda, e o desvio e a rodada vão
   abaixo dele.
5. **Proponha o commit** quando um ou mais passos fecharem com o repositório verde — os testes do que foi tocado —, na
   mensagem que a norma do `brza-studio` pede. **Commite só com confirmação de Anderson.** O commit vira evidência dos
   passos que fecha, gravada no plano logo depois dele.

## Fim de sessão

No `PLN`, gravados no mesmo commit:

- **O worklog da sessão, escrito antes da linha**, em `work/worklogs/`, pela norma da seção de `work/worklogs/` do
  `work/README.md`: a tarefa fecha num worklog na hora em que fecha.
- **Uma linha no `## Worklogs`**: a data, o que avançou citando IDs, o que travou e o link desse worklog.
- **O `Retomar` reescrito** com o próximo passo — no lugar, nunca empilhado: é a primeira linha abaixo do H1.
- **O `phase` conferido**: `phase: blocked` quando um pré-requisito falta, com o motivo no `ready` — `status: open`, com
  `blocks` e `owner`.
- **A issue aberta na sessão** entra em `## Pendências conhecidas`; a decisão, em `## Decisões`.
- **Arquivo acima do teto da `SKILL.md`** divide por sufixo, no mesmo commit, e o topo do corpo ganha a linha
  `Dividido`.

No worklog, o fim de sessão é o registro da execução abaixo do plano aceito; trabalho que não fechou é promovido a
`PLN`.

## Pedir a revisão da entrega

Com todo passo `[x]` ou `[-]`: `phase: review`, `status: in_review`.

1. **Chame um revisor que não executou o passo** — um subagente, de preferência do modelo mais forte, ou uma sessão
   separada —, com os caminhos do plano e do que foi entregue. Ele confere o entregue contra o `## Pedido` e a
   Cobertura, e as evidências contra o disco; revisa isolado, e não corrige: quem executou não revisa o que executou.
2. **Confira e faça a rodada dos achados**: refaça a evidência de cada achado antes de levá-lo. O que não se sustenta
   vai à rodada com `→ falso` como opção recomendada, o porquê e a evidência que o derruba — quem executou o passo não
   derruba sozinho achado sobre o próprio trabalho. Cada achado ganha o destino, escolhido por Anderson, com as opções
   e o custo.
3. **Grave o parecer** numa nota `work/reviews/pln-NNN-<assunto>.md`, com H1 `REV: …`, e a linha dela em `## Revisões`
   do `PLN`; no worklog, na revisão abaixo do plano aceito. Achado com destino `corrigido aqui` vira passo novo; o resto
   vai para a casa dona — uma issue, uma decisão, a norma do `brza-studio`.
4. **Casos manuais**, quando algum critério só se prova em aparelho: escreva os `C` a partir do critério, peça a quem
   tem o aparelho, e grave o Observado numa linha com o veredito.

**A forma do achado**, que o checador confere (`finding-without-evidence`):

```markdown
### A1 — Título que enuncia o defeito

> tipo · gravidade · confiança 90 · → destino

**Evidência:** `arquivo:linha` ou o comando e o resultado, refeitos nesta revisão.

### A2 — Título que enuncia o defeito suposto

> tipo · gravidade · confiança 95 · → falso

**Por que é falso:** numa oração, o que derruba a hipótese.

**Evidência:** `arquivo:linha` ou o comando e o resultado que a derrubam.
```

O `tipo` diz o que é (`bug`, `desvio`, `lacuna`); a `gravidade`, o peso (`importante`, `sugestão`) — no falso, o que
ele teria se fosse real; a `confiança`, de 0 a 100, a chance de o achado ser real depois de verificado — 90 ou mais por
comando ou leitura direta, de 80 a 89 com a premissa escrita na evidência; no falso, a de ele ser falso; o `destino`,
para onde vai: `corrigido aqui`, `issue <nota>`, `decisão <nota>`, `descartado`, com o porquê, ou `falso`. **`falso`
não é `descartado`:** o descartado é real e não vale o conserto; o falso parecia defeito e não é, e fica no parecer
para a próxima revisão não o reabrir.

**Abaixo de 80 o achado não entra no parecer.** O revisor verifica até subir ou deixa de fora, e a dúvida que importa
vai à rodada como pergunta, não como achado. A confiança é do revisor e não muda na rodada; o destino, sim.

A revisão fecha quando todo achado tem destino. Então vem o fechamento.
