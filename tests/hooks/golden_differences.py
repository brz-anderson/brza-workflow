"""A lista fechada de diferenças do teste de ouro (D4/D12 do PLN-006): tudo o que `render(core, manifesto do
brza-workspace)` muda no texto da `master` do `brza-workspace@d85c783`, além de trocar os placeholders pelos valores.

Cada diferença é uma troca de texto sobre a `master` — `old` aparece uma vez no arquivo, e vira `new` —, aplicada na
ordem. O teste (`test_golden_render.py`) a aplica à cópia da `master` em `fixtures/golden/master/` e exige que o
resultado seja igual ao render do núcleo: texto que mudou sem estar aqui reprova, e entrada daqui que não casa
reprova. O `brza sync --dry-run` do P8 só pode mostrar, no `brza-workspace`, estas diferenças: o mesmo `DIFFERENCES`.

`why` é uma chave de `WHY`.
"""

from collections import namedtuple

Difference = namedtuple("Difference", "path why old new")

WHY = {
    "p4-contador": "o `brza note plan-open` no lugar da linha \"Último PLN usado\" (P4, R5.3)",
    "p4-indice": "o índice `.brza/index.jsonl` na master, o campo `description` e a exceção da norma (P4, R5, R7.3)",
    "fixfy": "as menções à Fixfy saem do núcleo (R8.2)",
    "agents-md": "o `@AGENTS.md` na primeira linha do `CLAUDE.md` e o adaptador ajustado (R4.4, D5)",
    "rules-client": "a seção vai a `packages/client/core/.claude/rules/brza-client.md` (D3)",
    "rules-workspace": "a lista de plugins e de servidores MCP vai a `.claude/rules/workspace.md` (D3)",
    "readme-local": "os workstreams de agora, a chave de acesso e o perfil do `gh` vão ao `README.md` local (D3)",
    "local": "caminho ou texto só do brza-workspace que sai do núcleo (o gate local, o caminho da raiz)",
    "tests-moved": "os testes dos hooks moram em `tests/hooks/` do brza-workflow, e não mais no workspace",
    "app-client": "o texto do app e desta máquina (o scratch do chat, os artboards) vai ao `rules/brza-client.md` do "
                  "pacote `client` (A8 do P9)",
    "norma-comum": "a norma comum se edita no `core/` do brza-workflow e chega pelo `brza sync`, e só a local se grava na "
                   "branch da norma (A3 do P9)",
    "exemplo": "o exemplo de workstream do glossário deixa de ser o do brza-workspace",
    "aspas": "a regra dos blocos de shell manda aspas em caminho e expansão, depois do `head $(which brza)` sem aspas de "
             "2026-11-02: o caminho do app tem espaço, e o bloco roda em bash",
}

