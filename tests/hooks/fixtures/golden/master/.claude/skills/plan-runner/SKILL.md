---
name: plan-runner
description: >-
  Use quando alguém falar em plano — "vamos planejar X", "retomar o PLN-001", "onde isso parou?" —, quando um plano de
  implementação for aceito, ou o trabalho tiver lacuna a entrevistar ou não couber numa sessão. Decide se vira worklog
  ou PLN em plans/, entrevista em rodadas, desenha, conduz e fecha. Não use para trabalho de uma sessão só e sem lacuna
  (vai direto ao worklog), conserto de uma linha nem auditoria.
---

# Plano — decidir, abrir, conduzir e fechar

Um plano responde a uma pergunta só: **como estou fazendo este trabalho**. Ele guarda o que foi perguntado e decidido,
como fica o que vai ser construído, o que fazer em que ordem e com que prova, e se o entregue cumpre. Mora em `plans/`,
na raiz do brza-workspace, e lá fica: o `PLN` fechado ganha `status: done` e continua citável por `PLN-NNN/R2.1`, depois
que o durável saiu para a casa dona — a decisão em `work/decisions/`, a norma do `brza-studio` — e o resumo foi ao commit
ou ao pull request.

Esta skill decide o conteúdo do plano e roda na sessão que conduz o trabalho, onde está a ferramenta de pergunta. É essa
sessão que grava o plano em `plans/`, só nos caminhos da tarefa dela. **O código do `brza-studio` não é gravado por
aqui**: cada passo nomeia o executor, e o passo de código roda numa sessão aberta no `brza-studio`.

**A norma é a do [`plans/README.md`](../../../plans/README.md)** — a linha "Último PLN usado", o mapa de `phase` e
`status`, as origens, o que fecha — e a do [`work/README.md`](../../../work/README.md), para as propriedades comuns, os
links, o commit e o worklog. Esta skill traz o fluxo e o molde; não repete a norma. O checador é o `notes-checker`, ao
lado desta skill.

## Como perguntar

A norma das rodadas está no `AGENTS.md` da raiz, em "Decisões pendentes vão em rodadas de perguntas" e "Quem pergunta
e grava é a sessão principal". Esta skill segue essas seções e não repete o texto delas.

## Passo 0 — situar o trabalho

- **Ache a raiz do workspace**: `git rev-parse --show-toplevel`, de dentro do `brza-workspace`. Todo caminho desta skill
  — `plans/...`, `work/...` — é relativo a ela. Sem `plans/` ali, não abra plano em caminho inventado: diga que falta a
  pasta e pare.
- **Leia a norma de planos** — o `plans/README.md`: a linha "Último PLN usado", o mapa de `phase` e `status`, o que
  fecha —, a seção de `work/worklogs/` do `work/README.md` e o molde, `references/plan-template.md`. **O molde mora só
  nesta skill**, que é quem o lê: não há cópia em `plans/` para envelhecer em outro ritmo.
- **O trabalho já tem plano?** Procure pelo assunto antes de abrir outro: `ls plans`, `grep -rli "<assunto>" plans
  work/worklogs`. Plano duplicado é o primeiro passo para dois estados do mesmo trabalho.
- **Veja o que trava agora**: as notas com `blocks` e `status` fora de `done` e `dropped`, à procura de decisão pendente
  em que o trabalho esbarra:

  ```bash
  grep -rl '^blocks:' work plans wiki | xargs grep -LE '^status: (done|dropped)'
  ```

- **Se o trabalho toca o código**, leia a norma do `brza-studio` (`~/Projects/brza-studio`, o `AGENTS.md` ou o `CLAUDE.md`
  dele) antes de desenhar: é dela que saem as restrições e as dívidas que o plano não deve reabrir.
- **Plano que já existe** — "retomar o PLN-NNN", "onde isso parou?" — pula os Passos 1 e 2 e vai a
  `references/conduct.md`, "Retomar".

## Passo 1 — isso é plano?

**Plano é o documento de um trabalho que não cabe numa sessão só.** Abre quando qualquer um destes vale:

- há **lacuna a entrevistar** antes de alguém poder escrever — requisito aberto, escolha de desenho;
- o passo **precisa de detalhamento** — arquivos nomeados, subpassos com o teste que falha antes, o comando que prova;
- uma proposta **precisa de desenho** antes de virar decisão ou código — o plano pode parar no Desenho.

Nada mais abre plano:

| Não abre plano                                  | Vai para                                                                          |
| :---------------------------------------------- | :-------------------------------------------------------------------------------- |
| Trabalho de uma sessão só, sem lacuna           | O worklog: o plano de implementação aceito abre a seção `## Plano aceito em …`    |
| Bifurcação com as opções já claras              | Uma nota em `work/decisions/`, `open`, sem desenho antes                          |
| Pergunta cuja resposta existe no mundo          | Uma issue `type: unknown` em `work/issues/`                                       |
| Auditoria de um artefato                        | Um parecer em `work/reviews/`                                                     |
| Conserto de uma linha                           | O commit                                                                          |

Quando a resposta for "não é plano", diga para onde vai e por quê. Plano de implementação já aceito não volta por este
filtro: vai ao Passo 2.

## Passo 2 — uma sessão ou várias

**O Passo 1 diz se é plano; este diz onde ele mora.** A pergunta é se o plano vai ser citado por mais de um worklog:

| O trabalho                                                                               | Onde o plano mora                                                                              |
| :--------------------------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------- |
| Fecha nesta sessão, no mesmo dia                                                         | No worklog do trabalho, que o plano aceito abre: seção nomeada, com a data do aceite, imutável |
| Atravessa sessões — não fecha no dia, ou espera resposta ou entrega de fora desta sessão | Num `PLN` em `plans/`, com o `## Worklogs` citando o worklog de cada sessão                    |

O destino vai à primeira rodada, com a sua leitura como primeira opção: é a resposta que decide onde todo o resto é
gravado. Plano que chega já aceito — aprovado por Anderson na conversa, fora das rodadas desta skill — vai ao destino
como foi aceito, com a data do aceite: a frase de aprovação, citada, conta como a seleção na rodada do corte. Plano de
worklog que passa a atravessar sessões é promovido a `PLN`: o `PLN` nasce da seção datada, copiada com os mesmos IDs, e
o worklog ganha a linha "promovido a `PLN-NNN`". Worklog sem plano continua válido e não é desta skill: ela só abre o
worklog que nasce de um plano aceito.

## Quem grava — a sessão que conduz

**Só esta skill decide o conteúdo do plano; quem grava e commita em `plans/` e `work/` é a sessão que conduz**, só nos
caminhos da tarefa, como o `work/README.md` manda em "Commit": na branch do workstream da sessão, pelo `brza note
commit`, sem PR, com a identidade do `brz-anderson`.

1. **Grave só os caminhos da tarefa**: o plano dela, o worklog dela e, ao abrir `PLN`, a linha "Último PLN usado" do
   `plans/README.md`, que sobe na `master`, num commit próprio, antes de gravar o plano. O `phase` e o `status` saem do mapa de lá. Confira com o checador, de
   qualquer diretório do workspace — `python3 ${CLAUDE_SKILL_DIR}/../notes-checker/scripts/check_notes.py` — antes de
   commitar.
2. **O que é de outra tarefa, esta sessão não executa** — outro plano, o trabalho de outra issue, o passo de código que
   roda no `brza-studio`. Grava só o pedido, numa issue ou numa linha no `Retomar` do plano dono, e a outra tarefa abre
   sessão nova. O pedido leva o passo, os arquivos, o `D` e o que as rodadas decidiram, citado como foi respondido.
3. **A rodada vai com a resposta** nas palavras de quem respondeu, a data e a sessão em que foi respondida; respondida em
   outra sessão, entra no plano sem perguntar de novo. Só a nota de `work/decisions/` passa pela confirmação de Anderson,
   na sessão que a grava.

