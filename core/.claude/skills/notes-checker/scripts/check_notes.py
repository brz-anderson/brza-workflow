#!/usr/bin/env python3
"""Checador das notas do brza-workspace: work/, plans/ e wiki/ na raiz do repositório.

Confere a forma das notas contra a norma do work/README.md e do plans/README.md. Adaptado do checador do workspace da
Fixfy, sem o que é só de lá (o fixfy-vault, o painel, as origens sprint e card, o campo ticket, as tags da Fixfy):

  erro   nome de arquivo duplicado; link Markdown relativo quebrado — o que existe em outra branch workstream/* não
         está quebrado, e o origin_ref de plano também se procura lá; [[wikilink]] fora de código;
         status fora do vocabulário; campo obrigatório ausente em nota datada de CUTOFF em diante;
         plano aberto fora do molde do plans/README.md — o frontmatter, o Retomar e as seções (plan_findings)
  aviso  o mesmo campo ausente em nota sem data nenhuma; type redundante; tag fora do vocabulário; achado de um parecer
         de work/reviews/ sem **Evidência:**; nota de work/ que linka um plano aberto e falta na seção de ponteiro dele

Só biblioteca padrão (e o git, para as branches workstream/*). Não altera nada. Sai com 1 se houver erro, 2 se não
achar as notas.

Uso:
  check_notes.py [--root DIR] [--json] [--detail]

  --root    a raiz com work/, plans/ e wiki/; sem ela, a raiz do repositório onde o script mora
  --json    saída para outro programa
  --detail  lista cada aviso com o arquivo, em vez da contagem por regra
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from urllib.parse import unquote

# Os vocabulários são do work/README.md e do plans/README.md; mudou lá, muda aqui no mesmo commit.
STATUSES = {"open", "in_progress", "in_review", "done", "dropped"}
STATELESS_GENRES = {"guide", "worklog", "note"}
TAGS = {"auth", "chat", "design", "infra", "planning", "release", "tooling"}
FORBIDDEN_TAG_PREFIXES = ("work/", "repo/", "scope/", "status/")
# O dia em que o workspace nasceu (a abertura do PLN-001): toda nota datada daí em diante é cobrada; só a que não tem
# data nenhuma sai como aviso, porque data que não está escrita não se infere.
CUTOFF = "2026-10-26"
ROOT_ZONES = ("work", "plans", "wiki")
# O plano (plans/README.md): o status de cada phase e as origens
OPEN_STATUSES = {"open", "in_progress", "in_review"}
PHASE_STATUS = {"interview": "open", "design": "open", "execution": "in_progress", "review": "in_review",
                "blocked": "open", "closed": "done", "dropped": "dropped"}
ORIGINS = {"issue", "decision", "plan", "request"}
HOURS = ("estimate_h", "executed_h", "remaining_h")
# A linha do ## Worklogs leva o link do worklog a partir deste dia: o seguinte ao da criação do checador (o PLN-001 tem
# linha de 2026-10-27 sem link), fixado aqui e no plans/README.md. Sem ele, a regra não cobraria linha nenhuma.
WORKLOGS_LINK_FROM = "2026-10-28"
# A seção de ponteiro do plano onde cada gênero que o linka aparece; a issue também pode estar no origin_ref
POINTER_SECTIONS = {"worklog": ("## Worklogs",), "review": ("## Revisões",), "decision": ("## Decisões",),
                    "issue": ("## Pendências conhecidas", "### Fora de escopo")}
# Molde que as notas citam e que mora na skill dona: entra só para resolver link, e não é conferido como nota
SKILL_TEMPLATES = (".claude/skills/plan-runner/references/plan-template.md",)
DEFAULT_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), *[".."] * 4))

GENRES = {
    "work/notes": "note",
    "work/issues": "issue",
    "work/worklogs": "worklog",
    "work/reviews": "review",
    "work/decisions": "decision",
    "wiki/guides": "guide",
}
# A zona que é o próprio gênero: plans/<arquivo> e plans/<PLN-NNN-assunto>/<arquivo>; o README.md dela é a norma
FLAT_ZONES = {"plans": "plan"}

WIKILINK = re.compile(r"!?\[\[([^\[\]\n]+?)\]\]")
MD_LINK = re.compile(r"!?\[(?:\\.|[^\[\]\n])*\]\(([^()\s]+)(?:\s+\"[^\"]*\")?\)")
URI_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
FENCE = re.compile(r"^(```|~~~)")
INLINE_CODE = re.compile(r"`[^`\n]*`")


def walk(base, zone):
    for dirpath, dirs, names in os.walk(os.path.join(base, zone)):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(names):
            if not name.startswith("."):
                full = os.path.join(dirpath, name)
                yield os.path.relpath(full, base), full


def files(root):
    """{caminho relativo: caminho no disco}. De work/, plans/ e wiki/, e o molde do plano na plan-runner, que entra só
    para resolver link."""
    found = {}
    for zone in ROOT_ZONES:
        found.update(walk(root, zone))
    for rel in SKILL_TEMPLATES:
        if os.path.isfile(os.path.join(root, rel)):
            found[rel] = os.path.join(root, rel)
    return dict(sorted(found.items()))


def genre_of(rel):
    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] in FLAT_ZONES:
        return None if parts[-1] == "README.md" else FLAT_ZONES[parts[0]]
    if len(parts) >= 3:
        return GENRES.get("/".join(parts[:2]))
    return None


def frontmatter(text):
    """YAML mínimo: chave: valor e listas com '- '. Basta para o frontmatter das notas."""
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    data, key = {}, None
    for line in text[4:end].split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            if value.startswith("[") and value.endswith("]"):
                data[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
            elif value:
                data[key] = value.strip("'\"")
            else:
                data[key] = []
            continue
        m = re.match(r"^\s+-\s*(.*)$", line)
        if m and key is not None:
            if not isinstance(data.get(key), list):
                data[key] = [data[key]] if data.get(key) else []
            data[key].append(m.group(1).strip().strip("'\""))
    return data


def without_code(text):
    out, fenced = [], False
    for line in text.split("\n"):
        if FENCE.match(line.strip()):
            fenced = not fenced
            continue
        if not fenced:
            out.append(INLINE_CODE.sub("", line))
    return "\n".join(out)


def wikilink_targets(text):
    """Extrai [[wikilinks]] fora de código/crase (que são proibidos)."""
    for m in WIKILINK.finditer(without_code(text)):
        inner = m.group(1)
        if inner.startswith(" ") or inner.endswith(" "):
            continue
        raw = inner.replace("\\|", "|")
        target = raw.split("|", 1)[0].split("#", 1)[0].split("^", 1)[0].strip()
        yield target or raw.strip()


def md_link_targets(text):
    """Extrai o href de links Markdown relativos [rótulo](caminho) fora de código/crase."""
    for m in MD_LINK.finditer(without_code(text)):
        href = m.group(1).strip()
        if URI_SCHEME.match(href) or href.startswith(("#", "<")):
            continue
        path_part = unquote(href.split("#", 1)[0].split("?", 1)[0]).strip()
        if path_part:
            yield href, path_part


class WorkstreamIndex:
    """Os arquivos das branches workstream/* do repositório da raiz — as locais e as de origin/workstream/* —, para o
    link e o origin_ref que apontam para a nota de outro workstream. A lista de cada ref sai de um `git ls-tree` só, na
    primeira consulta, e não há chamada ao git por link. Fora de repositório git, ou sem branch workstream, não acha
    nada."""

    def __init__(self, root):
        self.root = os.path.realpath(root)
        self._paths = None
        self._base = None

    def _load(self):
        self._paths, self._base = set(), self.root
        try:
            top = self._git("rev-parse", "--show-toplevel").strip()
            refs = self._git("for-each-ref", "--format=%(refname)", "refs/heads/workstream/",
                             "refs/remotes/origin/workstream/").split()
        except (OSError, subprocess.CalledProcessError):
            return
        self._base = os.path.realpath(top)
        for ref in refs:
            try:
                names = self._git("ls-tree", "-r", "-z", "--name-only", ref).split("\0")
            except subprocess.CalledProcessError:
                continue
            self._paths.update(n for n in names if n)

    def _git(self, *args):
        return subprocess.run(["git", "-C", self.root, *args], capture_output=True, text=True, check=True).stdout

    def _repo_path(self, full):
        """O caminho de `full` relativo à raiz do repositório, ou None quando cai fora dele."""
        rel = os.path.relpath(os.path.realpath(full), self._base).replace(os.sep, "/")
        return None if rel == ".." or rel.startswith("../") else rel

    def has(self, full):
        """Há o arquivo `full` (caminho no disco, que pode não existir) em alguma branch workstream/*."""
        if self._paths is None:
            self._load()
        rel = self._repo_path(full)
        return rel is not None and rel in self._paths

    def has_prefix(self, full_prefix):
        """Há arquivo em alguma branch workstream/* cujo caminho começa por `full_prefix`."""
        if self._paths is None:
            self._load()
        rel = self._repo_path(full_prefix)
        return rel is not None and any(p.startswith(rel) for p in self._paths)


def resolves_md_link(path_part, src_full, full_paths, workstreams=None):
    target_full = os.path.normpath(os.path.join(os.path.dirname(src_full), path_part))
    # Aceita também arquivo existente fora do acervo, como o molde do plano, e o de outra branch workstream/*
    return (target_full in full_paths or os.path.isfile(target_full)
            or bool(workstreams and workstreams.has(target_full)))


def findings_without_evidence(text):
    """Os A<n> de um parecer de work/reviews/ sem `**Evidência:**`, que a forma do achado exige (plan-runner, references/conduct.md)."""
    missing, finding, proven = [], None, False
    for line in without_code(text).split("\n") + ["## fim"]:
        if line.startswith("#"):
            if finding and not proven:
                missing.append(finding)
            finding, proven = None, False
            m = re.match(r"^### (A\d+)\b", line)
            if m:
                finding = m.group(1)
        elif finding and "**Evidência:**" in line:
            proven = True
    return missing


def body_of(text):
    """O texto depois do frontmatter."""
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0:
            return text[end + 4:]
    return text


def section(text, heading):
    """As linhas da seção que abre com `heading`, até o próximo título do mesmo nível ou de cima; None sem ela."""
    level = len(heading) - len(heading.lstrip("#"))
    lines, inside = [], False
    for line in text.split("\n"):
        m = re.match(r"^(#+) ", line)
        if inside and m and len(m.group(1)) <= level:
            break
        if inside:
            lines.append(line)
        elif line.strip() == heading:
            inside = True
    return "\n".join(lines) if inside else None


def linked_paths(text, src_full):
    """O caminho no disco de cada link Markdown relativo do trecho, fora de código."""
    return {os.path.normpath(os.path.join(os.path.dirname(src_full), p)) for _, p in md_link_targets(text)}


def as_list(value):
    if isinstance(value, list):
        return value
    return [value] if value else []


def origin_ref_found(root, origin, ref, workstreams=None):
    """O origin_ref aponta para o que existe: a issue, a decisão ou a pasta do plano em plans/, na árvore ou numa branch
    workstream/*. O /A<n> não se confere."""
    if origin in ("issue", "decision"):
        path = os.path.join(root, "work", f"{origin}s", f"{ref}.md")
        return os.path.isfile(path) or bool(workstreams and workstreams.has(path))
    if origin == "plan":
        number = ref.split("/", 1)[0]
        folder = os.path.join(root, "plans")
        if os.path.isdir(folder) and any(n.startswith(number + "-") for n in os.listdir(folder)):
            return True
        return bool(workstreams and workstreams.has_prefix(os.path.join(folder, number + "-")))
    return True


def plan_findings(root, rel, full, fm, text, linking, workstreams=None):
    """(erros, avisos) do -plan.md aberto contra o molde do plans/README.md: o frontmatter, o Retomar e as seções de
    ponteiro. O plano done ou dropped não passa por aqui."""
    errors, warnings = [], []
    status, phase, origin, ref = fm.get("status"), fm.get("phase"), fm.get("origin"), fm.get("origin_ref")

    if not phase:
        errors.append(("plan-without-phase", rel, ""))
    elif phase not in PHASE_STATUS:
        errors.append(("phase-out-of-vocabulary", rel, phase))
    elif PHASE_STATUS[phase] != status:
        errors.append(("phase-status-mismatch", rel, f"{phase} pede status {PHASE_STATUS[phase]}, não {status}"))
    elif phase == "blocked" and not (fm.get("blocks") and fm.get("owner")):
        errors.append(("phase-status-mismatch", rel, "blocked pede blocks e owner"))

    if not origin:
        errors.append(("plan-without-origin", rel, ""))
    elif origin not in ORIGINS:
        errors.append(("origin-out-of-vocabulary", rel, origin))
    elif origin == "request":
        if ref:
            errors.append(("origin-ref-on-request", rel, ref))
    elif not ref:
        errors.append(("origin-ref-missing", rel, origin))
    elif not origin_ref_found(root, origin, ref, workstreams):
        errors.append(("origin-ref-not-found", rel, f"{origin} {ref}"))

    if not fm.get("ready"):
        errors.append(("plan-without-ready", rel, ""))
    for key in HOURS:
        if key in fm:
            try:
                float(fm[key])
            except (TypeError, ValueError):
                errors.append(("hours-not-a-number", rel, f"{key}: {fm[key]}"))

    body = without_code(body_of(text))
    lines = body.split("\n")
    title = next((i for i, line in enumerate(lines) if line.startswith("# ")), None)
    first = next((line for line in lines[title + 1:] if line.strip()), "") if title is not None else ""
    if not first.startswith("- **Retomar:**"):
        errors.append(("retomar-not-first", rel, first[:60]))

    worklogs = section(body, "## Worklogs")
    if worklogs is None:
        errors.append(("plan-without-worklogs", rel, ""))
    if worklogs and WORKLOGS_LINK_FROM:
        for entry in re.split(r"\n(?=- )", "\n" + worklogs):
            m = re.match(r"^- (\d{4}-\d{2}-\d{2})\b", entry.strip())
            if m and m.group(1) >= WORKLOGS_LINK_FROM and not any(
                    os.path.basename(os.path.dirname(p)) == "worklogs" for p in linked_paths(entry, full)):
                errors.append(("worklog-line-without-link", rel, m.group(1)))

    folder = os.path.dirname(os.path.normpath(full))
    for genre, headings_of_genre in POINTER_SECTIONS.items():
        pointed = set()
        for heading in headings_of_genre:
            pointed |= linked_paths(section(body, heading) or "", full)
        for note_rel, note_full, targets in linking[genre]:
            if folder not in targets or os.path.normpath(note_full) in pointed:
                continue
            if genre == "issue" and ref and os.path.splitext(os.path.basename(note_rel))[0] == ref:
                continue
            warnings.append(("backlink-missing", rel, note_rel))
    return errors, warnings


def date_of(fm):
    dates = [fm.get(k) for k in ("opened", "decided", "completed") if isinstance(fm.get(k), str)]
    return max(dates) if dates else None


def check(root):
    on_disk = files(root)
    all_files = list(on_disk)
    errors, warnings, broken = [], [], []
    open_plans, linking = [], defaultdict(list)
    full_paths = {os.path.normpath(p) for p in on_disk.values()}
    workstreams = WorkstreamIndex(root)

    names = defaultdict(list)
    for rel in all_files:
        name = os.path.basename(rel)
        key = name[:-3].lower() if name.endswith(".md") else name.lower()
        names[key].append(rel)
    for key, rels in sorted(names.items()):
        # O README.md é a norma de cada zona ou repositório, nunca nota: o nome se repete por construção
        if len(rels) > 1 and key != "readme":
            errors.append(("duplicate-name", ", ".join(rels), key))

    for rel in all_files:
        if not rel.endswith(".md") or rel in SKILL_TEMPLATES:
            continue
        src_full = on_disk[rel]
        with open(src_full, encoding="utf-8") as f:
            text = f.read()

        linked = text
        for wl in wikilink_targets(linked):
            errors.append(("wikilink-forbidden", rel, f"[[{wl}]]"))
        for href, path_part in md_link_targets(linked):
            if not resolves_md_link(path_part, src_full, full_paths, workstreams):
                broken.append((rel, href))

        genre = genre_of(rel)
        if genre in POINTER_SECTIONS:
            # a pasta de cada link da nota, para o aviso da nota que linka um plano e falta na seção dele
            linking[genre].append((rel, src_full, {os.path.dirname(p) for p in linked_paths(linked, src_full)}))
        if genre is None:
            continue  # o README.md de cada zona é a norma, sem gênero
        fm = frontmatter(text)
        if not fm:
            continue
        recent = (date_of(fm) or "") >= CUTOFF
        required = errors if recent else warnings

        status = fm.get("status")
        if status is not None:
            if status not in STATUSES:
                errors.append(("status-out-of-vocabulary", rel, status))
            elif genre in STATELESS_GENRES:
                warnings.append(("status-on-stateless-genre", rel, f"{genre} não leva status"))
        if status == "done" and not fm.get("completed") and genre != "decision":
            required.append(("done-without-completed", rel, ""))
        if bool(fm.get("decided")) != bool(fm.get("decided_by")):
            required.append(("decided-without-pair", rel, "decided e decided_by andam juntos"))
        if genre == "decision" and status == "done" and not (fm.get("decided") and fm.get("decided_by")):
            required.append(("decision-done-without-decided", rel, ""))
        if (
            genre == "decision"
            and status in {"open", "in_progress", "in_review"}
            and not (fm.get("trigger") or fm.get("deadline"))
        ):
            required.append(("open-decision-without-trigger", rel, ""))
        if fm.get("blocks") and not fm.get("owner"):
            required.append(("blocks-without-owner", rel, ""))
        if genre == "review":
            for finding in findings_without_evidence(text):
                warnings.append(("finding-without-evidence", rel, finding))
        if genre == "plan" and rel.endswith("-plan.md") and status in OPEN_STATUSES:
            open_plans.append((rel, src_full, fm, text))

        kind = fm.get("type")
        if kind is not None:
            if kind == "unknown" and genre not in (None, "issue"):
                warnings.append(("type-unknown-outside-issue", rel, kind))
            elif kind != "unknown":
                warnings.append(("type-redundant-or-invalid", rel, kind))

        tags = fm.get("tags") or []
        for tag in tags if isinstance(tags, list) else [tags]:
            if tag.startswith(FORBIDDEN_TAG_PREFIXES):
                warnings.append(("tag-echoes-field", rel, tag))
            elif tag not in TAGS:
                warnings.append(("tag-out-of-vocabulary", rel, tag))

    for rel, full, fm, text in open_plans:
        found_errors, found_warnings = plan_findings(root, rel, full, fm, text, linking, workstreams)
        errors.extend(found_errors)
        warnings.extend(found_warnings)

    return all_files, errors, warnings, broken


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=DEFAULT_ROOT)
    p.add_argument("--json", action="store_true")
    p.add_argument("--detail", action="store_true")
    a = p.parse_args()

    root = os.path.abspath(a.root)
    if not any(os.path.isdir(os.path.join(root, z)) for z in ROOT_ZONES):
        print(f"notas não encontradas: {root} não tem work/, plans/ nem wiki/; passe --root", file=sys.stderr)
        sys.exit(2)

    all_files, errors, warnings, broken = check(root)
    errors.extend(("unresolved-link", s, t) for s, t in broken)

    if a.json:
        print(json.dumps({"root": root, "files": len(all_files), "broken": len(broken),
                          "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=1))
    else:
        counts = defaultdict(int)
        for rule, *_ in warnings:
            counts[rule] += 1
        print(f"raiz: {root}")
        print(f"arquivos: {len(all_files)}  |  links não resolvidos: {len(broken)}")
        print(f"erros: {len(errors)}  |  avisos: {len(warnings)}")
        for rule, where, what in errors:
            print(f"  ERRO  {rule}: {where} — {what}")
        if a.detail:
            for rule, where, what in warnings:
                print(f"  aviso {rule}: {where} — {what}")
        else:
            for rule, n in sorted(counts.items()):
                print(f"  aviso {rule}: {n}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
