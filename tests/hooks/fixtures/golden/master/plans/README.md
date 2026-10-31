# plans/

Um plano responde a uma pergunta só: **como estou fazendo este trabalho**. Quem abre, conduz e fecha é a skill
`plan-runner`, e o molde mora nela, em `references/plan-template.md`. As propriedades comuns a toda nota —
`status`, `scope`, `opened`, `owner`, `blocks`, `tags` —, os links e o commit seguem o
[`work/README.md`](../work/README.md); aqui fica só o que é de plano. Adaptada da norma de planos do workspace da
Fixfy, sem o que é só de lá.

**Último PLN usado:** 007

**Abre plano** o trabalho que não cabe numa sessão só: lacuna a entrevistar, passo que precisa de detalhamento, ou
proposta que precisa de desenho. Trabalho de uma sessão só não abre plano: o plano de implementação dele abre o
worklog.

| Regra | Como |
| :---- | :--- |
| Pasta | Uma por plano, mesmo com um arquivo só: `plans/PLN-001-assunto/`, no workstream da sessão que o abre, e nunca na `master`. O número é sequencial e não se reusa. A linha "Último PLN usado" acima sobe na `master`, num commit de norma, antes de o plano ser gravado |
| Arquivos | `PLN-001-plan.md` sempre; `-interview.md` e `-design.md` quando o plano passa de ~400 linhas ou o desenho de ~800. O `-plan` é o arquivo lido para retomar. A imagem fica na pasta: a alternativa de interface escolhida no Desenho, `PLN-001-<assunto>.png`, pelo `brza shot`; o print de um caso manual, `PLN-001-C1.png`, pelo `brza print` |
| IDs | `R3.2` rodada e pergunta, só cresce · `D3` item do Desenho, reescrito no lugar · `P3` passo · `X1` desvio · `A1` achado · `C1` caso manual. Citação de fora: `PLN-001/D3` |
| Estado | Frontmatter **só no `-plan.md`**: `status`, `phase`, `origin`, `origin_ref`, `ready` e as três horas, na forma do molde; o `Retomar` é a primeira linha abaixo do H1 |
| Origem | `origin` em `issue` · `decision` · `plan` · `request`; `origin_ref` com o nome da nota sem `.md`, `PLN-NNN` ou `PLN-NNN/A<n>`, ausente com `request`. Uma origem por plano: com mais de uma, vale a ordem `issue` > `plan` > `decision`, e as outras ficam na linha `- **Origem:**` do Pedido. Plano que veio de outro workspace é `request`, com a procedência na linha `- **Origem:**` |
| Seções de ponteiro | `## Worklogs`, sempre; `## Revisões`, `## Decisões`, `## Pendências conhecidas` e `## Documentação` quando há o que apontar, depois dos Desvios. À mão; o checador avisa a nota de `work/` que linka o plano e falta na seção, e a `plan-runner` acerta ao retomar |
| Branch | O plano mora no workstream da frente, onde fica também depois de fechar. O trabalho de plano no `brza-studio` leva o plano no nome da branch: `<tipo>/pln-NNN-<slug>`, com um worktree em `<repo>/.worktrees/` e uma sessão por entrega. Ali, o `brza note` acha o workstream procurando o `plans/PLN-NNN-*` nas branches `workstream/*` |
| Rodadas | Gravadas na hora em que fecham, com o que foi apresentado antes, todas as opções com custo e quando se pagam, e a resposta |
| Evidência | `[x]` sem evidência não conta: o commit, o comando e o resultado, `arquivo:linha` |
| Estimativa | Todo passo que executa algo leva `Estimado: N h · Executado: — h`; o frontmatter soma `estimate_h`, `executed_h` e `remaining_h` |

| `phase` | `status` |
| :------ | :------- |
| `interview`, `design` | `open` |
| `execution` | `in_progress` |
| `review` | `in_review` |
| `blocked` | `open`, com `blocks` e `owner` |
| `closed` | `done`, com `completed` |
| `dropped` | `dropped`, com a linha do porquê |

- **O plano fecha, não se apaga**, depois que o durável saiu para a casa dona — a decisão em `work/decisions/`, a norma
  do repositório.
- **Passo de plano só executa a pedido explícito de Anderson, passo a passo.** Plano revisado ou aceito não autoriza
  execução.
- **O plano não substitui a decisão.** Rodada que decide algo que sobrevive ao plano leva a nota de `work/decisions/` a
  `done`, citando a rodada.
- **O `## Worklogs` cita o worklog** de cada sessão, numa linha com a data e os IDs, e o link do worklog.
- **O parecer da revisão é nota de `work/reviews/`**, `pln-NNN-<assunto>.md`, e o plano aponta para ela.
- **O checador barra o plano aberto fora do molde** — `open`, `in_progress`, `in_review` —: `phase` ausente, fora do
  vocabulário ou em desacordo com o `status`; `origin` ausente ou fora do vocabulário; `origin_ref` ausente, presente
  com `request`, ou apontando para issue, decisão ou plano que não existe; `ready` ausente; hora que não é número;
  o `Retomar` fora da primeira linha; o `## Worklogs` ausente; e a linha do `## Worklogs` sem o link do worklog, a
  partir da linha datada de 2026-10-28 — a anterior fica como foi escrita. **Avisa** a nota de `work/worklogs/`,
  `work/reviews/`, `work/decisions/` ou `work/issues/` que linka o plano e falta na seção dele — a issue vale também no
  `### Fora de escopo` e no `origin_ref`. `done` e `dropped` ficam como fecharam. Os vocabulários de `phase` e `origin`
  estão no script e nesta norma: mudou um, muda o outro no mesmo commit. O script é a skill `notes-checker`
  (`python3 .claude/skills/notes-checker/scripts/check_notes.py`, da raiz).
