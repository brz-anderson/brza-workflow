# brza-workflow

O núcleo de uma norma de trabalho com agentes, para um **workspace de notas ao lado de um repositório de código**. O
workspace guarda os planos, o registro do que foi feito, as decisões e a norma dos agentes; o código fica no
repositório dele.

O `brza init` monta um workspace desses num diretório vazio. O `brza sync` traz depois cada mudança da norma, sem tocar
a parte que é só do workspace.

## O que vem no núcleo

| Pasta | O que é | Vai ao workspace? |
| :-- | :-- | :-- |
| `core/` | A norma: `AGENTS.md`, `CLAUDE.md`, `plans/README.md`, `work/README.md`, as skills e os papéis em `.claude/`, os hooks de medição e da trava do `/compact`, os scripts da medição | Sim, pelo `brza sync`, renderizado com o manifesto |
| `templates/` | O que o `brza init` escreve uma vez: o manifesto, o `settings.json`, o `README.md`, a `.claude/rules/workspace.md`, as pastas de notas, o índice vazio | Sim, só no init; depois é local |
| `packages/client/` | O pacote opcional do cliente — o `claude-brza` do chat, o `brza-sessions`, o `brza-run`, o `brza-doctor`, o `brza-print`, o `brza-devices` e o bloco do shell | Só com `packages = ["client"]` no manifesto |
| `packages/mcp/` | O servidor MCP residente — o `brza-mcp` (`serve`, `start`, `stop`, `status`, `token`), em Python stdlib, sob o launchd; `brza mcp <verbo>` chega a ele pelo despacho `brza-<sub>` | Não: roda deste checkout, ligado pelo `brza install --mcp` |
| `bin/` | O CLI: `brza init`, `brza sync`, `brza note`, `brza register`, `brza where`, `brza install`, `brza agents` | Não: roda deste checkout |
| `tests/` | Os testes do CLI, dos hooks, do MCP e do checador de notas | Não |

A norma em `core/` é molde. Onde o texto depende do workspace, ele traz `{{tabela.chave}}`, como `{{workspace.name}}`
ou `{{author.email}}`, e o valor vem do `.brza/workspace.toml` do workspace.

## Requisitos

- **git**, com um remote para o workspace, se ele for sincronizar.
- **Python 3.10 ou mais** para o CLI e os testes dele. Os hooks e o `brza_root` rodam também no 3.9 do macOS, porque o
  Claude Code os chama com o `python3` que o PATH der.
- **Claude Code**. O `CLAUDE.md` do núcleo abre com `@AGENTS.md`, para a norma carregar sem configuração de usuário.

## Instalar, uma vez por máquina

```bash
git clone https://github.com/brz-anderson/brza-workflow ~/Projects/brza-workflow
```

```bash
~/Projects/brza-workflow/bin/brza-install --all
```

O `--all` liga os `brza-*` de `bin/` em `~/.local/bin`, inclusive o próprio `brza-install`. Daí em diante, `brza
install` basta:

- `--client`: o pacote do cliente;
- `--mcp`: o pacote do servidor MCP (`brza-mcp`);
- `--local <workspace>`: os scripts próprios de um workspace, em `scripts/brza/`;
- `--relink`: troca um link que aponta para outro lugar; sem ele, o install recusa tudo antes de mexer em qualquer
  link;
- `--dry-run`: mostra o que faria.

## Montar um workspace

```bash
brza init ~/Projects/meu-workspace --name meu-workspace --code ~/Projects/meu-app --title "Meu App"
```

- **O que escreve:** o manifesto, a norma renderizada, o `settings.json` com os hooks e a identidade git, as pastas de
  notas e o índice vazio.
- **A conferência:** o checador de notas tem de passar, e nada do que foi escrito pode citar outro workspace.
- **O fim:** um commit local, sem push, e o registro do par workspace → código em `~/.config/brza/workspaces.toml`.
- **O que recusa:** diretório que não esteja vazio, e código já ligado a outro workspace.
- **A identidade:** vem do `git config --global user.*`, ou de `--author-name`, `--author-email` e `--author-login`.
  O login sai do noreply do GitHub, quando o e-mail é um.
- **O cliente:** o pacote só entra com `--package client`.

## Acompanhar a norma

```bash
brza sync
```

- **De onde lê:** do commit da `core.ref` do manifesto (por padrão, `master`). O `.brza/core.lock` grava o sha256 de
  cada arquivo e o commit de onde veio.
- **O arquivo editado à mão:** é recusado com o diff, e o sync sai com 1. O `--force <caminho>` o sobrescreve.
- **O que saiu do núcleo:** é apagado, só se ninguém o editou.
- **O `settings.json`:** muda só na chave `hooks`.
- **O que nunca toca:** o manifesto, o índice, o `README.md` e a `.claude/rules/workspace.md`.
- **O commit:** vai à branch da norma do workspace, com `brza-workflow@<sha7>` na mensagem. Depois, o `brza note sync`
  leva a norma a cada worktree de workstream. O `--core-only` para antes disso, e o `--dry-run` não escreve nada.

## Mais de um workspace na máquina

O `brza where` diz o workspace do diretório atual e o papel dele:
- **`notes`:** dentro do workspace, pelo `.brza/workspace.toml`.
- **`code`:** dentro do repositório de código, pelo registro.

O `brza register <raiz> [--code <caminho>]` liga o código numa segunda máquina, onde ele mora noutro caminho.

O pacote do cliente traz o bloco do shell em `packages/client/shell/brza.zsh`. Carregue-o no `~/.zshrc`:

```zsh
source "$HOME/Projects/brza-workflow/packages/client/shell/brza.zsh"
```

Com ele, a função `claude` sobe com o `--settings` do workspace certo. Fora de workspace, ela chama a sua
`brza_claude_fallback`, se você a definir, e senão o `claude` de sempre.

## O índice das notas

A branch da norma do workspace guarda, além da norma, o `.brza/index.jsonl`: uma linha por plano, issue ou decisão
criada ou alterada, em qualquer workstream.

- **Quem escreve:** só o `brza note`. O `brza note plan-open` reserva o número do plano novo, e o `brza note commit`
  acrescenta a linha quando o frontmatter, o H1 ou o caminho de uma nota muda, por commit sem checkout.
- **A concorrência:** o `.gitattributes` liga o `merge=union`, para dois acréscimos simultâneos não conflitarem.
- **O reparo:** o `brza note index --rebuild` reconstrói o índice pelas branches.

## Testes

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/cli
```

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/hooks
```

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/mcp
```

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/notes-checker/run.py
```

O `tests/hooks` também roda no `/usr/bin/python3`. O teste de ouro, `tests/hooks/test_golden_render.py`, confere que o
núcleo renderizado com os valores do primeiro workspace, o `brza-workspace`, reproduz a norma dele, salvo a lista
fechada de `tests/hooks/golden_differences.py`.

## De onde veio

O núcleo saiu do `brz-anderson/brza-workspace` em 2026-10-31, pelo PLN-006 de lá. O `brza-workspace` é o primeiro
workspace que o segue pelo `brza sync`.
