> **Molde de plano da `plan-runner`.** A norma está no `plans/README.md`, e as propriedades comuns a toda nota, no
> `work/README.md`.
>
> **Como usar:** crie `plans/PLN-NNN-assunto/PLN-NNN-plan.md`, com o número seguinte ao "Último PLN usado" do
> `plans/README.md`, e suba aquela linha no mesmo commit. **Apague todas as citações `>`:** neste arquivo, citação é
> sempre instrução. Seção marcada "gatilho" só existe quando o gatilho vale; as outras existem sempre. Seção sem
> conteúdo é apagada, nunca deixada com "N/A".
>
> **Título:** o estado a alcançar, não a tarefa — "O chat bifurca a conversa na mensagem escolhida", não "Mexer no
> chat".

> **Frontmatter — só no `-plan.md`.** Os arquivos `-interview` e `-design` não levam. Chave em inglês, valor de
> vocabulário fechado em inglês, texto livre em português (`work/README.md`, "As propriedades").
>
> | Campo                                                                       | Valor                                                                                    | Quando                            |
> | :-------------------------------------------------------------------------- | :--------------------------------------------------------------------------------------- | :-------------------------------- |
> | `status`                                                                    | o do mapa abaixo                                                                         | sempre                            |
> | `phase`                                                                     | `interview` · `design` · `execution` · `review` · `blocked` · `closed` · `dropped`       | sempre                            |
> | `origin`                                                                    | `issue` · `decision` · `plan` · `request`                                                | sempre                            |
> | `origin_ref`                                                                | o nome da issue ou da decisão sem `.md`; `PLN-NNN` ou `PLN-NNN/A<n>`                     | sempre, menos com `request`       |
> | `ready`                                                                     | `sim`, ou `não — o que falta, e o que isso trava`; entre aspas quando o texto tem `: `   | sempre                            |
> | `estimate_h` · `executed_h` · `remaining_h`                                 | número, ponto decimal, sem unidade                                                       | quando algum passo tem `Estimado` |
> | `scope` · `opened` · `owner` · `tags` · `blocks` · `deadline` · `completed` | como em toda nota                                                                        | como em toda nota                 |
>
> | `phase`               | `status`                                          |
> | :-------------------- | :------------------------------------------------ |
> | `interview`, `design` | `open`                                            |
> | `execution`           | `in_progress`                                     |
> | `review`              | `in_review`                                       |
> | `blocked`             | `open`, com `blocks` e `owner`                    |
> | `closed`              | `done`, com `completed`                           |
> | `dropped`             | `dropped`, com a linha do porquê em `## Worklogs` |
>
> **A origem é uma só**: quando o pedido nasce de mais de uma coisa, vale a ordem `issue` > `plan` > `decision` — com
> duas do mesmo tipo, a primeira do texto —, e as outras ficam na linha `- **Origem:**` do Pedido — a decisão também em
> `## Decisões`. Plano que veio de outro workspace é `request`, com a procedência na linha `- **Origem:**`.
>
> **`ready`, `phase` e as três horas são reescritos no lugar** a cada passo fechado e no fim de sessão. `estimate_h` é a
> soma do `Estimado` de todos os passos; `executed_h`, a soma do `Executado` dos fechados; `remaining_h`, a soma do
> `Estimado` dos abertos. O estouro aparece na linha do passo, em `Executado` maior que `Estimado`. O passo `[-]`
> descartado sai das três somas; o `Estimado` dele fica visível na própria linha, junto do motivo do descarte. Campo sem
> valor fica ausente, nunca `—` nem vazio.
>
> ```yaml
> ---
> status: in_progress
> phase: execution
> origin: issue
> origin_ref: nome-da-issue
> ready: sim
> estimate_h: 12.5
> executed_h: 3
> remaining_h: 9.5
> scope:
>   - brza-studio
> opened: AAAA-MM-DD
> owner: Anderson
> ---
> ```

# PLN-NNN: Título que enuncia o resultado

- **Retomar:** uma linha — onde parou e qual é o próximo passo
- **Dividido:** [PLN-NNN — Entrevista](PLN-NNN-interview.md) · [PLN-NNN — Desenho](PLN-NNN-design.md)

> **`Retomar` é a primeira linha abaixo do H1**, reescrito no lugar a cada sessão, nunca empilhado. O histórico é do
> `## Worklogs`. **`Dividido`** só existe quando há `-interview` ou `-design`.
>
> **Plano parado no Desenho** — a proposta que espera decisão — fica com `phase: design` e
> `ready: não — aguarda decisão de Anderson sobre D2`. Não há fase própria para isso.

## Pedido

- **Origem:** pedido livre, na sessão `<nome>` de AAAA-MM-DD | `[H1 da decisão](../../work/decisions/<nota>.md)` |
  `[H1 do plano](../PLN-NNN-assunto/PLN-NNN-plan.md)` | `[H1 da issue](../../work/issues/<nota>.md)`