Quando gravar está em cada operação: a rodada ao fechar, a evidência com o commit que fecha os passos, e o worklog da
sessão, a linha dele no `## Worklogs`, o `Retomar` e o `phase` no fim da sessão.

## O nome, a pasta e os IDs

**Todo `PLN` é uma pasta**, mesmo com um arquivo só — é regra de descoberta, não de tamanho: `plans/PLN-NNN-assunto/`,
com o número seguinte ao "Último PLN usado" do `plans/README.md`.

| Arquivo                | O que guarda                                                                                                                                         |
| :--------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PLN-NNN-plan.md`      | Sempre. O frontmatter — só aqui —, o `Retomar`, o Pedido, os Passos, os Desvios, as seções de ponteiro e o Fechamento: é o arquivo lido para retomar |
| `PLN-NNN-interview.md` | A Entrevista, quando o `-plan` passa de ~400 linhas                                                                                                  |
| `PLN-NNN-design.md`    | O Desenho, pelo mesmo teto; `PLN-NNN-design-<assunto>.md` quando o desenho passa de ~800 linhas ou divide por assunto                                |

O worklog é `work/worklogs/AAAA-MM-DD-assunto-em-kebab.md`, com o assunto obrigatório, pelos três testes de corte do
`work/README.md`. Nele, o plano aceito é a seção `## Plano aceito em AAAA-MM-DD`, com o Pedido, a Cobertura, a
Entrevista, o Desenho e os Passos, e os mesmos IDs do molde — sem Retomar, Janela, seções de ponteiro, Fechamento nem
frontmatter; a execução vai abaixo, em `## Execução`, e a promoção a `PLN` copia a seção sem reescrevê-la.

**Os IDs têm um prefixo por tipo**, e é isso que impede um `A1` de ter três sentidos:

| ID     | O que é                                       | Regra                                                |
| :----- | :-------------------------------------------- | :--------------------------------------------------- |
| `R3.2` | Rodada 3, pergunta 2 — o histórico da decisão | A Entrevista só cresce; nunca renumera               |
| `D3`   | Item do Desenho — o artefato vigente, escrito | Declara `origem: R3.2`; no `PLN`, reescrito no lugar |
| `P3`   | Passo                                         | Cita o `D` que executa                               |
| `X1`   | Desvio da execução                            | Curto; o passo cita o desvio                         |
| `A1`   | Achado da revisão                             | Na forma de `references/conduct.md`, "Pedir a revisão" |
| `C1`   | Caso manual                                   | Só com o gatilho do teste em aparelho                |

Citação de fora: `PLN-NNN/D3`. Decisão reaberta ganha, na linha antiga, "→ reaberta em R10.1", e o `D` passa a declarar
a origem nova.

## O laço consultor

Leitura pesada e rascunho vão a um subagente (`Agent`), para não encher o contexto da sessão: o levantamento do Terreno,
a leitura do código do `brza-studio`, o rascunho de um item `D`. O consultor devolve e não grava, e o laço é sempre o
mesmo:

1. **Chame o subagente** com o pedido — a operação, o repositório alvo, o que as rodadas já decidiram, citado como foi
   respondido, e o que se espera de volta —, os caminhos do plano e **uma entrega por chamada**. O formato da devolução
   vai no primeiro pedido: correção mandada no meio da execução não é aplicada. Leitura e coleta vão a um modelo mais leve
   (Sonnet ou Haiku); desenho e revisão, ao mais forte.
2. **Ele devolve**, na ordem e só o que o pedido pediu: **levantamento** com cada fato e a fonte (`arquivo:linha`, aberta
   nesta execução); **pontos para rodada**, cada ponto sem lastro já como pergunta com opções, `Custo: … Se paga: …` em
   toda opção; **rascunho**, com cada escolha sem lastro marcada como ponto, nunca como texto final; **arquivos
   essenciais** (de 5 a 10); e **o que rodou e o que deu**, o comando e o resultado, ou que não rodou.
