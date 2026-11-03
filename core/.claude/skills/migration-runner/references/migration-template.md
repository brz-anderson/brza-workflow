> **Molde de migração (`MIG-NNN`) da `migration-runner`.** A norma está no `work/README.md`.
>
> **Como usar:** reserve o número com `brza note migration-open <slug> --title "<título>" --description "<frase>"` e
> crie `work/issues/MIG-NNN-<slug>.md`. **Apague todas as citações `>`:** neste arquivo, citação é sempre instrução.
> A seção `## Como desfazer` só existe quando a mudança sai do git; caso contrário, apague-a.

```yaml
---
status: open
type: migration
description: uma frase sobre o que a migração converte e por quê
scope:
  - workspace
opened: AAAA-MM-DD
priority: medium
estimate_h: 2
---
```

# MIG-NNN: Título que enuncia a conversão

## Estado-alvo

> O que passa a valer quando a migração terminar, apontando a norma, decisão ou plano (`PLN-NNN/D<n>`) que a exige.

- **Origem:** [Título da decisão ou plano](../../plans/PLN-NNN-assunto/PLN-NNN-plan.md)
- **Regra:** <o que deve estar verdade no acervo ou no repositório ao final>.

## Estado atual

> O levantamento verificável do que diverge hoje no disco — arquivos, contagens ou saída de comando.

- <arquivo ou conjunto de arquivos e o que ainda está no formato antigo>.

## Passos

> Um passo por ação verificável. Marque `[x]` somente com a evidência gravada.

- [ ] **1. <Primeiro passo>** — <comando, script ou arquivo>
  **Evidência:** `<commit>` · `<comando>` → `<resultado>`
- [ ] **2. <Segundo passo>** — <conferência e rebuild do índice>
  **Evidência:** `<commit>` · `<comando>` → `<resultado>`

## Critério de conclusão

> Comando determinístico que sai com `0` quando o estado-alvo está completo e zero vestígios do formato antigo restam.

```bash
python3 .claude/skills/notes-checker/scripts/check_notes.py
```

## Como desfazer

> **Gatilho:** a migração altera estado fora do git (banco de dados, arquivos externos ao repositório, serviços).
> Se toda a mudança está em commits git, apague esta seção.

- <comando ou procedimento exato para restaurar o estado anterior fora do git>.
