# {{workspace.name}}

O workspace de {{workspace.owner}} para o **{{workspace.title}}** (`{{code.path}}`): os planos, o registro do que foi feito
e as decisões ficam aqui, e o código fica no `{{code.name}}`.

As pastas de notas não moram na `{{workspace.branch}}`, que guarda só a norma: cada frente de trabalho é uma branch
`workstream/<nome>`, com worktree em `.worktrees/<nome>`. Os workstreams de agora: nenhum ainda; o primeiro nasce com
`brza note workstream <nome>`.

| Pasta | O que guarda |
| :---- | :----------- |
| `plans/` | Os planos, uma pasta por plano (`plans/PLN-001-…/`), com a norma no [`plans/README.md`](plans/README.md) |
| `work/issues/` | Pendência, dívida, bug conhecido e investigação |
| `work/worklogs/` | O registro de cada trabalho feito, um assunto por arquivo |
| `work/decisions/` | A questão e a decisão no mesmo arquivo, de `open` a `done` |
| `work/reviews/` | Parecer sobre um artefato numa versão |
| `work/notes/` | Rascunho do dia, de onde o conteúdo é promovido |
| `wiki/guides/` | Como {{workspace.owner}} usa uma ferramenta, API ou produto |

A norma de escrita das notas está no [`work/README.md`](work/README.md). Os comandos rodam da raiz deste repositório.

```bash
# Conferir as notas antes de commitar, no worktree do workstream
python3 .claude/skills/notes-checker/scripts/check_notes.py

# Levar a norma da {{workspace.branch}} a cada worktree de workstream
brza note sync

# Atualizar a norma comum a partir do brza-workflow
brza sync

# Resumir a medição de .measurements/measurements.jsonl
python3 scripts/summarize_measurements.py

# O acerto da confiança dos fatos do designer e do collector
python3 scripts/summarize_measurements.py --facts

# Os fatos a conferir, e o desfecho de um deles
python3 scripts/fact_outcome.py --pending
python3 scripts/fact_outcome.py <agente>:<F3> holds|fails "<porquê>" --ref <PLN-NNN/R…>
```

A norma de agente está no [`AGENTS.md`](AGENTS.md), e o adaptador do Claude Code no [`CLAUDE.md`](CLAUDE.md). O que é só
deste workspace está em [`.claude/rules/workspace.md`](.claude/rules/workspace.md).

## Quem commita

O commit é com `{{author.name}}` (`{{author.email}}`), pelo `env` do `.claude/settings.json`, e vai ao GitHub pela chave
de acesso que o `~/.ssh/config` indicar para o `origin`: a norma à `{{workspace.branch}}`, e as notas à branch do
workstream. O `gh` usa o perfil padrão, com a conta `{{author.login}}` ativa (`gh auth switch --user {{author.login}}`).

## Funções do shell

O `.claude/settings.json` daqui não é herdado pelo `{{code.name}}`, que fica ao lado deste repositório. Este bloco do
`~/.zshrc` acha o workspace do cwd — pelo `.brza/workspace.toml`, ou, no repositório de código, pelo
`~/.config/brza/workspaces.toml` — e passa a configuração às sessões abertas nos dois. Se ele mudar, mude aqui e copie
para lá. Fora do workspace, a função chama o `claude` que já existia.

```zsh
# brza: o workspace do cwd — pelo .brza/workspace.toml, ou, no código, pelo ~/.config/brza/workspaces.toml.
# O bloco "Funções do shell" do README do workspace. A BRZA_WORKSPACE exportada é override.
brza_claude() {
  local ws=$1 role=$2 json; shift 2
  local -a agents=()
  if [ "$role" = code ] && [[ " $* " != *" --agents"* ]]; then
    json=$(brza-agents --workspace "$ws" 2>/dev/null)
    [ -n "$json" ] && agents=(--agents "$json")
  fi
  BRZA_WORKSPACE="$ws" command claude --settings "$ws/.claude/settings.json" "${agents[@]}" "$@"
}

claude() { local w; w=$(brza-where 2>/dev/null); if [ -n "$w" ]; then brza_claude ${=w} "$@"; else command claude "$@"; fi; }
```
