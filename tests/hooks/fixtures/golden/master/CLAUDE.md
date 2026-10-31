# Adaptador do Claude Code

A norma do workspace está no `AGENTS.md`. O Claude Code lê esse arquivo junto com este quando a opção
`claude-md-and-agents-md` está ativa. Este arquivo traz só o adaptador: traduz o vocabulário da norma e acrescenta a
mecânica própria do Claude.

## O vocabulário do `AGENTS.md` no Claude Code

| Termo do `AGENTS.md`       | No Claude Code                                                                                   |
| :------------------------- | :----------------------------------------------------------------------------------------------- |
| pergunta com opções        | `AskUserQuestion`                                                                                |
| correio                    | `SendMessage`, para o nome da sessão; `ListAgents` lista as abertas                              |
| acionar uma skill          | `Skill`, ou `/nome` digitado                                                                     |
| subagente                  | `Agent`, com o `subagent_type` do papel e o `model` pela camada                                  |
| subagente por papel        | `.claude/agents/<papel>.md`, com `memory: project`                                               |
| retomar o subagente        | `SendMessage` com o nome ou o ID do agente, que volta com o contexto dele; não abre `Agent` novo |
| estágio de workflow        | cada `agent()` de um workflow                                                                    |
| camada alta, média, rápida | Opus, Sonnet, Haiku — o `haiku` é o Haiku 5.5, pelo `env` do `settings.json`                     |
| scratch                    | o scratchpad da sessão, anunciado no arranque, em `/private/tmp/claude-501/…`; no chat do BrzA Studio, o `chat-scratch/<hash>` do `~/Library/Application Support/brza-studio/`, que entra como diretório de trabalho adicional |
| sessão nova                | `/exit` e `claude --name <nome>` no worktree do workstream, `.worktrees/<nome>`, sem `--resume`, `--continue` nem `--rc`; pelo `brza sessions`, com `--checkout .worktrees/<nome>` |
| compactação                | `/compact`, depois do `/close-task`; o runtime também compacta sozinho perto do limite da janela |

**A própria sessão abre a sessão nova, pelo `brza sessions`**, e não passa o comando a Anderson:
`brza sessions <nome> --title "<título>" --prompt "<prompt>" --checkout .worktrees/<workstream> --wait`. O título segue
o padrão "PLN-002 P7: renomear chat". O `--rename` vai quando a sessão manda ou recebe correio. O
`claude --name <nome> "<prompt>"` fica para quando o `brza sessions` não serve. **Não use `--rc`**: com ele, o prompt
entre aspas não funciona. Não use `/clear` no lugar da sessão nova, porque ele
troca o `session_id` mas mantém a conversa anterior ligada à sessão.

**O caminho do scratch vai ao subagente por um link curto.** O do chat do BrzA Studio tem espaço e um hash de 32
caracteres, que o subagente copia errado sem dar erro: grava numa pasta nova com o nome trocado, ou para achando que o
arquivo não existe. Antes de delegar, `ln -sfn "<scratch>/<pasta>" /private/tmp/<nome>`, e o pedido leva só o link.

## A configuração chega pela função do shell

O `.claude/settings.json` daqui **não é herdado** pelo `brza-studio`, que fica ao lado e não dentro deste repositório. Por
isso a função `claude` do `~/.zshrc` passa `--settings ~/Projects/brza-workspace/.claude/settings.json` e
`BRZA_WORKSPACE` sempre que o cwd está no `brza-workspace` ou no `brza-studio`, inclusive nos worktrees. Fora deles, ela
segue para a função da Fixfy. O bloco está no `README.md`, em "Funções do shell".

O chat do BrzA Studio não passa pelo zsh de login: o app roda no lugar do `claude` o `agentCmdOverrides.claude`, que é
o `~/.local/bin/claude-brza` (`scripts/brza/claude-brza`, instalado pelo `brza install --chat-command`). Ele faz o mesmo
que a função, no `brza-workspace`, no `brza-studio` e nos worktrees que o app cria em `~/orca/workspaces/…`, achados
pelo `git --git-common-dir`. A sessão aberta sem nenhum dos dois — numa IDE, ou no app com o override vazio — sobe sem
os hooks e sem a identidade abaixo.

## A memória vai ao workstream

O `brza note workstream` grava no `.claude/settings.local.json` do worktree, fora do git, o `autoMemoryDirectory`
apontando para o `memory/` dele; o `brza note memory-setup` faz o mesmo nos worktrees que já existem. O `brza tree` faz
o mesmo no worktree do `brza-studio`, apontando para o `memory/` do workstream do plano, e liga o `.claude/agent-memory`
de lá, por symlink, ao do workstream. A sessão sem esse arquivo, como a da `master`, usa a memória compartilhada do
runtime, que fica vazia.

