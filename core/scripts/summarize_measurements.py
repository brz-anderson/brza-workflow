#!/usr/bin/env python3
"""Resume o log de medição do workspace brza, `.measurements/measurements.jsonl`, escrito pelo hook `measure.py` (um
evento por linha), pela trava `resume_guard.py`, já desligada, e pela trava `compact_guard.py` (a linha `guard`, com
`kind: compact`).

Agrega o subagente por agente, modelo e esforço, e por versão do plugin; o turno da sessão principal por modelo e
esforço, e pelas skills que entraram nele; os comandos digitados; as falhas por tipo de erro; o MCP por servidor; e as
decisões das travas de retomada e do /compact. As negações de hook saem com o motivo.

  python3 scripts/summarize_measurements.py                      lê o .measurements/ da $BRZA_WORKSPACE, ou do checkout principal
  python3 scripts/summarize_measurements.py --dir <diretório>    outro diretório com o log
  python3 scripts/summarize_measurements.py --since 2026-10-10   só os eventos a partir da data (AAAA-MM-DD)
  python3 scripts/summarize_measurements.py --denials            lista cada negação, com o agente, a data e o motivo
  python3 scripts/summarize_measurements.py --json               a mesma agregação em JSON
  python3 scripts/summarize_measurements.py --by-workstream      por workstream e por dia: turnos, custo, subagentes por
                                                                 agent_type, memórias e rodadas (com --json, em JSON)
  python3 scripts/summarize_measurements.py --facts              o acerto dos fatos do desenho (PLN-005), por papel, classe,
                                                                 faixa de confiança e tipo (com --json, em JSON); --since
                                                                 filtra pela data do fato
  python3 scripts/summarize_measurements.py --by-task            por tarefa (o worklog): o corrido, o agente, Anderson e a
                                                                 pausa, o custo, a CPU e o `brza run`; as tarefas não
                                                                 medidas; e a ociosa e os turnos sem tarefa por sessão
                                                                 (com --since, --workstream <nome> e --json)
  python3 scripts/summarize_measurements.py --by-task --pending --session <id>
                                                                 o que o commit da sessão vai fechar, e os turnos sem
                                                                 tarefa das outras sessões do workstream paradas há 2 h

Log que não existe é pulado; linha que não é JSON é contada e ignorada. Só biblioteca padrão, Python 3.9.
"""

import argparse
import bisect
import collections
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def git_root(here):
    """O pai do `git rev-parse --git-common-dir` de `here`, que é o checkout principal mesmo num worktree, ou None se o
    git falha ou o diretório comum não é um `.git`. A mesma função do fact_outcome.py."""
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
    """A raiz vem da BRZA_WORKSPACE, que a função do shell passa. Sem ela e sem a MEASURE_DIR (a do teste), o checkout
    principal pelo git: o terminal de um worktree leria o `.measurements/` velho do próprio worktree, e o `..` de um
    worktree cairia em `.worktrees/`. Se o git falhar, vale o diretório acima de scripts/."""
    env = os.environ if env is None else env
    if env.get("BRZA_WORKSPACE"):
        return env["BRZA_WORKSPACE"]
    if not env.get("MEASURE_DIR"):
        root = git_root(here)
        if root:
            return root
    return os.path.join(here, "..")


WORKSPACE = workspace_root()
DEFAULT_DIR = os.path.normpath(os.path.join(WORKSPACE, ".measurements"))
LOG = "measurements.jsonl"
UNKNOWN = "—"
NO_WORKSTREAM = "(sem workstream)"  # a linha de antes do campo `workstream`, ou de um cwd fora dos workstreams


