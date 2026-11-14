#!/usr/bin/env python3
"""Checador das notas do workspace: work/, plans/ e wiki/ na raiz do repositório.

Confere a forma das notas contra a norma do work/README.md e do plans/README.md.

  erro   nome de arquivo duplicado; link Markdown relativo quebrado — o que existe em outra branch workstream/* não
         está quebrado, e o origin_ref de plano também se procura lá; [[wikilink]] fora de código;
         status fora do vocabulário; campo obrigatório ausente em nota datada de CUTOFF em diante;
         plano aberto fora do molde do plans/README.md — o frontmatter, o Retomar e as seções (plan_findings);
         plano, issue ou decisão aberta de DESCRIPTION_FROM em diante sem o campo `description`, que o índice da master
         (`.brza/index.jsonl`) guarda; a nota anterior entra no índice com o H1 e não é cobrada;
         nota de work/feedbacks/ sem uma das cinco seções, fora da ordem, com model ou effort vazio, ou com result
         fora de done, refused e partial
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
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from urllib.parse import unquote

# Os vocabulários são do work/README.md e do plans/README.md; mudou lá, muda aqui no mesmo commit.
STATUSES = {"open", "in_progress", "in_review", "done", "dropped"}
STATELESS_GENRES = {"guide", "worklog", "note", "feedback"}
TAGS = {"auth", "chat", "design", "infra", "planning", "release", "tooling"}
FORBIDDEN_TAG_PREFIXES = ("work/", "repo/", "scope/", "status/")
# O dia em que o workspace nasceu: toda nota datada daí em diante é cobrada; só a que não tem data nenhuma sai como
# aviso, porque data que não está escrita não se infere. Este é o padrão, sem manifesto; com o `.brza/workspace.toml`
# na raiz, vale o `workspace.born` dele (`manifest_dates`).
CUTOFF = "2026-10-26"
ROOT_ZONES = ("work", "plans", "wiki")
# O dia a partir do qual plano, issue e decisão levam o campo `description` (uma frase, para o índice da master). Vale
# pela data de criação da nota (a menor entre opened, decided e completed); a nota sem data, ou de antes, não é cobrada:
# o índice usa o H1 dela. Fixado aqui e no work/README.md; o dia seguinte ao do P4 do PLN-006.
DESCRIPTION_FROM = "2026-11-01"
DESCRIPTION_GENRES = {"plan", "issue", "decision"}
# O plano (plans/README.md): o status de cada phase e as origens
OPEN_STATUSES = {"open", "in_progress", "in_review"}
PHASE_STATUS = {"interview": "open", "design": "open", "execution": "in_progress", "review": "in_review",
                "blocked": "open", "closed": "done", "dropped": "dropped"}
ORIGINS = {"issue", "decision", "plan", "request"}
HOURS = ("estimate_h", "executed_h", "remaining_h")
# A linha do ## Worklogs leva o link do worklog a partir deste dia: o seguinte ao da criação do checador, fixado aqui e no
# plans/README.md. Sem ele, a regra não cobraria linha nenhuma. Também o padrão sem manifesto; com ele, vale o
# `checker.worklogs_link_from`, ou o `workspace.born`.
WORKLOGS_LINK_FROM = "2026-10-28"
# A seção de ponteiro do plano onde cada gênero que o linka aparece; a issue também pode estar no origin_ref
POINTER_SECTIONS = {"worklog": ("## Worklogs",), "review": ("## Revisões",), "decision": ("## Decisões",),
                    "issue": ("## Pendências conhecidas", "### Fora de escopo")}
# Molde que as notas citam e que mora na skill dona: entra só para resolver link, e não é conferido como nota
SKILL_TEMPLATES = (
    ".claude/skills/plan-runner/references/plan-template.md",
    ".claude/skills/migration-runner/references/migration-template.md",
)
ISSUE_TYPES = {"unknown", "migration"}
PLAN_NOTE_RE = re.compile(r"^plans/(PLN-\d+)-[^/]+/\1-plan\.md$")
PLAN_FOLDER_RE = re.compile(r"^plans/(PLN-\d+)-[^/]+/")
REV_NOTE_RE = re.compile(r"^work/reviews/(REV-\d+)-[^/]+\.md$")
MIG_NOTE_RE = re.compile(r"^work/issues/(MIG-\d+)-[^/]+\.md$")
ISSUE_NOTE_RE = re.compile(r"^work/issues/(?!README\.md$)(?!MIG-\d+-)([^/]+)\.md$")
TEXT_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_:-])(PLN-\d+|REV-\d+|MIG-\d+|issue:[a-z0-9]+(?:-[a-z0-9]+)*)(?:/[A-Za-z0-9_-]+)?(?![A-Za-z0-9_])"
)
DEFAULT_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), *[".."] * 4))

GENRES = {
    "work/notes": "note",
    "work/issues": "issue",
    "work/worklogs": "worklog",
    "work/reviews": "review",
    "work/decisions": "decision",
    "work/feedbacks": "feedback",
    "wiki/guides": "guide",
}
# D2 do PLN-025: uma nota por execução, sem status. model e effort inválidos são os vazios — a lista de modelos e a
# escada de esforço mudam por runtime. result só aceita estes três.
FEEDBACK_SECTIONS = ("## Pedido", "## Escolha", "## Erro", "## Esforço", "## Próxima vez")
FEEDBACK_RESULTS = {"done", "refused", "partial"}
# A zona que é o próprio gênero: plans/<arquivo> e plans/<PLN-NNN-assunto>/<arquivo>; o README.md dela é a norma
FLAT_ZONES = {"plans": "plan"}

WIKILINK = re.compile(r"!?\[\[([^\[\]\n]+?)\]\]")
MD_LINK = re.compile(r"!?\[(?:\\.|[^\[\]\n])*\]\(([^()\s]+)(?:\s+\"[^\"]*\")?\)")
URI_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
FENCE = re.compile(r"^(```|~~~)")
INLINE_CODE = re.compile(r"`[^`\n]*`")


def dependency_satisfied(status):
    """D2 (R9.1): somente status 'done' satisfaz uma dependência; open, in_progress, in_review, dropped ou ausente não."""
    return status == "done"


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
        self._ref_of_path = {}
        self._base = None
        self._index_ids = None

    def _load(self):
        self._paths, self._ref_of_path, self._base = set(), {}, self.root
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
            for n in names:
                if n:
                    self._paths.add(n)
                    if n not in self._ref_of_path or ref.startswith("refs/heads/"):
                        self._ref_of_path[n] = ref

    def _git(self, *args):
        return subprocess.run(["git", "-C", self.root, *args], capture_output=True, text=True, check=True).stdout

    def _repo_path(self, full):
        """O caminho de `full` relativo à raiz do repositório, ou None quando cai fora dele."""
        if self._paths is None:
            self._load()
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

    def all_paths(self):
        if self._paths is None:
            self._load()
        return set(self._paths)

    def show(self, rel):
        if self._paths is None:
            self._load()
        ref = self._ref_of_path.get(rel)
        if not ref:
            return None
        try:
            return self._git("show", f"{ref}:{rel}")
        except (OSError, subprocess.CalledProcessError):
            return None

    def issue_id_for_path(self, rel):
        """Preserva a identidade issue:<slug_original> da issue após rename, via .brza/index.jsonl ou git log --follow."""
        m = ISSUE_NOTE_RE.match(rel)
        if not m:
            return None
        default_id = f"issue:{m.group(1)}"
        if self._index_ids is None:
            self._index_ids = self._load_index_ids()
        if rel in self._index_ids:
            return self._index_ids[rel]
        try:
            added = self._git("log", "--follow", "--diff-filter=A", "--format=", "--name-only", "--", rel).splitlines()
            if added:
                first_rel = added[-1].strip()
                if first_rel in self._index_ids:
                    return self._index_ids[first_rel]
                m_first = ISSUE_NOTE_RE.match(first_rel)
                if m_first:
                    return f"issue:{m_first.group(1)}"
        except (OSError, subprocess.CalledProcessError):
            pass
        return default_id

    def _load_index_ids(self):
        by_id = {}
        raw_text = None
        for ref in ("origin/master", "master", "HEAD"):
            try:
                raw_text = self._git("show", f"{ref}:.brza/index.jsonl")
                break
            except (OSError, subprocess.CalledProcessError):
                continue
        if raw_text is None:
            idx_file = os.path.join(self.root, ".brza", "index.jsonl")
            if os.path.isfile(idx_file):
                try:
                    with open(idx_file, encoding="utf-8") as f:
                        raw_text = f.read()
                except OSError:
                    raw_text = ""
        if not raw_text:
            return {}
        for line in raw_text.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            ts = str(item.get("ts") or "")
            if item["id"] not in by_id or ts >= by_id[item["id"]][0]:
                by_id[item["id"]] = (ts, item)
        return {
            entry["path"]: id_
            for id_, (_, entry) in by_id.items()
            if id_.startswith("issue:") and isinstance(entry.get("path"), str)
        }


class TargetCatalog:
    """Catálogo de alvos de refs e depends_on na árvore atual e nas branches workstream/*."""

    def __init__(self, root, on_disk=None, workstreams=None, texts_on_disk=None):
        self.root = os.path.realpath(root)
        self.on_disk = on_disk if on_disk is not None else files(self.root)
        self.workstreams = workstreams if workstreams is not None else WorkstreamIndex(self.root)
        self.texts_on_disk = texts_on_disk if texts_on_disk is not None else {}
        self.full_paths = {os.path.normpath(p) for p in self.on_disk.values()}
        self.valid_refs = set()
        self.dep_targets = {}
        self.id_by_rel = {}
        self._fm_cache = {}
        self._build()

    def _build(self):
        disk_rels = set(self.on_disk.keys())
        all_rels = disk_rels | self.workstreams.all_paths()
        plans = set()
        for rel in sorted(all_rels):
            on_disk = rel in disk_rels
            if m := PLAN_NOTE_RE.match(rel):
                pid = m.group(1)
                plans.add(pid)
                self.valid_refs.add(pid)
                self.dep_targets[pid] = (rel, on_disk)
                self.id_by_rel[rel] = pid
            elif m := REV_NOTE_RE.match(rel):
                rid = m.group(1)
                self.valid_refs.add(rid)
                self.id_by_rel[rel] = rid
            elif m := MIG_NOTE_RE.match(rel):
                mid = m.group(1)
                self.valid_refs.add(mid)
                self.dep_targets[mid] = (rel, on_disk)
                self.id_by_rel[rel] = mid
            elif ISSUE_NOTE_RE.match(rel):
                iid = self.workstreams.issue_id_for_path(rel) if not on_disk else self._disk_issue_id(rel)
                self.valid_refs.add(iid)
                self.dep_targets[iid] = (rel, on_disk)
                self.id_by_rel[rel] = iid
        for rel in sorted(all_rels):
            if rel not in self.id_by_rel and (m := PLAN_FOLDER_RE.match(rel)) and m.group(1) in plans:
                self.id_by_rel[rel] = m.group(1)

    def _disk_issue_id(self, rel):
        m = ISSUE_NOTE_RE.match(rel)
        default_id = f"issue:{m.group(1)}" if m else None
        resolved = self.workstreams.issue_id_for_path(rel)
        return resolved or default_id

    def resolves_path(self, target_full):
        return resolves_md_link("", target_full, self.full_paths, self.workstreams) if False else (
            os.path.normpath(target_full) in self.full_paths
            or os.path.isfile(target_full)
            or self.workstreams.has(target_full)
        )

    def rel_path(self, target_full):
        rel = os.path.relpath(os.path.realpath(target_full), self.root).replace(os.sep, "/")
        if rel != ".." and not rel.startswith("../"):
            return rel
        return self.workstreams._repo_path(target_full)

    def frontmatter_of(self, target_id):
        if target_id in self._fm_cache:
            return self._fm_cache[target_id]
        info = self.dep_targets.get(target_id)
        if not info:
            self._fm_cache[target_id] = {}
            return {}
        rel, on_disk = info
        text = None
        if on_disk:
            if rel in self.texts_on_disk:
                text = self.texts_on_disk[rel]
            elif rel in self.on_disk and os.path.isfile(self.on_disk[rel]):
                with open(self.on_disk[rel], encoding="utf-8") as f:
                    text = f.read()
        else:
            text = self.workstreams.show(rel)
        fm = frontmatter(text) if text else {}
        self._fm_cache[target_id] = fm
        return fm

    def deps_of(self, target_id):
        return as_list(self.frontmatter_of(target_id).get("depends_on"))


def without_links(line):
    """D3, R14.3: a linha sem os links Markdown inteiros, texto e alvo; o link conta pelo alvo, e não pelos IDs do texto
    que ele mostra. A crase inline fora de link fica; a que está no texto de um link sai com ele."""
    masked = INLINE_CODE.sub(lambda m: "`" + "x" * (len(m.group(0)) - 2) + "`", line)
    kept, last = [], 0
    for m in MD_LINK.finditer(masked):
        kept += [line[last:m.start()], " "]
        last = m.end()
    return "".join(kept) + line[last:]


def extract_worklog_refs(text, src_full, root, catalog=None):
    """D3: extrai refs de um worklog (prosa, crases inline, subitens e o alvo dos links relativos resolvidos, fora de
    cercas); o texto dos links fica de fora (R14.3).
    Retorna (refs_ordenados, avisos_id_desconhecido_como_lista_de_(lineno, id))."""
    if catalog is None:
        catalog = TargetCatalog(root)
    refs_found = set()
    unknown = []
    body = body_of(text)
    for _, path_part in md_link_targets(body):
        target_full = os.path.normpath(os.path.join(os.path.dirname(src_full), path_part))
        if catalog.resolves_path(target_full):
            target_rel = catalog.rel_path(target_full)
            if target_rel and target_rel in catalog.id_by_rel:
                refs_found.add(catalog.id_by_rel[target_rel])

    lines = text.split("\n")
    start_idx = 0
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0:
            start_idx = text[:end + 4].count("\n") + 1
    fenced = False
    for idx in range(start_idx, len(lines)):
        line = lines[idx]
        lineno = idx + 1
        if FENCE.match(line.strip()):
            fenced = not fenced
            continue
        if fenced:
            continue
        scan_line = without_links(line)
        for m in TEXT_REF_RE.finditer(scan_line):
            ref_id = m.group(1)
            if ref_id in catalog.valid_refs:
                refs_found.add(ref_id)
            else:
                unknown.append((lineno, ref_id))
    return sorted(refs_found), unknown


def _find_dependency_cycle(start_id, first_dep, catalog):
    def dfs(curr, trail, visited):
        if curr in trail:
            idx = trail.index(curr)
            return trail[idx:] + [curr]
        if curr in visited:
            return None
        visited.add(curr)
        trail.append(curr)
        for nxt in catalog.deps_of(curr):
            if nxt in catalog.dep_targets:
                found = dfs(nxt, trail, visited)
                if found:
                    return found
        trail.pop()
        return None

    return dfs(first_dep, [start_id], set())


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


def created_on(fm):
    """A data de criação da nota: a menor entre opened, decided e completed, ou None sem nenhuma."""
    dates = [fm.get(k) for k in ("opened", "decided", "completed") if isinstance(fm.get(k), str)]
    return min(dates) if dates else None


def date_of(fm):
    dates = [fm.get(k) for k in ("opened", "decided", "completed") if isinstance(fm.get(k), str)]
    return max(dates) if dates else None


def feedback_findings(text, fm):
    """Seções na ordem do D2, model e effort preenchidos, result no vocabulário. Devolve (regra, detalhe)."""
    found = []
    for line in without_code(text).split("\n"):
        title = line.strip()
        if title in FEEDBACK_SECTIONS and title not in found:
            found.append(title)
    missing = [title for title in FEEDBACK_SECTIONS if title not in found]
    findings = [("feedback-section-missing", title) for title in missing]
    if not missing and found != list(FEEDBACK_SECTIONS):
        findings.append(("feedback-section-order", ", ".join(found)))
    model, effort, result = fm.get("model"), fm.get("effort"), fm.get("result")
    if not (isinstance(model, str) and model.strip()):
        findings.append(("feedback-model-invalid", "" if model is None else str(model)))
    if not (isinstance(effort, str) and effort.strip()):
        findings.append(("feedback-effort-invalid", "" if effort is None else str(effort)))
    if result not in FEEDBACK_RESULTS:
        findings.append(("feedback-result-invalid", "" if result is None else str(result)))
    return findings


def check(root):
    on_disk = files(root)
    all_files = list(on_disk)
    errors, warnings, broken = [], [], []
    open_plans, linking = [], defaultdict(list)
    full_paths = {os.path.normpath(p) for p in on_disk.values()}
    workstreams = WorkstreamIndex(root)
    texts_on_disk = {}
    catalog = None

    def get_catalog():
        nonlocal catalog
        if catalog is None:
            catalog = TargetCatalog(root, on_disk, workstreams, texts_on_disk)
        return catalog

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
        texts_on_disk[rel] = text

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
        if genre == "worklog":
            _, unknown_mentions = extract_worklog_refs(text, src_full, root, get_catalog())
            for lineno, ref_id in unknown_mentions:
                warnings.append(("unknown-ref-mention", rel, f"{ref_id} (linha {lineno})"))
        fm = frontmatter(text)
        if genre == "feedback":
            for rule, what in feedback_findings(text, fm):
                errors.append((rule, rel, what))
        # D7, R14.1: a nota fora de número some do índice e do refs; avisa, sem barrar
        if genre == "review" and not REV_NOTE_RE.match(rel) and os.path.basename(rel) != "README.md":
            warnings.append(("review-without-number", rel, "o parecer nasce como work/reviews/REV-NNN-<slug>.md"))
        if genre == "issue" and MIG_NOTE_RE.match(rel) and fm.get("type") != "migration":
            warnings.append(("migration-without-type", rel, "a nota MIG-NNN leva type: migration"))
        if genre == "issue" and fm.get("type") == "migration" and not MIG_NOTE_RE.match(rel):
            warnings.append(("migration-type-without-number", rel, "a migração nasce como work/issues/MIG-NNN-<slug>.md"))
        if not fm:
            continue
        if genre == "worklog":
            if "date" in fm:
                warnings.append(("date-on-worklog", rel, str(fm["date"])))
            if "refs" in fm:
                cat = get_catalog()
                for ref_id in as_list(fm.get("refs")):
                    if ref_id not in cat.valid_refs:
                        errors.append(("ref-not-found", rel, ref_id))
        if genre == "issue" and "estimate_h" in fm:
            try:
                float(fm["estimate_h"])
            except (TypeError, ValueError):
                errors.append(("hours-not-a-number", rel, f"estimate_h: {fm['estimate_h']}"))
        if genre in ("plan", "issue") and (genre != "plan" or rel.endswith("-plan.md")) and "depends_on" in fm:
            cat = get_catalog()
            own_id = cat.id_by_rel.get(rel)
            for dep in as_list(fm.get("depends_on")):
                if dep not in cat.dep_targets:
                    errors.append(("depends-on-not-found", rel, dep))
                elif own_id:
                    cycle = _find_dependency_cycle(own_id, dep, cat)
                    if cycle:
                        errors.append(("depends-on-cycle", rel, " -> ".join(cycle)))

        recent = (date_of(fm) or "") >= CUTOFF
        required = errors if recent else warnings
        if (
            genre in DESCRIPTION_GENRES
            and (genre != "plan" or rel.endswith("-plan.md"))
            and (created_on(fm) or "") >= DESCRIPTION_FROM
            and not (isinstance(fm.get("description"), str) and fm["description"].strip())
        ):
            errors.append(("description-missing", rel, "o campo description, uma frase, vai ao índice da master"))

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
            elif kind not in ISSUE_TYPES or genre not in (None, "issue"):
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


def manifest_dates(root):
    """(CUTOFF, WORKLOGS_LINK_FROM) do `.brza/workspace.toml` de `root`: o `workspace.born`, e o
    `checker.worklogs_link_from`, que ausente é o dia de `born`. Sem manifesto, sem `born` ou com o manifesto ilegível,
    os padrões deste arquivo."""
    hooks = os.path.join(DEFAULT_ROOT, ".claude", "hooks", "brza_root.py")
    try:
        spec = importlib.util.spec_from_file_location("brza_root", hooks)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        manifest = module.read_manifest(root)
    except (OSError, ImportError, AttributeError, SyntaxError):
        return CUTOFF, WORKLOGS_LINK_FROM
    dated = re.compile(r"\d{4}-\d{2}-\d{2}$")
    workspace = (manifest or {}).get("workspace")
    born = workspace.get("born") if isinstance(workspace, dict) else None
    if not (isinstance(born, str) and dated.match(born)):
        return CUTOFF, WORKLOGS_LINK_FROM
    checker = (manifest or {}).get("checker")
    link = checker.get("worklogs_link_from") if isinstance(checker, dict) else None
    return born, link if isinstance(link, str) and dated.match(link) else born


def main():
    global CUTOFF, WORKLOGS_LINK_FROM
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=DEFAULT_ROOT)
    p.add_argument("--json", action="store_true")
    p.add_argument("--detail", action="store_true")
    a = p.parse_args()

    root = os.path.abspath(a.root)
    if not any(os.path.isdir(os.path.join(root, z)) for z in ROOT_ZONES):
        print(f"notas não encontradas: {root} não tem work/, plans/ nem wiki/; passe --root", file=sys.stderr)
        sys.exit(2)

    CUTOFF, WORKLOGS_LINK_FROM = manifest_dates(root)
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