> O `origin` e o `origin_ref` dizem o tipo e a chave; esta linha guarda o texto e os links.
>
> Nas palavras de quem pediu, entre aspas, sem paráfrase. É a régua da revisão no fim: o entregue é conferido contra
> isto, não contra a memória de alguém.

### Cobertura

> Uma linha por item do pedido. É o que prova que o plano cobre o que foi pedido. O nível do teste é a unidade, ou a
> conferência manual no app, com Anderson.

| Origem     | Critério de aceite     | Passo | Teste (nível)                                    |
| :--------- | :--------------------- | :---- | :----------------------------------------------- |
| `Pedido 1` | <a regra, numa oração> | P3    | `<Teste>` (unidade); conferência no app (manual) |

### Fora de escopo

> O que alguém esperaria razoavelmente deste trabalho e não vem agora, e para onde foi — outro plano, uma issue. A issue
> que nasce do corte fica aqui, com o link; a aberta durante o plano vai a `## Pendências conhecidas`.

- **O que ficou de fora.** Para onde foi.

## Janela

> **Gatilho:** o trabalho tem prazo. Sem prazo, a seção não existe.

- **Janela e meta:** de AAAA-MM-DD a AAAA-MM-DD; o que estará verdade no fim.
- **Cortes previstos:** o que cai primeiro se a janela apertar, decidido antes de apertar.

> As horas estão em cada passo (`Estimado` e `Executado`) e nas três horas do frontmatter. O plano com Janela fecha
> dizendo, em uma linha, se a meta foi cumprida — inclusive quando não foi.

## Restrições

> **Gatilho:** restrições particulares deste trabalho. As do `brza-studio` estão na norma dele; repeti-las aqui cria a
> cópia que envelhece.

- **A restrição.** O que ela proíbe, numa linha.

## Pré-requisitos

> **Gatilho:** o trabalho depende de algo que outro trabalho entrega. Escreva como assinatura, que se confere em
> segundos, e diga o que fazer quando faltar. É daqui que sai o `ready`.

## Entrevista

> **Gatilho:** houve rodada. Só cresce: rodada nova vai ao fim, nenhuma é renumerada. A pergunta é citada pela posição —
> `R3.2` é a rodada 3, pergunta 2.

### Rodada 1 — tema (enviada AAAA-MM-DD)

> **Apresentado antes:** o que foi mostrado antes das perguntas — o levantamento, a análise —, em poucas linhas.

Respondida em AAAA-MM-DD.

1. **Tema.** Opções, cada uma com o preço apresentado:
   - a primeira (recomendada) — custo: o que se gasta e quando; se paga: quando o ganho cobre o custo, ou nunca;
   - a segunda — custo: …; se paga: ….

   **Decidido — a escolhida**, com a consequência quando houver.

2. **Tema.** **Resposta:** "nas palavras de quem respondeu" — vai à rodada 2.

> Toda opção fica gravada com o preço, não só a escolhida. Decisão reaberta: a linha antiga ganha "**→ reaberta em
> R4.1**", e o item do Desenho declara a origem nova.

## Desenho

> **Gatilho:** bifurcação que precisa ser vista para ser escolhida, mudança que toca mais de uma parte do app ou
> remodelagem do que já existe.
>
> **O Desenho é o que o plano vai executar, escrito antes.** Cada item mostra o artefato como fica — o contrato em
> código, o texto da norma —, ou o trecho de hoje ao lado do que fica. Resumo de decisão não é desenho: a decisão é uma
> linha em "O que este desenho assume". **Reescrito no lugar**: mostra só o que vale; a crítica e a resposta vão para a
> Entrevista.

### O que este desenho assume

- **R1:** a decisão, numa linha.

### Terreno

> **Gatilho:** o desenho nasce de levantamento. O que existe hoje, verificável, com `arquivo:linha`, agrupado por
> assunto.

#### O assunto

- **O fato.** `caminho/arquivo:42` — o que está lá.

### D1 — O tema — `caminho/do/artefato` (P2)

- **Origem:** R1.2
- **Contexto:** o que força a decisão, em até duas linhas.
- **Protótipo:** o caminho da captura ou do arquivo — quando o item é de tela.

#### As alternativas

> **Gatilho:** bifurcação. Cada alternativa com custo, quando se paga e o que reabre, sobre os mesmos casos. Fechada a
> rodada, a que perdeu sai com uma linha **Descartada** e o porquê.

##### A1 — O nome da alternativa

- **Custo:** … · **Se paga:** … · **Reabre:** ….

#### Como fica

> O artefato escrito, como vai para o disco. O trecho é instrução, não fonte: fechado o passo, o disco é a verdade, e
> divergência é um desvio `X`.

#### Em aberto

> O que não fechou, na forma da rodada — opções, a recomendada primeiro, custo e quando se paga em cada uma.

## Passos