DIFFERENCES = [
    # --- .claude/agents
    Difference(".claude/agents/reviewer.md", "local",
               """antes da entrega; aqui, os `unittest` de `scripts/brza/tests/` e de
  `.claude/hooks/tests/`.""",
               """antes da entrega."""),

    # --- .claude/skills
    Difference(".claude/skills/agent-instructions-writer/SKILL.md", "local",
               """ (`~/Projects/brza-workspace`)""", ""),
    Difference(".claude/skills/agent-instructions-writer/SKILL.md", "norma-comum",
               """- **A norma mora na `master`.** Leia e edite no checkout da raiz, não no worktree do
  workstream, que só recebe a norma pelo merge.
""",
               """- **O arquivo diz onde se edita.** O que o `.brza/core.lock` segue — a norma comum — se edita no `core/` do `brza-workflow` e
  chega pelo `brza sync`. O que é só deste workspace, a `.claude/rules/workspace.md` e o `README.md` local, mora na
  `master`: leia e edite no checkout da raiz, não no worktree do workstream, que só recebe a norma pelo merge.
"""),
    Difference(".claude/skills/agent-instructions-writer/SKILL.md", "norma-comum",
               """1. Grave na `master`, com `git add` por caminho — nunca `git add -A`, porque o checkout da raiz pode ter arquivo de outra
   sessão — e `git pull --rebase` antes do push.
2. Leve aos workstreams com `brza note sync`.
""",
               """1. Grave onde o arquivo mora. A norma comum, no `core/` do `brza-workflow`, e depois o `brza sync` neste workspace. A norma
   só deste workspace, na `master`, com `git add` por caminho — nunca `git add -A`, porque o checkout da raiz
   pode ter arquivo de outra sessão — e `git pull --rebase` antes do push.
2. Leve aos workstreams com `brza note sync`, que o `brza sync` já roda ao fim.
"""),
    Difference(".claude/skills/notes-checker/SKILL.md", "fixfy",
               """ Adaptada do `notes-checker` do workspace da Fixfy, sem
o que é só de lá.""", ""),
    Difference(".claude/skills/plan-runner/SKILL.md", "p4-contador",
               """**A norma é a do [`plans/README.md`](../../../plans/README.md)** — a linha "Último PLN usado", o mapa de `phase` e
`status`, as origens,""",
               """**A norma é a do [`plans/README.md`](../../../plans/README.md)** — o número pelo `brza note plan-open`, o mapa de
`phase` e `status`, as origens,"""),
    Difference(".claude/skills/plan-runner/SKILL.md", "p4-contador",
               """- **Leia a norma de planos** — o `plans/README.md`: a linha "Último PLN usado", o mapa de `phase` e `status`, o que
  fecha —, a seção de `work/worklogs/` do `work/README.md` e o molde,""",
               """- **Leia a norma de planos** — o `plans/README.md`: o mapa de `phase` e `status`, o que fecha —, a seção de `work/worklogs/` do `work/README.md` e o molde,"""),
    Difference(".claude/skills/plan-runner/SKILL.md", "p4-contador",
               """o plano dela, o worklog dela e, ao abrir `PLN`, a linha "Último PLN usado" do
   `plans/README.md`, que sobe na `master`, num commit próprio, antes de gravar o plano. O `phase` e o `status` saem do mapa de lá. Confira com o checador, de""",
               """o plano dela e o worklog dela. Ao abrir `PLN`, o número sai do `brza note
   plan-open`, que o reserva no índice da `master` antes de o plano ser gravado. O `phase` e o `status` saem do mapa
   do `plans/README.md`. Confira com o checador, de"""),
    Difference(".claude/skills/plan-runner/SKILL.md", "p4-contador",
               """com o número seguinte ao "Último PLN usado" do `plans/README.md`.""",
               """com o número que o `brza note plan-open` reservou."""),
    Difference(".claude/skills/plan-runner/references/close.md", "p4-contador",
               """- **A linha "Último PLN usado" fica como está**: o número não é reusado.""",
               """- **O número não é reusado**: o índice da `master` só cresce, e o `plan-open` numera pelo maior."""),
    Difference(".claude/skills/plan-runner/references/open-and-plan.md", "p4-contador",
               """2. **Resolva o nome.** No `PLN`, o número seguinte ao "Último PLN usado" do `plans/README.md` — releia a linha na
   `origin/master` ao gravar, porque outra sessão pode ter aberto um plano no meio, suba-a num commit de norma na
   `master` e grave o plano no worktree do workstream da sessão — e o assunto em kebab-case; no
   worklog, `AAAA-MM-DD-assunto-em-kebab.md`.""",
               """2. **Resolva o nome.** No `PLN`, `brza note plan-open <assunto-em-kebab> --title "<H1 sem o PLN-NNN>" --description
   "<uma frase>"` reserva o número no índice da `master`, mesmo com outra sessão abrindo plano junto, e imprime o
   `PLN-NNN` e o caminho; o plano se grava no worktree do workstream da sessão. No worklog,
   `AAAA-MM-DD-assunto-em-kebab.md`."""),
    Difference(".claude/skills/plan-runner/references/open-and-plan.md", "app-client",
               """   - **No chat do BrzA Studio, cada alternativa de interface é um artboard**: um `.html` no `ORCA_CHAT_SCRATCH_DIR`,
     na forma da skill `orca-chat-visuals` do app, e uma linha `::orca-visual{file="<arquivo>.html" title="A — <rótulo>"}`
     por alternativa, com a letra da opção no `title`. Até 8 por resposta.
   - **Antes da rodada, a sessão confere cada artboard pelo PNG**, porque ela não vê o chat:
     `brza shot <artboard>.html <scratch>/<letra>.png --width 736`, a largura do chat, e a leitura da imagem. Coluna
     estourada, corte ou texto sobreposto se corrige no `.html` antes da rodada. Numa grade com texto que não quebra, a
     coluna é `minmax(0,1fr)` e o item tem `min-width:0`.
   - **Fora do chat**, sem o `ORCA_CHAT_SCRATCH_DIR`, o artboard vai ao _scratch_ e a rodada leva o caminho dele.
""",
               """   - **Cada alternativa de interface é um artboard**, um `.html` no _scratch_, e a rodada leva o caminho dele. No chat
     de um app que mostre o artboard na conversa, a forma de entregá-lo e de conferi-lo está em
     `.claude/rules/brza-client.md`, quando o pacote `client` está instalado.
"""),
    Difference(".claude/skills/plan-runner/references/plan-template.md", "p4-contador",
               """> **Como usar:** crie `plans/PLN-NNN-assunto/PLN-NNN-plan.md`, com o número seguinte ao "Último PLN usado" do
> `plans/README.md`, e suba aquela linha no mesmo commit. **Apague todas as citações `>`:** neste arquivo, citação é""",
               """> **Como usar:** crie `plans/PLN-NNN-assunto/PLN-NNN-plan.md`, com o número que o `brza note plan-open`
> reservou. **Apague todas as citações `>`:** neste arquivo, citação é"""),
    Difference(".claude/skills/plan-runner/references/plan-template.md", "p4-indice",
               """> | `origin_ref`                                                                | o nome da issue ou da decisão sem `.md`; `PLN-NNN` ou `PLN-NNN/A<n>`                     | sempre, menos com `request`       |
""",
               """> | `origin_ref`                                                                | o nome da issue ou da decisão sem `.md`; `PLN-NNN` ou `PLN-NNN/A<n>`                     | sempre, menos com `request`       |
> | `description`                                                               | uma frase, o que o plano entrega; vai ao índice da master                                | sempre                            |
"""),
    Difference(".claude/skills/plan-runner/references/plan-template.md", "p4-indice",
               """> origin_ref: nome-da-issue
""",
               """> origin_ref: nome-da-issue
> description: uma frase, o que o plano entrega
"""),

    # --- AGENTS.md
    Difference("AGENTS.md", "aspas",
               """num terminal do worktree do chat, nas cercas `sh`, `bash`, `zsh`, `shell` e `console`.
""",
               """num terminal do worktree do chat, nas cercas `sh`, `bash`, `zsh`, `shell` e `console`. **Caminho e expansão (`$(…)`,
`$VAR`) vão entre aspas**: o caminho de um app, como `/Applications/BrzA Studio.app/…`, tem espaço, e o bloco
roda em bash, que divide a expansão sem aspas nele; o zsh não divide, e por isso o erro só aparece no ▶.
"""),
    Difference("AGENTS.md", "p4-indice",
               """- **Na `master`:** só a norma — este arquivo, o `CLAUDE.md`, o `README.md`, o `plans/README.md`, o `work/README.md`, o
  `.claude/`, o `scripts/` e o `.gitignore`.""",
               """- **Na `master`:** a norma — este arquivo, o `CLAUDE.md`, o `README.md`, o `plans/README.md`, o `work/README.md`, o
  `.claude/`, o `scripts/`, o `.gitignore` e o `.gitattributes` — e o índice `.brza/index.jsonl`, a única exceção:
  uma linha por plano, issue ou decisão criada ou alterada, em qualquer workstream, para que quem coordena veja todas
  as frentes num arquivo só. Só o `brza note` acrescenta, e ninguém edita."""),
    Difference("AGENTS.md", "readme-local",
               """- **Os de agora:** o `studio`, o app; o `tools-kit`, o `brza`, os scripts e os plugins; o `workspace`, a própria norma.
  **Workstream novo só nasce por pedido de Anderson**, pelo `brza note workstream <nome>`.""",
               """- **Os de agora** estão no `README.md`. **Workstream novo só nasce por pedido de Anderson**, pelo `brza note
  workstream <nome>`."""),
    Difference("AGENTS.md", "p4-indice",
               """**Nota se grava na branch do workstream, pelo `brza note commit`**, que recusa a `master` e dá o push no workstream.""",
               """**Nota se grava na branch do workstream, pelo `brza note commit`**, que recusa a `master` e dá o push no workstream;
quando o frontmatter de um plano, issue ou decisão muda, ele acrescenta a linha do índice na `master`, sem checkout.
O número do plano novo sai do `brza note plan-open`."""),
    Difference("AGENTS.md", "readme-local",
               """e vão ao GitHub pela chave `personal`. A identidade vem do ambiente
da sessão""",
               """e vão ao GitHub, com a chave de acesso que o `README.md` documenta. A identidade vem do
ambiente da sessão"""),
    Difference("AGENTS.md", "rules-workspace",
               """- **Para todos os workstreams:** na configuração de projeto. O único plugin é o `context7`, e não há servidor MCP
  próprio.
- **Para um workstream só:** na configuração local do worktree dele, fora do git. Hoje, o `skill-creator`, no
  `workspace`.""",
               """- **Para todos os workstreams:** na configuração de projeto. A lista está em `.claude/rules/workspace.md`.
- **Para um workstream só:** na configuração local do worktree dele, fora do git. A lista também está lá."""),
    Difference("AGENTS.md", "norma-comum",
               """**Norma se grava na `master`**, com `git add` por caminho e `git pull --rebase` quando outro push corre junto, e chega
aos workstreams pelo `brza note sync`, que faz o `git merge` da `master` em cada worktree.
""",
               """**A norma se grava onde o arquivo mora.** O que o `.brza/core.lock` segue — a norma comum — se edita no `core/` do
`brza-workflow` e chega a este workspace pelo `brza sync`; editá-lo aqui trava o sync seguinte. O que é só deste workspace
vai à `.claude/rules/workspace.md` ou ao `README.md` local, que se editam na `master`, com `git add` por caminho e
`git pull --rebase` quando outro push corre junto. Dali, a norma chega aos workstreams pelo `brza note sync`, que faz o
`git merge` da `master` em cada worktree.
"""),
    Difference("AGENTS.md", "exemplo",
               """como o `studio` ou o `tools-kit`, com""", """como o `app` ou o `ferramental`, com"""),

    # --- CLAUDE.md
    Difference("CLAUDE.md", "agents-md",
               """# Adaptador do Claude Code

A norma do workspace está no `AGENTS.md`. O Claude Code lê esse arquivo junto com este quando a opção
`claude-md-and-agents-md` está ativa. Este arquivo traz só o adaptador: traduz o vocabulário da norma e acrescenta a
mecânica própria do Claude.""",
               """@AGENTS.md

# Adaptador do Claude Code

A norma do workspace está no `AGENTS.md`, importado na primeira linha deste arquivo: o Claude Code o carrega em
qualquer valor de **Project instructions**, e sem duplicar quando o valor é `claude-md-and-agents-md`. Este arquivo
traz só o adaptador: traduz o vocabulário da norma e acrescenta a mecânica própria do Claude. O que é só deste
workspace está em `.claude/rules/`, que o Claude Code também carrega no arranque."""),
    Difference("CLAUDE.md", "app-client",
               """o scratchpad da sessão, anunciado no arranque, em `/private/tmp/claude-501/…`; no chat do BrzA Studio, o `chat-scratch/<hash>` do `~/Library/Application Support/brza-studio/`, que entra como diretório de trabalho adicional |""",
               """o scratchpad da sessão, anunciado no arranque; com o pacote `client`, o do chat do app está em `.claude/rules/brza-client.md` |"""),
    Difference("CLAUDE.md", "rules-client",
               """## A configuração chega pela função do shell

O `.claude/settings.json` daqui **não é herdado** pelo `brza-studio`, que fica ao lado e não dentro deste repositório. Por
isso a função `claude` do `~/.zshrc` passa `--settings ~/Projects/brza-workspace/.claude/settings.json` e
`BRZA_WORKSPACE` sempre que o cwd está no `brza-workspace` ou no `brza-studio`, inclusive nos worktrees. Fora deles, ela
segue para a função da Fixfy. O bloco está no `README.md`, em "Funções do shell".

O chat do BrzA Studio não passa pelo zsh de login: o app roda no lugar do `claude` o `agentCmdOverrides.claude`, que é
o `~/.local/bin/claude-brza` (`scripts/brza/claude-brza`, instalado pelo `brza install --chat-command`). Ele faz o mesmo
que a função, no `brza-workspace`, no `brza-studio` e nos worktrees que o app cria em `~/orca/workspaces/…`, achados
pelo `git --git-common-dir`. A sessão aberta sem nenhum dos dois — numa IDE, ou no app com o override vazio — sobe sem
os hooks e sem a identidade abaixo.

""", ""),
    Difference("CLAUDE.md", "readme-local",
               """O `env` do `.claude/settings.json` define:
- `GIT_AUTHOR_*` e `GIT_COMMITTER_*`: `Anderson Pereira Brzezinski <240569289+brz-anderson@users.noreply.github.com>`;
- `GIT_SSH_COMMAND`: o `~/.ssh/config` padrão, onde mora o alias `personal`.""",
               """O `env` do `.claude/settings.json` define `GIT_AUTHOR_*` e `GIT_COMMITTER_*`:
`Anderson Pereira Brzezinski <240569289+brz-anderson@users.noreply.github.com>`. A chave de acesso e o perfil do `gh`
estão no `README.md`."""),
    Difference("CLAUDE.md", "fixfy",
               """e vence o ambiente da `fixfy-claude` que uma sessão
herde. O `git var GIT_AUTHOR_IDENT` confere""",
               """e vence o ambiente que uma sessão herde. O `git var
GIT_AUTHOR_IDENT` confere"""),
    Difference("CLAUDE.md", "readme-local",
               """O `gh` usa o perfil padrão, com a conta `brz-anderson` ativa (`gh auth switch`).

""", ""),
    Difference("CLAUDE.md", "rules-workspace",
               """O `.claude/settings.json` habilita o `context7`, para todos os workstreams. Não há `.mcp.json`. Nenhum plugin daqui vai
para a configuração de usuário.""",
               """O `.claude/settings.json` habilita os plugins de todos os workstreams; a lista, e os servidores MCP, estão em
`.claude/rules/workspace.md`. Nenhum plugin daqui vai para a configuração de usuário."""),
    Difference("CLAUDE.md", "rules-workspace",
               """ Hoje é o `skill-creator`, no `workspace`.""", ""),
    Difference("CLAUDE.md", "tests-moved",
               """- **Testes:** em `.claude/hooks/tests/`.""",
               """- **Testes:** no `brza-workflow`, em `tests/hooks/`."""),
    Difference("CLAUDE.md", "tests-moved",
               """- **Testes:** `.claude/hooks/tests/test_compact_guard.py`.""",
               """- **Testes:** `tests/hooks/test_compact_guard.py`, no `brza-workflow`."""),

    # --- plans/README.md
    Difference("plans/README.md", "fixfy",
               """ Adaptada da norma de planos do workspace da
Fixfy, sem o que é só de lá.""", ""),
    Difference("plans/README.md", "p4-contador",
               """**Último PLN usado:** 007

""", ""),
    Difference("plans/README.md", "p4-contador",
               """O número é sequencial e não se reusa. A linha "Último PLN usado" acima sobe na `master`, num commit de norma, antes de o plano ser gravado |""",
               """O número é sequencial e não se reusa: sai do `brza note plan-open <slug> --title … --description …`, que o reserva no índice da `master` antes de o plano ser gravado |"""),

    # --- work/README.md
    Difference("work/README.md", "fixfy",
               """ Adaptada da norma do
workspace da Fixfy, sem o que é só de lá.""", ""),
    Difference("work/README.md", "fixfy",
               """, ou uma nota do workspace da Fixfy""", ""),
    Difference("work/README.md", "p4-indice",
               """e recusa a `master`. O commit é com a identidade do""",
               """e recusa a `master`; quando o frontmatter, o H1 ou o
caminho de um plano, issue ou decisão muda, ele acrescenta a linha do índice `.brza/index.jsonl` na `master`, sem
checkout. O commit é com a identidade do"""),
    Difference("work/README.md", "readme-local",
               """e o push vai pela chave `personal` do `origin`
(`git@personal:brz-anderson/brza-workspace.git`).""",
               """e o push vai pelo `origin`, com a chave de acesso que o
`README.md` documenta."""),
    Difference("work/README.md", "p4-indice",
               """| `scope` | A que a nota se refere. Sempre **lista**: `brza-studio`, `workspace`, ou o que mais vier |
""",
               """| `scope` | A que a nota se refere. Sempre **lista**: `brza-studio`, `workspace`, ou o que mais vier |
| `description` | Uma frase: o que a nota entrega ou pede. Obrigatória em plano (`-plan.md`), issue e decisão criados a partir de 2026-11-01, e vai ao índice da `master`; nas anteriores, o índice usa o H1. O checador cobra `description-missing` |
"""),
    Difference("work/README.md", "local",
               """ou alterado fora de `~/Projects/brza-workspace` e do `brza-studio`,""",
               """ou alterado fora deste repositório e do `brza-studio`,"""),
    Difference("work/README.md", "fixfy",
               """| Nota, plano ou decisão do workspace da Fixfy | No workspace da Fixfy; daqui se cita em crase, com o caminho, e não se linka |
""", ""),
]
