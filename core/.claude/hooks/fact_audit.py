#!/usr/bin/env python3
"""Hook da auditoria dos fatos do desenho (PLN-005): grava no log do `measure.py` a confiança que o `designer` e o
`collector` marcaram em cada fato.

Um modo por entrada de hook, no primeiro argumento:

- `stop` (SubagentStop, o mesmo payload do `measure.py subagent`): lê o trecho novo do transcript do subagente e tira as
  marcas `[F3 · lido · 90]` — tipo `F` (fato) ou `C` (custo), número, a evidência (`rodado`, `lido`, `doc`, `inferido`;
  com parêntese opcional depois do tipo) e a confiança de 0 a 100, com texto opcional depois dela, de `;` ou `,` até o `]`
  — da devolução (`source: reply`: o `input.message` do `SubagentHandback`, ou, sem ele, a
  última mensagem de texto do assistente) e do `content` de cada `Write`
  do trecho (`source`: o caminho gravado). Grava um evento `fact` por marca nova e um `fact_capture` por trecho, com o
  total, as marcas malformadas e `unmarked` quando a devolução veio sem nenhuma. Com algum fato novo `rodado`, `lido` ou
  `doc` (de id `F`; o `inferido` e o `C` não se auditam), grava `fact-audit/<agent_id>-<offset do trecho>.json` ao lado
  do log — os fatos, o `cwd` e, para os `rodado`, o comando e a saída de cada `Bash` do trecho — e solta `run` em
  segundo plano, sem esperar.
- `run <arquivo>` (o processo que o `stop` solta): roda a sessão `claude -p --model haiku` da auditoria, que refaz a
  evidência de cada fato, e grava um `fact_check` por veredito (`holds`, `fails` ou `unknown`, mais `cite: ok|off`, que
  diz se a fonte aponta o lugar certo) e um `fact_audit` por rodada, com o custo e o erro, se houve. Nunca levanta, e
  apaga o arquivo da rodada ao fim.

Só mede: nunca bloqueia, e toda exceção vira `hook_error` com saída 0. Sai sem gravar com `BRZA_FACT_AUDIT` no
ambiente — a sessão `claude -p` da própria auditoria — ou com `agent_type` fora de `designer` e `collector`. O estado
(onde parou a leitura de cada transcript e os ids já vistos) fica em .measurements/fact-audit-state.json, sob a trava do
`measure.py`, de onde vêm o `agent_transcript`, o `read_from`, o `write`, o `now` e o `workstream_of`.
"""

