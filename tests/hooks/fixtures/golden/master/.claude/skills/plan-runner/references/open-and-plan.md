# O fluxo principal — abrir, entrevistar, desenhar, planejar

Quatro etapas, nesta ordem, e no `PLN` cada uma pode ser o ponto em que o plano para por uma sessão. O que decide a
ordem é que cada etapa depende da anterior: não há passo sem desenho quando o gatilho do Desenho vale, e não há desenho
sem saber o que foi pedido. Antes delas, os Passos 1 e 2 da `SKILL.md` já disseram que é plano e onde ele mora.

**No destino worklog, nada vai a `work/` antes do aceite.** Plano recusado ou reescrito antes do aceite não entra no
worklog: o rascunho e as rodadas ficam na sessão — no scratchpad, para sobreviver a uma compactação da conversa — e
entram junto com o plano aceito, na seção datada que o abre. No `PLN`, cada etapa vai ao plano quando fecha.

## Abrir

1. **Confirme que é plano e onde ele mora**, pelos Passos 1 e 2 da `SKILL.md`.
2. **Resolva o nome.** No `PLN`, o número seguinte ao "Último PLN usado" do `plans/README.md` — releia a linha na
   `origin/master` ao gravar, porque outra sessão pode ter aberto um plano no meio, suba-a num commit de norma na
   `master` e grave o plano no worktree do workstream da sessão — e o assunto em kebab-case; no
   worklog, `AAAA-MM-DD-assunto-em-kebab.md`.
3. **Escreva o frontmatter, o `Retomar`, o `## Pedido` e o `## Worklogs`** pelo molde, com o pedido nas palavras de quem
   pediu e a linha da sessão que abre. `phase: interview` quando há rodada a fazer; `phase: design` quando a origem já
   traz o requisito fechado e falta o como; nas duas, `status: open`, o `origin` da origem e o `origin_ref`, salvo com
   `origin: request`, que não o leva. No worklog, o plano aceito não leva frontmatter nem `Retomar`.
4. **Rascunhe a Cobertura e o Fora de escopo** com os itens do pedido: uma linha por item, com o critério de aceite, o
   passo e o teste.
5. **Grave**, pela seção "Quem grava" da `SKILL.md`. No `PLN`, a abertura vai já: é ela que reserva o número.

## Entrevistar

A entrevista fecha as lacunas antes de alguém escrever. **Até cinco rodadas por lacuna**; marcador no texto é o último
recurso, não o primeiro. As rodadas seguem "Como perguntar", na `SKILL.md`.

- **Levante antes de perguntar.** O que o repositório e as notas já respondem não se pergunta: leia o código do
  `brza-studio`, as decisões de `work/decisions/` e os planos vizinhos, e leve a inferência ao texto, com a fonte, como
  primeira opção a confirmar. Levantamento pesado vai a um subagente (o laço da `SKILL.md`).
- **O que costuma faltar** num pedido de app: o comportamento nos dois sistemas (macOS e Windows) quando importa, o que
  acontece sem rede ou com o agente parado, a referência visual, o padrão ligado ou desligado, e o que fica de fora.
- **Critério de aceite que falta é derivado aqui como proposta**: o critério é uma frase que se confere — o que se vê ou
  o que o teste afirma —, e Anderson confirma antes de ele entrar na Cobertura.
- **Cada rodada vai ao plano ao fechar**, com o "Apresentado antes", cada opção com o custo e quando se paga, e a
  resposta. Resposta em texto livre vai entre aspas, com a leitura que você fez dela e o que acontece em seguida.

Ao terminar, devolva em uma lista o que continua aberto, separado em **estrutura** (bloqueia o desenho) e **texto**
(vira marcador no plano).

## Desenhar

**Confira o gatilho** — o molde lista as condições que abrem o Desenho: bifurcação que precisa ser vista para ser
escolhida, mudança que toca mais de uma parte do app, remodelagem do que já existe. Sem Desenho, a ordem dos passos é a
do pedido, e o plano segue para Planejar.

1. **Peça o Terreno a um subagente**, que devolve o que existe — a norma, o código, o que outros trabalhos já decidiram —
   com fonte (`arquivo:linha`) e os arquivos essenciais. Ele não grava nada.
2. **Peça os itens `D` no molde, com o artefato escrito**, no mesmo laço. Bifurcação com três ou mais alternativas
   defensáveis ativa os consultores em paralelo, com foco; cada alternativa volta com custo e momento em que se paga.
