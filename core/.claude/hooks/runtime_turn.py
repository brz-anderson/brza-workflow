#!/usr/bin/env python3
"""Linha `turn` do Grok e do Codex no mesmo log do `measure.py`.

Um modo no argv, como o `measure.py`:

- `grok` (Stop): uma linha por `turns[].turnNumber` do `usage.json` irmão do `transcript_path` (a sonda é
  `updates.jsonl`). O objeto `session` fica de fora: ele carrega histórico herdado. `SubagentStop` não grava, mesmo
  com o `usage.json` de um filho no caminho.
- `codex` (Stop): uma linha por `token_usage_record` do rollout, com o delta do objeto `usage`. Sem tabela de preço.
  Rollout cuja `session_meta` tem `thread_source` `guardian_review` não grava (isso fica no PLN-004).

O id da linha é o `session_id` do payload (ou, no Grok, o `sessionId` quando o outro falta). `GROK_SESSION_ID` não
entra nessa escolha. A dedup olha o log, não o `state.json`. Só mede: sai 0, e o stdin inválido não derruba o agente.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure  # noqa: E402 — o log, o `now` e o workstream já resolvidos lá


def number(value, default=0):
    """Número de token, ou `default`. `bool` é `int` no Python e não é contagem."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value


def cost_usd(obj):
    """US$ de `costUsdTicks / 1e10`, sem arredondar. Sem a chave, ou com valor que não é número, null."""
    if not isinstance(obj, dict) or "costUsdTicks" not in obj:
        return None
    ticks = obj.get("costUsdTicks")
    if isinstance(ticks, bool) or not isinstance(ticks, (int, float)):
        return None
    return ticks / 1e10


def read_grok_usage(path):
    """`session_id` e `turns` do `usage.json`. Cada turno: `turn_number`, `input`, `cache_read`, `cache_write`,
    `output`, `cost_usd`. O objeto `session` não entra. Turno sem `turnNumber` não entra.

    `input` é `inputTokens - cachedReadTokens`: o arquivo conta a leitura de cache dentro da entrada."""
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("usage.json não é um objeto")
    turns = []
    for turn in data.get("turns") or []:
        if not isinstance(turn, dict) or turn.get("turnNumber") is None:
            continue
        cached = number(turn.get("cachedReadTokens"))
        turns.append({"turn_number": turn["turnNumber"], "input": number(turn.get("inputTokens")) - cached,
                       "cache_read": cached, "cache_write": number(turn.get("cacheCreationTokens")),
                       "output": number(turn.get("outputTokens")), "cost_usd": cost_usd(turn)})
    return {"session_id": data.get("sessionId"), "turns": turns}


