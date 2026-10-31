# O pacote do cliente

O pacote `client` do `brza-workflow` liga o Claude Code ao workspace: o `claude-brza`, o `brza sessions` e o `brza
doctor`. O `brza sync` escreve este arquivo; o que é só deste workspace está em `.claude/rules/workspace.md`.

## A configuração chega pela função do shell

O `.claude/settings.json` daqui **não é herdado** pelo `{{code.name}}`, que fica ao lado e não dentro deste repositório. Por
isso a função `claude` do `~/.zshrc` passa `--settings <raiz deste workspace>/.claude/settings.json` e
`BRZA_WORKSPACE` sempre que o cwd está no `{{workspace.name}}` ou no `{{code.name}}`, inclusive nos worktrees. Fora deles, ela
segue para a função que o `~/.zshrc` já tinha. O bloco está no `README.md`, em "Funções do shell".

O chat do {{workspace.title}} não passa pelo zsh de login: o app roda no lugar do `claude` o `agentCmdOverrides.claude`, que é
o `~/.local/bin/claude-brza` (o `claude-brza` deste pacote, instalado pelo `brza install --chat-command`). Ele faz o mesmo
que a função, no `{{workspace.name}}`, no `{{code.name}}` e nos worktrees que o app cria em `~/orca/workspaces/…`, achados
pelo `git --git-common-dir`. A sessão aberta sem nenhum dos dois — numa IDE, ou no app com o override vazio — sobe sem
os hooks e sem a identidade do `CLAUDE.md`.