3. **Leve as alternativas a uma rodada na mensagem, em texto**, no formato sem a ferramenta, lado a lado sobre os
   mesmos casos, com a tabela e a recomendação marcada: a caixa da pergunta com opções esconde o artefato que precisa
   estar à vista para escolher. Quem escolhe é Anderson. Opção que muda o que se vê vem com a comparação à vista:
   - **No chat do BrzA Studio, cada alternativa de interface é um artboard**: um `.html` no `ORCA_CHAT_SCRATCH_DIR`,
     na forma da skill `orca-chat-visuals` do app, e uma linha `::orca-visual{file="<arquivo>.html" title="A — <rótulo>"}`
     por alternativa, com a letra da opção no `title`. Até 8 por resposta.
   - **Antes da rodada, a sessão confere cada artboard pelo PNG**, porque ela não vê o chat:
     `brza shot <artboard>.html <scratch>/<letra>.png --width 736`, a largura do chat, e a leitura da imagem. Coluna
     estourada, corte ou texto sobreposto se corrige no `.html` antes da rodada. Numa grade com texto que não quebra, a
     coluna é `minmax(0,1fr)` e o item tem `min-width:0`.
   - **Fora do chat**, sem o `ORCA_CHAT_SCRATCH_DIR`, o artboard vai ao _scratch_ e a rodada leva o caminho dele.
4. **Grave o vigente**, com a origem de cada item. A alternativa que perdeu fica até o passo que a transcreve e então
   sai, com uma linha **Descartada** e o porquê. No `PLN`, a crítica posterior vai para a Entrevista, e o item é
   reescrito no lugar. A alternativa de interface escolhida vira PNG na pasta do plano, para sobreviver ao scratch:
   `brza shot <artboard>.html plans/PLN-NNN-…/PLN-NNN-<assunto>.png`, e o item `D` linka a imagem. **A escolhida que vai a
   uma issue**, e não a um plano, deixa o PNG ao lado dela, `work/issues/<nome-da-issue>.png`, e o corpo da issue linka
   a imagem.
5. **Aplique o reverso**: item que vira regra durável do `brza-studio` sai para a norma dele, e o plano cita. Sem isso o
   Desenho vira uma segunda norma.

**Consultor**, o subagente devolve o Terreno e os itens `D` no molde — a origem de cada item, as alternativas com custo
e quando se paga, cada ponto sem lastro marcado no lugar em que aparece —, e nada vai ao plano.

**O `PLN` pode parar aqui** — a proposta que espera decisão: `phase: design`,
`ready: não — aguarda decisão de <quem> sobre D2`. Sai do Desenho ao virar decisão, passos ou norma do `brza-studio`, ou
ao ser rejeitado, com `status: dropped`, `phase: dropped` e a linha do porquê em `## Worklogs`. O plano de worklog não
para no Desenho: ou é aceito, ou não entra em `work/`.

## Planejar

1. **Derive os passos dos itens `D`**, cada um citando o que executa. Sem Desenho, siga a ordem do pedido.
2. **Escolha a forma de cada passo**: detalhada quando toca código — os arquivos, o porquê que o `D` não diz, os
   subpassos com o teste que falha antes, o comando e o commit, a linha de evidência —; curta quando é uma rodada ou um
   registro. **Código no passo é instrução, não fonte**: o trecho que o passo pede — a assinatura, o teste que falha
   antes, o corpo quando o `D` o fixou — vale até o commit que o fecha, e dali em diante o disco é a verdade.
3. **Nomeie o executor**: a sessão que conduz, para o que é nota e rodada; **a sessão do `brza-studio`**, para o passo de
   código — aberta em `~/Projects/brza-studio`, num worktree em `.worktrees/`, na branch `<tipo>/pln-NNN-<slug>`, uma
   sessão por entrega (`plans/README.md`, "Branch"); um subagente, para leitura e rascunho; **Anderson**, para o que só
   se prova no aparelho dele.
4. **Feche a Cobertura**: toda linha com passo e teste, no nível que prova o critério — unidade, ou conferência manual no
   app. Linha da Origem leva o item do pedido (`Pedido 5`) ou a rodada (`R3.2`).
5. **Escreva o que tem gatilho, quando ele vale**: os Pré-requisitos como assinatura, as Restrições particulares deste
   trabalho, a Janela quando há prazo — meta, prazo e os cortes previstos —, e a Regressão quando o trabalho altera
   comportamento que já existe — as duas seções estão no molde. **A estimativa em horas não tem gatilho: todo passo que
   executa algo** — código, documento, protótipo, revisão — **leva a linha `Estimado`**, e as três horas do frontmatter
   são somadas dela. Passo de rodada ou "a pedido" não leva.
6. **Leve o corte a uma rodada** — aceitar, juntar, dividir — e só então grave. **A opção que aceita é o aceite do
   plano**, com a data da rodada; frase de aprovação na conversa conta igual, citada. O commit fecha a cada passo verde,
   então o corte em passos é também o corte em commits.

`ready: sim` quando todo pré-requisito existe em disco. No `PLN`: `phase: execution`, `status: in_progress`. No destino
worklog, o aceite abre o worklog com o plano dentro, sem frontmatter.