def find_grok_usage(session_id, sessions_root):
    """`sessions_root/*/<session_id>/usage.json`, um nível de cwd codificado. Não desce a `subagents/`. Mais de um:
    o arquivo cujo `sessionId` é o `session_id`. Sem nenhum, None."""
    if not isinstance(session_id, str) or not session_id or os.path.basename(session_id) != session_id:
        return None
    try:
        names = os.listdir(sessions_root)
    except OSError:
        return None
    found = []
    for name in names:
        candidate = os.path.join(sessions_root, name, session_id, "usage.json")
        if os.path.isfile(candidate):
            found.append(candidate)
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    for path in sorted(found):
        try:
            with open(path, encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("sessionId") == session_id:
            return path
    return None


def text_of(payload, *keys):
    """O primeiro valor não vazio, entre as chaves, quando é string."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def subagent_stop(payload, env=False):
    if payload.get("hook_event_name") == "SubagentStop":
        return True
    return bool(env) and os.environ.get("GROK_HOOK_EVENT") == "SubagentStop"


def workstream_of(cwd):
    try:
        return measure.workstream_of(cwd)
    except Exception:
        return None


def seen_turns(runtime, session_id):
    """`turn_number` e `response_id` já gravados para este runtime e esta sessão. O log ilegível conta como vazio
    numa linha: a linha quebrada não esconde as outras."""
    found = set()
    try:
        with open(measure.LOG, encoding="utf-8") as stream:
            lines = stream.read().splitlines()
    except OSError:
        return found
    for raw in lines:
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("event") != "turn" or row.get("runtime") != runtime:
            continue
        if row.get("session_id") != session_id:
            continue
        for key in ("turn_number", "response_id"):
            if row.get(key) is not None:
                found.add(row[key])
    return found


def turn_line(runtime, payload, session_id, turn_number, cost, tokens, extra=None):
    line = {"event": "turn", "runtime": runtime, "timestamp": measure.now(), "session_id": session_id,
            "turn_number": turn_number, "cwd": payload.get("cwd"), "workstream": workstream_of(payload.get("cwd")),
            "estimated_cost_usd": cost, "tokens": tokens, "machine": None}
    if extra:
        line.update(extra)
    return line


def tokens_of(turn):
    return {"input": turn["input"], "cache_read": turn["cache_read"], "cache_write": turn["cache_write"],
            "output": turn["output"]}


def grok(payload):
    # O id é o do payload. `GROK_SESSION_ID` não desempata quando os dois diferem, nem preenche a ausência.
    session_id = text_of(payload, "session_id", "sessionId")
    if not session_id or subagent_stop(payload, env=True):
        return
    transcript = text_of(payload, "transcript_path", "transcriptPath")
    if not transcript:
        return
    # O filho mora em `subagents/` e o `usage.json` do pai já o inclui. A linha dele fica no PLN-004.
    if "subagents" in os.path.normpath(transcript).split(os.sep):
        return
    path = os.path.join(os.path.dirname(transcript), "usage.json")
    if not os.path.isfile(path):
        return
    usage = read_grok_usage(path)
    if usage.get("session_id") != session_id:
        return
    seen = seen_turns("grok", session_id)
    for turn in usage["turns"]:
        if turn["turn_number"] in seen:
            continue
        measure.write(turn_line("grok", payload, session_id, turn["turn_number"], turn["cost_usd"], tokens_of(turn)))
        seen.add(turn["turn_number"])


def rollout_rows(path):
    rows = []
    with open(path, encoding="utf-8") as stream:
        for raw in stream:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def guardian_review(row):
    if row.get("type") != "session_meta":
        return False
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return row.get("thread_source") == "guardian_review" or payload.get("thread_source") == "guardian_review"


def usage_record(row):
    """`(response_id, usage)` do delta. Sem `usage`, não cai em `turn_token_usage` nem em `thread_token_usage`."""
    if row.get("type") != "token_usage_record":
        return None, None
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    usage = row.get("usage") if isinstance(row.get("usage"), dict) else payload.get("usage")
    if not isinstance(usage, dict):
        return None, None
    response_id = row.get("response_id") or payload.get("response_id")
    if not isinstance(response_id, str) or not response_id:
        return None, None
    return response_id, usage


def codex_tokens(usage):
    # Campos separados: `input_tokens` já não inclui o cache, e subtrair contaria duas vezes para baixo.
    return {"input": number(usage.get("input_tokens")), "cache_read": number(usage.get("cached_input_tokens")),
            "cache_write": number(usage.get("cache_write_input_tokens")), "output": number(usage.get("output_tokens"))}


def codex(payload):
    session_id = text_of(payload, "session_id")
    transcript = text_of(payload, "transcript_path")
    if not session_id or not transcript or subagent_stop(payload):
        return
    if not os.path.isfile(transcript):
        return
    rows = rollout_rows(transcript)
    if any(guardian_review(row) for row in rows):
        return
    seen = seen_turns("codex", session_id)
    for row in rows:
        response_id, usage = usage_record(row)
        if response_id is None or response_id in seen:
            continue
        measure.write(turn_line("codex", payload, session_id, response_id, None, codex_tokens(usage),
                                {"response_id": response_id}))
        seen.add(response_id)


MODES = {"grok": grok, "codex": codex}


def main():
    mode = (sys.argv[1:] or [None])[0]
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("payload não é um objeto")
        MODES[mode](payload)
    except Exception as error:  # o hook mede; nunca derruba a chamada medida
        try:
            measure.write({"event": "hook_error", "timestamp": measure.now(), "mode": mode, "error": repr(error)})
        except Exception:
            pass
    sys.exit(0)


if __name__ == "__main__":
    main()
