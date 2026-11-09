---
name: close-task
description: >-
  Use ao fechar uma tarefa, antes do /compact e quando a trava do /compact barrar — "fecha a tarefa", "vou compactar",
  "grava o worklog". Grava o worklog do dia e o pendente no Retomar do plano ou numa issue, junta a memória que mudou e
  commita as notas do workstream pelo `brza note commit`, para o /compact passar.
---

# close-task — fechar a tarefa nas notas do workstream

A norma diz que **a tarefa fecha num worklog na hora em que fecha**, com o contexto ainda quente (`AGENTS.md`, "Um
workstream, uma sessão; uma tarefa, um worklog"). Esta skill é esse fechamento. A trava do `/compact`, o
`.claude/hooks/compact_guard.py`, barra o `/compact` digitado enquanto ele não aconteceu: há arquivo sem commit em
`work/`, `plans/`, `wiki/`, `memory/` ou `.claude/agent-memory/`, ou falta o worklog de hoje em `work/worklogs/`.

O `/compact` é de quem pediu: o modelo não o roda. A skill termina dizendo que ele pode ser digitado.

## 1. Situar

- **A raiz** é o worktree do workstream: `git rev-parse --show-toplevel`, numa branch `workstream/<nome>`. Na `{{workspace.branch}}`,
  pare: lá não mora nota, e a trava não confere nada.
- **O que ficou aberto:**

```bash
git status --short --untracked-files=all -- work plans wiki memory .claude/agent-memory
ls work/worklogs/ | grep "^$(date +%F)-"
```

## 2. O que o commit vai fechar

```bash
python3 scripts/summarize_measurements.py --by-task --pending --session "$CLAUDE_CODE_SESSION_ID" \
  --workstream "$(git branch --show-current | sed 's|^workstream/||')"
```

A saída lista os turnos desde o último worklog que esta sessão commitou — a hora e o começo do pedido de cada um — e
os turnos sem tarefa das outras sessões do workstream. **Se os turnos listados são de mais de uma tarefa**, feche antes
a mais antiga: o worklog dela e `brza note commit <worklog> --until <HH:MM> -m "docs: …"`. O `--until` leva a hora que
a lista mostra para o primeiro turno da tarefa seguinte (a lista dá a hora do começo do turno, e o turno vai à tarefa
pela hora em que começa); se o último turno da tarefa anterior começou no mesmo minuto, use a hora ISO, com os
segundos. Com três ou mais, repita da mais antiga para a mais nova. O resto fica para o worklog da tarefa em curso.
**Os turnos sem tarefa das outras sessões** se dizem a quem pediu, numa linha, sem fechá-los daqui.

Sem turno pendente, a saída é "nada a fechar". Sem a `CLAUDE_CODE_SESSION_ID` no ambiente, fora do Claude Code, o passo
não se aplica: o commit sai sem carimbo de sessão.

## 3. O worklog de hoje

- **A tarefa já tem worklog de hoje:** acrescente a ele o que veio depois, acima da `## Medida` quando ela existe: o
  que ficar abaixo dela se perde no commit seguinte.
- **Não tem:** crie `work/worklogs/AAAA-MM-DD-assunto-em-kebab.md`, com `scope` no frontmatter e sem `status` nem `date`,
  na forma da seção `work/worklogs/` do `work/README.md`. Mudou o assunto, virou o dia ou houve decisão: é arquivo novo.
- **O conteúdo** é o registro escrito, não a transcrição: o que se fez, o que se decidiu (com a resposta literal da
  rodada), o que ficou aberto, e `## Fora do workspace` quando algo mudou fora dos dois repositórios. Cite no corpo os
  IDs (`PLN-NNN`, `REV-NNN`, `MIG-NNN`, `issue:<slug>`) ou links relativos das notas tocadas: o `brza note commit`
  extrai e preenche `refs:` no frontmatter do worklog automaticamente. Questão aberta não fica no worklog: vai ao passo 4.
- **A `## Medida` não se escreve à mão:** o `brza note commit` a põe como última seção do worklog e a reescreve a cada
  commit dele.
- **Depois de uma compactação**, escreva a partir do resumo que ela deixou no contexto, sem inventar o que ele não diz.

## 4. O pendente

- **Com plano:** o `Retomar`, a primeira linha abaixo do H1 do `PLN-NNN-plan.md`, diz de onde a próxima sessão parte. O
  `## Worklogs` ganha a linha com a data, os IDs e o link do worklog. A forma está no `plans/README.md`.
- **Sem plano:** o pendente vira issue em `work/issues/`, ou linha no `Retomar` do plano dono.
- **Os fatos a conferir:** `python3 scripts/fact_outcome.py --pending` lista os fatos `inferido`, abaixo de 80 e os
  custos do workstream sem desfecho. O que a sonda, a rodada ou a execução da tarefa respondeu vai ao
  `fact_outcome.py` com a `--ref`; o resto fica.

## 5. A memória

Leia o que mudou em `memory/` e em `.claude/agent-memory/`. A memória que mudou vai no mesmo commit do worklog. O que vale
para todos os workstreams é candidato à norma: diga isso no worklog e a quem pediu, sem mexer na norma daqui.

## 6. Commitar

```bash
brza note commit <caminhos> -m "docs: <o que a tarefa fechou>"
```

Os caminhos são o worklog, o plano ou a issue, e a memória. O comando atualiza o `refs:` dos worklogs pedidos, carimba
neles o `sessions:` com a sessão, escreve a `## Medida` e grava o marco da tarefa na medição; recusa a
`{{workspace.branch}}`, roda o checador antes e dá o push. Se o checador barrar, corrija e rode de novo; o
`notes-checker` explica o erro.

**As tarefas mais antigas, quando o passo 2 achou mais de uma**, fecham antes, uma a uma, cada uma com
`--until <HH:MM>` (ou a hora ISO): o marco vai à hora dada, e os turnos depois dela ficam para o commit seguinte; o
último fica com a tarefa em curso. A hora tem de cair entre o marco anterior da sessão e agora; fora disso, o commit
recusa sem tocar o worklog.

## 7. Conferir e devolver

Rode de novo o `git status` do passo 1: os cinco caminhos devem estar limpos, e o worklog de hoje deve existir. Diga a
quem pediu, numa linha, o commit e que o `/compact` já pode ser digitado.

**Sem nada a registrar** — a sessão só leu, ou a tarefa já fechou noutro worklog de hoje —, não crie worklog vazio:
diga isso, e que o `/compact sem-fechar` passa pela trava.
