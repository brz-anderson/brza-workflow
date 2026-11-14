#!/usr/bin/env python3
"""Suíte do check_notes.py e do move_note.py: monta uma raiz de fixture em diretório temporário, com uma nota por regra,
e confere que cada regra acusa o que deve, no nível que deve, e que move_note.py reescreve os links relativos nos dois sentidos.

  python3 tests/notes-checker/run.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# Não deixa __pycache__ dentro da skill
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

SCRIPTS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "core", ".claude",
                                              "skills", "notes-checker", "scripts"))
CHECKER = os.path.join(SCRIPTS_DIR, "check_notes.py")
MOVER = os.path.join(SCRIPTS_DIR, "move_note.py")
sys.path.insert(0, SCRIPTS_DIR)
import check_notes as cn  # noqa: E402

NOTES = {
    'work/README.md': '# Norma das notas\n[ok-issue](issues/ok-issue.md)\n',
    'plans/README.md': '# Norma dos planos\n',
    'work/issues/ok-issue.md': '---\nstatus: open\nopened: 2026-10-27\ntags:\n  - tooling\n---\nVeja [guia](../../wiki/guides/guia.md#secao). Em código não conta: `[[fantasma-em-codigo]]` e `[fantasma](fantasma.md)`.\n',
    'wiki/guides/guia.md': '---\ntags: [design]\n---\n```\n[[fantasma-em-cerca]]\n[fantasma](nao-existe.md)\n```\n',
    'work/issues/link-quebrado.md': '---\nstatus: open\nopened: 2026-10-27\n---\n[apelido](nota-que-nao-existe.md)\n',
    'work/issues/wikilink-proibido.md': '---\nstatus: open\nopened: 2026-10-27\n---\n[[ok-issue]]\n',
    'work/issues/status-ruim.md': '---\nstatus: urgent\nopened: 2026-10-27\n---\n',
    'work/issues/repetido.md': '---\nstatus: open\nopened: 2026-10-27\n---\n',
    'wiki/guides/repetido.md': '# guia com o mesmo nome\n',
    'work/issues/feito-recente.md': '---\nstatus: done\nopened: 2026-10-27\n---\n',
    'work/issues/feito-antigo.md': '---\nstatus: done\nopened: 2026-10-20\n---\n',
    'work/issues/feito-sem-data.md': '---\nstatus: done\n---\n',
    'work/decisions/decidida-sem-autor.md': '---\nstatus: done\ndecided: 2026-10-27\n---\n',
    'work/decisions/aberta-sem-gatilho.md': '---\nstatus: open\nopened: 2026-10-27\n---\n',
    'work/issues/trava-sem-dono.md': '---\nstatus: open\nopened: 2026-10-27\nblocks:\n  - brza-studio\n---\n',
    'wiki/templates/molde-solto.md': '---\nstatus: open\n---\n# Molde\n',
    'plans/PLN-998-foco-antigo/PLN-998-plan.md': '---\nstatus: open\nopened: 2026-10-27\n---\n\n# PLN-998: O `foco` antigo\n\nVeja [PLN-998 — Entrevista](PLN-998-interview.md) e [guia](../../wiki/guides/guia.md). Fora do acervo: [arquivo](file:///tmp/fora.md).\n',
    'plans/PLN-998-foco-antigo/PLN-998-interview.md': '# PLN-998 — Entrevista\n\nDo [PLN-998: O `foco` antigo](PLN-998-plan.md). Exemplo: `[x](PLN-998-plan.md)`.\n',
    'work/issues/cita-plano.md': '---\nstatus: open\nopened: 2026-10-27\n---\n[PLN-998: O `foco` antigo](../../plans/PLN-998-foco-antigo/PLN-998-plan.md#log) e [apelido](../../plans/PLN-998-foco-antigo/PLN-998-plan.md).\n',
    'work/reviews/pln-997-revisado.md': '---\nstatus: done\nopened: 2026-10-27\ncompleted: 2026-10-27\n---\n\n# REV: Revisado\n\n### A1 — Com prova\n\n> bug · importante · → corrigido aqui\n\n**Evidência:** `x.py:1`.\n\n### A2 — Sem prova\n\n> bug · sugestão · → corrigido aqui\n\n```\n### A3 — em código\n**Evidência:** só em código\n```\n',
    'work/decisions/tipo-norma.md': '---\nstatus: open\nopened: 2026-10-27\ntrigger: "quando vier"\ntype: norm\n---\n',
    'work/worklogs/2026-10-27-dia.md': '---\nstatus: done\ncompleted: 2026-10-27\ntype: worklog\ntags:\n  - status/done\n  - inventada\n  - payment\n  - vault\n---\n',
}


def run_checker(*args, cwd=None):
    r = subprocess.run([sys.executable, CHECKER, *args], capture_output=True, text=True, cwd=cwd)
    return r.returncode, r.stdout


def write_notes(root, notes):
    for rel, content in notes.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def main():
    tmp = os.path.realpath(tempfile.mkdtemp())
    try:
        write_notes(tmp, NOTES)
        fails = (check_rules(tmp) + check_template_and_empty_root(tmp) + check_move_note(tmp)
                 + check_move_plan(tmp) + check_plans_at_root(tmp))
        fails += check_plan_rules()
        fails += check_description()
        fails += check_manifest_dates()
        fails += check_workstream_links()
        fails += check_pln008_p1()
        fails += check_pln008_p8()
        fails += check_pln012_p5()
        fails += check_feedback()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if fails else 0)


def check_rules(root):
    """Cada regra no nível certo."""
    exit_code, stdout = run_checker("--root", root, "--json")
    out = json.loads(stdout)
    errors = {(r, w) for r, w, _ in out["errors"]}
    warnings = {(r, w) for r, w, _ in out["warnings"]}
    fails = total = 0

    def expect(label, ok):
        nonlocal fails, total
        total += 1
        print(("  ok  " if ok else "FALHA ") + label)
        fails += not ok

    i = "work/issues/"
    expect("nome duplicado é erro", any(r == "duplicate-name" for r, _ in errors))
    expect("README.md de norma não é nome repetido", not any(r == "duplicate-name" and "README.md" in w for r, w in errors))
    expect("link relativo quebrado é erro", ("unresolved-link", i + "link-quebrado.md") in errors)
    expect("wikilink fora de código é erro", ("wikilink-forbidden", i + "wikilink-proibido.md") in errors)
    expect("link/wikilink em código não conta", not any("fantasma" in t for _, _, t in out["errors"]))
    expect("status fora do vocabulário é erro", ("status-out-of-vocabulary", i + "status-ruim.md") in errors)
    expect("done sem completed, datado do corte em diante, é erro", ("done-without-completed", i + "feito-recente.md") in errors)
    expect("done sem completed antes do corte é aviso", ("done-without-completed", i + "feito-antigo.md") in warnings)
    expect("done sem completed e sem data é aviso", ("done-without-completed", i + "feito-sem-data.md") in warnings)
    expect("decided sem decided_by é erro", ("decided-without-pair", "work/decisions/decidida-sem-autor.md") in errors)
    expect("decisão feita sem par é erro", ("decision-done-without-decided", "work/decisions/decidida-sem-autor.md") in errors)
    expect("decisão aberta sem trigger é erro", ("open-decision-without-trigger", "work/decisions/aberta-sem-gatilho.md") in errors)
    expect("blocks sem owner é erro", ("blocks-without-owner", i + "trava-sem-dono.md") in errors)
    w = "work/worklogs/2026-10-27-dia.md"
    expect("status em worklog é aviso", ("status-on-stateless-genre", w) in warnings)
    expect("wiki/templates/ não é gênero", not any(w_ == "wiki/templates/molde-solto.md" for _, w_ in errors | warnings))
    expect("type redundante é aviso", ("type-redundant-or-invalid", w) in warnings)
    expect("type: norm é type inválido", ("type-redundant-or-invalid", "work/decisions/tipo-norma.md") in warnings)
    expect("tag que ecoa campo é aviso", ("tag-echoes-field", w) in warnings)
    expect("tag fora do vocabulário é aviso", ("tag-out-of-vocabulary", w) in warnings)
    tags = {t for r, w_, t in out["warnings"] if r == "tag-out-of-vocabulary" and w_ == w}
    expect("as tags do workspace de origem (payment, vault) estão fora do vocabulário", {"payment", "vault"} <= tags)
    expect("as tags do vocabulário passam", not any(r == "tag-out-of-vocabulary" and w_ in (i + "ok-issue.md", "wiki/guides/guia.md")
                                                   for r, w_, _ in out["warnings"]))
    p = "work/reviews/pln-997-revisado.md"
    missing = {t for r, w_, t in out["warnings"] if r == "finding-without-evidence" and w_ == p}
    expect("achado do parecer em reviews/ sem Evidência é aviso", missing == {"A2"})
    expect("### A em código não conta", "A3" not in missing)
    expect("nota em ordem não aparece", not any(i + "ok-issue.md" in w_ for _, w_ in errors | warnings))
    expect("com erro, sai com 1", exit_code == 1)
    expect("o vocabulário não tem o que é só do workspace de origem",
           "vault" not in cn.TAGS and "payment" not in cn.TAGS and not ({"sprint", "card"} & cn.ORIGINS))

    print(f"\n{total - fails} de {total} casos passaram." if fails else f"\n{total} casos, todos passaram.")
    return fails


def check_template_and_empty_root(tmp):
    """O molde na plan-runner resolve link sem ser conferido como nota; raiz sem work/, plans/ nem wiki/ sai com 2."""
    fails = 0
    skill = os.path.join(tmp, ".claude", "skills", "plan-runner", "references")
    os.makedirs(skill)
    with open(os.path.join(skill, "plan-template.md"), "w", encoding="utf-8") as f:
        f.write("# molde\n[[placeholder-do-molde]] e [fantasma](nao-existe.md)\n")
    with open(os.path.join(tmp, "work", "issues", "aponta-o-molde.md"), "w", encoding="utf-8") as f:
        f.write("---\nstatus: open\nopened: 2026-10-27\n---\n[molde](../../.claude/skills/plan-runner/references/plan-template.md)\n")
    _, stdout = run_checker("--root", tmp, "--json")
    out = json.loads(stdout)
    if any("plan-template" in w + t or "placeholder-do-molde" in t or "aponta-o-molde" in w for _, w, t in out["errors"]):
        print("FALHA o molde da plan-runner não resolveu, ou foi conferido como nota")
        fails += 1
    empty = tempfile.mkdtemp()
    code, _ = run_checker("--root", empty)
    os.rmdir(empty)
    if code != 2:
        print(f"FALHA raiz sem work/, plans/ nem wiki/ deveria sair com 2, saiu com {code}")
        fails += 1
    if not fails:
        print("  ok  o molde da plan-runner resolve link sem ser conferido, e raiz vazia sai com 2")
    return fails


def check_default_root():
    """Sem --root, a raiz é a do repositório onde o script mora: quatro níveis acima de scripts/."""
    expected = os.path.normpath(os.path.join(SCRIPTS_DIR, "..", "..", "..", ".."))
    return 0 if cn.DEFAULT_ROOT == expected else 1


def check_move_note(tmp):
    """move_note.py move a nota e reescreve tanto os links que apontam para ela quanto os que saem dela."""
    fails = 0
    r = subprocess.run(
        [sys.executable, MOVER, "work/issues/ok-issue.md", "work/decisions/ok-decisao.md", "--root", tmp],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(f"FALHA move_note.py saiu com {r.returncode}: {r.stderr}")
        return 1
    readme_text = open(os.path.join(tmp, "work", "README.md"), encoding="utf-8").read()
    moved_text = open(os.path.join(tmp, "work", "decisions", "ok-decisao.md"), encoding="utf-8").read()
    if "[ok-issue](decisions/ok-decisao.md)" not in readme_text:
        print(f"FALHA move_note.py não reescreveu link de entrada em work/README.md: {readme_text!r}")
        fails += 1
    if "[guia](../../wiki/guides/guia.md#secao)" not in moved_text:
        print(f"FALHA move_note.py quebrou link de saída na nota movida: {moved_text!r}")
        fails += 1
    # Move para nível diferente (a raiz de plans/) para provar ajuste de profundidade nos links de saída
    r2 = subprocess.run(
        [sys.executable, MOVER, "work/decisions/ok-decisao.md", "plans/2026-01-01-solto.md", "--root", tmp],
        capture_output=True,
        text=True,
    )
    moved2_text = open(os.path.join(tmp, "plans", "2026-01-01-solto.md"), encoding="utf-8").read()
    if r2.returncode != 0 or "[guia](../wiki/guides/guia.md#secao)" not in moved2_text:
        print(f"FALHA move_note.py não ajustou profundidade do link de saída ao subir de pasta: {moved2_text!r}")
        fails += 1
    if not fails:
        print("  ok  move_note.py move a nota e reescreve links de entrada e de saída")
    return fails


def check_move_plan(tmp):
    """A pasta de plano se move inteira, e o --title troca o H1 e os rótulos iguais a ele; os outros ficam e se contam."""
    fails = 0
    r = subprocess.run(
        [sys.executable, MOVER, "plans/PLN-998-foco-antigo", "plans/PLN-998-foco-novo",
         "--title", "PLN-998: O foco novo", "--root", tmp],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(f"FALHA move_note.py com pasta de plano saiu com {r.returncode}: {r.stderr}")
        return 1
    plans = os.path.join(tmp, "plans")
    plan = open(os.path.join(plans, "PLN-998-foco-novo", "PLN-998-plan.md"), encoding="utf-8").read()
    interview = open(os.path.join(plans, "PLN-998-foco-novo", "PLN-998-interview.md"), encoding="utf-8").read()
    issue = open(os.path.join(tmp, "work", "issues", "cita-plano.md"), encoding="utf-8").read()
    expected = [
        (not os.path.exists(os.path.join(plans, "PLN-998-foco-antigo")), "a pasta antiga ficou"),
        ("# PLN-998: O foco novo\n" in plan, f"H1 não trocado: {plan!r}"),
        ("[guia](../../wiki/guides/guia.md)" in plan, f"link de saída quebrado: {plan!r}"),
        ("[arquivo](file:///tmp/fora.md)" in plan, f"link file:/// reescrito como relativo: {plan!r}"),
        ("[PLN-998: O foco novo](PLN-998-plan.md)" in interview, f"rótulo de dentro da pasta não trocado: {interview!r}"),
        ("`[x](PLN-998-plan.md)`" in interview, f"link em código inline mudou: {interview!r}"),
        ("[PLN-998: O foco novo](../../plans/PLN-998-foco-novo/PLN-998-plan.md#log)" in issue,
         f"link de entrada: {issue!r}"),
        ("[apelido](../../plans/PLN-998-foco-novo/PLN-998-plan.md)" in issue, f"rótulo diferente do H1 mudou: {issue!r}"),
        ("rótulos trocados: 2 | rótulos diferentes do H1 novo: 1" in r.stdout, f"contagem: {r.stdout!r}"),
    ]
    for ok, msg in expected:
        if not ok:
            print(f"FALHA move_note.py pasta de plano: {msg}")
            fails += 1
    # Só o H1: origem e destino iguais
    r2 = subprocess.run(
        [sys.executable, MOVER, "work/issues/cita-plano.md", "work/issues/cita-plano.md", "--root", tmp],
        capture_output=True,
        text=True,
    )
    if r2.returncode == 0:
        print("FALHA move_note.py aceitou origem igual ao destino sem --title")
        fails += 1
    r3 = subprocess.run(
        [sys.executable, MOVER, "plans/PLN-998-foco-novo", "plans/PLN-998-foco-novo",
         "--title", "PLN-998: O foco final", "--root", tmp],
        capture_output=True,
        text=True,
    )
    issue = open(os.path.join(tmp, "work", "issues", "cita-plano.md"), encoding="utf-8").read()
    if r3.returncode != 0 or "[PLN-998: O foco final](../../plans/PLN-998-foco-novo/PLN-998-plan.md#log)" not in issue:
        print(f"FALHA move_note.py --title sem mover: {r3.stderr or issue!r}")
        fails += 1
    if not fails:
        print("  ok  move_note.py move a pasta de plano e troca o H1 e os rótulos iguais a ele com --title")
    return fails


def check_plans_at_root(tmp):
    """O plano em plans/, na raiz, tem gênero e é lido pelo checador; o plans/README.md é a norma da zona, sem gênero;
    a raiz só com plans/ já tem notas; e a raiz padrão é a do repositório onde o script mora."""
    fails = 0
    with open(os.path.join(tmp, "plans", "README.md"), "w", encoding="utf-8") as f:
        f.write("# Norma dos planos\n[PLN-998](PLN-998-foco-novo/PLN-998-plan.md)\n")
    with open(os.path.join(tmp, "plans", "PLN-998-foco-novo", "PLN-998-design.md"), "w", encoding="utf-8") as f:
        f.write("# PLN-998 — Desenho\n[fantasma](nao-existe.md)\n")
    _, stdout = run_checker("--root", tmp, "--json")
    out = json.loads(stdout)
    only_plans = tempfile.mkdtemp()
    os.makedirs(os.path.join(only_plans, "plans"))
    code, _ = run_checker("--root", only_plans, "--json")
    shutil.rmtree(only_plans)
    expected = [
        (not any(r_ == "unresolved-link" and "PLN-998-plan" in w + t for r_, w, t in out["errors"]),
         "link do plano movido ficou quebrado"),
        (("unresolved-link", "plans/PLN-998-foco-novo/PLN-998-design.md") in {(r_, w) for r_, w, _ in out["errors"]},
         "link quebrado em plans/ não foi acusado: o checador não lê plans/"),
        (cn.genre_of("plans/PLN-998-foco-novo/PLN-998-plan.md") == "plan", "o plano em plans/ ficou sem gênero"),
        (cn.genre_of("plans/README.md") is None, "plans/README.md ganhou gênero"),
        (not any(r_ == "duplicate-name" and "README.md" in w for r_, w, _ in out["errors"]),
         "plans/README.md virou nome repetido"),
        (code == 0, f"raiz só com plans/ saiu com {code}"),
        (check_default_root() == 0, "a raiz padrão não é a do repositório onde o script mora"),
    ]
    for ok, msg in expected:
        if not ok:
            print(f"FALHA plans/ na raiz: {msg}")
            fails += 1
    if not fails:
        print("  ok  plans/ na raiz: o plano tem gênero e é lido, o plans/README.md fica sem gênero, e a raiz padrão é a do script")
    return fails


# O plano aberto conforme ao molde: cada caso de PLAN_CASES muda um trecho dele e quebra uma regra.
PLAN = """---
status: in_progress
phase: execution
origin: issue
origin_ref: uma-issue
ready: "não — aguarda: a revisão"
estimate_h: 12.5
executed_h: 3
remaining_h: 9.5
opened: 2026-10-27
owner: Anderson
---

