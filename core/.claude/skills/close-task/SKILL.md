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

- **A raiz** é o worktree do workstream: `git rev-parse --show-toplevel`, numa branch `workstream/<nome>`. Na `master`,
  pare: lá não mora nota, e a trava não confere nada.
- **O que ficou aberto:**

```bash
git status --short --untracked-files=all -- work plans wiki memory .claude/agent-memory
ls work/worklogs/ | grep "^$(date +%F)-"
```

## 2. O worklog de hoje

- **A tarefa já tem worklog de hoje:** acrescente a ele o que veio depois.
- **Não tem:** crie `work/worklogs/AAAA-MM-DD-assunto-em-kebab.md`, com `scope` no frontmatter e sem `status`, na forma
  da seção `work/worklogs/` do `work/README.md`. Mudou o assunto, virou o dia ou houve decisão: é arquivo novo.
- **O conteúdo** é o registro escrito, não a transcrição: o que se fez, o que se decidiu (com a resposta literal da
  rodada), o que ficou aberto, e `## Fora do workspace` quando algo mudou fora dos dois repositórios. Questão aberta
  não fica no worklog: vai ao passo 3.
- **Depois de uma compactação**, escreva a partir do resumo que ela deixou no contexto, sem inventar o que ele não diz.

## 3. O pendente

- **Com plano:** o `Retomar`, a primeira linha abaixo do H1 do `PLN-NNN-plan.md`, diz de onde a próxima sessão parte. O
  `## Worklogs` ganha a linha com a data, os IDs e o link do worklog. A forma está no `plans/README.md`.
- **Sem plano:** o pendente vira issue em `work/issues/`, ou linha no `Retomar` do plano dono.
- **Os fatos a conferir:** `python3 scripts/fact_outcome.py --pending` lista os fatos `inferido`, abaixo de 80 e os
  custos do workstream sem desfecho. O que a sonda, a rodada ou a execução da tarefa respondeu vai ao
  `fact_outcome.py` com a `--ref`; o resto fica.

## 4. A memória

Leia o que mudou em `memory/` e em `.claude/agent-memory/`. A memória que mudou vai no mesmo commit do worklog. O que vale
para todos os workstreams é candidato à norma: diga isso no worklog e a quem pediu, sem mexer na norma daqui.

## 5. Commitar

```bash
brza note commit <caminhos> -m "docs: <o que a tarefa fechou>"
```

Os caminhos são o worklog, o plano ou a issue, e a memória. O comando recusa a `master`, roda o checador antes e dá o
push. Se o checador barrar, corrija e rode de novo; o `notes-checker` explica o erro.

## 6. Conferir e devolver

Rode de novo o `git status` do passo 1: os cinco caminhos devem estar limpos, e o worklog de hoje deve existir. Diga a
quem pediu, numa linha, o commit e que o `/compact` já pode ser digitado.

**Sem nada a registrar** — a sessão só leu, ou a tarefa já fechou noutro worklog de hoje —, não crie worklog vazio:
diga isso, e que o `/compact sem-fechar` passa pela trava.