Os subagentes por papel vêm do `.claude/agents/` daqui. No `brza-studio`, que não lê esse diretório, o `claude-brza` e
a função `claude` os passam pelo `--agents`, com o JSON que o `brza-agents` monta.

## A identidade é a do brz-anderson, pelo `env`

O `env` do `.claude/settings.json` define:
- `GIT_AUTHOR_*` e `GIT_COMMITTER_*`: `Anderson Pereira Brzezinski <240569289+brz-anderson@users.noreply.github.com>`;
- `GIT_SSH_COMMAND`: o `~/.ssh/config` padrão, onde mora o alias `personal`.

Isso vale para os commits deste repositório e do `brza-studio`, e vence o ambiente da `fixfy-claude` que uma sessão
herde. O `git var GIT_AUTHOR_IDENT` confere: deve sair o noreply do `brz-anderson`.

O `gh` usa o perfil padrão, com a conta `brz-anderson` ativa (`gh auth switch`).

## Os plugins são habilitados só aqui

O `.claude/settings.json` habilita o `context7`, para todos os workstreams. Não há `.mcp.json`. Nenhum plugin daqui vai
para a configuração de usuário.

O plugin de um workstream só vai no `.claude/settings.local.json` do worktree dele, pelo
`claude plugin install <plugin> --scope local` rodado no worktree. Hoje é o `skill-creator`, no `workspace`.
- **O CLI grava no checkout da `master`:** o `enabledPlugins` vai ao `.claude/settings.local.json` da raiz do
  repositório, embora o `~/.claude/plugins/installed_plugins.json` registre o worktree como `projectPath`. Mova a
  entrada para o arquivo do worktree e apague a da `master`.
- **Conferir:** `claude plugin list`, rodado no worktree, mostra o plugin com `Scope: local` e `enabled`.
- **O `brza note memory-setup` preserva a chave**, porque grava o `autoMemoryDirectory` com merge.

## O hook de medição

O `.claude/hooks/measure.py`, ligado no `settings.json`, grava uma linha JSON por evento em
`.measurements/measurements.jsonl`:

| Evento     | Quando                                            | O que grava                                                                                                                                         |
| :--------- | :------------------------------------------------ | :-------------------------------------------------------------------------------------------------------------------------------------------------- |
| `subagent` | `SubagentStop`, inclusive o `agent()` de workflow | duração, chamadas à API e de ferramenta, tokens por tipo, o maior contexto de uma chamada, custo estimado em US$, conta, modelo, modelo pedido, esforço, versão do plugin, workflow e fase |
| `turn`     | `Stop`, o fim de cada turno da sessão principal   | o mesmo, as skills e os agentes que rodaram no turno, e as rodadas: perguntas com opções, na recomendada, em outra opção e em texto livre          |
| `skill`    | a ferramenta `Skill`                              | o nome, os argumentos e a versão do plugin                                                                                                         |
| `command`  | `/nome` digitado, que não passa pela `Skill`      | o nome, os argumentos e a origem                                                                                                                   |
| `failure`  | `StopFailure`                                     | o erro de API que encerrou o turno, como o limite de uso                                                                                           |
| `mcp`      | toda chamada `mcp__*`                             | o servidor, a ferramenta, a duração e a falha                                                                                                      |
| `memory`   | `Stop` e `SubagentStop`, pelo retrato da memória  | a memória criada, editada ou apagada no workstream: o dono (a sessão ou o papel), o arquivo, o nome e o tipo                                        |

- **O workstream em toda linha:** sai da branch do cwd; no `brza-studio`, do plano da branch. O retrato da memória fica
  em `.measurements/memory-snapshot.json`, e o primeiro retrato de um worktree não gera evento.
- **Só o trecho novo do transcript:** retomada e turno medem só o que veio depois da última leitura.
- **Onde grava:** sempre no `.measurements/` daqui, também nas sessões do `brza-studio`. O comando acha o script pela
  `BRZA_WORKSPACE` antes da `CLAUDE_PROJECT_DIR`, que no `brza-studio` apontaria para lá.
- **Sem bloquear:** o `|| true` impede que um script ausente barre a sessão.
- **Python:** o da `BRZA_PYTHON`, ou `python3` sem ela.
- **Precisão:** a saída de tokens fica subcontada, porque o transcript guarda a contagem do início de cada mensagem.
  Entrada e cache valem, e são eles que pesam no custo.
- **Resumo:** `python3 scripts/summarize_measurements.py`, `--by-workstream` para o aprendizado por workstream e dia, e
  `--facts` para o acerto da confiança dos fatos.
- **Testes:** em `.claude/hooks/tests/`.

## A auditoria da confiança dos fatos

O `designer` e o `collector` marcam cada fato com o tipo de evidência e a confiança, como `[F3 · lido · 90]`; a regra
está nos dois papéis. O `.claude/hooks/fact_audit.py`, segundo hook do `SubagentStop`, lê as marcas da devolução — o
`SubagentHandback` — e dos arquivos que o subagente gravou, e grava no mesmo log:

