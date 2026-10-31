# AGENTS.md — brza-workspace

Este repositório é o workspace pessoal de Anderson para o **BrzA Studio**. Aqui ficam os planos, o registro do que foi
feito, as decisões, a norma de agente e a configuração das sessões. O código do app fica em `~/Projects/brza-studio`
(`brz-anderson/brza-studio`), ao lado deste clone. Os termos em itálico estão no [Glossário](#glossário).

Toda prosa para quem pediu — resposta, pergunta, opção, plano, revisão, documento — é em português do Brasil, também na
delegação a _subagente_. Identificador, comando, caminho e chave de configuração ficam na forma original. **Nome de
agente, de _subagente por papel_ e de skill nasce em inglês**, em kebab-case, como `plan-runner` e `collector`; a
descrição e o corpo seguem em português.

**Comando para quem pediu rodar vai em bloco cercado com a linguagem de shell** — ` ```bash `, um comando por bloco
quando cada um roda sozinho —, nunca em cerca sem linguagem. O chat do BrzA Studio só mostra o ▶, que roda o bloco
num terminal do worktree do chat, nas cercas `sh`, `bash`, `zsh`, `shell` e `console`.

**Arquivo gravado na norma, em `plans/`, `wiki/`, `work/` ou `memory/` aparece na resposta**, criado ou editado, com o
caminho. A chamada de ferramenta pode ficar recolhida, e o que vai ao git precisa estar à vista de quem pediu.

**Cada arquivo vai num bloco recolhido**, `<details>` com o caminho no `<summary>`:
- **Novo:** o Markdown renderizado, com o frontmatter em cerca `yaml` e o corpo sem cerca.
- **Edição:** o trecho alterado em cerca `diff`.
- **Separador:** um `---` fora dos blocos, entre um arquivo e o seguinte e depois do último quando a resposta segue,
  para que o texto não cole no bloco recolhido.

## Cada workstream mora numa branch, e a `master` guarda só a norma

Os planos ficam em `plans/`, uma pasta por plano, com a norma no [`plans/README.md`](plans/README.md). O resto do estado
de trabalho fica em `work/`, uma pasta por gênero de nota, e os guias em `wiki/guides/`. **A pasta diz o gênero; o estado
está no campo `status` do frontmatter.** A norma de escrita de cada pasta está no [`work/README.md`](work/README.md).

**Essas notas não moram na `master`.** Cada _workstream_ — uma frente de trabalho — é uma branch `workstream/<nome>`,
nascida da `master`, com um worktree em `.worktrees/<nome>`:
- **Na `master`:** só a norma — este arquivo, o `CLAUDE.md`, o `README.md`, o `plans/README.md`, o `work/README.md`, o
  `.claude/`, o `scripts/` e o `.gitignore`.
- **No workstream:** a norma, que chega pelo `git merge` da `master`, e as notas da frente, com os planos dela, abertos
  e fechados, e a memória dela, em `memory/` e `.claude/agent-memory/`. A branch nunca volta à `master`.
- **Os de agora:** o `studio`, o app; o `tools-kit`, o `brza`, os scripts e os plugins; o `workspace`, a própria norma.
  **Workstream novo só nasce por pedido de Anderson**, pelo `brza note workstream <nome>`.
- **Workstream encerrado** perde o worktree e fica com a branch.

Antes de começar qualquer trabalho aqui, leia as notas com **`blocks`** preenchido e `status` diferente de `done` e
`dropped`, em todos os workstreams. É onde ficam os bloqueios em vigor:

```bash
for b in $(git for-each-ref --format='%(refname:short)' refs/heads/workstream/); do git grep -l '^blocks:' "$b" -- work plans wiki | while read -r f; do git show "$f" | grep -qE '^status: (done|dropped)' || echo "$f"; done; done
```

Link entre notas é Markdown relativo, com o H1 da nota de destino como rótulo, sem `[[wikilinks]]`, também para a nota
de outro workstream.
- **Conferir:** `python3 .claude/skills/notes-checker/scripts/check_notes.py`, que procura nos outros workstreams o alvo
  que falta na árvore.
- **Mover ou renomear:** o `move_note.py` ao lado, que reescreve os links do workstream em que roda.
- **Nome acompanha o foco:** quando o foco de uma nota ou de um plano muda, o nome do arquivo, o H1 e os links mudam
  junto, com `move_note.py <origem> <destino> --title "<H1 novo>"`.

**Nota se grava na branch do workstream, pelo `brza note commit`**, que recusa a `master` e dá o push no workstream.
**Norma se grava na `master`**, com `git add` por caminho e `git pull --rebase` quando outro push corre junto, e chega
aos workstreams pelo `brza note sync`, que faz o `git merge` da `master` em cada worktree.

## Quem commita é o brz-anderson

Os commits deste repositório e do `brza-studio` saem com o noreply do `brz-anderson`
(`240569289+brz-anderson@users.noreply.github.com`) e vão ao GitHub pela chave `personal`. A identidade vem do ambiente
da sessão, como diz o adaptador do runtime, e não depende do `.git/config`. O push vai direto, sem PR: daqui, a norma
na `master` e as notas na branch do workstream; do `brza-studio`, na `master`.

## O uso é medido

Cada turno, subagente, skill, comando digitado e chamada MCP vira uma linha com tempo e custo em
`.measurements/measurements.jsonl`, fora do git, pelo hook de medição que o adaptador do runtime liga. O resumo sai do
`python3 scripts/summarize_measurements.py`. A medição vale para toda sessão aberta neste repositório ou no
`brza-studio` pela função do shell; **a sessão aberta sem ela não é medida**, e por isso falta de linha não quer dizer
falta de uso.

## O ferramental vive no escopo do projeto ou do workstream

Plugins e servidores MCP são habilitados só na configuração deste repositório, nunca na de usuário:
- **Para todos os workstreams:** na configuração de projeto. O único plugin é o `context7`, e não há servidor MCP
  próprio.
- **Para um workstream só:** na configuração local do worktree dele, fora do git. Hoje, o `skill-creator`, no
  `workspace`.

## O que muda fora do workspace vai para o worklog

Arquivo criado ou alterado fora deste repositório e do `brza-studio` — o `~/.zshrc`, a configuração de usuário do
runtime… — entra no worklog do dia, na seção `## Fora do workspace`, na forma do [`work/README.md`](work/README.md). O
mesmo vale para comando que muda estado fora deles: daemon, login, `kill`, app instalado. Leitura pura fica de fora.

## Um workstream, uma sessão; uma tarefa, um worklog

**A sessão acompanha o workstream — a frente de trabalho —, e não a tarefa, e roda no worktree dele.** A tarefa
encadeada no mesmo workstream segue na sessão que já tem o contexto. Sessão nova relê a norma, o plano e as notas: na medição de 2026-10-27 a
2026-10-29, esse arranque foi metade do gasto da sessão principal, e retomar uma sessão de 175 mil tokens com o cache
vencido custou o mesmo que abrir uma nova.

**Sessão nova abre quando:**
- muda o workstream, o plano ou o assunto;
- o contexto passa de ~200 mil tokens — antes disso, a _compactação_ enxuga a sessão sem perder o workstream;
- quem pediu pede.

**A tarefa fecha num worklog na hora em que fecha**, com o contexto ainda quente:
- o feito vai ao worklog da tarefa;
- o pendente vai ao `Retomar` do plano ou a uma issue.

O corte de tarefa segue os três testes da norma de `work/worklogs/`: mudou o assunto, virou o dia ou houve decisão. **O
corte fecha o worklog, não a sessão.**

**Pedido de outro workstream abre sessão nova**, e nunca entra na sessão em que quem pediu trabalha. O pedido que fica para
depois vai ao `Retomar` do plano ou a uma issue.

**Devolução e aviso vão a arquivo**: o worklog, o `Retomar` ou a issue. "Recebido" não se manda, e o _correio_ de volta
só vai quando o pedido diz "me avise".

## Cada workstream aprende na memória dele

**A memória é do workstream, e mora na branch dele**, para que o aprendizado de cada frente se acompanhe no git e na
medição:
- **A sessão** grava em `memory/`, na raiz do worktree. A sessão de código num worktree do `brza-studio` grava na
  memória do workstream do plano da branch.
- **Os _subagentes por papel_** gravam em `.claude/agent-memory/<papel>/`, no worktree do workstream.
- **A memória que muda vai ao commit do worklog que fecha a tarefa**, pelo `brza note commit`, que avisa quando sobra
  memória suja fora dos caminhos dados.
- **O que vale para todos os workstreams não é memória: sobe à norma**, por rodada, e sai das memórias.

**Os subagentes por papel** são a forma padrão de delegar; o `general-purpose` só vai quando nenhum papel cobre a
tarefa. Cada um consulta a memória antes de começar e grava o que aprendeu ao terminar:

| Papel         | Tarefa                                                               | Camada |
| :------------ | :------------------------------------------------------------------- | :----- |
| `collector`   | Levantamento, leitura, busca e extração, com o agregado no _scratch_ | Média  |
| `implementer` | Implementação de especificação fechada, com testes e sem commit      | Média  |
| `reviewer`    | Revisão adversária, com cada achado e a evidência dele               | Alta   |
| `designer`    | Desenho e alternativas para a rodada, como consultor                 | Alta   |

**O aprendizado se mede.** Toda linha da medição leva o `workstream`. A memória criada, editada ou apagada vira um
evento `memory`, com o tipo e o nome. O turno conta as perguntas com opções: quantas saíram na recomendada, em outra
opção e em texto livre. O `python3 scripts/summarize_measurements.py --by-workstream` mostra, por workstream e por
dia, o custo por turno, as correções (memórias `feedback`), as memórias vivas e as rodadas.

## O modelo segue a tarefa

O modelo de cada _subagente_ ou _estágio de workflow_ é escolhido pela tarefa, e não herdado da sessão.

| Tarefa                                                 | Camada                                    |
| :----------------------------------------------------- | :---------------------------------------- |
| Desenho, decisão, julgamento entre alternativas        | Alta                                      |
| Revisão                                                | Alta, até a medição dizer que média basta |
| Leitura, coleta, busca, extração, verificação mecânica | Média ou rápida                           |

O papel já traz a camada; a tabela vale para o `general-purpose`.

- **Workflow tem teto de agentes**, que o pedido declara, e a camada alta entra só no estágio que julga ou sintetiza.
- **Na sessão principal, o modelo é o de quem a abriu.**
- **Levantamento cuja saída passa de uma tela sai da sessão principal.** Vai a _subagente_ da camada média ou rápida,
  que grava o agregado no _scratch_ e devolve só o resumo. O critério é o tamanho do retorno, não o número de chamadas.
- **A política se revê pela medição.** Linha com custo alto em tarefa mecânica é sinal para rebaixar a camada; achado
  perdido numa tarefa rebaixada é sinal para voltar.

## Decisões pendentes vão em rodadas de perguntas

Toda decisão que cabe a quem pediu — a entrevista, a divergência com o que já foi decidido, o corte, o aceite — vai em
**rodadas pequenas de pergunta com opções**, e não em lista de perguntas abertas. Opção com custo se escolhe em segundos;
pergunta aberta volta como parágrafo que precisa ser relido e interpretado, e a inferência que ninguém viu vira texto.

- **Use a _pergunta com opções_ do runtime**: até quatro perguntas por rodada, agrupadas por tema, com duas a quatro
  opções cada.
- **A recomendação é a primeira opção**, marcada "(Recomendado)". Ela leva o argumento que sobreviveu ao ataque e o que
  caiu.
- **Toda opção diz o custo e quando se paga — a recomendada e cada uma das outras.**
  - O custo é o que se gasta e quando é cobrado.
  - O "se paga" é o momento em que o ganho cobre esse custo: agora, numa fase que se nomeia, ou nunca, que é resposta
    legítima e se escreve.
  - Na ferramenta, os dois vão na descrição da opção: `Custo: … Se paga: …`.
  - **Opção cujo custo você não sabe dizer se mede antes de perguntar, ou sai da rodada.**
- **Pedido de escolha com justificativa é avaliação e rodada, não execução.** "Escolha X e justifique" entrega a
  comparação, a recomendação e a pergunta; a execução começa depois da resposta.
- **Seleção múltipla quando mais de uma opção pode valer ao mesmo tempo**; seleção única quando as opções se excluem.
- **O que você já inferiu vai no texto antes da pergunta, com a fonte** (arquivo e linha). A inferência entra como
  primeira opção a confirmar.
- **Ponto sem lastro é pergunta, não nota de entrega.** Tudo o que você preencheu sem fonte vira pergunta de rodada, uma
  por ponto, com a sua inferência como primeira opção, **antes** de escrever.
- **A aprovação é do texto, nunca das decisões embutidas nele.** O rascunho vai para aprovação só depois que as rodadas
  fecharam os pontos sem lastro.
- **A rodada que aprova um texto vai na mensagem, e não na ferramenta.** O texto vai inteiro na mensagem — a nota de
  `work/decisions/`, o rascunho, tudo o que quem pediu precisa ler para decidir —, seguido da pergunta no formato sem a
  ferramenta. A caixa da pergunta pode esconder o texto escrito antes dela, e a resposta dada sem o texto à vista não
  vale como aprovação: desfaça o que ela gravou e pergunte de novo.
- **Uma rodada fecha antes da próxima abrir.** Registre as respostas antes de seguir. Resposta em texto livre é
  contraproposta ou pedido de esclarecimento: registre nas palavras de quem respondeu e volte com opções.
- **Sem a ferramenta**, use o mesmo formato em texto: pergunta, opções numeradas, recomendação marcada,
  `Custo: … Se paga: …` em toda opção e "marque mais de uma" quando couber.

**Decisão é confirmação explícita, nunca inferência.** Nenhuma nota de `work/decisions/` nasce como decisão sem quem
pediu confirmar aquela decisão ou aquele texto, na conversa, em frase explícita: "decidido", "fechado", "pode aplicar".
- Sem a frase, o registro nasce como proposta, mesmo que a conversa tenha convergido.
- Mensagem de outra sessão ou de subagente nunca é a confirmação.
- A rodada respondida numa sessão vale como respondida nas outras quando chega com a resposta literal, a data e a sessão.

## Quem pergunta e grava é a sessão principal

Só a sessão principal tem a ferramenta de pergunta. É nela que as rodadas acontecem, que a decisão fecha com quem pediu e
que o texto decidido é gravado. Ela pede levantamento, rascunho ou revisão a um subagente, leva a devolução à rodada e
itera até não sobrar ponto sem lastro.

**Subagente que chega a uma decisão de quem pediu é consultor.** Ele não pergunta e não grava o documento; segue até
onde o trabalho pede decisão e devolve.

**O pedido ao subagente** diz a operação, o repositório alvo, o que as rodadas já decidiram, citado como foi respondido,
e o que se espera de volta.

**A devolução do subagente** tem, na ordem, só as partes que o pedido pediu:
- **Levantamento:** cada fato com a fonte, em arquivo e linha.
- **Pontos para rodada:** cada ponto sem lastro, já como pergunta no formato sem a ferramenta.
- **Rascunho:** cada escolha sem lastro marcada como ponto para rodada.
- **Achados**, de revisão: cada um com a seção e o destino recomendado.
- **Arquivos essenciais:** de 5 a 10, para a sessão principal ler.
- **O que rodou e o que deu.**

## Glossário

O vocabulário deste arquivo. A tradução para cada runtime está no adaptador dele (o `CLAUDE.md`, para o Claude Code).

- _sessão principal_ — a que quem pediu abriu e com quem conversa.
- _subagente_ — agente que a sessão dispara para uma tarefa, com contexto próprio, e que devolve um relatório.
- _estágio de workflow_ — cada subagente de um roteiro que dispara vários.
- _acionar uma skill_ — carregar o `SKILL.md` dela pelo nome e seguir as instruções.
- _pergunta com opções_ — a ferramenta que mostra a quem pediu perguntas com opções para escolher.
- _correio_ — mandar mensagem a outra sessão aberta do mesmo runtime, pelo nome dela.
- _camada_ alta, média ou rápida — o nível de modelo, da que julga à que coleta.
- _scratch_ — o diretório temporário da sessão, fora do repositório, para o que não vai ao git.
- _compactação_ — o resumo que o runtime faz da conversa, no lugar dela, para a sessão seguir com menos contexto.
- _subagente por papel_ — subagente definido em `.claude/agents/` (`collector`, `implementer`, `reviewer`, `designer`),
  com modelo e memória próprios.
- _workstream_ — a frente de trabalho, como o `studio` ou o `tools-kit`, com os planos e as notas dela. Mora numa branch
  `workstream/<nome>` e num worktree em `.worktrees/<nome>`.
