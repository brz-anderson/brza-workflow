#!/usr/bin/env python3
"""O desfecho que a sessão dá aos fatos do desenho (PLN-005): grava `fact_outcome` no log de medição, lista os fatos
que ainda pedem conferência e mostra uma devolução com os dois vereditos, o do Haiku e o da sessão.

  python3 scripts/fact_outcome.py <agent_id>:<F3|C2> <holds|fails> "<por quê>" [--ref <PLN-004/R8.1>]
                                                             registra o desfecho de um fato
  python3 scripts/fact_outcome.py --pending [--workstream <nome>|all] [--since AAAA-MM-DD]
                                                             os fatos `inferido`, abaixo de 80 ou `C…`, sem desfecho
  python3 scripts/fact_outcome.py --list <agent_id>          todos os fatos da devolução, com o Haiku e a sessão

O `agent_id` aceita prefixo, desde que único entre os eventos `fact` do log. Registrar de novo o mesmo fato é permitido:
vale o último desfecho, que é como o resumo (`summarize_measurements.py --facts`) lê. O desfecho leva o `workstream` do
fato, e não o do cwd, porque chega de outra sessão.

Saída 0, ou 2 no erro de uso, no fato ou prefixo que não se resolve e na falha de leitura ou gravação do log. O log é o
do `measure.py` (`MEASURE_DIR` para teste), de `<raiz do workspace>/.claude/hooks/`: a raiz é a `BRZA_WORKSPACE` ou, sem
ela e sem a `MEASURE_DIR`, o pai do `git rev-parse --git-common-dir` — o checkout principal, também de um worktree —,
ou o diretório acima de scripts/ se o git falhar. O `--pending` diz no stderr o log lido e o workstream filtrado, e
avisa quando ele é a branch da norma (`[workspace].branch` do manifesto) ou vazio. Só biblioteca padrão, Python 3.9.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(".claude", "hooks", "measure.py")


def git_root(here):
    """O pai do `git rev-parse --git-common-dir` de `here`, que é o checkout principal mesmo num worktree, ou None se o
    git falha ou o diretório comum não é um `.git`. A mesma função do summarize_measurements.py."""
    try:
        done = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=here, text=True,
                              capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    common = done.stdout.strip()
    if done.returncode != 0 or os.path.basename(common) != ".git":
        return None
    return os.path.dirname(common)


def workspace_root(here=HERE, env=None):
    """A raiz do workspace: com a `BRZA_WORKSPACE` ou a `MEASURE_DIR`, a de sempre (a variável, ou o diretório acima de
    scripts/); sem as duas, o checkout principal pelo git, para o terminal de um worktree ler o log vivo, e não o
    `.measurements/` velho do próprio worktree. O `measure.py` carregado dali escreve e lê no mesmo diretório."""
    env = os.environ if env is None else env
    if env.get("BRZA_WORKSPACE"):
        return env["BRZA_WORKSPACE"]
    if not env.get("MEASURE_DIR"):
        root = git_root(here)
        if root and os.path.isfile(os.path.join(root, HOOK)):
            return root
    return os.path.join(here, "..")


WORKSPACE = workspace_root()
MEASURE = os.path.normpath(os.path.join(WORKSPACE, HOOK))


def norm_branch():
    """A branch da norma do workspace do cwd (`[workspace].branch` do manifesto, pelo `brza_root` do `measure.py`), ou
    None fora de um workspace: o `workstream_of` devolve esse nome no checkout da norma, que não guarda fatos."""
    ws = measure.brza_root.find(os.getcwd())
    return measure.brza_root.workspace_branch(ws.manifest) if ws else None


def load_measure():
    try:
        spec = importlib.util.spec_from_file_location("measure", MEASURE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (OSError, AttributeError) as e:
        print(f"fact_outcome.py: não consegui carregar {MEASURE}: {e}", file=sys.stderr)
        sys.exit(2)
    return module


measure = load_measure()

VERDICTS = ("holds", "fails")
WHY_LIMIT = 300
LINE_WIDTH = 160
LOW_CONFIDENCE = 80
FACT_ID = re.compile(r"^[FC]\d+$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DASH = "—"


class Failure(Exception):
    """Falha que sai com 2 e a mensagem, sem traceback."""


def read_log():
    """As linhas do log, em ordem, sem as que não são JSON. Log ausente é log vazio; erro de leitura levanta Failure."""
    rows = []
    try:
        with open(measure.LOG, encoding="utf-8") as f:
            for raw in f:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as e:
        raise Failure(f"não consegui ler {measure.LOG}: {e}")
    return rows


def resolve_agent(rows, prefix):
    """O agent_id inteiro de um prefixo, entre os eventos `fact`. Vale o id exato; prefixo que casa mais de um ou
    nenhum levanta Failure, com os candidatos."""
    ids = []
    for row in rows:
        agent_id = row.get("agent_id")
        if row.get("event") == "fact" and isinstance(agent_id, str) and agent_id not in ids:
            ids.append(agent_id)
    if prefix in ids:
        return prefix
    found = [i for i in ids if i.startswith(prefix)]
    if not found:
        raise Failure(f"nenhum fato de agent_id {prefix!r} no log")
    if len(found) > 1:
        raise Failure(f"o prefixo {prefix!r} é ambíguo, candidatos: {', '.join(found)}")
    return found[0]


def facts_of(rows, agent_id):
    """Os eventos `fact` do agente, na ordem do log, com o primeiro de cada fact_id."""
    seen, facts = set(), []
    for row in rows:
        if row.get("event") == "fact" and row.get("agent_id") == agent_id and row.get("fact_id") not in seen:
            seen.add(row.get("fact_id"))
            facts.append(row)
    return facts


def latest(rows, event):
    """{(agent_id, fact_id): o último evento}, pela ordem do log."""
    last = {}
    for row in rows:
        if row.get("event") == event:
            last[(row.get("agent_id"), row.get("fact_id"))] = row
    return last


def one_line(text, width=None):
    text = " ".join(str(text or "").split())
    return text if width is None or len(text) <= width else text[:width - 1] + "…"


def register(rows, target, verdict, why, ref):
    prefix, _, fact_id = target.rpartition(":")
    agent_id = resolve_agent(rows, prefix)
    fact = next((f for f in facts_of(rows, agent_id) if f.get("fact_id") == fact_id), None)
    if fact is None:
        raise Failure(f"o fato {fact_id} da devolução {agent_id} não está no log")
    try:
        measure.write({"event": "fact_outcome", "timestamp": measure.now(), "agent_id": agent_id,
                       "agent_type": fact.get("agent_type"), "fact_id": fact_id, "verdict": verdict,
                       "why": why[:WHY_LIMIT], "ref": ref or None, "workstream": fact.get("workstream")})
    except OSError as e:
        raise Failure(f"não consegui gravar em {measure.LOG}: {e}")
    print(f"fact_outcome: {agent_id}:{fact_id} {verdict} ({fact.get('workstream') or DASH})")


def is_pending(fact):
    confidence = fact.get("confidence")
    return (fact.get("kind") == "inferido" or str(fact.get("fact_id", "")).startswith("C")
            or (isinstance(confidence, (int, float)) and confidence < LOW_CONFIDENCE))


def pending(rows, workstream, since):
    outcomes = latest(rows, "fact_outcome")
    facts = []
    for row in rows:
        if row.get("event") != "fact" or not is_pending(row):
            continue
        if (row.get("agent_id"), row.get("fact_id")) in outcomes:
            continue
        if workstream != "all" and row.get("workstream") != workstream:
            continue
        if since and str(row.get("timestamp", ""))[:10] < since:
            continue
        facts.append(row)
    facts.sort(key=lambda f: str(f.get("timestamp", "")))
    for f in facts:
        head = (f"{str(f.get('agent_id'))[:8]}:{f.get('fact_id')}  {str(f.get('timestamp', ''))[:10]}  "
                f"{f.get('agent_type')}  {f.get('kind')} {f.get('confidence')}  ")
        line = head + one_line(f.get("text"))
        print(line if len(line) <= LINE_WIDTH else line[:LINE_WIDTH - 1] + "…")
    if not facts:
        print("nenhum fato a conferir")
    else:
        print(f"{len(facts)} {'fato' if len(facts) == 1 else 'fatos'} a conferir")


def show(rows, prefix):
    agent_id = resolve_agent(rows, prefix)
    facts = facts_of(rows, agent_id)
    checks, outcomes = latest(rows, "fact_check"), latest(rows, "fact_outcome")
    print(f"{agent_id}  {facts[0].get('agent_type')}  {facts[0].get('workstream') or DASH}  "
          f"{str(facts[0].get('timestamp', ''))[:10]}")
    for f in facts:
        key = (agent_id, f.get("fact_id"))
        c, o = checks.get(key), outcomes.get(key)
        haiku = f"Haiku {c.get('verdict')} (cite {c.get('cite')})" if c else f"Haiku {DASH}"
        session = DASH
        if o:
            session = f"{o.get('verdict')} ({o['ref']})" if o.get("ref") else str(o.get("verdict"))
        print(f"{f.get('fact_id')}  {f.get('kind')} {f.get('confidence')}  {haiku}  sessão {session}  "
              f"{one_line(f.get('text'))}")


def parser():
    p = argparse.ArgumentParser(
        prog="fact_outcome.py", description="O desfecho da sessão para os fatos do desenho (PLN-005).",
        usage='fact_outcome.py <agent_id>:<F3|C2> <holds|fails> "<por quê>" [--ref REF]\n'
              "       fact_outcome.py --pending [--workstream NOME|all] [--since AAAA-MM-DD]\n"
              "       fact_outcome.py --list AGENT_ID")
    p.add_argument("target", nargs="?", help="<agent_id>:<fato>, com prefixo único do agent_id")
    p.add_argument("verdict", nargs="?", help="holds ou fails")
    p.add_argument("why", nargs="?", help="o porquê, até 300 caracteres")
    p.add_argument("--ref", help="a decisão ou rodada que o desfecho cita, como PLN-004/R8.1")
    p.add_argument("--pending", action="store_true", help="lista os fatos a conferir")
    p.add_argument("--list", metavar="AGENT_ID", help="mostra os fatos de uma devolução")
    p.add_argument("--workstream", help="com --pending: o workstream, ou all; o padrão é o do cwd")
    p.add_argument("--since", help="com --pending: só os fatos a partir da data (AAAA-MM-DD)")
    return p


def main(argv=None):
    p = parser()
    a = p.parse_args(argv)
    positional = [x for x in (a.target, a.verdict, a.why) if x is not None]
    if a.pending and a.list:
        p.error("--pending e --list não se combinam")
    if a.pending or a.list:
        if positional:
            p.error("--pending e --list não levam o fato, o veredito nem o porquê")
        if a.ref:
            p.error("--ref é do registro")
    if a.pending and a.since and not DATE.match(a.since):
        p.error(f"--since pede AAAA-MM-DD, veio {a.since!r}")
    if not a.pending and (a.workstream or a.since):
        p.error("--workstream e --since são do --pending")
    if a.pending or a.list:
        mode = "pending" if a.pending else "list"
    else:
        mode = "register"
        if len(positional) < 3:
            p.error("faltam argumentos: <agent_id>:<fato> <holds|fails> \"<por quê>\"")
        prefix, colon, fact_id = a.target.rpartition(":")
        if not colon or not prefix or not FACT_ID.match(fact_id):
            p.error(f"o fato pede <agent_id>:<F3|C2>, veio {a.target!r}")
        if a.verdict not in VERDICTS:
            p.error(f"veredito inválido {a.verdict!r}: use holds ou fails")
        if not a.why.strip():
            p.error("o porquê não pode ficar vazio")
    try:
        rows = read_log()
        if mode == "register":
            register(rows, a.target, a.verdict, a.why.strip(), a.ref)
        elif mode == "list":
            show(rows, a.list)
        else:
            workstream = a.workstream or measure.workstream_of(os.getcwd())
            print(f"fact_outcome.py: lendo {measure.LOG}; workstream {workstream or DASH}", file=sys.stderr)
            if workstream and workstream == norm_branch():
                print(f"fact_outcome.py: o workstream filtrado é {workstream}, a branch da norma, que não guarda fatos; "
                      "use --workstream <nome>|all", file=sys.stderr)
            elif not workstream:
                print("fact_outcome.py: o cwd não está num workstream; use --workstream <nome>|all",
                      file=sys.stderr)
            pending(rows, workstream, a.since)
    except Failure as e:
        print(f"fact_outcome.py: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