3. **Faça a rodada** com os pontos, aqui, e grave-a na Entrevista.
4. **Retome o mesmo subagente** (`SendMessage`, com o contexto que ele já tem) com as respostas, enquanto sobrar ponto,
   até cinco rodadas por lacuna. Com zero pontos ele devolve "pronto para gravar", e quem grava é esta sessão.

**Consultores em paralelo, com foco**, quando o gatilho vale: bifurcação com três ou mais alternativas defensáveis vai a
dois ou três subagentes — mudança mínima, arquitetura limpa, equilíbrio —, cada um devolvendo uma alternativa com custo
e quando se paga. Você junta; **quem escolhe é Anderson**, numa rodada com as alternativas lado a lado e a recomendação
marcada.

## Operações

O fluxo principal abre, entrevista, desenha e planeja; conduzir executa, retoma e revisa; fechar confere e muda o
estado. Leia a reference da operação antes de começá-la, e o molde antes de gravar a primeira linha.

| Operação                                      | Quando                                                   | Reference                     |
| :-------------------------------------------- | :------------------------------------------------------- | :---------------------------- |
| **Abrir → entrevistar → desenhar → planejar** | Trabalho novo, ou plano parado antes dos Passos          | `references/open-and-plan.md` |
| **Conduzir** (inclui retomar e revisar)       | "Continuar", executar passos, pedir a revisão da entrega | `references/conduct.md`       |
| **Fechar**                                    | Todo passo feito e todo achado com destino               | `references/close.md`         |

## Invariantes

**Só esta skill decide o conteúdo do plano, e quem grava é a sessão que conduz, só nos caminhos da tarefa** — ver "Quem
grava". Duas mãos decidindo o mesmo plano produzem dois estados dele.

**`[x]` sem evidência não conta.** Evidência é o que outra pessoa refaz: o commit, o comando e o resultado,
`arquivo:linha`. "Os testes passam" não é evidência; `<comando que roda os testes> → 42 passed` é, e o commit vem com o
repositório: `brza-studio@a1b2c3d`. **O passo que tem `Estimado` fecha também com o `Executado`**, e `executed_h` e
`remaining_h` do frontmatter são reescritos no mesmo ato.

**A Entrevista só cresce; o Desenho é reescrito no lugar** — no `PLN`. A crítica e a resposta vão para a Entrevista, e o
Desenho mostra só o que vale; a alternativa que perdeu fica até o passo que a transcreve, e então sai com uma linha
**Descartada** e o porquê. No worklog, o plano aceito não muda: o que divergir vai abaixo dele.

**O trecho de código no plano é instrução, não fonte.** Fechado o passo, o código em disco é a verdade, e divergência
entre os dois é um desvio `X`, registrado. O plano carrega o trecho que o passo pede — a assinatura, o teste que falha
antes, o corpo quando o `D` o fixou —, e como ele fica em `plans/` depois de fechar, o passo fechado cita o commit: quem
lê o plano depois lê o código por ele.

**Passo só executa a pedido explícito de Anderson, passo a passo.** Plano revisado ou aceito não autoriza execução.

**Quem recomenda não decide.** Nem esta skill nem o consultor escolhem entre alternativas: recomendam, com o custo e
quando se paga de cada uma, e Anderson escolhe.

**O plano fecha, não se apaga.** A numeração `PLN` é sequencial e não reusada; o plano fechado fica com `status: done`,
e o abandonado, com `dropped` e a linha do porquê.

## Arquivos

| Arquivo                                                              | Quando ler                                   |
| :------------------------------------------------------------------- | :------------------------------------------- |
| `references/plan-template.md`                                        | Antes de gravar a primeira linha de um plano |
| `plans/README.md`, e a seção de `work/worklogs/` do `work/README.md` | Passo 0: o número, o mapa de `phase` e `status`, o que fecha |
| `references/open-and-plan.md`                                        | Abrir, entrevistar, desenhar, planejar       |
| `references/conduct.md`                                              | Conduzir, retomar, pedir a revisão           |
| `references/close.md`                                                | Fechar                                       |
| `../notes-checker/SKILL.md`                                          | Conferir o plano e as notas; mover o plano   |