| Evento         | Quando                                       | O que grava                                                         |
| :------------- | :------------------------------------------- | :------------------------------------------------------------------ |
| `fact`         | uma por marca                                | o id, o tipo de evidência, a confiança, o texto e a fonte           |
| `fact_capture` | uma por trecho lido                          | as marcas, as malformadas e a devolução sem marca                   |
| `fact_check`   | uma por fato `rodado`, `lido` ou `doc`       | o veredito do Haiku (`holds`, `fails`, `unknown`) e o `cite`        |
| `fact_audit`   | uma por auditoria                            | os fatos enviados e conferidos, o custo, a duração e o erro         |
| `fact_outcome` | pelo `scripts/fact_outcome.py`               | o desfecho que a sessão registra, com a `ref`                       |

- **É o único hook que solta processo:** com fato auditável, o `stop` grava
  `.measurements/fact-audit/<agente>-<offset>.json` e solta, sem esperar, um `claude -p --model haiku` só de leitura (`Read`, `Grep`, `Glob`, `WebFetch`; teto de
  US$ 0,25 e 10 min) que refaz a evidência. O fato `rodado` se confere pela saída gravada, sem rodar de novo.
- **Sem auditar a auditoria:** a sessão `-p` roda com `BRZA_FACT_AUDIT=1`; o `fact_audit.py` não a lê, e o `turn` dela
  sai com `fact_audit: true`, fora dos agregados do resumo.
- **O desfecho da sessão:** o fato `inferido`, abaixo de 80 ou de custo (`C…`) não vai ao Haiku. A sessão o registra
  quando a sonda ou a rodada responde, com `python3 scripts/fact_outcome.py <agente>:<F3> holds|fails "<porquê>" --ref
  <PLN-NNN/R…>`; o `close-task` lista os pendentes.

## A trava de fechamento antes do `/compact`

O resumo da compactação não grava nada em disco: a tarefa que não fechou num worklog antes dele fecha de memória, ou
não fecha. Por isso o `/compact` vem depois do **`/close-task`**, a skill que grava o worklog de hoje e o `Retomar`,
junta a memória e roda o `brza note commit`. A sessão também a aciona sozinha ao fechar uma tarefa. Quem digita o
`/compact` é Anderson, porque o modelo não o roda.

O `.claude/hooks/compact_guard.py` confere o worktree do workstream do cwd — no `brza-studio`, o do plano da branch:

| Evento         | Matcher   | O que faz                                                                                                                                  |
| :------------- | :-------- | :----------------------------------------------------------------------------------------------------------------------------------------- |
| `PreCompact`   | `manual`  | barra o `/compact` com arquivo sem commit em `work/`, `plans/`, `wiki/`, `memory/` ou `.claude/agent-memory/`, ou sem o worklog de hoje   |
| `SessionStart` | `compact` | depois de qualquer compactação, com nota sem commit, põe no contexto a ordem de atualizar o worklog a partir do resumo e fechar a tarefa  |

- **O worklog de hoje** é o arquivo de `work/worklogs/` com a data de hoje no prefixo do nome.
- **A mensagem do bloqueio** aparece para quem digitou e aponta o `/close-task`. No `brza-studio`, que não carrega as
  skills daqui, ela dá os passos.
- **Sem nada a registrar:** `/compact sem-fechar` passa, e silencia o aviso do `SessionStart` da mesma sessão por 30 min.
- **A compactação automática nunca é barrada**, porque ela pode vir para se recuperar de erro de limite, e barrá-la
  faria o pedido falhar. Só recebe o aviso depois.
- **Não confere** na `master`, fora de workstream, sem o worktree do workstream, quando o git falha nem em exceção.
- **Medida:** cada conferência vira uma linha `guard` com `kind: compact` — `passed`, `blocked`, `released`,
  `injected` —, que o resumo mostra por workstream.
- **Testes:** `.claude/hooks/tests/test_compact_guard.py`.

## O cache da sessão principal dura 1 h

O mesmo `settings.json` põe o cache da sessão principal em 1 h (`promptCacheTtl`) e o do resto — subagentes, workflows,
pedidos auxiliares — em 5 min (`subagentPromptCacheTtl`). Na medição de 2026-10-27 a 2026-10-29, o cache de 1 h custou
US$ 11 a mais na escrita e evitou US$ 19 de regravação, em 32 pausas de 5 a 60 min.

Não há trava de retomada. O `resume_guard.py` barrava o prompt que acordava uma sessão grande parada além do TTL, mas em
dois dias barrou uma vez só, e saiu do `UserPromptSubmit` com a norma de uma sessão por workstream do `AGENTS.md`.