import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def load_measure():
    spec = importlib.util.spec_from_file_location("measure", os.path.join(HERE, "measure.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


measure = load_measure()

AUDITED = ("designer", "collector")
# A marca é `[F3 · lido · 90]`, com dois acréscimos que os subagentes escrevem: um parêntese depois do tipo
# (`[F6 · rodado (grep) · 85]`) e um texto depois do número, de `;` ou `,` até o `]` (`[F3 · lido · 80; premissa …]`).
MARK = re.compile(r"\[([FC])(\d+) · (rodado|lido|doc|inferido)(?: \([^)\]\n]*\))? · (\d{1,3})(?:[;,][^\]\n]*)?\]")
# A referência agrupada (`[F3, F25 · lido · 80-90]`, `[F7-F9 · …]`: um id, `,` `-` ou `–` e outro id) cita fatos já
# marcados em outro lugar: não é fato nem erro.
REFERENCE = re.compile(r"\[[FC]\d+\s*[,\-\u2013]\s*[FC]\d+")
MARK_START = re.compile(r"\[[FC]\d")  # todo `[F3` ou `[C2` é tentativa: a que nem MARK nem REFERENCE casa é malformada
TEXT_LIMIT = 300
STATE_FILE = "fact-audit-state.json"
AUDIT_DIR = "fact-audit"
AUDITED_KINDS = ("rodado", "lido", "doc")  # o `inferido` não tem evidência a refazer
RUN_OUTPUT_LIMIT = 4000
RUN_TIMEOUT = 600
BUDGET_PER_FACT = 0.012  # US$ por fato enviado: ~0,0065 medido em 2026-10-31, com folga para o fato difícil
BUDGET_MIN = 0.25
VERDICTS = ("holds", "fails", "unknown")
CITES = ("ok", "off")
PROMPT = """Você é o auditor dos fatos de um desenho. Leia o arquivo {arquivo}: é um JSON com `cwd`, `facts` (cada fato \
tem `id`, `kind`, `confidence`, `text` e `source`) e `runs` (cada comando `Bash` que o autor rodou, com `command` e \
`output`, a saída que ficou gravada).

Para cada fato, refaça a evidência que ele diz ter, sem confiar no `text`:
- `lido`: abra o `arquivo:linha` citado no `text`, relativo ao `cwd`, e confira o que há ali;
- `doc`: abra a URL ou a documentação citada no `text` e confira o que ela diz;
- `rodado`: NÃO rode nada. Compare o fato com a saída gravada em `runs` e veja se ela o sustenta.

Não edite nada.

Responda só com uma linha JSON por fato, sem cerca e sem texto antes ou depois:
{"id": "F3", "verdict": "holds|fails|unknown", "cite": "ok|off", "why": "uma oração"}

Os critérios:
- `verdict`: `holds` quando o fato é verdadeiro; `fails` quando é falso; `unknown` quando a evidência não está ao seu \
alcance (arquivo inexistente, URL fora do ar, saída que não cobre o fato);
- `cite`: `off` quando a fonte não aponta o lugar em que o fato se confere (linha, arquivo ou URL errados), mesmo que o \
fato seja verdadeiro; `ok` quando aponta."""


def agent_type_of(p, path):
    """O tipo do subagente pelo payload, ou pelo `agentType` do .meta.json ao lado do transcript."""
    if p.get("agent_type"):
        return p["agent_type"]
    try:
        with open(path.replace(".jsonl", ".meta.json"), encoding="utf-8") as f:
            return json.load(f).get("agentType")
    except (OSError, ValueError):
        return None


def blocks_of(line):
    """Os blocos de conteúdo de uma linha de assistente do transcript, ou []."""
    try:
        entry = json.loads(line)
    except ValueError:
        return []
    message = entry.get("message") if isinstance(entry, dict) else None
    if not isinstance(message, dict) or (entry.get("type") or message.get("role")) != "assistant":
        return []
    content = message.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def sources_of(lines):
    """O texto a ler no trecho, na ordem: o `content` de cada `Write`, e a devolução, como `reply`: o `input.message` do
    último `tool_use` `SubagentHandback` — é assim que o subagente devolve —, ou, sem ele, a última mensagem do
    assistente que tenha bloco `text`. Devolve [(source, texto)]."""
    writes, reply, handback = [], None, None
    for line in lines:
        blocks = blocks_of(line)
        for b in blocks:
            if b.get("type") == "tool_use" and b.get("name") == "Write":
                tool = b.get("input") or {}
                if isinstance(tool.get("content"), str):
                    writes.append((tool.get("file_path"), tool["content"]))
            elif b.get("type") == "tool_use" and b.get("name") == "SubagentHandback":
                message = (b.get("input") or {}).get("message")
                if isinstance(message, str):
                    handback = message
        texts = [b["text"] for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str)]
        if texts:
            reply = "\n".join(texts)
    reply = handback if handback is not None else reply
    return writes + ([("reply", reply)] if reply is not None else [])


def line_of(text, pos):
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end >= 0 else len(text)].strip()[:TEXT_LIMIT]


def marks_of(sources):
    """(marcas, malformadas): as marcas válidas na ordem das fontes, com a primeira ocorrência de cada id, e a contagem
    das ocorrências malformadas. Cada marca é (id, kind, confidence, texto, source); `found` conta as válidas antes de
    tirar as repetidas."""
    marks, ids, malformed, found = [], set(), 0, 0
    for source, text in sources:
        for start in MARK_START.finditer(text):
            m = MARK.match(text, start.start())
            if not m or int(m.group(4)) > 100:
                if not m and REFERENCE.match(text, start.start()):
                    continue
                malformed += 1
                continue
            found += 1
            fact_id = m.group(1) + m.group(2)
            if fact_id not in ids:
                ids.add(fact_id)
                marks.append((fact_id, m.group(3), int(m.group(4)), line_of(text, m.start()), source))
    return marks, malformed, found


