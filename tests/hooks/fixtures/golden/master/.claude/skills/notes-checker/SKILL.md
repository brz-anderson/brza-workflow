---
name: notes-checker
description: >-
  Use depois de escrever ou mover notas em work/, plans/ ou wiki/, e no encerramento do dia — "confere as notas",
  "checa as notas", "move a nota". Roda check_notes.py (confere nome repetido, link relativo quebrado, wikilink
  proibido, status fora do vocabulário, campos por estado, type, tags e o plano aberto contra o molde) e fornece
  move_note.py (move ou renomeia nota com git mv reescrevendo os links Markdown relativos nos dois sentidos).
---

# notes-checker

Conferência determinística da forma das notas — `work/`, `plans/` e `wiki/` na raiz do brza-workspace — contra a norma
do [`work/README.md`](../../../work/README.md) e do [`plans/README.md`](../../../plans/README.md). O molde do plano, na
`plan-runner`, entra só para resolver links que apontem para ele. Adaptada do `notes-checker` do workspace da Fixfy, sem
o que é só de lá. É um script, e não uma leitura do agente, de propósito: regra conferida por agente varia entre
execuções. A skill só o roda e lê o resultado.

## Rodar a conferência

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/check_notes.py
```

Sem `--root`, o script lê a raiz do repositório onde mora, de qualquer diretório. Raiz sem `work/`, `plans/` nem `wiki/`
sai com 2. Ele só lê.

| Flag       | Para quê                                                     |
| :--------- | :----------------------------------------------------------- |
| `--detail` | lista cada aviso com o arquivo, em vez da contagem por regra |
| `--json`   | saída para outro programa                                    |
| `--root`   | outra raiz, com `work/`, `plans/` e `wiki/`                  |

## Mover ou renomear nota (`move_note.py`)

Nunca use `mv` ou `git mv` cru numa nota: com links Markdown relativos, mover o arquivo sozinho quebra tanto os links de
outras notas que apontam para ele quanto os links dentro dele que apontam para outras pastas. Use sempre:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/move_note.py <origem> <destino> [--title "<H1 novo>"] [--dry-run]
```

**Foco novo, nome novo** (`work/README.md`, "Como funciona"): quando a nota ou o plano muda ou expande o foco, mudam
juntos o nome do arquivo, o H1 e os links. A origem pode ser a pasta do plano, que se move inteira, com o número `PLN-NNN`
mantido. O `--title` troca o H1 — na pasta, o do `PLN-NNN-plan.md` — e o rótulo de todo link que era igual ao H1 antigo;
o rótulo diferente fica, e a saída o conta para você conferir. Para trocar só o H1, passe o mesmo caminho como origem e
destino.

O script reescreve os links relativos que apontam para `<origem>` em todo o acervo (`work/`, `plans/` e `wiki/`), ajusta
a profundidade dos links que saem da nota movida quando ela muda de diretório, e executa `git mv`. Em seguida, rode
`check_notes.py` para confirmar zero erros.

## Ler o resultado

Sai com 0 sem erro, com 1 com erro. O corte é **o que quebra consulta ou ponteiro** contra **o que é dívida conhecida**:

- **Erro, barra:** `duplicate-name`, `unresolved-link` (link Markdown relativo quebrado), `wikilink-forbidden`
  (`[[wikilink]]` fora de código), `status-out-of-vocabulary`, e os campos obrigatórios por estado
  (`done-without-completed`, `decided-without-pair`, `decision-done-without-decided`, `open-decision-without-trigger`,
  `blocks-without-owner`) em nota datada de 2026-10-26 em diante, o dia em que o workspace nasceu; e, no `-plan.md`
  aberto — `open`, `in_progress`, `in_review` —, o molde do `plans/README.md`: `plan-without-phase`,
  `phase-out-of-vocabulary`, `phase-status-mismatch`, `plan-without-origin`, `origin-out-of-vocabulary`,
  `origin-ref-on-request`, `origin-ref-missing`, `origin-ref-not-found`, `plan-without-ready`, `hours-not-a-number`,
  `retomar-not-first`, `plan-without-worklogs` e `worklog-line-without-link` (a linha datada de 2026-10-28 em diante).
- **Aviso, relata e segue:** os mesmos campos obrigatórios em nota sem data nenhuma — data que não está escrita não se
  infere —, `type` redundante ou inválido, tag fora do vocabulário ou que repete campo, `status` em gênero sem estado,
  achado `### A<n>` de um parecer de `work/reviews/` sem `**Evidência:**` (`finding-without-evidence`) e nota de
  `work/worklogs/`, `work/reviews/`, `work/decisions/` ou `work/issues/` que linka um plano aberto e falta na seção de
  ponteiro dele (`backlink-missing`).

**Link para nota de outro workstream:** o alvo que não está na árvore se procura nas branches `workstream/*`, as locais
e as de `origin`, pelo `git ls-tree` de cada uma, calculado uma vez; só o que não existe em nenhuma é `unresolved-link`.
O `origin_ref` de plano que aponta para issue, decisão ou plano (`PLN-NNN`) fora da árvore se procura do mesmo jeito.
Fora de repositório git, ou sem branch `workstream/*`, só vale a árvore. O `move_note.py` reescreve só os links da
árvore atual.

**Nome repetido continua proibido**: dois arquivos com o mesmo nome confundem buscas e referências no acervo. A exceção
é o `README.md`, que é a norma de cada zona, nunca nota — o `work/README.md` e o `plans/README.md` repetem o nome por
construção.

## Depois de rodar

- **Reporte o resultado como saiu**, com os erros um a um. Aviso não vira tarefa por conta própria.
- **Erro em nota que a sessão escreveu agora:** corrija pela norma do `work/README.md`.
- **Erro em nota de outra tarefa:** não corrija; relate a Anderson.
- **Os vocabulários estão no script, no `work/README.md` e no `plans/README.md`.** Se a norma mudar status, gênero, tag,
  `phase` ou `origin`, o script muda no mesmo commit; divergência entre os dois é achado, não escolha silenciosa.

## Verificação dos scripts

`python3 ${CLAUDE_SKILL_DIR}/tests/run.py` monta uma raiz de fixture com uma nota por regra e confere o nível de cada
regra do `check_notes.py` e a reescrita de links do `move_note.py`.
