#!/usr/bin/env python3
"""Trava de fechamento: barra o /compact digitado enquanto a tarefa não fechou nas notas do workstream, e avisa depois
da compactação que deixou nota sem commit.

O worktree conferido é o do workstream do cwd, pelo `measure.resolve_workstream`: o próprio, no brza-workspace, ou, no
brza-studio, o do workstream do plano da branch. Dois modos:

- `pre`, no PreCompact com matcher `manual`: barra quando há arquivo sujo ou não rastreado em work/, plans/, wiki/,
  memory/ ou .claude/agent-memory/, ou quando falta em work/worklogs/ o arquivo com a data de hoje. A mensagem, que
  aparece para quem digitou, aponta o /close-task; no brza-studio, que não carrega as skills daqui, dá os passos. O
  `/compact sem-fechar` passa, e o `sem-fechar` fica no estado por 30 min.
- `post`, no SessionStart com matcher `compact`, automática ou manual: com nota suja no mesmo worktree, injeta no
  contexto a ordem de atualizar o worklog do dia a partir do resumo e fechar a tarefa. Depois de um `sem-fechar` da
  mesma sessão, não injeta.

Nunca barra na master, fora de workstream, sem o worktree do workstream, quando o git falha nem em qualquer exceção.
Cada conferência vira uma linha `guard` com `kind: compact` na medição.
"""

import json
import os
import re
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure  # noqa: E402 — o log, o git e o workstream do cwd vêm de lá

NOTE_PATHS = ("work", "plans", "wiki", "memory", ".claude/agent-memory")
WORKLOGS = os.path.join("work", "worklogs")
ESCAPE = re.compile(r"(^|\s)sem-fechar(\s|$)")
ESCAPE_S = 30 * 60  # o `sem-fechar` vale para o SessionStart que vem logo depois
STATE = os.path.join(measure.DIR, "compact_guard.json")
SHOWN = 5  # arquivos sujos citados na mensagem


def dirty(worktree):
    """Os arquivos sujos ou não rastreados nos caminhos das notas, ou None se o git falhar."""
    out = measure.git("status", "--porcelain", "-z", "--untracked-files=all", "--", *NOTE_PATHS, cwd=worktree)
    if out is None:
        return None
    fields, found = out.split("\0"), []
    while fields:
        entry = fields.pop(0)
        if len(entry) < 4:
            continue
        if entry[0] in "RC":
            fields.pop(0)  # o caminho de origem do renome ou da cópia
        found.append(entry[3:])
    return sorted(found)


def worklog_of(worktree, today):
    """O worklog de hoje em work/worklogs/, `AAAA-MM-DD-assunto.md`, ou None."""
    prefix = f"{today.isoformat()}-"
    try:
        names = os.listdir(os.path.join(worktree, WORKLOGS))
    except OSError:
        return None
    return next((n for n in sorted(names) if n.startswith(prefix) and n.endswith(".md")), None)


def inside(path, root):
    path, root = measure.realpath(path), measure.realpath(root)
    return path == root or path.startswith(root + os.sep)


def target(p):
    """(workstream, worktree, no_studio) da sessão, ou None quando não há o que conferir."""
    cwd = p.get("cwd") or os.getcwd()
    name, worktree = measure.resolve_workstream(cwd)
    if not name or name == "master" or not worktree:
        return None
    return name, worktree, not inside(cwd, worktree)


def listing(files):
    shown = ", ".join(files[:SHOWN])
    return f"{shown} e mais {len(files) - SHOWN}" if len(files) > SHOWN else shown


def closing(name, worktree, studio):
    """Como fechar a tarefa: o /close-task no workspace; os passos no brza-studio, que não carrega a skill."""
    if not studio:
        return "rode /close-task, que grava o worklog e o Retomar e commita as notas"
    return (f"feche a tarefa no worktree do workstream {name} ({worktree}): grave o worklog de hoje em work/worklogs/ "
            "e o pendente no Retomar do plano, e rode lá o `brza note commit <caminhos> -m \"…\"`")


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(data, now):
    data = {k: v for k, v in data.items() if v >= now - ESCAPE_S}
    os.makedirs(measure.DIR, exist_ok=True)
    with open(STATE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(STATE + ".tmp", STATE)


def log(p, name, decision, **extra):
    measure.write(dict({"event": "guard", "kind": "compact", "timestamp": measure.now(),
                        "session_id": p.get("session_id"), "bridge_session_id": measure.bridge_session(),
                        "cwd": p.get("cwd"), "workstream": name, "decision": decision}, **extra))


def pre(p, today=None, clock=None):
    if p.get("trigger", "manual") != "manual":
        return None
    found = target(p)
    if not found:
        return None
    name, worktree, studio = found
    if ESCAPE.search(p.get("custom_instructions") or ""):
        if p.get("session_id"):
            now = (clock or time.time)()
            data = load_state()
            data[p["session_id"]] = now
            save_state(data, now)
        log(p, name, "released")
        return None
    files = dirty(worktree)
    if files is None:
        return None
    today = today or date.today()
    worklog = worklog_of(worktree, today)
    if not files and worklog:
        log(p, name, "passed", worklog=worklog)
        return None
    reasons = []
    if files:
        reasons.append(f"{len(files)} arquivo(s) sem commit nas notas do workstream {name}: {listing(files)}")
    if not worklog:
        reasons.append(f"falta o worklog de hoje, work/worklogs/{today.isoformat()}-<assunto>.md")
    log(p, name, "blocked", dirty=len(files), worklog=worklog, studio=studio)
    return {"decision": "block",
            "reason": (f"O /compact espera o fechamento da tarefa — {'; '.join(reasons)}.\n"
                       f"Antes, {closing(name, worktree, studio)}; depois, o /compact de novo.\n"
                       "Sem nada a registrar: /compact sem-fechar.")}


def post(p, clock=None):
    if p.get("source", "compact") != "compact":
        return None
    found = target(p)
    if not found:
        return None
    name, worktree, studio = found
    released = load_state().get(p.get("session_id") or "")
    if released is not None and released >= (clock or time.time)() - ESCAPE_S:
        return None
    files = dirty(worktree)
    if not files:
        return None
    log(p, name, "injected", dirty=len(files), studio=studio)
    return {"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": (
            f"A compactação deixou {len(files)} arquivo(s) sem commit nas notas do workstream {name}: "
            f"{listing(files)}. Antes do próximo passo, atualize o worklog de hoje a partir do resumo acima — o que se "
            "fez, o que se decidiu, o que ficou aberto — e o pendente no Retomar do plano; depois, "
            f"{closing(name, worktree, studio)}.")}}


MODES = {"pre": pre, "post": post}


def main():
    mode = (sys.argv[1:] or [None])[0]
    try:
        out = MODES[mode](json.load(sys.stdin))
        if out:
            print(json.dumps(out, ensure_ascii=False))
    except Exception as error:  # a trava nunca barra por falha própria
        try:
            measure.write({"event": "hook_error", "timestamp": measure.now(), "mode": f"compact_guard {mode}",
                           "error": repr(error)})
        except Exception:
            pass
    sys.exit(0)


if __name__ == "__main__":
    main()