def runs_of(lines):
    """Cada `Bash` do trecho, na ordem, com o texto do `tool_result` casado pelo `tool_use_id`: [{command, output}]."""
    runs, by_id = [], {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "Bash":
                run = {"command": (b.get("input") or {}).get("command"), "output": ""}
                runs.append(run)
                by_id[b.get("id")] = run
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in by_id:
                by_id[b["tool_use_id"]]["output"] = measure.text_of(b.get("content"))[:RUN_OUTPUT_LIMIT]
    return runs


def stop(p):
    if os.environ.get("BRZA_FACT_AUDIT"):
        return
    path = measure.agent_transcript(p)
    if not path or not os.path.exists(path):
        return
    agent_type = agent_type_of(p, path)
    if agent_type not in AUDITED:
        return
    audit_file = None
    with measure.State(os.path.join(measure.DIR, STATE_FILE)) as state:
        entry = state.data.get(path) or {}
        offset, seen = entry.get("offset", 0), set(entry.get("seen", []))
        lines, end = measure.read_from(path, offset)
        if not lines:
            return  # trecho vazio: nada novo, e a leitura não avança
        marks, malformed, found = marks_of(sources_of(lines))
        marks = [m for m in marks if m[0] not in seen]  # o id já visto num trecho anterior não se grava de novo
        base = {"timestamp": measure.now(), "session_id": p.get("session_id"), "agent_id": p.get("agent_id"),
                "agent_type": agent_type, "workstream": measure.workstream_of(p.get("cwd"))}
        for fact_id, kind, confidence, text, source in marks:
            measure.write({"event": "fact", **base, "fact_id": fact_id, "kind": kind, "confidence": confidence,
                           "text": text, "source": source})
        capture = {"event": "fact_capture", **base, "facts": len(marks), "malformed": malformed,
                   "segment": "resume" if offset else "first"}
        if not found:
            capture["unmarked"] = True
        measure.write(capture)
        state.data[path] = {"offset": end, "seen": sorted(seen | {m[0] for m in marks})}
        state.save()
        auditable = [{"id": i, "kind": k, "confidence": c, "text": t, "source": s} for i, k, c, t, s in marks
                     if i.startswith("F") and k in AUDITED_KINDS]
        if auditable:
            audit_file = write_audit_file(p, base, auditable, offset,
                                          runs_of(lines) if any(f["kind"] == "rodado" for f in auditable) else [])
    if audit_file:  # fora da trava: o hook solta o processo e sai, sem esperar a auditoria
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "run", audit_file], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write_audit_file(p, base, facts, offset, runs):
    directory = os.path.join(measure.DIR, AUDIT_DIR)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{p.get('agent_id')}-{offset}.json")
    data = {"agent_id": base["agent_id"], "agent_type": base["agent_type"], "session_id": base["session_id"],
            "workstream": base["workstream"], "cwd": p.get("cwd"), "facts": facts, "runs": runs}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


class InvalidOutput(Exception):
    """A saída do `claude -p` não é o objeto JSON que o `--output-format json` promete."""


def claude_binary():
    return os.environ.get("BRZA_FACT_AUDIT_CLAUDE") or shutil.which("claude")


def budget_usd(facts):
    """O `--max-budget-usd` da rodada: BUDGET_PER_FACT por fato enviado, com o piso BUDGET_MIN, em duas casas."""
    return f"{max(BUDGET_MIN, BUDGET_PER_FACT * facts):.2f}"