# PLN-900: Conforme

- **Retomar:** o P2 em andamento

## Pedido

- **Origem:** [Uma issue](../../work/issues/uma-issue.md)

### Fora de escopo

- [Issue cortada](../../work/issues/issue-cortada.md) — fica para depois

## Passos

### P1 — Passo

- [x] feito; o cabeçalho antigo em crase não conta: `- **Status:** Execução`

## Worklogs

- 2026-10-27 — Aberto. Parou antes do P1.
- 2026-10-29 — P1 feito.
  [O dia](../../work/worklogs/2026-10-29-dia.md)

## Revisões

- [REV: Do PLN-900](../../work/reviews/pln-900-parecer.md) — 2026-10-29, 1 achado

## Decisões

- [Uma decisão](../../work/decisions/uma-decisao.md) — R1.1, `open`
"""

PLAN_CASES = {
    # número: (trocas sobre PLAN, as regras esperadas no -plan.md)
    "901": ([("phase: execution\n", "")], {"plan-without-phase"}),
    "902": ([("phase: execution", "phase: doing")], {"phase-out-of-vocabulary"}),
    "903": ([("phase: execution", "phase: review")], {"phase-status-mismatch"}),
    "904": ([("status: in_progress\nphase: execution", "status: open\nphase: blocked")], {"phase-status-mismatch"}),
    "905": ([("origin: issue\norigin_ref: uma-issue\n", "")], {"plan-without-origin"}),
    "906": ([("origin: issue", "origin: pedido")], {"origin-out-of-vocabulary"}),
    "907": ([("origin: issue\norigin_ref: uma-issue\n", "origin: decision\n")], {"origin-ref-missing"}),
    "908": ([("origin: issue", "origin: request")], {"origin-ref-on-request"}),
    "909": ([("origin: issue\norigin_ref: uma-issue\n", "origin: card\norigin_ref: FIX-30\n")],
            {"origin-out-of-vocabulary"}),
    "910": ([("origin_ref: uma-issue", "origin_ref: issue-que-nao-existe")], {"origin-ref-not-found"}),
    "911": ([("origin: issue\norigin_ref: uma-issue", "origin: plan\norigin_ref: PLN-950/A2")], {"origin-ref-not-found"}),
    "912": ([("origin: issue\norigin_ref: uma-issue", "origin: plan\norigin_ref: PLN-900/A1")], set()),
    "913": ([("origin: issue\norigin_ref: uma-issue\n", "origin: request\n")], set()),
    "914": ([("origin: issue\norigin_ref: uma-issue", "origin: decision\norigin_ref: decisao-que-nao-existe")],
            {"origin-ref-not-found"}),
    "915": ([('ready: "não — aguarda: a revisão"\n', "")], {"plan-without-ready"}),
    "916": ([("estimate_h: 12.5", "estimate_h: 12,5")], {"hours-not-a-number"}),
    "919": ([("- **Retomar:**", "> Nota antes do Retomar.\n\n- **Retomar:**")], {"retomar-not-first"}),
    "920": ([("## Worklogs", "## Log")], {"plan-without-worklogs"}),
    "922": ([("- 2026-10-29 — P1 feito.\n  [O dia](../../work/worklogs/2026-10-29-dia.md)",
              "- 2026-10-28 — P1 feito, sem o link.\n- 2026-10-29 — P1 feito.\n  [O dia](../../work/worklogs/2026-10-29-dia.md)")],
            {"worklog-line-without-link"}),
}

PLAN_NOTES = {
    "work/issues/uma-issue.md": "---\nstatus: open\nopened: 2026-10-27\n---\n# Uma issue\n[PLN-900](../../plans/PLN-900-x/PLN-900-plan.md)\n",
    "work/issues/issue-cortada.md": "---\nstatus: open\nopened: 2026-10-27\n---\n# Issue cortada\n[PLN-900](../../plans/PLN-900-x/PLN-900-design.md)\n",
    "work/worklogs/2026-10-29-dia.md": "# O dia\n[PLN-900](../../plans/PLN-900-x/PLN-900-plan.md)\n",
    "work/reviews/pln-900-parecer.md": "---\nstatus: done\nopened: 2026-10-29\ncompleted: 2026-10-29\n---\n# REV: Do PLN-900\n[PLN-900](../../plans/PLN-900-x/PLN-900-plan.md)\n\n### A1 — Achado\n\n**Evidência:** `x.py:1`.\n",
    "work/decisions/uma-decisao.md": "---\nstatus: open\nopened: 2026-10-27\ntrigger: quando vier\n---\n# Uma decisão\n[PLN-900](../../plans/PLN-900-x/PLN-900-plan.md)\n",
    "plans/PLN-900-x/PLN-900-design.md": "# PLN-900 — Desenho\n\nDo [PLN-900: Conforme](PLN-900-plan.md).\n",
    # O plano fechado não é conferido pelas regras do plano aberto, mesmo fora do molde
    "plans/PLN-930-fechado/PLN-930-plan.md": "---\nstatus: done\nopened: 2026-10-26\ncompleted: 2026-10-27\n---\n\n# PLN-930: Fechado\n\nSem Retomar, sem phase e sem Worklogs.\n",
    # Linkado por um worklog, uma issue, um parecer e uma decisão que não estão nas seções dele: os avisos
    "plans/PLN-940-sem-volta/PLN-940-plan.md": PLAN.replace("PLN-900: Conforme", "PLN-940: Sem volta"),
    "work/worklogs/2026-10-29-outro.md": "# Outro dia\n[PLN-940](../../plans/PLN-940-sem-volta/PLN-940-plan.md)\n",
    "work/issues/issue-solta.md": "---\nstatus: open\nopened: 2026-10-27\n---\n# Issue solta\n[PLN-940](../../plans/PLN-940-sem-volta/PLN-940-plan.md)\n",
    "work/reviews/pln-940-parecer.md": "---\nstatus: done\nopened: 2026-10-29\ncompleted: 2026-10-29\n---\n# REV: Do PLN-940\n[PLN-940](../../plans/PLN-940-sem-volta/PLN-940-plan.md)\n",
    "work/decisions/outra-decisao.md": "---\nstatus: open\nopened: 2026-10-27\ntrigger: quando vier\n---\n# Outra decisão\n[PLN-940](../../plans/PLN-940-sem-volta/PLN-940-plan.md)\n",
}


def check_plan_rules():
    """As regras do plano aberto: o conforme sem achado, cada caso com a regra dele, o fechado fora do molde isento, e o
    aviso da nota que linka o plano e falta na seção. Roda o check() no processo, para fixar a data de
    WORKLOGS_LINK_FROM, com e sem ela."""
    tmp = os.path.realpath(tempfile.mkdtemp())
    fails = 0
    try:
        notes = {"plans/PLN-900-x/PLN-900-plan.md": PLAN, **PLAN_NOTES}
        for number, (swaps, _) in PLAN_CASES.items():
            text = PLAN.replace("PLN-900: Conforme", f"PLN-{number}: Caso")
            for old, new in swaps:
                assert old in text, (number, old)
                text = text.replace(old, new)
            notes[f"plans/PLN-{number}-caso/PLN-{number}-plan.md"] = text
        write_notes(tmp, notes)

        saved = cn.WORKLOGS_LINK_FROM
        try:
            cn.WORKLOGS_LINK_FROM = "2026-10-28"
            _, errors, warnings, broken = cn.check(tmp)
            cn.WORKLOGS_LINK_FROM = None
            _, errors_without_date, _, _ = cn.check(tmp)
        finally:
            cn.WORKLOGS_LINK_FROM = saved
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    def rules(found, rel):
        return {r for r, w, _ in found if w == rel}

    def expect(label, ok, detail=""):
        nonlocal fails
        print(("  ok  " if ok else "FALHA ") + label + ("" if ok else f": {detail}"))
        fails += not ok

    conforming = "plans/PLN-900-x/PLN-900-plan.md"
    expect("plano aberto conforme não tem erro nem aviso", not rules(errors + warnings, conforming),
           rules(errors + warnings, conforming))
    expect("links das notas de exemplo resolvem", not broken, broken)
    for number, (_, expected) in PLAN_CASES.items():
        rel = f"plans/PLN-{number}-caso/PLN-{number}-plan.md"
        found = rules(errors, rel)
        expect(f"PLN-{number}: {', '.join(sorted(expected)) or 'sem erro'}", found == expected, found)
    closed = "plans/PLN-930-fechado/PLN-930-plan.md"
    expect("plano done fora do molde fica isento", not rules(errors + warnings, closed),
           rules(errors + warnings, closed))
    lost = {t for r, w, t in warnings if r == "backlink-missing" and w == "plans/PLN-940-sem-volta/PLN-940-plan.md"}
    expect("worklog, issue, parecer e decisão fora das seções são aviso", lost == {
        "work/worklogs/2026-10-29-outro.md", "work/issues/issue-solta.md", "work/reviews/pln-940-parecer.md",
        "work/decisions/outra-decisao.md"}, lost)
    expect("backlink-missing nunca é erro", not any(r == "backlink-missing" for r, _, _ in errors))
    expect("sem a data, a linha sem link do Worklogs não é cobrada",
           "worklog-line-without-link" not in rules(errors_without_date, "plans/PLN-922-caso/PLN-922-plan.md"))
    return fails


DESCRIPTION_NOTES = {
    # plano, issue e decisão novos (de 2026-11-01 em diante): sem o campo, erro; com ele, conforme
    "work/issues/nova-sem.md": "---\nstatus: open\nopened: 2026-11-01\n---\n\n# Sem\n",
    "work/issues/nova-com.md": "---\nstatus: open\nopened: 2026-11-01\ndescription: Uma frase.\n---\n\n# Com\n",
    "work/issues/nova-vazia.md": "---\nstatus: open\nopened: 2026-11-01\ndescription:\n---\n\n# Vazia\n",
    "work/decisions/nova-sem.md": "---\nstatus: open\nopened: 2026-11-02\ntrigger: \"quando vier\"\n---\n\n# Sem\n",
    "work/decisions/nova-decidida-sem.md": "---\nstatus: done\ndecided: 2026-11-02\ndecided_by: Anderson\n---\n",
    "work/decisions/nova-com.md": "---\nstatus: done\ndecided: 2026-11-02\ndecided_by: Anderson\n"
                                  "description: Decide isto.\n---\n",
    # a anterior à data entra no índice com o H1 e não é cobrada, nem se fechou depois
    "work/issues/antiga-sem.md": "---\nstatus: done\nopened: 2026-10-31\ncompleted: 2026-11-03\n---\n\n# Antiga\n",
    "work/decisions/antiga-sem.md": "---\nstatus: done\ndecided: 2026-10-31\ndecided_by: Anderson\n---\n",
    # nota sem data não é cobrada; worklog e guia não levam o campo
    "work/issues/sem-data.md": "---\nstatus: open\n---\n",
    "work/worklogs/2026-11-02-w.md": "---\nscope:\n  - workspace\n---\n\n# W\n",
    "wiki/guides/guia-novo.md": "---\ntags: [design]\n---\n\n# Guia\n",
    # o plano: só o -plan.md é cobrado, e não o -design
    "plans/PLN-950-novo/PLN-950-plan.md": PLAN.replace("PLN-900: Conforme", "PLN-950: Novo"),
    "plans/PLN-950-novo/PLN-950-design.md": "# PLN-950 — Desenho\n",
    "plans/PLN-951-novo/PLN-951-plan.md": PLAN.replace("PLN-900: Conforme", "PLN-951: Novo")
                                          .replace("---\n", "---\ndescription: O plano novo.\n", 1),
}


def check_description():
    """O campo description: obrigatório em plano, issue e decisão criados a partir de DESCRIPTION_FROM; a nota de antes
    ou sem data não é cobrada."""
    tmp = os.path.realpath(tempfile.mkdtemp())
    try:
        notes = dict(DESCRIPTION_NOTES)
        # o plano de exemplo é de antes da data: leva a data nova para ser cobrado
        for key in ("plans/PLN-950-novo/PLN-950-plan.md", "plans/PLN-951-novo/PLN-951-plan.md"):
            assert "opened: 2026-10-" in notes[key], key
            notes[key] = re.sub(r"opened: 2026-10-\d\d", "opened: 2026-11-01", notes[key], count=1)
        write_notes(tmp, notes)
        _, errors, _, _ = cn.check(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    missing = {w for r, w, _ in errors if r == "description-missing"}
    fails = 0

    def expect(label, ok, detail=""):
        nonlocal fails
        print(("  ok  " if ok else "FALHA ") + label + ("" if ok else f": {detail}"))
        fails += not ok

    expect("a nota nova sem description é erro; vazia também", missing == {
        "work/issues/nova-sem.md", "work/issues/nova-vazia.md", "work/decisions/nova-sem.md",
        "work/decisions/nova-decidida-sem.md", "plans/PLN-950-novo/PLN-950-plan.md"}, sorted(missing))
    expect("a nota nova com description não tem o erro", not missing & {
        "work/issues/nova-com.md", "work/decisions/nova-com.md", "plans/PLN-951-novo/PLN-951-plan.md"}, sorted(missing))
    expect("a nota de antes da data, sem data, o worklog, o guia e o -design não são cobrados", not missing & {
        "work/issues/antiga-sem.md", "work/decisions/antiga-sem.md", "work/issues/sem-data.md",
        "work/worklogs/2026-11-02-w.md", "wiki/guides/guia-novo.md", "plans/PLN-950-novo/PLN-950-design.md"},
           sorted(missing))
    return fails


MANIFEST_DATES_NOTE = "---\nstatus: done\nopened: 2026-11-15\n---\n"  # done sem completed


def check_manifest_dates():
    """O CUTOFF e o WORKLOGS_LINK_FROM vêm do `.brza/workspace.toml` da raiz, quando há; sem ele, os padrões."""
    tmp = os.path.realpath(tempfile.mkdtemp())
    fails = 0

    def expect(label, ok, detail=""):
        nonlocal fails
        print(("  ok  " if ok else "FALHA ") + label + ("" if ok else f": {detail}"))
        fails += not ok

    def root_with(name, manifest):
        root = os.path.join(tmp, name)
        write_notes(root, {"work/issues/feito.md": MANIFEST_DATES_NOTE})
        if manifest is not None:
            write_notes(root, {".brza/workspace.toml": manifest})
        return root

    def done_without_completed(root):
        _, out = run_checker("--root", root, "--json")
        found = json.loads(out)
        return ("done-without-completed" in {r for r, _, _ in found["errors"]},
                "done-without-completed" in {r for r, _, _ in found["warnings"]})

    try:
        born = '[workspace]\nborn = "2026-12-01"\n'
        roots = {
            "sem": root_with("sem", None),
            "born": root_with("born", 'schema = 1\n' + born),
            "link": root_with("link", 'schema = 1\n' + born + '\n[checker]\nworklogs_link_from = "2026-12-15"\n'),
            "sem-born": root_with("sem-born", 'schema = 1\n[workspace]\nname = "x"\n'),
            "data-ruim": root_with("data-ruim", 'schema = 1\n[workspace]\nborn = "ontem"\n'),
            "ilegivel": root_with("ilegivel", "isto = não é toml ["),
        }
        defaults = (cn.CUTOFF, cn.WORKLOGS_LINK_FROM)
        expect("sem manifesto, valem os padrões do arquivo", cn.manifest_dates(roots["sem"]) == defaults)
        expect("o `born` é o corte, e o link parte do mesmo dia", cn.manifest_dates(roots["born"]) == ("2026-12-01",) * 2)
        expect("o `checker.worklogs_link_from` vale quando está lá",
               cn.manifest_dates(roots["link"]) == ("2026-12-01", "2026-12-15"))
        expect("manifesto sem `born` cai nos padrões", cn.manifest_dates(roots["sem-born"]) == defaults)
        expect("`born` que não é data cai nos padrões", cn.manifest_dates(roots["data-ruim"]) == defaults)
        expect("manifesto ilegível cai nos padrões", cn.manifest_dates(roots["ilegivel"]) == defaults)
        expect("o corte do padrão cobra a nota de 2026-11-15: erro", done_without_completed(roots["sem"]) == (True, False))
        expect("o `born` de 2026-12-01 faz a mesma nota virar aviso",
               done_without_completed(roots["born"]) == (False, True), done_without_completed(roots["born"]))
        expect("o manifesto ilegível não derruba o checador", done_without_completed(roots["ilegivel"]) == (True, False))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return fails


GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True, text=True, env=GIT_ENV).stdout


def commit_branch(repo, branch, notes, start="master"):
    """Cria `branch` a partir de `start` (None: no lugar, para o primeiro commit) no `repo`, grava `notes` nela e
    commita; deixa o repositório na branch."""
    git(repo, "checkout", "-q", "-B", branch, *([start] if start else []))
    write_notes(repo, notes)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", branch)


WORKSTREAM_STUDIO = {
    "work/issues/issue-do-workstream.md": "---\nstatus: open\nopened: 2026-10-27\n---\n# Issue do workstream\n",
    "work/decisions/decisao-do-workstream.md": "---\nstatus: open\nopened: 2026-10-27\ntrigger: quando vier\n---\n# Decisão do workstream\n",
    "plans/PLN-810-do-workstream/PLN-810-plan.md": "# PLN-810: Do workstream\n",
}
# O plano aberto cujo origin_ref aponta para o que o workstream studio tem ou não tem
WORKSTREAM_PLANS = {"801": "origin: issue\norigin_ref: issue-do-workstream", "802": "origin: decision\norigin_ref: decisao-do-workstream",
               "803": "origin: plan\norigin_ref: PLN-810/A1", "804": "origin: issue\norigin_ref: issue-em-lugar-nenhum",
               "805": "origin: plan\norigin_ref: PLN-899", "806": "origin: issue\norigin_ref: issue-so-no-origin"}


def check_workstream_links():
    """Link e origin_ref que apontam para nota de outro workstream: o checador procura nas branches workstream/*, as locais e as de
    origin/workstream/*, com um git ls-tree por ref; sem repositório git ou sem branch workstream, vale só a árvore."""
    tmp = os.path.realpath(tempfile.mkdtemp())
    fails = 0

    def expect(label, ok, detail=""):
        nonlocal fails
        print(("  ok  " if ok else "FALHA ") + label + ("" if ok else f": {detail}"))
        fails += not ok

    try:
        origin, repo = os.path.join(tmp, "origin.git"), os.path.join(tmp, "ws")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", origin], check=True)
        subprocess.run(["git", "clone", "-q", origin, repo], check=True, capture_output=True, env=GIT_ENV)
        commit_branch(repo, "master", {"work/README.md": "# Norma\n"}, start=None)
        git(repo, "push", "-q", "origin", "master")
        commit_branch(repo, "workstream/studio", WORKSTREAM_STUDIO)
        # Só no origin: a branch local é apagada depois do push
        commit_branch(repo, "workstream/so-origin", {"work/issues/issue-so-no-origin.md":
                                                "---\nstatus: open\nopened: 2026-10-27\n---\n# Só no origin\n"})
        git(repo, "push", "-q", "origin", "workstream/so-origin")
        notes = {
            "work/issues/link-de-outro-workstream.md": "---\nstatus: open\nopened: 2026-10-27\n---\n"
                                                  "[a](issue-do-workstream.md) [b](../decisions/decisao-do-workstream.md#x) "
                                                  "[c](../../plans/PLN-810-do-workstream/PLN-810-plan.md)\n",
            "work/issues/link-so-no-origin.md": "---\nstatus: open\nopened: 2026-10-27\n---\n[d](issue-so-no-origin.md)\n",
            "work/issues/link-em-lugar-nenhum.md": "---\nstatus: open\nopened: 2026-10-27\n---\n[e](nao-esta-em-workstream.md)\n",
            "work/issues/link-fora-do-repo.md": "---\nstatus: open\nopened: 2026-10-27\n---\n[f](../../../fora.md)\n",
        }
        for number, ref in WORKSTREAM_PLANS.items():
            text = PLAN.replace("PLN-900: Conforme", f"PLN-{number}: Caso")
            text = text.replace("origin: issue\norigin_ref: uma-issue", ref)
            notes[f"plans/PLN-{number}-caso/PLN-{number}-plan.md"] = text
        commit_branch(repo, "workstream/tools-kit", notes)
        git(repo, "branch", "-q", "-D", "workstream/so-origin")
        git(repo, "fetch", "-q", "origin")

        calls = []
        real_run = subprocess.run

        def spy(cmd, *a, **k):
            calls.append(cmd)
            return real_run(cmd, *a, **k)

        cn.subprocess.run = spy
        try:
            _, errors, _, broken = cn.check(repo)
        finally:
            cn.subprocess.run = real_run
        broken_links = {(w, h) for w, h in broken}
        i = "work/issues/"
        expect("link para nota de outro workstream local resolve",
               not any(w == i + "link-de-outro-workstream.md" for w, _ in broken_links), broken_links)
        expect("link para nota que só está em origin/workstream/* resolve",
               not any(w == i + "link-so-no-origin.md" for w, _ in broken_links), broken_links)
        expect("link para o que nenhum workstream tem continua quebrado",
               (i + "link-em-lugar-nenhum.md", "nao-esta-em-workstream.md") in broken_links, broken_links)
        expect("link que sai da raiz do repositório continua quebrado",
               (i + "link-fora-do-repo.md", "../../../fora.md") in broken_links, broken_links)
        found = {n: {r for r, w, _ in errors if w == f"plans/PLN-{n}-caso/PLN-{n}-plan.md"} for n in WORKSTREAM_PLANS}
        for number, ref in WORKSTREAM_PLANS.items():
            lost = number in ("804", "805")
            expect(f"origin_ref {ref.split(chr(10))[1].split(': ')[1]} "
                   f"{'não achado é erro' if lost else 'em outro workstream resolve'}",
                   ("origin-ref-not-found" in found[number]) == lost, found[number])
        trees = [c for c in calls if "ls-tree" in c]
        refs = git(repo, "for-each-ref", "--format=%(refname)", "refs/heads/workstream/", "refs/remotes/origin/workstream/").split()
        expect("um ls-tree por ref, e não um git por link", len(trees) == len(refs) > 2, (len(trees), refs))

        # Repositório sem branch workstream/*: só vale a árvore
        solo = os.path.join(tmp, "solo")
        git(tmp, "init", "-q", "-b", "master", solo)
        write_notes(solo, {"work/issues/a.md": "---\nstatus: open\nopened: 2026-10-27\n---\n[x](b.md)\n"})
        git(solo, "add", ".")
        git(solo, "commit", "-q", "-m", "um")
        _, _, _, broken_solo = cn.check(solo)
        expect("repositório sem branch workstream/* mantém o comportamento", broken_solo == [("work/issues/a.md", "b.md")],
               broken_solo)
    finally:
        cn.subprocess.run = subprocess.run
        shutil.rmtree(tmp, ignore_errors=True)
    return fails


def check_pln008_p1():
    """P1 do PLN-008: refs, depends_on, estimate_h na issue e aviso date no worklog."""
    tmp = os.path.realpath(tempfile.mkdtemp())
    fails = 0

    def expect(label, ok, detail=""):
        nonlocal fails
        print(("  ok  " if ok else "FALHA ") + label + ("" if ok else f": {detail}"))
        fails += not ok

    try:
        origin, repo = os.path.join(tmp, "origin.git"), os.path.join(tmp, "ws")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", origin], check=True)
        subprocess.run(["git", "clone", "-q", origin, repo], check=True, capture_output=True, env=GIT_ENV)
        commit_branch(repo, "master", {"work/README.md": "# Norma\n"}, start=None)
        git(repo, "push", "-q", "origin", "master")

        # Workstream remoto/local com alvos e uma parte de ciclo entre branches
        studio_notes = {
            "plans/PLN-810-studio/PLN-810-plan.md": "---\nstatus: done\nopened: 2026-10-20\ncompleted: 2026-10-25\n"
                                                    "depends_on:\n  - PLN-820\n---\n# PLN-810: Studio\n",
            "work/issues/issue-remota.md": "---\nstatus: done\nopened: 2026-10-20\ncompleted: 2026-10-25\n---\n# Issue remota\n",
            "work/reviews/REV-001-parecer.md": "---\nstatus: done\nopened: 2026-10-20\ncompleted: 2026-10-25\n---\n# REV-001: Parecer\n",
            "work/issues/MIG-001-migracao.md": "---\nstatus: open\ntype: migration\nopened: 2026-10-27\n---\n# MIG-001: Migração\n",
        }
        commit_branch(repo, "workstream/studio", studio_notes)

        tools_notes = {
            "plans/PLN-800-ok/PLN-800-plan.md": "---\nstatus: done\nopened: 2026-10-20\ncompleted: 2026-10-25\n---\n# PLN-800: Ok\n",
            "plans/PLN-820-ciclo-branch/PLN-820-plan.md": "---\nstatus: done\nopened: 2026-10-20\ncompleted: 2026-10-25\n"
                                                          "depends_on:\n  - PLN-810\n---\n# PLN-820: Ciclo entre branches\n",
            "work/issues/issue-local.md": "---\nstatus: open\nopened: 2026-10-27\nestimate_h: 2.5\n"
                                          "depends_on:\n  - PLN-800\n  - issue:issue-remota\n  - MIG-001\n---\n# Issue local\n",
            "work/issues/issue-hora-ruim.md": "---\nstatus: open\nopened: 2026-10-27\nestimate_h: 2,5\n---\n# Hora ruim\n",
            "work/issues/issue-alvo-inexistente.md": "---\nstatus: open\nopened: 2026-10-27\n"
                                                     "depends_on:\n  - PLN-999\n  - issue:nao-existe\n---\n# Alvo inexistente\n",
            "work/issues/issue-auto.md": "---\nstatus: open\nopened: 2026-10-27\n"
                                         "depends_on:\n  - issue:issue-auto\n---\n# Autorreferência\n",
            "work/issues/ciclo-a.md": "---\nstatus: open\nopened: 2026-10-27\ndepends_on:\n  - issue:ciclo-b\n---\n# A\n",
            "work/issues/ciclo-b.md": "---\nstatus: open\nopened: 2026-10-27\ndepends_on:\n  - issue:ciclo-a\n---\n# B\n",
            "work/worklogs/2026-10-27-com-date.md": "---\nscope:\n  - workspace\ndate: 2026-10-27\ntags:\n  - tooling\n"
                                                    "refs:\n  - PLN-800\n  - REV-001\n  - MIG-001\n  - issue:issue-remota\n---\n# Worklog ok\n",
            "work/worklogs/2026-10-27-ref-invalido.md": "---\nscope:\n  - workspace\nrefs:\n  - PLN-999\n  - issue:fantasma\n---\n# Ref inválido\n",
            "work/worklogs/2026-10-27-extracao.md": (
                "---\nscope:\n  - workspace\n---\n\n# Extração\n\n"
                "Prosa cita PLN-800/D1 e `REV-001`, além de [Issue local](../issues/issue-local.md)\n"
                "e [Issue remota](../issues/issue-remota.md) e `MIG-001/P1`.\n"
                "Menção histórica a ID inexistente PLN-404 gera aviso sem entrar.\n"
                "```bash\n"
                "# Em bloco cercado PLN-820 e MIG-999 não contam\n"
                "echo PLN-820\n"
                "```\n"
            ),
        }
        commit_branch(repo, "workstream/tools-kit", tools_notes)

        _, errors, warnings, _ = cn.check(repo)
        err_by_file = {(r, w, t) for r, w, t in errors}
        warn_by_file = {(r, w, t) for r, w, t in warnings}

        # R9.1: estados de satisfação
        expect("D2 R9.1: só status done satisfaz dependência",
               getattr(cn, "dependency_satisfied", lambda _: None)("done") is True
               and all(getattr(cn, "dependency_satisfied", lambda _: True)(s) is False
                       for s in ("open", "in_progress", "in_review", "dropped", None)))

        # R5.2: estimate_h opcional na issue
        expect("issue com estimate_h numérico válido e depends_on entre branches passa",
               not any(w == "work/issues/issue-local.md" for _, w, _ in err_by_file | warn_by_file),
               [x for x in err_by_file | warn_by_file if x[1] == "work/issues/issue-local.md"])
        expect("issue com estimate_h inválido é erro hours-not-a-number",
               any(r == "hours-not-a-number" and w == "work/issues/issue-hora-ruim.md" for r, w, _ in err_by_file),
               err_by_file)

        # R5.3: date no worklog é aviso, tags continua válido
        expect("date no worklog gera aviso date-on-worklog",
               any(r == "date-on-worklog" and w == "work/worklogs/2026-10-27-com-date.md" for r, w, _ in warn_by_file),
               warn_by_file)
        expect("refs válidos (inclusive entre branches) em worklog não geram erro",
               not any(w == "work/worklogs/2026-10-27-com-date.md" for _, w, _ in err_by_file),
               err_by_file)

        # D3 / R1.1: refs com ID inexistente é erro no checador
        bad_refs = {t for r, w, t in err_by_file if r == "ref-not-found" and w == "work/worklogs/2026-10-27-ref-invalido.md"}
        expect("ID inexistente no próprio refs gera erro ref-not-found",
               bad_refs == {"PLN-999", "issue:fantasma"}, bad_refs)

        # D2 / R9.2: depends_on com alvo inexistente, autorreferência e ciclo (local e entre branches)
        bad_deps = {t for r, w, t in err_by_file if r == "depends-on-not-found" and w == "work/issues/issue-alvo-inexistente.md"}
        expect("depends_on com alvo inexistente gera erro depends-on-not-found",
               bad_deps == {"PLN-999", "issue:nao-existe"}, bad_deps)
        expect("depends_on com autorreferência gera erro depends-on-cycle",
               any(r == "depends-on-cycle" and w == "work/issues/issue-auto.md" and "issue:issue-auto -> issue:issue-auto" in t
                   for r, w, t in err_by_file), err_by_file)
        expect("depends_on com ciclo local gera erro depends-on-cycle",
               any(r == "depends-on-cycle" and w == "work/issues/ciclo-a.md" and "issue:ciclo-a -> issue:ciclo-b -> issue:ciclo-a" in t
                   for r, w, t in err_by_file), err_by_file)
        expect("depends_on com ciclo entre branches gera erro depends-on-cycle",
               any(r == "depends-on-cycle" and w == "plans/PLN-820-ciclo-branch/PLN-820-plan.md"
                   and "PLN-820 -> PLN-810 -> PLN-820" in t for r, w, t in err_by_file), err_by_file)

        # D3 / R10: extração fora de cercas, subitem, links, crases inline e aviso para ID desconhecido
        expect("menção textual a ID inexistente fora de cerca gera aviso unknown-ref-mention com ID e linha",
               any(r == "unknown-ref-mention" and w == "work/worklogs/2026-10-27-extracao.md"
                   and "PLN-404" in t and "10" in t for r, w, t in warn_by_file)
               and not any("PLN-820" in t or "MIG-999" in t for r, w, t in warn_by_file if w == "work/worklogs/2026-10-27-extracao.md"),
               warn_by_file)
        extractor = getattr(cn, "extract_worklog_refs", None)
        if extractor:
            ext_path = os.path.join(repo, "work/worklogs/2026-10-27-extracao.md")
            refs_out, unk_out = extractor(open(ext_path, encoding="utf-8").read(), ext_path, repo)
            expect("extract_worklog_refs extrai prosa, crases, subitem e links fora de cercas",
                   refs_out == ["MIG-001", "PLN-800", "REV-001", "issue:issue-local", "issue:issue-remota"]
                   and [(lineno, uid) for lineno, uid in unk_out] == [(10, "PLN-404")],
                   (refs_out, unk_out))
        else:
            expect("extract_worklog_refs existe em check_notes.py", False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return fails


def check_pln008_p8():
    """P8 do PLN-008: o checador avisa, sem barrar, a nota fora de número (D7, R14.1), e o ID só no texto de um link
    não entra no refs (D3, R14.3)."""
    tmp = os.path.realpath(tempfile.mkdtemp())
    fails = 0

    def expect(label, ok, detail=""):
        nonlocal fails
        print(("  ok  " if ok else "FALHA ") + label + ("" if ok else f": {detail}"))
        fails += not ok

    rev_sem_numero = "work/reviews/pln-001-sem-numero.md"
    mig_sem_type = "work/issues/MIG-002-sem-type.md"
    migracao_sem_numero = "work/issues/migracao-sem-numero.md"
    rev_certo, mig_certa = "work/reviews/REV-001-certo.md", "work/issues/MIG-001-certa.md"
    so_link, link_e_prosa = "work/worklogs/2026-11-03-so-link.md", "work/worklogs/2026-11-03-link-e-prosa.md"
    notes = {
        "work/README.md": "# Norma\n",
        "work/reviews/README.md": "# Norma dos pareceres\n",
        rev_sem_numero: "---\nstatus: done\nopened: 2026-11-03\ncompleted: 2026-11-03\ndescription: Sem número.\n---\n"
                        "# Parecer sem número\n",
        mig_sem_type: "---\nstatus: open\nopened: 2026-11-03\ndescription: Sem type.\n---\n# MIG-002: Sem type\n",
        migracao_sem_numero: "---\nstatus: open\ntype: migration\nopened: 2026-11-03\ndescription: Sem número.\n---\n"
                             "# Migração sem número\n",
        rev_certo: "---\nstatus: done\nopened: 2026-11-03\ncompleted: 2026-11-03\ndescription: Certo.\n---\n"
                   "# REV-001: Certo\n",
        mig_certa: "---\nstatus: open\ntype: migration\nopened: 2026-11-03\ndescription: Certa.\n---\n# MIG-001: Certa\n",
        so_link: "---\nscope:\n  - workspace\n---\n\n# Só o link\n\n"
                 "Aplicada a [MIG-001: Renumeração (REV-001..REV-004)](../issues/MIG-001-certa.md).\n"
                 "E [o `REV-001` em crase no título](../issues/MIG-001-certa.md).\n",
        link_e_prosa: "---\nscope:\n  - workspace\n---\n\n# Link e prosa\n\n"
                      "Veja [o título REV-004](../issues/MIG-001-certa.md) e REV-001 na prosa.\n",
    }
    try:
        write_notes(tmp, notes)
        exit_code, stdout = run_checker("--root", tmp, "--json")
        out = json.loads(stdout)
        errors = {(r, w) for r, w, _ in out["errors"]}
        warnings = {(r, w) for r, w, _ in out["warnings"]}

        # D7 / R14.1: os três avisos, no nível de aviso
        expect("parecer de work/reviews/ fora de REV-NNN-<slug>.md é aviso review-without-number",
               ("review-without-number", rev_sem_numero) in warnings, sorted(warnings))
        expect("nota MIG-NNN sem type: migration é aviso migration-without-type",
               ("migration-without-type", mig_sem_type) in warnings, sorted(warnings))
        expect("issue type: migration sem número MIG-NNN é aviso migration-type-without-number",
               ("migration-type-without-number", migracao_sem_numero) in warnings, sorted(warnings))
        expect("os três avisos não barram: nenhum erro nas três notas e saída 0",
               exit_code == 0 and not any(w in (rev_sem_numero, mig_sem_type, migracao_sem_numero) for _, w in errors),
               (exit_code, sorted(errors)))
        expect("REV-NNN, MIG-NNN com type: migration e o README.md de reviews/ ficam sem aviso",
               not any(w in (rev_certo, mig_certa, "work/reviews/README.md") for _, w in errors | warnings),
               sorted(errors | warnings))

        # D3 / R14.3: o ID só no texto do link não entra no refs nem gera aviso; o alvo do link gera
        for rel, expected in ((so_link, ["MIG-001"]), (link_e_prosa, ["MIG-001", "REV-001"])):
            full = os.path.join(tmp, rel)
            refs_out, unk_out = cn.extract_worklog_refs(open(full, encoding="utf-8").read(), full, tmp)
            expect(f"ID só no texto do link não entra no refs: {os.path.basename(rel)}",
                   refs_out == expected and unk_out == [], (refs_out, unk_out))
        expect("ID inexistente só no texto do link não gera unknown-ref-mention",
               not any(r == "unknown-ref-mention" for r, _ in warnings), sorted(warnings))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return fails


def read_core(*parts):
    with open(os.path.join(os.path.dirname(SCRIPTS_DIR), "..", "..", "..", *parts), encoding="utf-8") as f:
        return f.read()


def check_pln012_p5():
    """P5 do PLN-012: o worklog com `sessions:` e a `## Custo da tarefa`, que o `brza note commit` escreve, passa sem aviso (D3);
    a norma, a skill `close-task` e o adaptador do Claude Code dizem o carimbo, a seção, o aviso e os eventos (D3, D6)."""
    tmp = os.path.realpath(tempfile.mkdtemp())
    fails = 0

    def expect(label, ok, detail=""):
        nonlocal fails
        print(("  ok  " if ok else "FALHA ") + label + ("" if ok else f": {detail}"))
        fails += not ok

    worklog = "work/worklogs/2026-11-03-medida.md"
    antigo = "work/worklogs/2026-11-02-sem-medida.md"
    notes = {
        "work/README.md": "# Norma\n",
        "work/issues/medida-alvo.md": "---\nstatus: open\nopened: 2026-11-03\ndescription: A medida.\n---\n"
                                      "# A medida\n",
        worklog: "---\nscope:\n  - workspace\nrefs:\n  - issue:medida-alvo\nsessions:\n"
                 "  - 08e6c812-8bb3-42fc-a47f-403fdb472ccd\n  - 4a578e01-1c2d-4e5f-8a9b-0c1d2e3f4a5b\n---\n\n"
                 "# A medida\n\nFechou a [medida](../issues/medida-alvo.md).\n\n## Custo da tarefa\n\n"
                 "Corrido 18 min — agente 7 min, Anderson 10 min, pausa 0 · US$ 2,35 (turnos 2,35, subagentes 0,00) · "
                 "CPU 0,8 s do\n`claude` e 6,1 s dos comandos, RSS até 357 MB · 1 turno, 1 sessão · `brza run`: 2 "
                 "comandos, CPU 14 min, pico 1,8 GB · até 22:16 de 2026-11-03, pelo `brza note commit`\n",
        antigo: "---\nscope:\n  - workspace\nrefs:\n  - issue:medida-alvo\n---\n\n# Sem medida\n\nA [medida](../issues/medida-alvo.md) antes do carimbo.\n",
    }
    try:
        write_notes(tmp, notes)
        exit_code, stdout = run_checker("--root", tmp, "--json")
        out = json.loads(stdout)
        found = [(r, w, t) for r, w, t in out["errors"] + out["warnings"] if w in (worklog, antigo)]
        expect("o worklog com sessions: e ## Custo da tarefa passa sem erro nem aviso", not found and exit_code == 0,
               (exit_code, found))
        expect("o worklog antigo, sem sessions: nem ## Custo da tarefa, também passa", not any(w == antigo for _, w, _ in found))
        full = os.path.join(tmp, worklog)
        refs_out, unk_out = cn.extract_worklog_refs(open(full, encoding="utf-8").read(), full, tmp)
        expect("a ## Custo da tarefa não põe nada no refs nem gera aviso de ID", refs_out == ["issue:medida-alvo"] and unk_out == [],
               (refs_out, unk_out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # A norma (D3): a linha sessions na tabela, e o sessions: e a ## Custo da tarefa na seção work/worklogs/
    norm = read_core("work", "README.md")
    table = [line for line in norm.splitlines() if line.startswith("| `sessions` |")]
    expect("a tabela de propriedades tem a linha sessions, mantida pelo brza note commit",
           len(table) == 1 and "`brza note commit`" in table[0] and "sessões" in table[0], table)
    worklogs = norm.split("### `work/worklogs/`", 1)[-1].split("\n### ", 1)[0]
    expect("a seção work/worklogs/ diz o sessions: no frontmatter", "`sessions:`" in worklogs)
    expect("a seção work/worklogs/ diz a ## Custo da tarefa como última seção, escrita pelo comando e não à mão",
           "`## Custo da tarefa`" in worklogs and "última seção" in worklogs and "não à mão" in worklogs)
    expect("a seção work/worklogs/ diz que o worklog não ganha opened nem hora em completed (R3.2)",
           "`opened`" in worklogs and "`completed`" in worklogs)

    # A skill (D6): o passo 2 novo, os seguintes renumerados de 3 a 7, e o --until no Commitar
    skill = read_core(".claude", "skills", "close-task", "SKILL.md")
    heads = re.findall(r"^## (\d+)\. (.+)$", skill, re.M)
    expect("o close-task tem os passos 1 a 7, com o 2 'O que o commit vai fechar'",
           [n for n, _ in heads] == [str(i) for i in range(1, 8)] and heads[1][1] == "O que o commit vai fechar"
           and heads[2][1] == "O worklog de hoje", heads)
    pending = skill.split("## 2. O que o commit vai fechar", 1)[-1].split("\n## 3.", 1)[0]
    expect("o passo 2 roda o --by-task --pending --session da sessão",
           'summarize_measurements.py --by-task --pending --session "$CLAUDE_CODE_SESSION_ID"' in pending)
    expect("o passo 2 manda fechar a tarefa mais antiga com --until", "--until" in pending)
    commit = skill.split("## 6. Commitar", 1)[-1].split("\n## 7.", 1)[0]
    expect("o Commitar cita o --until", "--until" in commit and "`sessions:`" in commit, commit[:200])
    expect("as referências internas aos passos seguem a renumeração",
           "vai ao passo 4" in skill and "do passo 1" in skill and "vai ao passo 3" not in skill)

    # O adaptador do Claude Code: os eventos task e run e o --by-task
    claude = read_core("CLAUDE.md")
    hook = claude.split("## O hook de medição", 1)[-1].split("\n## ", 1)[0]
    expect("a tabela do hook de medição tem os eventos task e run",
           re.search(r"^\| `task` +\|", hook, re.M) is not None and re.search(r"^\| `run` +\|", hook, re.M) is not None)
    expect("o resumo cita o --by-task e o --pending", "--by-task" in hook and "--pending" in hook)
    return fails


def check_feedback():
    """D2: work/feedbacks/ exige as cinco seções, nesta ordem, e model, effort e result válidos."""
    sections = ("## Pedido", "## Escolha", "## Erro", "## Esforço", "## Próxima vez")

    def body(*titles):
        return "\n".join(f"{title}\n\ntexto\n" for title in titles)

    front = (
        "---\n"
        "scope:\n"
        "  - workspace\n"
        "builder: bob\n"
        "task_type: code\n"
        "model: grok-4.7\n"
        "effort: medium\n"
        "result: done\n"
        "---\n\n"
        "# Feedback\n\n"
    )
    valid = "work/feedbacks/2026-11-14-valido.md"
    missing = "work/feedbacks/2026-11-14-sem-secao.md"
    fields = "work/feedbacks/2026-11-14-campos.md"
    order = "work/feedbacks/2026-11-14-ordem.md"
    fence = "work/feedbacks/2026-11-14-cerca.md"
    status = "work/feedbacks/2026-11-14-com-status.md"
    refused = "work/feedbacks/2026-11-14-recusado.md"
    notes = {
        valid: front + body(*sections),
        refused: front.replace("result: done", "result: refused") + body(*sections),
        missing: front + body("## Pedido", "## Escolha", "## Erro", "## Próxima vez"),
        fields: '---\nmodel: ""\nresult: no\n---\n\n# Feedback\n\n' + body(*sections),
        order: front + body("## Pedido", "## Erro", "## Escolha", "## Esforço", "## Próxima vez"),
        fence: front + "## Pedido\n\np\n\n## Escolha\n\ne\n\n```\n## Erro\n```\n\n## Esforço\n\ne\n\n## Próxima vez\n\np\n",
        status: front.replace("result: done", "result: partial").replace("---\n", "---\nstatus: open\n", 1) + body(*sections),
    }
    tmp = os.path.realpath(tempfile.mkdtemp())
    fails = total = 0
    try:
        write_notes(tmp, notes)
        _, stdout = run_checker("--root", tmp, "--json")
        out = json.loads(stdout)
        errors = {(r, w, t) for r, w, t in out["errors"]}
        warnings = {(r, w) for r, w, _ in out["warnings"]}

        def expect(label, ok):
            nonlocal fails, total
            total += 1
            print(("  ok  " if ok else "FALHA ") + label)
            fails += not ok

        expect("feedback válido não gera erro nem aviso",
               not any(w == valid for _, w, _ in out["errors"]) and not any(w == valid for _, w in warnings))
        expect("result refused é válido", not any(w == refused for _, w, _ in out["errors"]))
        expect("seção ausente é erro", ("feedback-section-missing", missing, "## Esforço") in errors)
        expect("a seção que está na nota não é acusada",
               not any(r == "feedback-section-missing" and w == missing and t != "## Esforço" for r, w, t in errors))
        expect("model vazio é erro", ("feedback-model-invalid", fields, "") in errors)
        expect("effort ausente é erro", ("feedback-effort-invalid", fields, "") in errors)
        expect("result fora dos três é erro", ("feedback-result-invalid", fields, "no") in errors)
        expect("seções fora de ordem é erro", any(r == "feedback-section-order" and w == order for r, w, _ in errors))
        expect("seção só dentro de cerca não conta", ("feedback-section-missing", fence, "## Erro") in errors)
        expect("result partial é válido", not any(r == "feedback-result-invalid" and w == status for r, w, _ in errors))
        expect("status em feedback é aviso, não erro de vocabulário",
               ("status-on-stateless-genre", status) in warnings
               and not any(r == "status-out-of-vocabulary" and w == status for r, w, _ in errors))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{total - fails} de {total} casos de feedback passaram." if fails else f"\n{total} casos de feedback, todos passaram.")
    return fails


if __name__ == "__main__":
    main()