> **Status no checkbox:** `[x]` só com a evidência escrita e o `Executado` preenchido; `[-]` descartado, com o motivo.
>
> **A linha `Estimado` é de todo passo que executa algo** — código, documento, protótipo, revisão —, escrita ao
> planejar, e fica em parágrafo próprio, logo abaixo da linha `Executa`. `Executado` é preenchido ao fechar o passo,
> junto da evidência. Passo de rodada ou "a pedido" não leva a linha.
>
> **O executor** é a sessão que conduz, a sessão do `brza-studio`, um subagente ou Anderson. Cada passo só executa a
> pedido explícito de Anderson.

### P1 — Título do passo

Executa D1 · executor: sessão do `brza-studio`

Estimado: 3 h · Executado: — h

- [ ] **1. Teste que falha**
- [ ] **2. Implementar**
- [ ] **3. Rodar e ver passar** — o comando
- [ ] **4. Commit**

**Evidência:** `brza-studio@a1b2c3d` · `<comando dos testes>` → 42 passed

## Regressão

> **Gatilho:** o trabalho altera comportamento que já existe. Liste o que tem de continuar igual e como se confere, para
> a execução e a revisão rodarem a mesma conferência.

- **O comportamento que fica.** Como se confere: o teste, o comando ou o caminho na tela.

## Desvios

> **Gatilho:** a execução saiu do planejado. Até duas linhas por desvio. Desvio que muda um item do Desenho volta à
> Entrevista antes de seguir.

- **X1 — O que mudou.** Por quê; afeta P1 e D2.

## Worklogs

> **Sempre.** Uma linha por sessão: a data, os IDs que avançaram, o que travou e o link do worklog da sessão. A sessão
> grava o worklog antes da linha, e os dois vão no mesmo commit. Nunca repete conteúdo do worklog. O link é exigido na
> linha datada a partir do dia que o `plans/README.md` fixa; a linha anterior fica como foi escrita.

- AAAA-MM-DD — R1: escopo; D1–D3 rascunhados. Parou em R1.4.
  [H1 do worklog da sessão](../../work/worklogs/AAAA-MM-DD-<assunto>.md)

## Revisões

> **Gatilho:** houve revisão da entrega. O parecer é uma nota `work/reviews/pln-NNN-<assunto>.md`, com H1 `REV: …`, e
> achados `### A<n>` com `**Evidência:**` (a forma está na `references/conduct.md`); aqui fica uma linha por parecer.

- [REV: título do parecer](../../work/reviews/pln-NNN-assunto.md) — AAAA-MM-DD, N achados, todos com destino.

## Decisões

> **Gatilho:** nota de `work/decisions/` que o plano respondeu, abriu ou segue. Uma linha por nota, com a rodada e o
> `status`.

- [H1 da decisão](../../work/decisions/<a-decisao>.md) — R2.1, `done`.

## Pendências conhecidas

> **Gatilho:** issue aberta durante o plano — achado, sobra, desvio. O corte do pedido fica no `### Fora de escopo`, com
> o destino; o checador aceita a issue em qualquer dos dois, e no `origin_ref`.

- [H1 da issue](../../work/issues/<a-issue>.md) — de A3; o que fica pendente.

## Documentação

> **Gatilho:** o plano produz ou altera documento durável fora de `plans/` e `work/`. Só o que produz ou altera, nunca o
> que leu.

- [Título do guia](../../wiki/guides/<guia>.md) — criado em P3.
- `brza-studio/AGENTS.md`, "Seção" — emendado em P5, pela sessão do `brza-studio`.

## Casos manuais

> **Gatilho:** algum critério só se prova em aparelho. Escreva os `C` a partir do critério e peça a execução a quem tem
> o aparelho. O Observado e o veredito vão na linha do caso, e o Observado linka o print do aparelho, tirado pelo
> `brza print plans/PLN-NNN-…/PLN-NNN-C1.png` — o simulador iOS, o Android do adb ou, com `--app`, a janela do app.

| ID  | Critério                    | Passos                  | Esperado             | Observado       | Veredito      |
| :-- | :-------------------------- | :---------------------- | :------------------- | :-------------- | :------------ |
| C1  | o critério que o caso prova | o que fazer no aparelho | o que deve acontecer | o que aconteceu | passa / falha |

## Fechamento

> Marque cada item quando ele for verdade, e então mude o frontmatter para `status: done` e `phase: closed`, com
> `completed`. O plano fecha, não se apaga.

- [ ] Todo passo `[x]` com evidência, ou `[-]` com motivo.
- [ ] Todo achado com destino que existe.
- [ ] Um resumo por commit ou PR do `brza-studio` da entrega, com o link do plano: os achados e o destino de cada um.
      Trabalho só em notas de `work/` e `plans/` não tem resumo.
- [ ] O durável saiu para a casa dona e está em `## Decisões` e `## Documentação`: a decisão em `work/decisions/`, a
      norma do `brza-studio`, o guia.
- [ ] As decisões que o plano respondeu estão `done`, citando a rodada.
- [ ] `status: done`, `phase: closed` e `completed` no frontmatter.