def read_log(path, since):
    """As linhas do log a partir da data, e quantas não eram JSON. Log ausente devolve nada, sem erro."""
    rows, bad = [], 0
    if not os.path.isfile(path):
        return rows, bad, False
    with open(path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                bad += 1
                continue
            if since and str(row.get("timestamp", ""))[:10] < since:
                continue
            rows.append(row)
    return rows, bad, True


def short_name(value):
    return str(value or "?").rpartition(":")[2]


def label(value):
    return UNKNOWN if value in (None, "") else str(value)


def reason_key(reason):
    """A parte do motivo que identifica a regra, sem o alvo: tira o prefixo do hook e o que vem entre crases."""
    text = re.sub(r"^PreToolUse:\w+ hook error:\s*", "", str(reason))
    text = re.sub(r"`[^`]*`", "`…`", text)
    return " ".join(text.split())[:110]


def new_cost_bucket():
    return {"n": 0, "cost": 0.0, "no_cost": 0, "duration_s": 0.0, "api_calls": 0, "denials": 0, "resumes": 0}


def add_cost(bucket, row):
    bucket["n"] += 1
    cost = row.get("estimated_cost_usd")
    if cost is None:
        bucket["no_cost"] += 1
    else:
        bucket["cost"] += float(cost)
    bucket["duration_s"] += float(row.get("duration_s") or 0)
    bucket["api_calls"] += int(row.get("api_calls") or 0)
    bucket["denials"] += int(row.get("denials") or 0)
    if row.get("segment") == "resume":
        bucket["resumes"] += 1


def aggregate(measurements):
    agents = collections.defaultdict(new_cost_bucket)  # (agente, modelo, esforço)
    models = collections.defaultdict(new_cost_bucket)
    versions = collections.defaultdict(lambda: {"subagents": 0, "cost": 0.0, "skills": 0, "commands": 0})
    turns = collections.defaultdict(new_cost_bucket)  # (modelo, esforço)
    turns_by_skill = collections.defaultdict(new_cost_bucket)
    skills = collections.Counter()
    commands = collections.Counter()  # (comando, origem)
    failures = collections.Counter()
    mcp = collections.defaultdict(lambda: {"n": 0, "failed": 0, "duration_ms": 0})
    guard = collections.defaultdict(lambda: {"n": 0, "cost": 0.0, "no_cost": 0, "context": 0, "idle_min": 0})
    compact = collections.Counter()  # (workstream, decisão) da trava do /compact
    route = {"decision": collections.Counter(), "held_by": collections.Counter(), "resolver": collections.Counter()}
    reasons = collections.Counter()
    denials = []
    hook_errors = 0

    for row in measurements:
        event = row.get("event")
        if event == "turn" and row.get("fact_audit"):
            continue  # o turno da sessão `claude -p` da auditoria: o custo dela vai no --facts, e não no da sessão principal
        if event == "subagent":
            agent = short_name(row.get("agent_type"))
            add_cost(agents[(agent, label(row.get("model")), label(row.get("effort")))], row)
            add_cost(models[label(row.get("model"))], row)
            if row.get("plugin_version") and row.get("agent_type"):
                version = versions[f"{row['agent_type'].partition(':')[0]} {row['plugin_version']}"]
                version["subagents"] += 1
                version["cost"] += float(row.get("estimated_cost_usd") or 0)
            for reason in row.get("denial_reasons") or []:
                reasons[reason_key(reason)] += 1
                denials.append((str(row.get("timestamp", ""))[:16], agent, str(reason)))
        elif event == "turn":
            add_cost(turns[(label(row.get("model")), label(row.get("effort")))], row)
            for name in sorted(set(row.get("skills") or [])):
                add_cost(turns_by_skill[short_name(name)], row)
        elif event == "skill":
            skills[short_name(row.get("skill"))] += 1
            if row.get("plugin_version"):
                versions[f"{str(row.get('skill')).partition(':')[0]} {row['plugin_version']}"]["skills"] += 1
        elif event == "command":
            commands[(str(row.get("command") or "?"), label(row.get("source")))] += 1
            if row.get("plugin_version"):
                versions[f"{str(row.get('command')).partition(':')[0]} {row['plugin_version']}"]["commands"] += 1
        elif event == "failure":
            failures[(label(row.get("error")), short_name(row.get("agent_type")) if row.get("agent_type") else "principal")] += 1
        elif event == "mcp":
            server = mcp[label(row.get("server"))]
            server["n"] += 1
            server["failed"] += 1 if row.get("failed") else 0
            server["duration_ms"] += int(row.get("duration_ms") or 0)
        elif event == "route":  # o orquestrador MCP: uma linha por decisão de roteamento
            route["decision"][label(row.get("decision"))] += 1
            if row.get("held_by"):
                route["held_by"][str(row["held_by"])] += 1
            if isinstance(row.get("resolver"), dict) and row["resolver"].get("path"):
                route["resolver"][str(row["resolver"]["path"])] += 1
        elif event == "guard" and row.get("kind") == "compact":
            compact[(label(row.get("workstream")), label(row.get("decision")))] += 1
        elif event == "guard":
            bucket = guard[label(row.get("decision"))]
            bucket["n"] += 1
            bucket["context"] += int(row.get("max_context") or 0)
            bucket["idle_min"] += int(row.get("idle_min") or 0)
            if row.get("estimated_cost_usd") is not None:  # só a linha `blocked` traz o custo
                bucket["cost"] += float(row["estimated_cost_usd"])
            elif row.get("decision") == "blocked":
                bucket["no_cost"] += 1
        elif event == "hook_error":
            hook_errors += 1

    return {
        "agents": agents, "models": models, "versions": versions, "turns": turns, "turns_by_skill": turns_by_skill,
        "skills": skills, "commands": commands, "failures": failures, "mcp": mcp, "guard": guard, "compact": compact,
        "route": route, "reasons": reasons,
        "denials": denials, "hook_errors": hook_errors,
    }


def usd(value):
    """Duas casas, como o Claude grava; quatro abaixo de um centavo."""
    return f"{value:.4f}" if 0 < abs(value) < 0.01 else f"{value:.2f}"


def money(bucket):
    text = usd(bucket["cost"])
    return text + (f" (+{bucket['no_cost']} s/ custo)" if bucket["no_cost"] else "")


def mean(bucket):
    return usd(bucket["cost"] / max(bucket["n"] - bucket["no_cost"], 1))


def kilo(n):
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def print_table(title, headers, aligns, rows):
    if not rows:
        return
    print(title)
    widths = [max(len(str(r[i])) for r in [headers] + rows) for i in range(len(headers))]

    def fmt(row):
        return "  ".join(str(c).ljust(w) if a == "<" else str(c).rjust(w) for c, w, a in zip(row, widths, aligns))

    print(fmt(headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(fmt(r))
    print()


def print_report(agg, show_denials):
    by_cost = lambda items: sorted(items, key=lambda kv: -kv[1]["cost"])  # noqa: E731
    print_table("Subagentes, por agente, modelo e esforço",
                ("agente", "modelo", "esforço", "n", "US$ total", "US$ média", "min", "api", "neg", "retom."),
                ("<", "<", "<", ">", ">", ">", ">", ">", ">", ">"),
                [(a, m, e, b["n"], money(b), mean(b), f"{b['duration_s'] / 60:.1f}", b["api_calls"], b["denials"],
                  b["resumes"] or "") for (a, m, e), b in by_cost(agg["agents"].items())])
    print_table("Subagentes, por modelo", ("modelo", "n", "US$ total", "US$ média", "min", "api"),
                ("<", ">", ">", ">", ">", ">"),
                [(m, b["n"], money(b), mean(b), f"{b['duration_s'] / 60:.1f}", b["api_calls"])
                 for m, b in by_cost(agg["models"].items())])
    print_table("Por versão do plugin", ("plugin e versão", "subagentes", "US$ subagentes", "skills", "comandos"),
                ("<", ">", ">", ">", ">"),
                [(k, v["subagents"], usd(v["cost"]), v["skills"], v["commands"])
                 for k, v in sorted(agg["versions"].items())])
    print_table("Turnos da sessão principal, por modelo e esforço",
                ("modelo", "esforço", "turnos", "US$ total", "US$ média", "min", "api", "retom."),
                ("<", "<", ">", ">", ">", ">", ">", ">"),
                [(m, e, b["n"], money(b), mean(b), f"{b['duration_s'] / 60:.1f}", b["api_calls"], b["resumes"] or "")
                 for (m, e), b in by_cost(agg["turns"].items())])
    print_table("Turnos com skill — o turno inteiro, que pode ter mais de uma skill",
                ("skill", "turnos", "US$ total", "US$ média", "min"), ("<", ">", ">", ">", ">"),
                [(s, b["n"], money(b), mean(b), f"{b['duration_s'] / 60:.1f}")
                 for s, b in by_cost(agg["turns_by_skill"].items())])
    print_table("Skills (evento skill, o começo da janela)", ("skill", "n"), ("<", ">"), agg["skills"].most_common())
    print_table("Comandos digitados", ("comando", "origem", "n"), ("<", "<", ">"),
                [(c, s, n) for (c, s), n in agg["commands"].most_common()])
    print_table("Falhas, por tipo de erro", ("erro", "onde", "n"), ("<", "<", ">"),
                [(e, w, n) for (e, w), n in agg["failures"].most_common()])
    print_table("MCP, por servidor", ("servidor", "chamadas", "falhas", "ms médio"), ("<", ">", ">", ">"),
                [(s, b["n"], b["failed"], round(b["duration_ms"] / max(b["n"], 1)))
                 for s, b in sorted(agg["mcp"].items(), key=lambda kv: -kv[1]["n"])])
    print_table("Trava de retomada, por decisão (US$ é a regravação estimada na decisão)",
                ("decisão", "n", "parada média (min)", "contexto médio", "US$ total"), ("<", ">", ">", ">", ">"),
                [(d, b["n"], round(b["idle_min"] / b["n"]), kilo(round(b["context"] / b["n"])), money(b))
                 for d, b in sorted(agg["guard"].items())])
    print_table("Trava do /compact, por workstream e decisão", ("workstream", "decisão", "n"), ("<", "<", ">"),
                [(w, d, n) for (w, d), n in sorted(agg["compact"].items())])
    if any(agg["route"].values()):
        print_table("Roteamento do orquestrador (linha route), por decisão", ("decisão", "n"), ("<", ">"),
                    agg["route"]["decision"].most_common())
        print_table("Roteamento, pedidos segurados por teto (decisão held)", ("teto", "n"), ("<", ">"),
                    agg["route"]["held_by"].most_common())
        print_table("Roteamento, desempate do resolver por caminho", ("caminho", "n"), ("<", ">"),
                    agg["route"]["resolver"].most_common())
    print_table("Negações de hook, por motivo", ("n", "motivo (sem o alvo)"), (">", "<"),
                [(n, r) for r, n in agg["reasons"].most_common()])
    if show_denials and agg["denials"]:
        print("Cada negação")
        for when, agent, reason in agg["denials"]:
            print(f"- {when} {agent}: {reason}")
        print()
    if agg["hook_errors"]:
        print(f"Erros do próprio hook de medição: {agg['hook_errors']}")


def to_json(agg):
    def bucket_rows(mapping, keys):
        return [{**dict(zip(keys, k if isinstance(k, tuple) else (k,))), **v} for k, v in mapping.items()]

    return {
        "agents": bucket_rows(agg["agents"], ("agent", "model", "effort")),
        "models": bucket_rows(agg["models"], ("model",)),
        "plugin_versions": bucket_rows(agg["versions"], ("plugin_version",)),
        "turns": bucket_rows(agg["turns"], ("model", "effort")),
        "turns_by_skill": bucket_rows(agg["turns_by_skill"], ("skill",)),
        "skills": dict(agg["skills"]),
        "commands": [{"command": c, "source": s, "n": n} for (c, s), n in agg["commands"].items()],
        "failures": [{"error": e, "where": w, "n": n} for (e, w), n in agg["failures"].items()],
        "mcp": bucket_rows(agg["mcp"], ("server",)),
        "guard": bucket_rows(agg["guard"], ("decision",)),
        "compact_guard": [{"workstream": w, "decision": d, "n": n} for (w, d), n in agg["compact"].items()],
        "denial_reasons": dict(agg["reasons"]),
        "route": {k: dict(v) for k, v in agg["route"].items()},
        "hook_errors": agg["hook_errors"],
    }


def new_workstream_day():
    return {"turns": new_cost_bucket(), "subagents": collections.defaultdict(new_cost_bucket),
            # (dono, é feedback?) → {created, updated, deleted}
            "memory": collections.defaultdict(lambda: {"created": 0, "updated": 0, "deleted": 0}),
            "live": 0,
            "rounds": {"questions": 0, "recommended": 0, "other_option": 0, "free_text": 0}}


def aggregate_by_workstream(measurements):
    """{(workstream, dia): contas}: turnos, custo de subagente por agent_type, memórias por dono (as `feedback` à parte),
    as vivas no fim do dia e as rodadas. O dia é o do `timestamp` da linha. O índice (MEMORY.md, type "index") não é
    memória e fica de fora das contas. As vivas saem dos eventos, acumuladas dos dias anteriores: created e updated
    põem o arquivo vivo; deleted tira."""
    days = collections.defaultdict(new_workstream_day)
    for row in measurements:
        event = row.get("event")
        if event not in ("turn", "subagent", "memory") or (event == "turn" and row.get("fact_audit")):
            continue  # o turno de auditoria fica de fora: o custo dela vai no --facts
        workstream = row.get("workstream") or NO_WORKSTREAM
        day = days[(workstream, str(row.get("timestamp", ""))[:10] or UNKNOWN)]
        if event == "turn":
            add_cost(day["turns"], row)
            for field in day["rounds"]:
                day["rounds"][field] += int((row.get("rounds") or {}).get(field) or 0)
        elif event == "subagent":
            add_cost(day["subagents"][short_name(row.get("agent_type"))], row)
        elif row.get("type") != "index" and row.get("action") in ("created", "updated", "deleted"):
            owner = row.get("owner") or UNKNOWN
            day["memory"][(owner, row.get("type") == "feedback")][row["action"]] += 1
    # As vivas de cada dia: o acumulado do workstream até o fim dele, recontado na ordem dos dias.
    running = collections.defaultdict(set)
    events = sorted(((str(r.get("timestamp", "")), r) for r in measurements
                     if r.get("event") == "memory" and r.get("type") != "index"), key=lambda t: t[0])
    live_at = {}  # (workstream, dia) → vivas no fim do dia
    for stamp, row in events:
        workstream = row.get("workstream") or NO_WORKSTREAM
        key = (row.get("owner") or UNKNOWN, row.get("file"))
        (running[workstream].discard if row.get("action") == "deleted" else running[workstream].add)(key)
        live_at[(workstream, stamp[:10])] = len(running[workstream])
    last = {}
    for (workstream, day) in sorted(days):
        if (workstream, day) in live_at:
            last[workstream] = live_at[(workstream, day)]
        days[(workstream, day)]["live"] = last.get(workstream, 0)
    return dict(days)


def pct(part, whole):
    return f"{100 * part / whole:.0f}%" if whole else ""


def print_by_workstream(days):
    keys = sorted(days)
    print_table("Por workstream e dia — turnos, custo e rodadas",
                ("workstream", "dia", "turnos", "US$ total", "US$ por turno", "perguntas", "recomendada", "outra opção",
                 "texto livre"), ("<", "<", ">", ">", ">", ">", ">", ">", ">"),
                [(w, d, v["turns"]["n"], money(v["turns"]), mean(v["turns"]) if v["turns"]["n"] else "",
                  v["rounds"]["questions"] or "", pct(v["rounds"]["recommended"], v["rounds"]["questions"]),
                  pct(v["rounds"]["other_option"], v["rounds"]["questions"]),
                  pct(v["rounds"]["free_text"], v["rounds"]["questions"])) for (w, d), v in ((k, days[k]) for k in keys)])
    print_table("Subagentes por workstream, dia e agent_type", ("workstream", "dia", "agent_type", "n", "US$ total"),
                ("<", "<", "<", ">", ">"),
                [(w, d, a, b["n"], money(b)) for (w, d) in keys
                 for a, b in sorted(days[(w, d)]["subagents"].items(), key=lambda kv: -kv[1]["cost"])])
    memory_rows = []
    for (w, d) in keys:
        v = days[(w, d)]
        for (owner, feedback), c in sorted(v["memory"].items(), key=lambda kv: (kv[0][0], kv[0][1])):
            memory_rows.append((w, d, owner, "feedback" if feedback else "outras", c["created"], c["updated"],
                                c["deleted"], v["live"]))
    print_table("Memórias por workstream, dia e dono — vivas é o total do workstream no fim do dia",
                ("workstream", "dia", "dono", "tipo", "criadas", "editadas", "apagadas", "vivas"),
                ("<", "<", "<", "<", ">", ">", ">", ">"), memory_rows)


def to_json_by_workstream(days):
    return [{"workstream": w, "day": d,
             "turns": days[(w, d)]["turns"],
             "subagents": {a: b for a, b in days[(w, d)]["subagents"].items()},
             "memory": [{"owner": o, "feedback": f, **c} for (o, f), c in days[(w, d)]["memory"].items()],
             "live_memories": days[(w, d)]["live"], "rounds": days[(w, d)]["rounds"]} for (w, d) in sorted(days)]


FACT_BANDS = ("90+", "80–89", "<80")
FACT_KINDS = ("rodado", "lido", "doc", "inferido")
FACT_CLASSES = ("fato", "custo")
PENDING_HINT = "python3 scripts/fact_outcome.py --pending --workstream all"  # a contagem é de todos os workstreams (PLN-005/R10.3)


def fact_band(confidence):
    if isinstance(confidence, (int, float)) and confidence >= 90:
        return FACT_BANDS[0]
    if isinstance(confidence, (int, float)) and confidence >= 80:
        return FACT_BANDS[1]
    return FACT_BANDS[2]  # a confiança que não é número também fica a conferir


def in_range(row, since):
    return not since or str(row.get("timestamp", ""))[:10] >= since


def last_by_fact(measurements, event):
    """{(agent_id, fact_id): o último evento do tipo}, pela ordem do log."""
    last = {}
    for row in measurements:
        if row.get("event") == event:
            last[(row.get("agent_id"), row.get("fact_id"))] = row
    return last


def fact_sort_key(key):
    agent, klass, band, kind = key
    return (agent, FACT_CLASSES.index(klass), FACT_BANDS.index(band),
            FACT_KINDS.index(kind) if kind in FACT_KINDS else len(FACT_KINDS), kind)


def aggregate_facts(measurements, since=""):
    """O acerto dos fatos do desenho: {rows, captures, audits, pending}. O fato é o par (agent_id, fact_id), e vale o
    primeiro `fact` de cada par; o `--since` filtra pela data dele. O veredito final é o último `fact_outcome` da sessão
    (holds ou fails) e, sem ele, o último `fact_check` do Haiku; o desfecho e o check que chegam depois da data valem.
    O `unknown` não entra no acerto. O `cite` vem do último `fact_check`. As leituras (`fact_capture`) e as auditorias
    (`fact_audit`) filtram pela data delas."""
    first = {}
    for row in measurements:
        if row.get("event") == "fact":
            first.setdefault((row.get("agent_id"), row.get("fact_id")), row)
    checks, outcomes = last_by_fact(measurements, "fact_check"), last_by_fact(measurements, "fact_outcome")
    buckets, pending = {}, 0
    for key, row in first.items():
        if not in_range(row, since):
            continue
        fact_id = str(row.get("fact_id") or "")
        confidence, kind = row.get("confidence"), label(row.get("kind"))
        klass = "custo" if fact_id.startswith("C") else "fato"
        session, haiku = outcomes.get(key), checks.get(key)
        verdict = session.get("verdict") if session and session.get("verdict") in ("holds", "fails") else None
        if verdict is None and haiku:
            verdict = haiku.get("verdict") if haiku.get("verdict") in ("holds", "fails", "unknown") else None
        bucket = buckets.setdefault((short_name(row.get("agent_type")), klass, fact_band(confidence), kind), {
            "facts": 0, "with_outcome": 0, "holds": 0, "fails": 0, "unknown": 0, "confidence_n": 0,
            "confidence_sum": 0.0, "checked": 0, "cite_off": 0, "downgraded": 0})
        bucket["facts"] += 1
        if verdict == "unknown":
            bucket["unknown"] += 1
        elif verdict:
            bucket["with_outcome"] += 1
            bucket[verdict] += 1
            if isinstance(confidence, (int, float)):
                bucket["confidence_n"] += 1
                bucket["confidence_sum"] += confidence
        if haiku:
            bucket["checked"] += 1
            bucket["cite_off"] += 1 if haiku.get("cite") == "off" else 0
            # rebaixado: o último check teve o veredito do auditor trocado por `unknown` porque o `read` não casou; o
            # check antigo, sem `read_ok`, não conta
            if haiku.get("read_ok") is False and haiku.get("claimed") not in (None, "unknown"):
                bucket["downgraded"] += 1
        if key not in outcomes and (kind == "inferido" or klass == "custo" or fact_band(confidence) == FACT_BANDS[2]):
            pending += 1

    rows = {}
    for key in sorted(buckets, key=fact_sort_key):
        b = buckets[key]
        holds_pct = 100 * b["holds"] / b["with_outcome"] if b["with_outcome"] else None
        mean_confidence = b["confidence_sum"] / b["confidence_n"] if b["confidence_n"] else None
        rows[key] = {
            "facts": b["facts"], "with_outcome": b["with_outcome"], "holds": b["holds"], "fails": b["fails"],
            "holds_pct": holds_pct, "mean_confidence": mean_confidence,
            "distance": mean_confidence - holds_pct if holds_pct is not None and mean_confidence is not None else None,
            "unknown": b["unknown"], "downgraded": b["downgraded"], "checked": b["checked"], "cite_off": b["cite_off"],
            "cite_off_pct": 100 * b["cite_off"] / b["checked"] if b["checked"] else None}

    captures = {}
    audits = {"n": 0, "cost": 0.0, "failed": 0, "ignored": 0, "downgraded": 0}
    for row in measurements:
        if not in_range(row, since):
            continue
        if row.get("event") == "fact_capture":
            c = captures.setdefault(short_name(row.get("agent_type")), {"read": 0, "unmarked": 0, "malformed": 0})
            c["read"] += 1
            c["unmarked"] += 1 if row.get("unmarked") else 0
            c["malformed"] += int(row.get("malformed") or 0)
        elif row.get("event") == "fact_audit":
            audits["n"] += 1
            audits["cost"] += float(row.get("cost_usd") or 0)
            audits["failed"] += 1 if row.get("error") else 0
            audits["ignored"] += int(row.get("ignored") or 0)
            audits["downgraded"] += int(row.get("downgraded") or 0)
    audits["cost"] = round(audits["cost"], 6)
    return {"rows": rows, "captures": dict(sorted(captures.items())), "audits": audits, "pending": pending}


def signed(value):
    value = round(value, 1) + 0.0  # tira o -0.0
    return f"{value:+.1f}"


def percent(value):
    return UNKNOWN if value is None else f"{value:.1f}%"


def print_facts(facts):
    print_table("Fatos do desenho, por papel, classe, faixa de confiança e tipo — distância = confiança média menos "
                "holds %, positiva é excesso de confiança",
                ("papel", "classe", "faixa", "tipo", "fatos", "com desfecho", "holds %", "confiança média", "distância",
                 "unknown", "rebaixados", "cite off %"),
                ("<", "<", "<", "<", ">", ">", ">", ">", ">", ">", ">", ">"),
                [(agent, klass, band, kind, r["facts"], r["with_outcome"], percent(r["holds_pct"]),
                  UNKNOWN if r["mean_confidence"] is None else f"{r['mean_confidence']:.1f}",
                  UNKNOWN if r["distance"] is None else signed(r["distance"]), r["unknown"], r["downgraded"],
                  percent(r["cite_off_pct"]))
                 for (agent, klass, band, kind), r in facts["rows"].items()])
    if facts["captures"]:
        print("Por papel: " + "; ".join(f"{a}: lidas {c['read']}, sem marca {c['unmarked']}, malformadas {c['malformed']}"
                                        for a, c in facts["captures"].items()))
    audits = facts["audits"]
    if audits["n"]:
        print(f"Auditorias: rodaram {audits['n']}, custo US$ {audits['cost']:.4f}, falharam {audits['failed']}, "
              f"ignorados {audits['ignored']}, rebaixados {audits['downgraded']}")
    if facts["rows"]:
        print(f"Pendentes: {facts['pending']} sem desfecho (inferido, abaixo de 80 ou custo) — {PENDING_HINT}")


def to_json_facts(facts):
    return {"rows": [{"agent": a, "class": k, "band": b, "kind": t, **r} for (a, k, b, t), r in facts["rows"].items()],
            "captures": [{"agent": a, **c} for a, c in facts["captures"].items()],
            "audits": facts["audits"], "pending": facts["pending"]}

# --- A medida por tarefa (PLN-012, D4) --------------------------------------------------------------------------------
#
# A tarefa é o worklog: o `brza note commit` grava um evento `task` por commit com worklog (D1), e cada um é um marco da
# sessão. O segmento k de uma sessão são os turnos dela cujo começo (`timestamp − duration_s`) cai em
# (marco k−1, marco k]; depois do último marco, os turnos ficam "sem tarefa". O `brza-note` usa `task_segments` e
# `task_measure` por `runpy`, para a `## Medida` do worklog e esta tabela darem o mesmo número.

PAUSE_S = 1800  # a espera acima disto é pausa, e não tempo de Anderson (PLN-012/R2.3)
STALE_S = 2 * 3600  # a outra sessão do workstream parada há mais disto entra no aviso do --pending (R3.3)
NO_SESSION = "(sem sessão)"
ADDITIVE = ("agent_s", "anderson_s", "pause_s", "elapsed_s", "cost_turns", "cost_subagents", "cpu_self_s",
            "cpu_children_s", "run_cpu_s", "run_duration_s")
COUNTS = ("turns", "subagents", "runs", "machine_turns", "no_cost_turns", "no_cost_subagents")
PEAKS = ("rss_max_mb", "run_max_rss_mb")


def parse_time(value):
    """O `datetime` com fuso de um carimbo ISO 8601, ou None. Sem fuso, vale o local."""
    if not value:
        return None
    try:
        stamp = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.astimezone()


def row_end(row):
    return parse_time(row.get("timestamp"))


def row_start(row):
    """O começo da linha: o `timestamp` é o fim, e o `duration_s` volta até o primeiro pedido do trecho (D1)."""
    end = row_end(row)
    try:
        duration = max(float(row.get("duration_s") or 0), 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return end - datetime.timedelta(seconds=duration) if end else None


def task_mark(task):
    """O marco do evento `task`: o `until` (`HH:MM`, do dia e no fuso do commit, ou ISO), ou o `timestamp`."""
    stamp, until = row_end(task), task.get("until")
    if until and stamp:
        match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(until).strip())
        if match:
            hour, minute, second = (int(g or 0) for g in match.groups())
            return stamp.replace(hour=hour, minute=minute, second=second, microsecond=0)
        return parse_time(until) or stamp
    return stamp


def counts_for_task(row):
    """O turno, o subagente e o `run` entram na tarefa; o turno da auditoria fica de fora, como no --by-workstream."""
    event = row.get("event")
    return event in ("subagent", "run") or (event == "turn" and not row.get("fact_audit"))


def task_segments(rows):
    """Os segmentos de cada sessão, na ordem dos marcos: `{"session_id", "workstream", "task", "worklogs", "start",
    "end", "rows"}`, com `start` e `end` os marcos que o cercam (ISO, ou None) e `rows` os turnos, subagentes e `run`
    dele. O segmento de depois do último marco, com `task` None, é o "sem tarefa"; o `task` sem sessão vira um segmento
    sem linhas. Só contam as linhas que terminam a partir do primeiro evento `task` do log (R4.1)."""
    tasks = [r for r in rows if r.get("event") == "task" and row_end(r)]
    first = min((row_end(t) for t in tasks), default=None)
    items = [r for r in rows if counts_for_task(r) and row_end(r) and (first is None or row_end(r) >= first)]

    order, marks = [], collections.defaultdict(list)  # sessão → [(marco, task)], em ordem
    loose = []
    for t in sorted(tasks, key=task_mark):
        sid = t.get("session_id") or None
        if sid is None:
            loose.append(t)
            continue
        if sid not in marks:
            order.append(sid)
        marks[sid].append((task_mark(t), t))
    for r in items:
        sid = r.get("session_id") or None
        if sid not in marks:
            order.append(sid)
            marks[sid] = []

    by_session = {}
    for sid in order:
        segments, previous = [], None
        for mark, t in marks[sid]:
            segments.append({"session_id": sid, "workstream": t.get("workstream"), "task": t,
                             "worklogs": list(t.get("worklogs") or []), "start": previous and previous.isoformat(),
                             "end": mark.isoformat(), "rows": []})
            previous = mark
        segments.append({"session_id": sid, "workstream": None, "task": None, "worklogs": [],
                         "start": previous and previous.isoformat(), "end": None, "rows": []})
        by_session[sid] = segments

    def by_marks(sid, start):
        stamps = [m for m, _ in marks[sid]]
        return by_session[sid][bisect.bisect_left(stamps, start)]  # o turno do marco fica com a tarefa que ele fecha

    turns_of = collections.defaultdict(list)  # sessão → [(começo, fim, segmento)]
    for r in sorted((r for r in items if r.get("event") == "turn"), key=row_start):
        sid = r.get("session_id") or None
        segment = by_marks(sid, row_start(r))
        segment["rows"].append(r)
        turns_of[sid].append((row_start(r), row_end(r), segment))
    for r in sorted((r for r in items if r.get("event") != "turn"), key=row_start):
        sid, start = r.get("session_id") or None, row_start(r)
        # o subagente e o `run` vão ao turno da sessão que contém o começo deles; fora de turno, pelos marcos (D4)
        inside = next((seg for s, e, seg in turns_of[sid] if s <= start <= e), None)
        (inside or by_marks(sid, start))["rows"].append(r)

    segments = [seg for sid in order for seg in by_session[sid]]
    for seg in segments:
        if seg["workstream"] is None:
            seg["workstream"] = next((r.get("workstream") for r in reversed(seg["rows"]) if r.get("workstream")), None)
    for t in loose:
        segments.append({"session_id": None, "workstream": t.get("workstream"), "task": t,
                         "worklogs": list(t.get("worklogs") or []), "start": None, "end": task_mark(t).isoformat(),
                         "rows": []})
    return segments


def new_measure():
    measure = {key: 0.0 for key in ADDITIVE}
    measure.update({key: 0 for key in COUNTS})
    measure.update({key: None for key in PEAKS})
    measure.update({"start": None, "end": None})
    return measure


def peak(current, value):
    if not isinstance(value, (int, float)):
        return current
    return value if current is None else max(current, value)


def split_wait(measure, wait):
    measure["anderson_s"] += min(wait, PAUSE_S)
    measure["pause_s"] += max(wait - PAUSE_S, 0)


def task_measure(rows):
    """Os três tempos, o custo e a máquina das linhas de um segmento (D2): agente = Σ (duration_s − Σ waits_s);
    Anderson e pausa dividem, no PAUSE_S, cada `waits_s` e cada intervalo entre dois turnos; o corrido é a soma. A
    linha sem `waits_s` não teve espera; sem `machine`, não tem CPU (`machine_turns` conta as que têm)."""
    measure = new_measure()
    turns = sorted((r for r in rows if r.get("event") == "turn" and row_end(r)), key=row_start)
    last_end = None
    for r in turns:
        start, end = row_start(r), row_end(r)
        waits = [max(float(w), 0.0) for w in (r.get("waits_s") or []) if isinstance(w, (int, float))]
        measure["agent_s"] += max((end - start).total_seconds() - sum(waits), 0.0)
        if last_end is not None:
            waits.append(max((start - last_end).total_seconds(), 0.0))
        for wait in waits:
            split_wait(measure, wait)
        last_end = end if last_end is None else max(last_end, end)
        measure["turns"] += 1
        if r.get("estimated_cost_usd") is None:
            measure["no_cost_turns"] += 1
        else:
            measure["cost_turns"] += float(r["estimated_cost_usd"])
        machine = r.get("machine")
        if isinstance(machine, dict):
            measure["machine_turns"] += 1
            measure["cpu_self_s"] += float(machine.get("cpu_self_s") or 0)
            measure["cpu_children_s"] += float(machine.get("cpu_children_s") or 0)
            measure["rss_max_mb"] = peak(measure["rss_max_mb"], machine.get("rss_mb"))
    measure["elapsed_s"] = measure["agent_s"] + measure["anderson_s"] + measure["pause_s"]
    if turns:
        measure["start"], measure["end"] = row_start(turns[0]).isoformat(), last_end.isoformat()
    for r in rows:
        if r.get("event") == "subagent":
            measure["subagents"] += 1
            if r.get("estimated_cost_usd") is None:
                measure["no_cost_subagents"] += 1
            else:
                measure["cost_subagents"] += float(r["estimated_cost_usd"])
        elif r.get("event") == "run":
            measure["runs"] += 1
            measure["run_cpu_s"] += float(r.get("cpu_s") or 0)
            measure["run_duration_s"] += float(r.get("duration_s") or 0)
            measure["run_max_rss_mb"] = peak(measure["run_max_rss_mb"], r.get("max_rss_mb"))
    return measure


def add_measure(total, measure, share=1.0):
    """Soma a medida de um segmento à da tarefa. O segmento de um commit com n worklogs entra com share = 1/n nos tempos,
    no custo e na CPU; as contagens e os picos entram inteiros."""
    for key in ADDITIVE:
        total[key] += measure[key] * share
    for key in COUNTS:
        total[key] += measure[key]
    for key in PEAKS:
        total[key] = peak(total[key], measure[key])
    for key, pick in (("start", min), ("end", max)):
        values = [v for v in (total[key], measure[key]) if v]
        total[key] = pick(values, key=parse_time) if values else None
    return total


def worklog_day_and_name(path):
    name = os.path.basename(str(path))
    name = name[:-3] if name.endswith(".md") else name
    match = re.match(r"(\d{4}-\d{2}-\d{2})-(.*)", name)
    return (match.group(1), match.group(2)) if match else (None, name)


def session_label(sid, tasks):
    if sid is None:
        return NO_SESSION
    names = [t.get("session_name") for t in tasks if t.get("session_id") == sid and t.get("session_name")]
    return names[-1] if names else str(sid)[:8]


def aggregate_by_task(rows, since="", workstream=""):
    """{"tasks", "unmeasured", "sessions"} do --by-task (D4). A tarefa é o worklog, somado em todos os segmentos e
    sessões que o fecharam. Não medido é o `task` sem sessão ou de sessão sem turno no log. A sessão traz a ociosa — do
    fim do último turno de um segmento ao começo do primeiro do seguinte — e os turnos sem tarefa. O `--since` e o
    `--workstream` filtram a saída: a tarefa pelo último marco, e a sessão pelo último turno."""
    segments = task_segments(rows)
    task_rows = [r for r in rows if r.get("event") == "task"]
    with_turns = {seg["session_id"] for seg in segments
                  if seg["session_id"] and any(r.get("event") == "turn" for r in seg["rows"])}

    tasks, unmeasured = {}, []
    for seg in segments:
        t = seg["task"]
        if not t:
            continue
        if not seg["session_id"] or seg["session_id"] not in with_turns:
            for path in seg["worklogs"]:
                unmeasured.append({"worklog": path, "commit": t.get("commit"), "timestamp": seg["end"],
                                   "workstream": t.get("workstream"),
                                   "reason": "sem sessão" if not seg["session_id"] else "sessão sem turno"})
            continue
        measure = task_measure(seg["rows"])
        share = 1.0 / max(len(seg["worklogs"]), 1)
        for path in seg["worklogs"]:
            acc = tasks.get(path)
            if acc is None:
                day, name = worklog_day_and_name(path)
                acc = tasks[path] = {"worklog": path, "workstream": t.get("workstream") or NO_WORKSTREAM,
                                     "day": day or seg["end"][:10], "task": name, "session_ids": [], "commit_ids": [],
                                     "shared_with": 0, "last_mark": seg["end"], "segments": [], **new_measure()}
            add_measure(acc, measure, share)
            if seg["session_id"] not in acc["session_ids"]:
                acc["session_ids"].append(seg["session_id"])
            if t.get("commit") not in acc["commit_ids"]:
                acc["commit_ids"].append(t.get("commit"))
            acc["shared_with"] = max(acc["shared_with"], len(seg["worklogs"]) - 1)
            acc["last_mark"] = max(acc["last_mark"], seg["end"], key=parse_time)
            acc["workstream"] = t.get("workstream") or acc["workstream"]
            acc["segments"].append({"session_id": seg["session_id"], "commit": t.get("commit"), "start": measure["start"],
                                    "end": measure["end"], "mark": seg["end"], "turns": measure["turns"]})

    sessions = []
    for sid in dict.fromkeys(seg["session_id"] for seg in segments if seg["rows"]):  # a sessão só de `task` fica fora
        own = [seg for seg in segments if seg["session_id"] == sid and (seg["rows"] or seg["task"] is None)]
        idle, last_end = 0.0, None
        for seg in own:
            measure = task_measure(seg["rows"])
            if measure["start"] is None:
                continue
            if last_end is not None:
                idle += max((parse_time(measure["start"]) - last_end).total_seconds(), 0.0)
            last_end = parse_time(measure["end"])
        open_ = task_measure(own[-1]["rows"]) if own and own[-1]["task"] is None else new_measure()
        ends = [row_end(r) for seg in own for r in seg["rows"]]
        workstreams = [r.get("workstream") for seg in own for r in seg["rows"] if r.get("workstream")]
        sessions.append({"session_id": sid, "name": session_label(sid, task_rows),
                         "workstream": workstreams[-1] if workstreams else NO_WORKSTREAM, "idle_s": idle,
                         "no_task_turns": open_["turns"], "no_task_subagents": open_["subagents"],
                         "no_task_cost_turns": open_["cost_turns"], "no_task_cost_subagents": open_["cost_subagents"],
                         "no_task_no_cost": open_["no_cost_turns"] + open_["no_cost_subagents"],
                         "no_task_since": open_["start"], "last": max(ends).isoformat() if ends else None})

    def keep(stamp, ws):
        return (not since or str(stamp or "")[:10] >= since) and (not workstream or ws == workstream)

    out = []
    for acc in sorted(tasks.values(), key=lambda a: (a["workstream"], a["day"], parse_time(a["last_mark"]))):
        if not keep(acc["last_mark"], acc["workstream"]):
            continue
        acc["sessions"], acc["commits"] = len(acc.pop("session_ids")), len(acc.pop("commit_ids"))
        if not acc["machine_turns"]:
            acc["cpu_self_s"] = acc["cpu_children_s"] = None
        out.append(acc)
    return {"tasks": out,
            "unmeasured": [u for u in sorted(unmeasured, key=lambda u: parse_time(u["timestamp"]))
                           if keep(u["timestamp"], u["workstream"])],
            "sessions": [s for s in sessions if keep(s["last"], s["workstream"])]}


def hm(seconds):
    minutes = int(round(float(seconds or 0) / 60))
    return f"{minutes // 60}:{minutes % 60:02d}"


def human_duration(seconds):
    """Segundos abaixo de 1 min, minutos inteiros até 60 min, "1 h 12 min" acima (a forma da `## Medida`, D3)."""
    seconds = float(seconds or 0)
    if seconds < 59.5:
        return f"{round(seconds)} s"
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h" + (f" {minutes} min" if minutes else "")


def brl(value):
    """O valor em US$ com vírgula decimal, como no aviso do close-task."""
    return f"{value:.2f}".replace(".", ",")


def cpu_min(value):
    return UNKNOWN if value is None else f"{value / 60:.1f}"


def print_by_task(result):
    print_table("Tarefas, por workstream e dia — tempos em h:mm, CPU em minutos",
                ("workstream", "dia", "tarefa", "sessões", "turnos", "corrido", "agente", "Anderson", "pausa",
                 "US$ turnos", "US$ subag.", "US$ total", "CPU claude", "CPU comandos", "runs", "CPU runs",
                 "pico runs MB", "RSS máx MB"),
                ("<", "<", "<", ">", ">", ">", ">", ">", ">", ">", ">", ">", ">", ">", ">", ">", ">", ">"),
                [(t["workstream"], t["day"], t["task"] + (f" ÷{t['shared_with'] + 1}" if t["shared_with"] else ""),
                  t["sessions"], t["turns"], hm(t["elapsed_s"]), hm(t["agent_s"]), hm(t["anderson_s"]),
                  hm(t["pause_s"]), money({"cost": t["cost_turns"], "no_cost": t["no_cost_turns"]}),
                  money({"cost": t["cost_subagents"], "no_cost": t["no_cost_subagents"]}),
                  money({"cost": t["cost_turns"] + t["cost_subagents"],
                         "no_cost": t["no_cost_turns"] + t["no_cost_subagents"]}),
                  cpu_min(t["cpu_self_s"]), cpu_min(t["cpu_children_s"]), t["runs"] or "",
                  cpu_min(t["run_cpu_s"]) if t["runs"] else "", label(t["run_max_rss_mb"]) if t["runs"] else "",
                  label(t["rss_max_mb"])) for t in result["tasks"]])
    print_table("Tarefas não medidas", ("worklog", "commit", "hora", "motivo"), ("<", "<", "<", "<"),
                [(u["worklog"], label(u["commit"]), str(u["timestamp"])[:16].replace("T", " "), u["reason"])
                 for u in result["unmeasured"]])
    print_table("Sessões: ociosa e sem tarefa — sem tarefa é o turno depois do último marco da sessão",
                ("sessão", "workstream", "ociosa", "turnos sem tarefa", "US$ sem tarefa", "desde"),
                ("<", "<", ">", ">", ">", "<"),
                [(s["name"], s["workstream"], hm(s["idle_s"]), s["no_task_turns"],
                  money({"cost": s["no_task_cost_turns"] + s["no_task_cost_subagents"], "no_cost": s["no_task_no_cost"]})
                  if s["no_task_turns"] or s["no_task_subagents"] else "",
                  str(s["no_task_since"] or "")[:16].replace("T", " ")) for s in result["sessions"]])


def session_workstream(segments):
    """O workstream da sessão: o último `workstream` das linhas dos segmentos dela e, sem linha, o dos `task`."""
    workstreams = [r.get("workstream") for seg in segments for r in seg["rows"] if r.get("workstream")]
    workstreams += [seg["task"].get("workstream") for seg in segments if seg["task"] and seg["task"].get("workstream")]
    return workstreams[-1] if workstreams else None


def pending(rows, session_id, now):
    """O que o commit da sessão vai fechar (D6): os turnos depois do último marco dela, e os sem tarefa das outras
    sessões do mesmo workstream cujo último turno tem mais de STALE_S."""
    segments = task_segments(rows)
    own = [seg for seg in segments if seg["session_id"] == session_id]
    open_ = own[-1] if own and own[-1]["task"] is None else {"rows": []}
    turns = sorted((r for r in open_["rows"] if r.get("event") == "turn"), key=row_start)
    workstream = session_workstream(own)
    others = {"turns": 0, "sessions": 0, "cost": 0.0}
    if workstream:
        for sid in dict.fromkeys(seg["session_id"] for seg in segments):
            if sid in (None, session_id):
                continue
            theirs = [seg for seg in segments if seg["session_id"] == sid]
            last = max((row_end(r) for seg in theirs for r in seg["rows"] if r.get("event") == "turn"), default=None)
            tail = task_measure(theirs[-1]["rows"]) if theirs[-1]["task"] is None else new_measure()
            if (session_workstream(theirs) != workstream or not tail["turns"] or last is None
                    or (now - last).total_seconds() <= STALE_S):
                continue
            others["turns"] += tail["turns"]
            others["sessions"] += 1
            others["cost"] += tail["cost_turns"] + tail["cost_subagents"]
    return {"session_id": session_id, "workstream": workstream, "measure": task_measure(open_["rows"]),
            "turns": [{"start": row_start(r).isoformat(), "prompt_head": r.get("prompt_head") or ""} for r in turns],
            "others": others}


def pending_text(rows, session_id, now):
    data = pending(rows, session_id, now)
    measure, lines = data["measure"], []
    if not data["turns"]:
        lines.append("nada a fechar")
    else:
        n = len(data["turns"])
        cost = f"US$ {brl(measure['cost_turns'])}"
        if measure["subagents"]:
            cost += (f" dos turnos e US$ {brl(measure['cost_subagents'])} de {measure['subagents']} "
                     + ("subagente" if measure["subagents"] == 1 else "subagentes"))
        since = parse_time(data["turns"][0]["start"]).strftime("%H:%M")
        lines.append(f"este commit fecha {n} {'turno' if n == 1 else 'turnos'} desde {since} "
                     f"(corrido {human_duration(measure['elapsed_s'])}, {cost}):")
        for t in data["turns"]:
            lines.append(f"  {parse_time(t['start']).strftime('%H:%M')} {t['prompt_head']}".rstrip())
    others = data["others"]
    if others["turns"]:
        k = others["sessions"]
        lines.append(f"sem tarefa no workstream {data['workstream']}: {others['turns']} "
                     f"{'turno' if others['turns'] == 1 else 'turnos'} em {k} "
                     f"{'sessão parada' if k == 1 else 'sessões paradas'} há mais de 2 h (US$ {brl(others['cost'])})")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", default=DEFAULT_DIR, help="diretório com o log")
    parser.add_argument("--since", default="", help="data mínima, AAAA-MM-DD")
    parser.add_argument("--denials", action="store_true", help="lista cada negação com o motivo inteiro")
    parser.add_argument("--json", action="store_true", help="imprime a agregação em JSON")
    parser.add_argument("--by-workstream", action="store_true",
                        help="só o resumo por workstream e por dia: turnos, custo, subagentes, memórias e rodadas")
    parser.add_argument("--facts", action="store_true",
                        help="só o acerto dos fatos do desenho, por papel, classe, faixa de confiança e tipo")
    parser.add_argument("--by-task", action="store_true",
                        help="só a medida por tarefa: as tarefas por workstream e dia, as não medidas e as sessões")
    parser.add_argument("--workstream", default="", help="com --by-task, só as tarefas e as sessões do workstream")
    parser.add_argument("--pending", action="store_true",
                        help="com --by-task e --session, o que o commit da sessão vai fechar (o aviso do close-task)")
    parser.add_argument("--session", default="", help="o id da sessão do --pending")
    args = parser.parse_args()
    if args.facts and (args.by_workstream or args.denials):
        parser.error("--facts não se combina com --by-workstream nem com --denials")
    if args.by_task and (args.facts or args.by_workstream or args.denials):
        parser.error("--by-task não se combina com --facts, --by-workstream nem --denials")
    if (args.pending or args.workstream) and not args.by_task:
        parser.error("--pending e --workstream vão com --by-task")
    if args.pending != bool(args.session):
        parser.error("--pending e --session vão juntos")

    # No --facts a data filtra o `fact`, e não o log: o desfecho que chega depois dela vale para o fato de dentro. No
    # --by-task, filtra a saída, porque os marcos de antes da data cortam os segmentos de depois.
    rows, bad, exists = read_log(os.path.join(args.dir, LOG), "" if args.facts or args.by_task else args.since)
    if not exists:
        if args.pending:
            print("nada a fechar")  # o aviso do close-task sai 0 sempre
            return 0
        print(f"nenhum log em {args.dir}: {LOG}", file=sys.stderr)
        return 2

    if args.pending:
        print(pending_text(rows, args.session, datetime.datetime.now().astimezone()))
        return 0

    if args.by_task:
        result = aggregate_by_task(rows, args.since, args.workstream)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        first = min((str(r.get("timestamp")) for r in rows if r.get("event") == "task" and row_end(r)),
                    key=parse_time, default=None)
        print(f"{args.dir}: {LOG} {len(rows)}" + (f" (+{bad} ilegível)" if bad else "")
              + (f", medida por tarefa desde {first[:16].replace('T', ' ')}" if first else "")
              + (f", saída desde {args.since}" if args.since else "")
              + (f", workstream {args.workstream}" if args.workstream else ""))
        print()
        if not first:
            print("nenhum evento task no log ainda: a medida por tarefa começa no primeiro `brza note commit` de worklog")
            return 0
        print_by_task(result)
        return 0

    if args.facts:
        facts = aggregate_facts(rows, args.since)
        if args.json:
            print(json.dumps(to_json_facts(facts), ensure_ascii=False, indent=2))
            return 0
        print(f"{args.dir}: {LOG} {sum(1 for r in rows if in_range(r, args.since))}"
              + (f" (+{bad} ilegível)" if bad else "") + (f", desde {args.since}" if args.since else ""))
        print()
        if not facts["rows"]:
            print("nenhum fato marcado ainda")
        print_facts(facts)
        return 0

    if args.by_workstream:
        days = aggregate_by_workstream(rows)
        if args.json:
            print(json.dumps(to_json_by_workstream(days), ensure_ascii=False, indent=2))
            return 0
        print(f"{args.dir}: {LOG} {len(rows)}" + (f" (+{bad} ilegível)" if bad else "")
              + (f", desde {args.since}" if args.since else ""))
        print()
        print_by_workstream(days)
        return 0

    agg = aggregate(rows)
    if args.json:
        print(json.dumps(to_json(agg), ensure_ascii=False, indent=2))
        return 0
    print(f"{args.dir}: {LOG} {len(rows)}" + (f" (+{bad} ilegível)" if bad else "")
          + (f", desde {args.since}" if args.since else ""))
    print()
    print_report(agg, args.denials)
    return 0


if __name__ == "__main__":
    sys.exit(main())