def call_claude(path, cwd, facts):
    """(saída, JSON de saída): roda a sessão da auditoria e lê o JSON também com a saída diferente de 0, que o
    `--max-budget-usd` usa para cortar; `facts` é o número de fatos do arquivo, que dimensiona o teto. Levanta FileNotFoundError, TimeoutExpired ou InvalidOutput."""
    binary = claude_binary()
    if not binary:
        raise FileNotFoundError("claude")
    command = [binary, "-p", "--model", "haiku", "--tools", "Read,Grep,Glob,WebFetch", "--add-dir",
               os.path.dirname(path), "--output-format", "json", "--max-budget-usd", budget_usd(facts),
               PROMPT.replace("{arquivo}", path)]
    process = subprocess.Popen(command, cwd=cwd, env={**os.environ, "BRZA_FACT_AUDIT": "1"}, text=True,
                               encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        out, _ = process.communicate(timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)  # o grupo todo: um neto com o pipe aberto seguraria o communicate
        except OSError:
            process.kill()
        process.communicate()
        raise
    try:
        result = json.loads(out)
    except ValueError as e:
        raise InvalidOutput(str(e))
    if not isinstance(result, dict):
        raise InvalidOutput("a saída não é um objeto JSON")
    return result


FENCE = re.compile(r"^```[\w-]*$")  # a cerca que o Haiku põe mesmo pedido sem ela (PLN-005/R7.1)


def checks_of(result, ids):
    """([(id, verdict, cite, why)], ignoradas): as linhas JSON válidas do `result`. A linha em branco e a de cerca
    (```, ```json) não contam; a que não é JSON objeto, de id fora do arquivo ou repetido, ou de `verdict`/`cite` fora do
    vocabulário, é ignorada."""
    checks, ignored, done = [], 0, set()
    for raw in (result if isinstance(result, str) else "").splitlines():
        if not raw.strip() or FENCE.match(raw.strip()):
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            row = None
        if (not isinstance(row, dict) or row.get("id") not in ids or row["id"] in done
                or row.get("verdict") not in VERDICTS or row.get("cite") not in CITES):
            ignored += 1
            continue
        done.add(row["id"])
        checks.append((row["id"], row["verdict"], row["cite"], str(row.get("why") or "")[:TEXT_LIMIT]))
    return checks, ignored


def run(path):
    """A rodada da auditoria: nunca levanta, e apaga o arquivo ao fim. Toda falha vira o `error` do `fact_audit`."""
    started, data, result, error = time.monotonic(), {}, {}, None
    checked = ignored = 0
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        result = call_claude(path, data.get("cwd") or None, len(data.get("facts") or []))
        if result.get("is_error") or str(result.get("subtype") or "success") != "success":
            error = result.get("subtype") or "is_error"
        ids = {f.get("id") for f in data.get("facts") or []}
        checks, ignored = checks_of(result.get("result"), ids)
        for fact_id, verdict, cite, why in checks:
            measure.write({"event": "fact_check", "timestamp": measure.now(), "session_id": data.get("session_id"),
                           "agent_id": data.get("agent_id"), "agent_type": data.get("agent_type"),
                           "workstream": data.get("workstream"), "fact_id": fact_id, "verdict": verdict, "cite": cite,
                           "why": why})
            checked += 1
    except subprocess.TimeoutExpired:
        error = "timeout"
    except FileNotFoundError as e:  # o binário; o arquivo da rodada ou o cwd ausente leva o caminho no repr
        error = "binary_not_found" if e.filename in (None, "claude", claude_binary()) else repr(e)
    except InvalidOutput:
        error = "invalid_json"
    except Exception as e:
        error = repr(e)
    try:
        measure.write({"event": "fact_audit", "timestamp": measure.now(), "session_id": data.get("session_id"),
                       "agent_id": data.get("agent_id"), "agent_type": data.get("agent_type"),
                       "workstream": data.get("workstream"), "facts": len(data.get("facts") or []), "checked": checked,
                       "ignored": ignored, "cost_usd": result.get("total_cost_usd"),
                       "duration_s": round(time.monotonic() - started, 1), "num_turns": result.get("num_turns"),
                       "subtype": result.get("subtype"), "error": error})
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


MODES = {"stop": stop}


def main():
    mode = (sys.argv[1:] or [None])[0]
    try:
        if mode == "run":  # o processo solto pelo `stop`: o arquivo da rodada vem no argumento, e não há payload
            run(sys.argv[2])
        else:
            MODES[mode](json.load(sys.stdin))
    except Exception as error:  # o hook mede; nunca derruba a chamada medida
        try:
            measure.write({"event": "hook_error", "timestamp": measure.now(), "mode": f"fact_audit:{mode}",
                           "error": repr(error)})
        except Exception:
            pass
    sys.exit(0)


if __name__ == "__main__":
    main()
