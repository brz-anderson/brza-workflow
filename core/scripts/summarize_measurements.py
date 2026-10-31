#!/usr/bin/env python3
"""Resume o log de medição do workspace brza, `.measurements/measurements.jsonl`, escrito pelo hook `measure.py` (um
evento por linha), pela trava `resume_guard.py`, já desligada, e pela trava `compact_guard.py` (a linha `guard`, com
`kind: compact`).

Agrega o subagente por agente, modelo e esforço, e por versão do plugin; o turno da sessão principal por modelo e
esforço, e pelas skills que entraram nele; os comandos digitados; as falhas por tipo de erro; o MCP por servidor; e as
decisões das travas de retomada e do /compact. As negações de hook saem com o motivo.

  python3 scripts/summarize_measurements.py                      lê o .measurements/ da $BRZA_WORKSPACE, ou de ..
  python3 scripts/summarize_measurements.py --dir <diretório>    outro diretório com o log
  python3 scripts/summarize_measurements.py --since 2026-10-10   só os eventos a partir da data (AAAA-MM-DD)
  python3 scripts/summarize_measurements.py --denials            lista cada negação, com o agente, a data e o motivo
  python3 scripts/summarize_measurements.py --json               a mesma agregação em JSON
  python3 scripts/summarize_measurements.py --by-workstream      por workstream e por dia: turnos, custo, subagentes por
                                                                 agent_type, memórias e rodadas (com --json, em JSON)
  python3 scripts/summarize_measurements.py --facts              o acerto dos fatos do desenho (PLN-005), por papel, classe,
                                                                 faixa de confiança e tipo (com --json, em JSON); --since
                                                                 filtra pela data do fato

Log que não existe é pulado; linha que não é JSON é contada e ignorada. Só biblioteca padrão, Python 3.9.
"""

import argparse
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# A raiz vem da BRZA_WORKSPACE, que a função do shell passa: rodado de um worktree, o `..` cairia em `.worktrees/`.
# Sem a variável, vale o diretório acima de scripts/.
WORKSPACE = os.environ.get("BRZA_WORKSPACE") or os.path.join(HERE, "..")
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
        "reasons": reasons,
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
            "confidence_sum": 0.0, "checked": 0, "cite_off": 0})
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
            "unknown": b["unknown"], "checked": b["checked"], "cite_off": b["cite_off"],
            "cite_off_pct": 100 * b["cite_off"] / b["checked"] if b["checked"] else None}

    captures = {}
    audits = {"n": 0, "cost": 0.0, "failed": 0, "ignored": 0}
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
                 "unknown", "cite off %"),
                ("<", "<", "<", "<", ">", ">", ">", ">", ">", ">", ">"),
                [(agent, klass, band, kind, r["facts"], r["with_outcome"], percent(r["holds_pct"]),
                  UNKNOWN if r["mean_confidence"] is None else f"{r['mean_confidence']:.1f}",
                  UNKNOWN if r["distance"] is None else signed(r["distance"]), r["unknown"], percent(r["cite_off_pct"]))
                 for (agent, klass, band, kind), r in facts["rows"].items()])
    if facts["captures"]:
        print("Por papel: " + "; ".join(f"{a}: lidas {c['read']}, sem marca {c['unmarked']}, malformadas {c['malformed']}"
                                        for a, c in facts["captures"].items()))
    audits = facts["audits"]
    if audits["n"]:
        print(f"Auditorias: rodaram {audits['n']}, custo US$ {audits['cost']:.4f}, falharam {audits['failed']}, "
              f"ignorados {audits['ignored']}")
    if facts["rows"]:
        print(f"Pendentes: {facts['pending']} sem desfecho (inferido, abaixo de 80 ou custo) — {PENDING_HINT}")


def to_json_facts(facts):
    return {"rows": [{"agent": a, "class": k, "band": b, "kind": t, **r} for (a, k, b, t), r in facts["rows"].items()],
            "captures": [{"agent": a, **c} for a, c in facts["captures"].items()],
            "audits": facts["audits"], "pending": facts["pending"]}


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
    args = parser.parse_args()
    if args.facts and (args.by_workstream or args.denials):
        parser.error("--facts não se combina com --by-workstream nem com --denials")

    # No --facts a data filtra o `fact`, e não o log: o desfecho que chega depois dela vale para o fato de dentro.
    rows, bad, exists = read_log(os.path.join(args.dir, LOG), "" if args.facts else args.since)
    if not exists:
        print(f"nenhum log em {args.dir}: {LOG}", file=sys.stderr)
        return 2

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
