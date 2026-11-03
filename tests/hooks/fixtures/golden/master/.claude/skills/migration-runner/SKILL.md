---
name: migration-runner
description: >-
  Use quando uma mudança de norma ou decisão exigir migrar o acervo ou os repositórios — "abre uma migração",
  "cria a MIG", "revisa a MIG-001", "executa a MIG-001". Cria, revisa (com revisor independente e parecer REV-NNN)
  e executa issues `type: migration` (`work/issues/MIG-NNN-<slug>.md`) pelo molde de `references/migration-template.md`.
---

# migration-runner — criar, revisar e executar migrações (`MIG-NNN`)

Uma migração (`MIG-NNN`) registra e conduz a convergência entre o que está no disco e o que uma norma ou decisão
vigente passou a exigir. Mora em `work/issues/MIG-NNN-<assunto>.md`, com `type: migration`, H1 `# MIG-NNN: <título>` e
número reservado no índice da `master` pelo `brza note migration-open`.

**A norma é a do [`work/README.md`](../../../work/README.md).** Esta skill reúne as três operações de uma `MIG` numa
única entrada (`Criar`, `Revisar`, `Executar`) e guarda o molde em `references/migration-template.md`.

## Operações

| Operação | Quando | O que faz |
| :------- | :----- | :-------- |
| **1. Criar** | Norma ou decisão mudou e o acervo/repositório ainda está na forma anterior | Reserva `MIG-NNN` com `brza note migration-open`, levanta o estado atual no disco e grava `work/issues/MIG-NNN-<slug>.md` pelo molde |
| **2. Revisar** | Antes de executar uma `MIG` que toca múltiplos arquivos/workstreams, ou após a execução | Chama revisor independente, conduz a rodada dos achados com Anderson, reserva `REV-NNN` com `brza note review-open` e grava `work/reviews/REV-NNN-<slug>.md` |
| **3. Executar** | Anderson autorizou a execução dos passos da `MIG-NNN` | Executa os passos autorizados, registra a evidência em cada passo, roda o comando do `## Critério de conclusão` e fecha a nota com `status: done` e `completed` |

## 1. Criar (`MIG-NNN`)

1. **Situe o workstream** onde a migração nasce (`git rev-parse --show-toplevel`, numa branch `workstream/<nome>`).
2. **Reserve o número no índice da `master`:**

   ```bash
   brza note migration-open <assunto-em-kebab> --title "<título sem MIG-NNN:>" --description "<uma frase>"
   ```

   O comando imprime o `MIG-NNN` e o caminho `work/issues/MIG-NNN-<assunto-em-kebab>.md`.
3. **Preencha o arquivo pelo molde** (`references/migration-template.md`):
   - Frontmatter com `status: open`, `type: migration`, `description`, `scope`, `opened`, `priority` e, quando houver,
     `depends_on`, `estimate_h`, `blocks`, `owner`, `deadline`.
   - `## Estado-alvo`: o contrato que a norma ou decisão fixa, com link para a decisão, plano ou norma de origem.
   - `## Estado atual`: o levantamento verificável do que diverge hoje no disco (contagens, arquivos ou comandos).
   - `## Passos`: checkboxes `- [ ]` na ordem de execução, nomeando o comando, script ou workstream de cada passo.
   - `## Critério de conclusão`: comando determinístico que retorna `0` somente quando o estado-alvo foi atingido e nada
     do estado antigo restou.
   - `## Como desfazer`: **só quando a mudança sai do git** (arquivos fora de repositório, banco, configuração externa).
     Quando tudo está sob versionamento git, omita a seção.
4. **Conferir e commitar:** rode o `brza note commit` com o caminho da `MIG-NNN` e o worklog da sessão.

## 2. Revisar (parecer `REV-NNN`)

Quem criou ou executou a `MIG-NNN` **não revisa o próprio trabalho**:

1. **Chame um revisor independente** — subagente `reviewer` (preferencialmente no modelo mais forte) ou outra sessão —
   passando o caminho de `work/issues/MIG-NNN-<slug>.md`, a norma ou o item do Desenho de origem e os arquivos tocados.
   O revisor confere a cobertura do estado-alvo, a segurança dos passos e a completude do critério de conclusão (ou as
   evidências contra o disco, na revisão pós-execução), sem editar arquivos.
2. **Confira cada achado e conduza a rodada** com Anderson, na forma de `plan-runner/references/conduct.md`
   (`### A<n> — Título`, `> tipo · gravidade · confiança NN · → destino`, `**Evidência:**` e, quando falso,
   `**Por que é falso:**`). Achado abaixo de 80 não entra no parecer.
3. **Reserve o número do parecer e grave a revisão:**

   ```bash
   brza note review-open <slug-da-revisao> --title "<título sem REV-NNN:>" --description "<uma frase>"
   ```

   Grave `work/reviews/REV-NNN-<slug-da-revisao>.md` com H1 `# REV-NNN: <título>`, aponte a `MIG-NNN` revisada e
   registre o link do parecer na `MIG-NNN`.

## 3. Executar

**A execução só começa a pedido explícito de Anderson:**

1. **Confira `depends_on` e pré-requisitos:** nenhuma dependência aberta pode ser ignorada (`check_notes.py` barra
   dependência inexistente ou em ciclo; para considerar uma dependência satisfeita, ela precisa estar `done`).
2. **Mude `status: in_progress`** na `MIG-NNN` ao iniciar, se a execução atravessar etapas ou commits.
3. **Para cada passo de `## Passos`:**
   - Use sempre `move_note.py` para renomear notas preservando links relativos, e `brza note commit` para commitar e
     recalcular `refs:` dos worklogs.
   - Quando a migração atravessar workstreams, cada workstream recebe o commit na sua própria árvore/sessão dona, e o
     índice da `master` é sincronizado ao final (`brza note index --rebuild`).
   - Grave a `**Evidência:**` (commit, comando e saída) no passo antes de marcar `[x]`.
4. **Rode o `## Critério de conclusão`** e o `check_notes.py`: ambos devem sair com `0`.
5. **Feche a `MIG-NNN`** com `status: done` e `completed: AAAA-MM-DD`, grave o worklog da sessão (que citará `MIG-NNN` e
   terá `refs:` preenchido pelo `brza note commit`) e commite pelo `brza note commit`.
