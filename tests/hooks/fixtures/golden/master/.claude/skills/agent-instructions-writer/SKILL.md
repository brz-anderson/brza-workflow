---
name: agent-instructions-writer
description: >-
  Use ao mexer na norma do workspace — AGENTS.md, CLAUDE.md, README.md, plans/README.md, work/README.md, os papéis de
  .claude/agents/ — e ao revisá-la: "registre na norma", "suba isso à norma", "o agente não sabe que…", "revise o
  CLAUDE.md", "isso está duplicado?". Também quando uma memória de workstream vira regra de todos, quando um script,
  hook ou comando muda e a norma precisa acompanhar, ou quando a sessão nota que a norma diz algo que o disco desmente.
  Diz em que arquivo cada coisa mora, edita, propaga e revisa. Não use para nota de work/, plans/ ou wiki/ (vai pelo
  plan-runner ou pela norma da pasta) nem para escrever skill.
---

# agent-instructions-writer — a norma do workspace

A norma é o que um agente lê sem ser mandado: o `AGENTS.md` e o `CLAUDE.md` carregam em toda sessão, de todo
workstream, e o resto chega por ponteiro. Linha errada aqui não dá erro — o agente segue com o que leu — e linha a mais
se paga em toda sessão. Esta skill diz onde cada coisa mora, como a mudança entra e como se confere.

## Onde cada coisa mora

| Arquivo | Chega ao agente | Cabe | Não cabe |
| :------ | :-------------- | :--- | :------- |
| `AGENTS.md` | Toda sessão, em todo runtime | A norma do workspace, neutra de runtime, no vocabulário do Glossário | Mecânica de um runtime (hook, `settings.json`, nome de ferramenta) |
| `CLAUDE.md` | Toda sessão do Claude Code | O adaptador: a tabela de vocabulário e a mecânica do Claude — hooks, `settings.json`, `env`, a função do shell | Regra que vale para outro runtime também: essa sobe ao `AGENTS.md` |
| `README.md` | Só quando lido | Instalação, comandos do dia a dia, o bloco das funções do shell | Regra de comportamento do agente |
| `plans/README.md`, `work/README.md` | Quando a sessão escreve nota | A norma de escrita de cada pasta: gênero, frontmatter, forma | Regra de sessão ou de delegação |
| `.claude/agents/<papel>.md` | Quando o papel roda | O que o papel faz, a camada e o que devolve | O que vale para toda sessão |
| `.claude/skills/<nome>/` | Quando a skill é acionada | Procedimento longo, de uso ocasional | O que toda sessão precisa saber antes de agir |
| `memory/` do workstream | Na sessão do workstream | O aprendizado de uma frente, em medição | O que vale para todos os workstreams: esse sobe à norma e sai da memória |

A ordem de custo, do mais caro ao mais barato: **sempre carregado** (`AGENTS.md`, `CLAUDE.md`) → **por papel ou por
pasta** → **sob demanda** (skill, `README.md`). Cada coisa fica no nível mais baixo que ainda chega ao agente a tempo.

## Passo 0 — situar

- **O pedido é norma?** Decisão que ninguém tomou é rodada, e a nota vai a `work/decisions/`; pendência vai a
  `work/issues/`; o que muda fora do workspace vai ao worklog do dia. Só o que já está decidido entra na norma.
- **Qual arquivo**, pela tabela acima. A mesma informação em dois arquivos é duplicata: escolha um e aponte do outro, ou
  não aponte.
- **O que o arquivo diz hoje**, e as seções vizinhas: a regra nova costuma reescrever uma que já existe, e não ganhar
  seção própria. Procure os termos dela em todos os arquivos da tabela antes de escrever.
- **A norma mora na `master`.** Leia e edite no checkout da raiz (`~/Projects/brza-workspace`), não no worktree do
  workstream, que só recebe a norma pelo merge.

## Escrever

- **A forma da casa:** H2 que afirma ("Quem commita é o brz-anderson"), a frase-chave em negrito no começo do
  parágrafo, lista curta com rótulo em negrito, linhas de até 120 colunas, prosa em português e identificador na forma
  original.
