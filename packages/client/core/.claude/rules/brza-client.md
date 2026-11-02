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

## O scratch e os artboards do chat do {{workspace.title}}

O núcleo diz o genérico: o _scratch_ é o diretório temporário da sessão, anunciado no arranque. O que é do app fica aqui.

- **Onde está o scratch:** o scratchpad da sessão, em `/private/tmp/claude-<uid>/…`; no chat do {{workspace.title}}, o
  `chat-scratch/<hash>` do `~/Library/Application Support/{{code.name}}/`, que entra como diretório de trabalho adicional.
- **Cada alternativa de interface é um artboard**: um `.html` no `ORCA_CHAT_SCRATCH_DIR`, na forma da skill
  `orca-chat-visuals` do app, e uma linha `::orca-visual{file="<arquivo>.html" title="A — <rótulo>"}` por alternativa,
  com a letra da opção no `title`. Até 8 por resposta.
- **Antes da rodada, a sessão confere cada artboard pelo PNG**, porque ela não vê o chat:
  `brza shot <artboard>.html <scratch>/<letra>.png --width 736`, a largura do chat, e a leitura da imagem. Coluna
  estourada, corte ou texto sobreposto se corrige no `.html` antes da rodada. Numa grade com texto que não quebra, a
  coluna é `minmax(0,1fr)` e o item tem `min-width:0`.
- **Fora do chat**, sem o `ORCA_CHAT_SCRATCH_DIR`, o artboard vai ao _scratch_ e a rodada leva o caminho dele.
