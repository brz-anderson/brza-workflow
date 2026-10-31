# work/ e wiki/

**A norma de escrita das notas do brza-workspace** — o que está em andamento, o que trava, o que foi decidido e como
Anderson usa as ferramentas —, para quem escreve em `work/` ou em `wiki/`, pessoa ou agente. Adaptada da norma do
workspace da Fixfy, sem o que é só de lá.

> [!IMPORTANT]
>
> **A pasta é o gênero; o estado é o `status`**
>
> Cada assunto é **um arquivo**, e a pasta em que ele está diz **que tipo de coisa ele é**, nunca em que pé está. O
> estado vive no frontmatter, e por isso **nada migra de pasta**: a issue nasce e morre em `work/issues/`, a decisão
> vai de `open` a `done` sem sair de `work/decisions/`.

Este é o único arquivo de norma das duas zonas. Norma do código fica no repositório dele, o `brza-studio`.

---

## Índice

- [As duas zonas](#as-duas-zonas)
- [Onde escrever o quê](#onde-escrever-o-quê)
- [Como escolher a pasta](#como-escolher-a-pasta)
- [Começo rápido](#começo-rápido)
- [Como funciona](#como-funciona)
- [As propriedades](#as-propriedades)
- [A norma de cada pasta](#a-norma-de-cada-pasta)
  - [`work/issues/`](#workissues)
  - [`plans/`](#plans)
  - [`work/worklogs/`](#workworklogs)
  - [`work/reviews/`](#workreviews)
  - [`work/decisions/`](#workdecisions)
  - [`work/notes/`](#worknotes)
  - [`wiki/guides/`](#wikiguides)
- [Como escrever uma nota](#como-escrever-uma-nota)
- [O que não entra aqui](#o-que-não-entra-aqui)

---

## As duas zonas

| Zona | O que guarda |
| :--- | :----------- |
| `work/` | O trabalho no BrzA Studio e as decisões de contexto de trabalho |
| `wiki/` | O conhecimento consolidado, que não tem fim previsto |

**Os planos moram em `plans/`, ao lado de `work/`**, com norma própria no [`plans/README.md`](../plans/README.md).

**A fronteira é por gênero, não por maturidade.** O teste é *isto é trabalho ou decisão sobre o trabalho, ou é
conhecimento que vale enquanto valer?* Lida como maturidade — "quando amadurece, vai para `wiki/`" —, a fronteira traria
de volta a mudança de pasta a cada mudança de estado, e todo arquivo em movimento quebra link.

---

## Onde escrever o quê

O nome da pasta é o **plural do gênero** da nota que ela guarda. **Subpasta só existe em `plans/`, uma por
plano**; no resto a pasta é plana, e o nome do arquivo carrega o que a subpasta diria: o guia do Firecrawl é
`uso-da-cli-do-firecrawl`, não `firecrawl/uso-da-cli`.

### `work/`

| Pasta | Gênero | O que guarda |
| :---- | :----- | :----------- |
| `issues/` | `issue` | Pendência, dívida, bug conhecido, investigação — inclusive resolvida |
| `worklogs/` | `worklog` | Registro de um trabalho feito, um assunto por arquivo |
| `reviews/` | `review` | Parecer sobre um artefato numa versão, citável por quem foi revisado |
| `decisions/` | `decision` | A questão e a decisão **no mesmo arquivo**, de `open` a `done` |
| `notes/` | `note` | Rascunho do dia, de onde o conteúdo é promovido a arquivo próprio |

### `wiki/`

| Pasta | Gênero | O que guarda |
| :---- | :----- | :----------- |
| `guides/` | `guide` | Como **nós** usamos uma ferramenta, API ou produto — seleção opinativa, não cobertura |

### As pastas que não existem

| Pasta | Por que não |
| :---- | :---------- |
| `archive/` | O item encerrado fica onde nasceu, com `status: done` ou `dropped`; um `archive/` seria a pasta de estado `done/` com outro nome |
| `unknowns/`, `questions/` | Já são campo: investigação é `type: unknown` em `issues/`; questão é `status: open` em `decisions/` |
| `entities/`, `syntheses/` | Nomeiam assunto ou procedência, não gênero |
| `templates/` | O único molde de nota, o do plano, mora na skill `plan-runner` (`.claude/skills/plan-runner/references/plan-template.md`), que é quem o lê |
| `sessions/`, `memories/` | O gênero é `worklogs/`; memória de agente que vale para todos vira nota do gênero dela |

Diretório novo sem decisão não nasce: a estrutura é decidida, não improvisada.

---

## Como escolher a pasta

Quatro testes, na ordem:

1. **É trabalho, decisão sobre o trabalho ou conhecimento?** → a zona.
2. **Qual o gênero?** → a pasta, pelas tabelas acima.
3. **Quem resolve é uma escolha ou uma investigação?** → escolha é `work/decisions/` com `status: open`;
   investigação é `work/issues/` com `type: unknown`. Ninguém *decide* como uma ferramenta de terceiro funciona —
   alguém *descobre*.
4. **Trava alguém?** → não muda a pasta: preenche `blocks` e `owner`, e `deadline` se houver data.

### Quando um arquivo mistura gêneros

Classifica-se pelo que o arquivo é, lido inteiro — nunca pela pasta em que estava.

- **Fundir** só questão com decisão do mesmo assunto. **Nunca** decisão com o trabalho que ela gera: são dois arquivos,
  cada um apontando o outro.
- **Questões irmãs** fundem quando uma única resposta fecha as duas; ficam separadas, e se citam, quando cada uma admite
  resposta própria.
- **Análise que alimenta decisão** classifica-se pela razão de existir: nasceu para pôr uma escolha diante de alguém, é
  decisão; nasceu para registrar trabalho feito numa data, é worklog, e a escolha tomada no caminho vai para
  `decisions/`; é pendência com escolhas substanciais e sem outro arquivo, parte em issue mais decisão.
- **Escolha escondida dentro de outro arquivo** funde com a decisão do mesmo assunto, se existir; é extraída para
  `work/decisions/`, se vale além do arquivo, deixando no lugar a linha com o link; ou fica, se for passo de execução
  do próprio trabalho, e só em issue ou plano. **Em worklog, review ou guide nenhuma questão aberta fica.**
- **A decisão extraída nasce com o status fiel ao texto de origem:** `done` só quando o texto registra a confirmação de
  quem decide, com a data; senão, `open` com `trigger`.
- **Partir** só quando a mistura de gêneros é real e grande. Seção de contexto não justifica.
- **O arquivo fundido fica com o nome do que já registra a decisão.** O mais citado só desempata fusão de questões
  ainda abertas: contagem não é estável, e o nome mais citado costuma ser o obsoleto.

---

## Começo rápido

Os comandos rodam da raiz do workspace. O checador e o `move_note.py` moram na skill `notes-checker`, em
`.claude/skills/notes-checker/`.

```bash
# O que trava agora — em qualquer pasta, porque urgência é campo
grep -rl '^blocks:' work plans wiki | xargs grep -LE '^status: (done|dropped)'

# Conferir as notas antes de encerrar
python3 .claude/skills/notes-checker/scripts/check_notes.py

# Mover ou renomear uma nota, reescrevendo os links nos dois sentidos
python3 .claude/skills/notes-checker/scripts/move_note.py <origem> <destino> --title "<H1 novo>"
```

```yaml
# Nota nova em work/issues/ — sem `type`, porque a pasta já diz que é issue
---
status: open
scope:
  - brza-studio
opened: 2026-10-27
---
```

---

## Como funciona

**Um assunto por arquivo**, em Markdown, nome em kebab-case e **único no acervo inteiro** — salvo o `README.md`, que é
a norma de cada zona, nunca nota, e repete o nome por construção. Prefixo de data `AAAA-MM-DD-` só onde a data é parte
do assunto — `worklogs/` e `notes/`. O plano leva número, não data: `PLN-001-…`.

**A imagem de uma nota fica ao lado dela**, com o nome da nota e um sufixo quando há mais de uma — `<nome>.png`,
`<nome>-escuro.png` — e o corpo a linka. É assim com o PNG da alternativa escolhida numa issue e com o print de um
worklog.

**Todo arquivo diz três coisas:** o que é, em que pé está e, quando trava, quem decide.

**Nada migra de pasta; o conteúdo, sim, é promovido.** O registro fica num lugar e o produto vai para outro: o rascunho
de `notes/` vira arquivo próprio; a escolha feita num worklog vai para `decisions/`; o limite durável que uma revisão
fixa vai para `decisions/`; o plano que atravessa sessões sai do worklog para `plans/`; a investigação respondida com
substância vira guia.

**Quando o foco de uma nota muda, o nome muda junto** — o arquivo (no plano, a pasta, e o número fica), o H1 e os links
que apontam para ela —, pelo `move_note.py`.

### Links

**Link entre notas é Markdown relativo, com o H1 da nota de destino como rótulo:**
`[Título da nota](../issues/assunto.md)`. `[[wikilink]]` não existe. Todo caminho é relativo **ao arquivo que contém o
link**, e só quando o arquivo existe em disco; nome de arquivo solto em prosa fica em crase — é citação, não link. O
que mora em outro repositório — o código do `brza-studio`, ou uma nota do workspace da Fixfy — se cita em crase, com o
caminho, e não vira link. `README.md` de repositório nunca vira link de rótulo genérico: diga de qual repositório.

**Callout** — os cinco tipos do GitHub (`NOTE`, `TIP`, `IMPORTANT`, `WARNING`, `CAUTION`), com a marca sozinha
na primeira linha, uma linha `>` vazia e o título em negrito abaixo. A linha vazia mantém o alerta se o arquivo passar
por um formatador que junta linhas.

### Commit

`work/`, `plans/` e `wiki/` são deste repositório, mas **não moram na `master`**: cada nota mora na branch do
workstream dela, `workstream/<nome>`, no worktree `.worktrees/<nome>`, como diz o `AGENTS.md`. **A nota nasce no
workstream da sessão que a escreve.** **O commit é pelo `brza note commit`**, que roda o checador, commita só os
caminhos dados e dá o push na branch do workstream, sem PR, e recusa a `master`. O commit é com a identidade do
`brz-anderson`, que vem do `env` da sessão, e o push vai pela chave `personal` do `origin`
(`git@personal:brz-anderson/brza-workspace.git`).

O `brza-studio` é outro repositório, com branch `master` e a norma dele: o plano daqui só aponta para ele.

---

## As propriedades

**Identificador em inglês, prosa em português.** Chave de campo, valor de vocabulário fechado (`status`, `type`,
`priority`, `tags`) e nome de pasta são identificador: inglês, `snake_case`, **sem acento**. Os campos de prosa —
`blocks`, `trigger`, `owner` — ficam em português, porque são frase, não símbolo.

| Propriedade | O que guarda |
| :---------- | :----------- |
| `type` | **Ausente**, quer dizer o gênero da pasta. Escrito só quando refina o caminho: `unknown`, para investigação em `issues/` |
| `status` | `open` existe e ninguém pegou · `in_progress` alguém está fazendo · `in_review` feito, esperando parecer · `done` encerrado **com** desfecho · `dropped` encerrado **sem** desfecho, por escolha. **Omitido** nos gêneros sem ciclo de vida: `guide`, `worklog` e `note` |
| `scope` | A que a nota se refere. Sempre **lista**: `brza-studio`, `workspace`, ou o que mais vier |
| `priority` | Só em `issues/`: `high` · `medium` · `low`. Opcional e sem default |
| `opened` | Quando o assunto foi aberto |
| `decided` / `decided_by` | Data da decisão e quem decidiu — par indivisível |
| `completed` | Data da conclusão |
| `owner` | Quem ainda precisa decidir |
| `blocks` | O que o item trava — é isto, e não a pasta, que marca urgência |
| `trigger` | A condição que encerra a espera, em prosa |
| `deadline` | A **data** que encerra a espera, quando existe uma de verdade. Opcional, em `issues/`, `decisions/` e `plans/` |
| `superseded_by` | A decisão que substitui esta. Supersedência é relação, não estado: a nota fica `done` |
| `tags` | Assunto transversal, e só ele: `auth` · `chat` · `design` · `infra` · `planning` · `release` · `tooling`. Opcional. É a lista do checador; tag nova entra nele antes de entrar numa nota |

`blocks` e `trigger` guardam **a primeira frase** do campo correspondente no corpo: a propriedade é resumo para
varredura. **Tag nunca espelha campo nem caminho** — `work/*`, `scope/*` e `status/*` estão proibidas — e **é atalho,
nunca inventário**: com preenchimento opcional, buscar por tag mente por omissão.

### O que o estado obriga

| Quando | Exige | Porque sem isso |
| :----- | :---- | :-------------- |
| `status: done` | `completed` — em `decisions/`, o `decided` faz o papel dele | a nota afirma que terminou e não diz quando |
| decisão tomada | `decided` **e** `decided_by` | quem decidiu sem quando é metade do registro |
| `blocks` preenchido | `owner` | pendência que trava alguém e não tem dono não é cobrável |
| decisão `open` | `trigger` **ou** `deadline` | decisão sem evento nem data fica aberta para sempre |

A exigência vale **ao criar a nota ou ao mudar o estado dela**, nunca para trás: data que não está escrita não se
infere. Neste workspace, que nasceu em 2026-10-26, toda nota datada dali em diante é cobrada, e só a que não tem data
nenhuma sai como aviso. Obriga-se o que é irrecuperável, não o que é útil — `opened`, `owner` sozinho e `tags` seguem
facultativos.

> [!NOTE]
>
> **Três campos de pressão, e a régua entre eles**
>
> `blocks` diz o que **já** está parado; `trigger`, a condição que encerra a espera; `deadline`, a data, quando há uma
> real. `priority` é julgamento, e vem por último: **data antes de evento, evento antes de rótulo.** Quando `priority`
> discordar de `blocks`, o fato manda.

> [!CAUTION]
>
> **O `status` é a fonte — e o checador o confere**
>
> O `notes-checker` **barra** nome de arquivo repetido, link relativo quebrado, `[[wikilink]]`, `status` fora do
> vocabulário, campo obrigatório ausente em nota nova ou que mudou de estado e o plano aberto fora do molde, como diz o
> [`plans/README.md`](../plans/README.md); **relata** o campo ausente no acervo anterior à regra, `type` redundante ou
> inválido, tag fora da lista, achado de parecer de `reviews/` sem evidência e a nota que linka um plano aberto e falta
> na seção dele. Os vocabulários estão no script (`.claude/skills/notes-checker/scripts/check_notes.py`) e nesta norma:
> mudou um, muda o outro no mesmo commit.
>
> **Quem escreve numa nota existente lê o frontmatter primeiro**, antes de qualquer trecho do corpo: nota `done` ou
> `dropped` recebe só emenda datada e reescrita de link; conteúdo novo vai à nota aberta que a norma ou o plano
> aponta, ou reabre esta, com `status` e `completed` acompanhando. Lida aos pedaços, a nota perde o estado.

---

## A norma de cada pasta

### `work/issues/`

Pendência, dívida, bug conhecido e trabalho a executar — inclusive o que uma decisão gerou, que aponta para ela e é
apontado de volta. Issue resolvida fica com `status: done`, `completed` e uma linha dizendo como se resolveu.

**Investigação é `type: unknown`**: a pergunta cuja resposta existe no mundo e nós não temos. Resolvida, a resposta
curta é uma linha no próprio arquivo, e a substantiva vira guia; a issue fecha apontando para ela.

**`priority` ordena a fila entre as que não travam ninguém.** Sem o campo, a issue cai no fim da fila: a ausência
distingue "ninguém avaliou" de "é média".

### `plans/`

Os planos moram em `plans/`, na raiz, e a norma deles está no [`plans/README.md`](../plans/README.md).

### `work/worklogs/`

Registro **escrito** de um trabalho feito — o que se discutiu, o que se decidiu, o que ficou aberto —, nunca
transcrição.

- **Nome `AAAA-MM-DD-assunto-em-kebab.md`**, com o assunto obrigatório.
- **Três testes de corte abrem arquivo novo:** mudou o assunto, virou outro dia, ou o que se decidiu vai para
  `decisions/`.
- **O plano de implementação aceito abre o worklog**, quando houve um: seção `## Plano aceito em AAAA-MM-DD`,
  **imutável**. Divergiu, escreve-se abaixo, jamais corrigindo o plano.
- **Plano que atravessa mais de uma sessão** é copiado para `plans/`, com os mesmos IDs, e o worklog ganha
  "promovido a PLN-NNN".
- **O que muda estado fora do workspace tem seção própria**, `## Fora do workspace`, no worklog do dia: arquivo criado
  ou alterado fora de `~/Projects/brza-workspace` e do `brza-studio`, cujo commit já é o registro, e comando que muda
  estado fora deles; leitura pura não entra. Um item por coisa, com quatro campos — **o quê e onde**; **o risco**, o
  que pode dar errado e para quem; **estado**, se segue ativo; **como desfazer**.

### `work/reviews/`

Parecer sobre um artefato **numa versão**, citável por quem foi revisado, com o H1 `REV: …` e achados `### A<n>`, cada um
com a confiança e `**Evidência:**`, e o falso também com `**Por que é falso:**` — a forma está na `plan-runner`, em
`references/conduct.md`. Fica `in_progress` enquanto é escrito e `done` quando o parecer está escrito.

**Revisão que produz limite durável extrai o limite** para `work/decisions/`, e fica só com o achado datado,
apontando para lá.

### `work/decisions/`

A questão e a decisão moram **no mesmo arquivo**: `open` enquanto espera, `done` quando decidida, `dropped` quando
deixou de importar — com a linha dizendo por que morreu.

| Enquanto `open`, o arquivo diz | Onde |
| :----------------------------- | :--- |
| Quem decide | `owner` e corpo |
| O que espera | corpo; `blocks` se já trava alguém |
| Opções, com o argumento de cada uma | corpo |
| Gatilho | `trigger` ou `deadline` — vencido, pede decisão ou `trigger` novo, nunca fica em silêncio |

| Decidida, o arquivo ganha | Por quê |
| :------------------------ | :------ |
| A decisão, numa frase, no título e no corpo | Enunciado, não histórico do debate |
| "Decidido por … em …", e `decided` e `decided_by` | Quem e quando |
| Fundamento curto, com a alternativa recusada e o motivo | Impede a reabertura por esquecimento |
| Onde mais está registrada — o commit, o PR do `brza-studio` | "Só aqui" é dívida |

- **A decisão nasce como proposta.** Quem fecha é Anderson, por frase explícita — "decidido", "fechado", "pode
  aplicar".
- **Trabalho não entra.** Diagnóstico, passos e ponteiro a corrigir vão para `work/issues/`, e os dois arquivos se
  apontam.
- **Decisão revogada, emendada ou substituída não é apagada nem movida**: ganha a nova decisão, com data, e
  `superseded_by` quando a sucessora é outro arquivo.

### `work/notes/`

Rascunho: observação solta, coisa anotada no meio de outra tarefa para não se perder. **Não é norma, não é estado e
não é o registro do dia** — nada escrito aqui está em vigor. O que já nasce com forma de assunto — título,
consequência, dono — nasce na pasta do seu gênero, não aqui.

- **Um arquivo por dia:** `AAAA-MM-DD-assunto.md`.
- **Não se move nem se apaga.** O conteúdo sai como arquivo próprio na pasta do gênero, e a linha passa a apontar para
  ele.
- **A releitura é no encerramento do dia.**

### `wiki/guides/`

Como **nós** usamos uma ferramenta, API ou produto — seleção parcial e opinativa, não cobertura. O nome do arquivo
carrega a ferramenta.

1. **Cópia da documentação oficial não entra.** O guia é o que nós escrevemos sobre o uso.
2. **Procedimento que se repete é skill**, não guia.

---

## Como escrever uma nota

1. **Escolha a pasta pelos quatro testes** — gênero, nunca assunto.
2. **Nomeie em kebab-case**, único no acervo; prefixo de data só onde a data é parte do assunto.
3. **Preencha o frontmatter que o estado pede** — `status` e `scope` quase sempre; `type` só na exceção.
4. **Diga as três coisas**: o que é, em que pé está, quem decide quando trava.
5. **Prosa no `wiki/`, enxuto no `work/`.** Tabela e lista só para o que é de fato comparável ou enumerável. Nota
   antiga muda de formato quando alguém mexe nela, nunca em lote.
6. **Aponte com link Markdown relativo**, com o H1 de destino como rótulo.
7. **Se trava alguém, preencha `blocks` e `owner`** — não mude o arquivo de lugar.
8. **Rode o `check_notes.py`** antes de commitar.

---

## O que não entra aqui

| Não entra | Onde vive |
| :-------- | :-------- |
| Norma do código do BrzA Studio | No `brza-studio`, no `AGENTS.md` dele |
| O código, o build e os testes | No `brza-studio` |
| Procedimento que se repete | Skill |
| Material de terceiros copiado | Em lugar nenhum daqui: a nota cita a fonte e guarda só o achado |
| Nota, plano ou decisão do workspace da Fixfy | No workspace da Fixfy; daqui se cita em crase, com o caminho, e não se linka |