- **O estado presente.** Narrativa de como se chegou à regra, a versão anterior e "antes era assim" ficam no worklog. A
  data entra quando é a fonte de uma medição que sustenta a regra ("na medição de 2026-10-27 a 2026-10-29…") ou a de
  uma citação de Anderson.
- **Regra com o porquê**, em uma frase: a regra sem motivo é seguida à letra e quebrada no caso que ela não previu.
- **Ponto sem lastro é pergunta** — texto que você preencheu sem fonte vira rodada antes de gravar, como manda o
  `AGENTS.md`, "Decisões pendentes vão em rodadas de perguntas".

## Gravar

1. Grave na `master`, com `git add` por caminho — nunca `git add -A`, porque o checkout da raiz pode ter arquivo de outra
   sessão — e `git pull --rebase` antes do push.
2. Leve aos workstreams com `brza note sync`.
3. Mostre cada arquivo na resposta, na forma do `AGENTS.md` ("Arquivo gravado na norma… aparece na resposta").
4. Quando a regra veio de uma memória de workstream, a memória sai (ou fica só com o que é da frente), e o índice
   `memory/MEMORY.md` acompanha, pelo `brza note commit` no workstream.

## Propagar — quando algo que a norma cita muda

Um script ganhou flag, um hook mudou de evento, uma pasta mudou de nome, uma decisão trocou uma regra:

1. **Ache tudo o que cita a coisa**, por nome, caminho e termo, em todos os arquivos da tabela, inclusive
   `.claude/skills/*/` e `.claude/agents/`: `git grep -n '<termo>' -- '*.md' .claude`.
2. **Ajuste cada citação**, ou tire a que deixou de valer.
3. **Sem nada a ajustar, diga**: "nenhuma linha da norma cita X" é resultado, e poupa a próxima busca.

## Revisar

Revisar compara a norma com ela mesma e com o disco, e só aponta. **Leia o arquivo inteiro, linha a linha, como quem
vai segui-lo**: a frase que não fecha, a regra que depende de algo que ninguém versionou e o comando que funciona só
numa das branches são achados tão bons quanto os da régua abaixo, que é o mínimo, e não o roteiro.

- **Siga o ponteiro até onde ele leva**, também para fora do repositório — `~/.claude/settings.json`, `~/.zshrc`, o
  `brza-studio` —, só lendo. "Não conferi" é a última saída, e vem com o porquê.
- **Diga o que quebra**: para cada achado, o que o agente faz de errado por causa dele, e se falha em silêncio.
- **A resposta segue as perguntas do pedido**, uma seção por pergunta, em lista com o `arquivo:linha`. Tabela só com
  célula curta; o argumento longo não cabe numa célula que o chat corta.

A régua:

- **Ponteiro morto** — arquivo, seção, skill ou comando citado que não existe. Confira em disco: `ls`, `git grep`,
  `<comando> --help`.
- **Comando que não roda**, flag que o script não aceita mais.
- **Duplicata** — a mesma regra no `AGENTS.md` e no `CLAUDE.md`, ou na norma e numa skill. Uma das duas sai.
- **Mecânica de runtime no `AGENTS.md`**, ou norma neutra presa no `CLAUDE.md`.
- **Norma desatualizada pelo disco** — a contagem, a lista ("o único plugin é…", "os workstreams de agora…") que o
  disco já desmente.
- **Custo** — caracteres ÷ 4 dá a ordem de grandeza em tokens. Aponte o que pode descer um nível na ordem de custo.
- **Histórico dentro da regra** — "antes", "passou a", narrativa de sessão.

## Invariantes

- **Todo ponteiro existe e todo comando roda**, conferidos em disco antes de entregar, com o que rodou dito na resposta.
- **O que carrega sempre paga em toda sessão**: cada linha do `AGENTS.md` e do `CLAUDE.md` precisa ganhar o lugar.
- **A norma não decide sozinha**: o que entra já foi decidido por Anderson, na conversa ou numa nota de
  `work/decisions/`. Quando o pedido contraria uma escolha registrada antes — numa memória, numa decisão, num worklog
  —, cite a escolha anterior na resposta, com a data, para que a troca seja vista.
