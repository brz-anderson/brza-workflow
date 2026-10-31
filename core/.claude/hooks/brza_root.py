"""A raiz do workspace e o parser TOML que a acha: o que os hooks, o `brza note` e os comandos `brza where` e
`brza register` compartilham, em vez de cada um repetir a regra.

A raiz se acha, nesta ordem (`find`):
1. `BRZA_WORKSPACE`, quando aponta para um diretório com `.brza/workspace.toml`: o override, que escolhe a raiz;
2. o checkout principal do cwd — o pai do `git rev-parse --git-common-dir` — com o manifesto: papel `notes`, em
   qualquer worktree do workspace, onde quer que ele esteja;
3. o registro da máquina (`~/.config/brza/workspaces.toml`): a entrada cujo `code` é o checkout principal do cwd, e
   cujo `root` tem o manifesto: papel `code`, também num worktree fora do diretório do código;
4. nada disso: None.

O módulo roda no Python 3.9 do sistema, porque o hook roda no `python3` que o PATH der e o `|| true` cala a quebra: nada
de `X | None` em execução e nada de `tomllib`. O parser é próprio, de um subconjunto — tabela, `[[tabela]]`, string,
inteiro, booleano, lista, comentário —, conferido contra o `tomllib` nos testes. O que sai do subconjunto (float, data,
tabela em linha, string de várias linhas, chave pontilhada) é recusado com `TomlError`, e não adivinhado.

Também mora aqui o que lê e escreve o manifesto e o lock (`validate_manifest`, `dump_manifest`, `dump_lock`) e o
`render` dos placeholders `{{tabela.chave}}` das normas em Markdown e do molde do `settings.json`, que o `brza init`
e o `brza sync` usam.

Só a stdlib.
"""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import NamedTuple, Optional

MANIFEST = ".brza/workspace.toml"
REGISTRY_HEADER = (
    "# ~/.config/brza/workspaces.toml — os workspaces desta máquina. Escrito pelo brza init e pelo brza register;\n"
    "# arquivo de usuário: cada escrita vai ao worklog, em \"## Fora do workspace\".\n"
)
DEFAULT_PLAN_BRANCH = r"[a-z]+/pln-(\d+)(-|$)"


class TomlError(ValueError):
    """O texto não é TOML, ou usa algo fora do subconjunto; a mensagem diz a linha."""


# O parser

_BARE = re.compile(r"[A-Za-z0-9_-]+")
_INT = re.compile(r"[+-]?(0|[1-9](_?[0-9])*)")
_BOOL = re.compile(r"(true|false)")
_ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
_AFTER_VALUE = " \t\r\n,]#"


class _Parser:
    def __init__(self, text):
        self.s = text
        self.i = 0
        self.root = {}
        self.defined = set()  # id() das tabelas já definidas por cabeçalho ou por chave
        self.arrays = set()   # id() das listas nascidas de [[tabela]]

    def fail(self, message):
        raise TomlError("linha %d: %s" % (self.s.count("\n", 0, self.i) + 1, message))

    def peek(self):
        return self.s[self.i] if self.i < len(self.s) else ""

    def skip_blank(self):
        while self.peek() in (" ", "\t"):
            self.i += 1

    def skip_comment(self):
        if self.peek() == "#":
            while self.peek() not in ("", "\n"):
                self.i += 1

    def end_of_line(self):
        self.skip_blank()
        self.skip_comment()
        if self.peek() == "\r":
            self.i += 1
        if self.peek() not in ("", "\n"):
            self.fail("texto depois do valor")
        if self.peek() == "\n":
            self.i += 1

    def skip_space_lines(self):
        """Branco, quebras e comentários entre os itens de uma lista, ou antes de uma linha."""
        while True:
            self.skip_blank()
            if self.peek() in ("\n", "\r"):
                self.i += 1
            elif self.peek() == "#":
                self.skip_comment()
            else:
                return

    def parse(self):
        current = self.root
        while True:
            self.skip_space_lines()
            if not self.peek():
                return self.root
            if self.peek() == "[":
                current = self.header()
            else:
                self.assign(current)

    def key(self):
        c = self.peek()
        if c in ('"', "'"):
            return self.string()
        m = _BARE.match(self.s, self.i)
        if not m:
            self.fail("chave esperada")
        self.i = m.end()
        return m.group()

    def key_path(self):
        parts = [self.key()]
        self.skip_blank()
        while self.peek() == ".":
            self.i += 1
            self.skip_blank()
            parts.append(self.key())
            self.skip_blank()
        return parts

    def header(self):
        array = self.s.startswith("[[", self.i)
        self.i += 2 if array else 1
        self.skip_blank()
        parts = self.key_path()
        closing = "]]" if array else "]"
        if not self.s.startswith(closing, self.i):
            self.fail("cabeçalho sem %s" % closing)
        self.i += len(closing)
        self.end_of_line()
        node = self.root
        for part in parts[:-1]:
            node = self.step(node, part)
        last = parts[-1]
        if array:
            existing = node.get(last)
            if existing is None:
                existing = node[last] = []
                self.arrays.add(id(existing))
            elif id(existing) not in self.arrays:
                self.fail("%s já existe e não é uma lista de tabelas" % last)
            table = {}
            existing.append(table)
        else:
            table = node.get(last)
            if table is None:
                table = node[last] = {}
            elif not isinstance(table, dict) or id(table) in self.defined:
                self.fail("a tabela %s já foi definida" % ".".join(parts))
            self.defined.add(id(table))
        return table

    def step(self, node, part):
        """Desce um nível do cabeçalho: a tabela, ou o último item da lista de tabelas; cria a que falta."""
        child = node.get(part)
        if child is None:
            child = node[part] = {}
        elif isinstance(child, list) and id(child) in self.arrays:
            child = child[-1]
        elif not isinstance(child, dict):
            self.fail("%s não é uma tabela" % part)
        return child

    def assign(self, table):
        parts = self.key_path()
        if len(parts) > 1:
            self.fail("chave pontilhada fora do subconjunto; use uma tabela")
        self.skip_blank()
        if self.peek() != "=":
            self.fail("'=' esperado depois da chave")
        self.i += 1
        self.skip_blank()
        if not self.peek() or self.peek() in "\r\n#":
            self.fail("valor esperado")
        key = parts[0]
        if key in table:
            self.fail("a chave %s se repete" % key)
        table[key] = self.value()
        self.end_of_line()

    def value(self):
        c = self.peek()
        if c in ('"', "'"):
            if self.s.startswith(c * 3, self.i):
                self.fail("string de várias linhas fora do subconjunto")
            return self.string()
        if c == "[":
            return self.array()
        if c == "{":
            self.fail("tabela em linha fora do subconjunto")
        m = _BOOL.match(self.s, self.i)
        if m and self.s[m.end():m.end() + 1] in tuple(_AFTER_VALUE) + ("",):
            self.i = m.end()
            return m.group() == "true"
        m = _INT.match(self.s, self.i)
        if m and self.s[m.end():m.end() + 1] in tuple(_AFTER_VALUE) + ("",):
            self.i = m.end()
            return int(m.group().replace("_", ""))
        self.fail("valor fora do subconjunto (string, inteiro, booleano ou lista)")

    def array(self):
        self.i += 1
        items = []
        while True:
            self.skip_space_lines()
            if self.peek() == "]":
                self.i += 1
                return items
            if not self.peek():
                self.fail("lista sem ']'")
            items.append(self.value())
            self.skip_space_lines()
            if self.peek() == ",":
                self.i += 1
            elif self.peek() != "]":
                self.fail("',' ou ']' esperado na lista")

    def string(self):
        quote = self.peek()
        self.i += 1
        out = []
        while True:
            c = self.peek()
            if c == "" or c == "\n":
                self.fail("string sem fim")
            self.i += 1
            if c == quote:
                return "".join(out)
            if c == "\\" and quote == '"':
                out.append(self.escape())
            elif (ord(c) < 0x20 and c != "\t") or ord(c) == 0x7F:
                self.fail("caractere de controle dentro da string")
            else:
                out.append(c)

    def escape(self):
        c = self.peek()
        self.i += 1
        if c in _ESCAPES:
            return _ESCAPES[c]
        if c in ("u", "U"):
            size = 4 if c == "u" else 8
            digits = self.s[self.i:self.i + size]
            if len(digits) != size or not re.fullmatch(r"[0-9A-Fa-f]+", digits):
                self.fail("escape \\%s inválido" % c)
            code = int(digits, 16)
            if 0xD800 <= code <= 0xDFFF or code > 0x10FFFF:
                self.fail("escape \\%s fora do escalar Unicode" % c)
            self.i += size
            return chr(code)
        self.fail("escape desconhecido na string")


def parse_toml(text):
    """O dict do TOML `text` no subconjunto; TomlError com a linha quando o texto quebra ou sai dele."""
    return _Parser(text).parse()


def quote(value):
    """`value` como string TOML básica, que o `parse_toml` lê de volta."""
    out = ['"']
    for c in value:
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\n":
            out.append("\\n")
        elif c == "\t":
            out.append("\\t")
        elif c == "\r":
            out.append("\\r")
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append("\\u%04x" % ord(c))
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


# O git

def git(*args, cwd):
    """A saída do git, ou None se ele falhar, demorar ou não existir: quem acha a raiz nunca quebra por causa do git."""
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5,
                              env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return done.stdout if done.returncode == 0 else None


def _dirs(cwd):
    """(a raiz do worktree do cwd, o checkout principal do repositório), em caminho físico, ou None fora de um
    repositório git ou num repositório sem árvore. Uma chamada ao git, ~9 ms."""
    if not cwd:
        return None
    out = git("rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir", cwd=str(cwd))
    lines = (out or "").strip().split("\n")
    if len(lines) < 2:
        return None
    top, common = Path(os.path.realpath(lines[0])), Path(os.path.realpath(lines[1]))
    return top, (common.parent if common.name == ".git" else common)


def branch_of(top):
    """A branch do worktree `top`, ou None detached ou com falha do git."""
    return (git("symbolic-ref", "--short", "-q", "HEAD", cwd=str(top)) or "").strip() or None


# O manifesto e o registro

def key(path):
    """A forma de comparar dois caminhos: física e sem maiúsculas, como o APFS os trata."""
    return os.path.realpath(os.path.expanduser(str(path))).lower()


def read_manifest(root):
    """O manifesto do workspace em `root` como dict, ou None sem arquivo ou ilegível."""
    try:
        with open(os.path.join(str(root), MANIFEST), encoding="utf-8") as f:
            return parse_toml(f.read())
    except (OSError, ValueError):
        return None


def registry_path():
    """O registro: BRZA_REGISTRY (os testes) ou $XDG_CONFIG_HOME/brza/workspaces.toml ou ~/.config/brza/..."""
    if os.environ.get("BRZA_REGISTRY"):
        return Path(os.environ["BRZA_REGISTRY"])
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "brza" / "workspaces.toml"


def read_registry():
    """As entradas `{root, code}` do registro, na ordem do arquivo; sem arquivo, []. Arquivo ilegível levanta
    TomlError (ou OSError), para o `brza register` não sobrescrevê-lo; `find` o trata como vazio."""
    try:
        with open(str(registry_path()), encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return []
    entries = parse_toml(text).get("workspace", [])
    if not isinstance(entries, list):
        raise TomlError("o registro não tem [[workspace]]")
    return [{"root": str(e["root"]), "code": str(e["code"])} for e in entries
            if isinstance(e, dict) and isinstance(e.get("root"), str) and isinstance(e.get("code"), str)]


def manifest_code_path(manifest):
    """O `[code].path` do manifesto, o padrão que o init e o `brza register` propõem, ou None."""
    path = ((manifest or {}).get("code") or {}).get("path")
    return Path(os.path.expanduser(path)) if isinstance(path, str) and path else None


def code_path(root, manifest=None):
    """O checkout do código do workspace `root` nesta máquina: a entrada do registro, ou o `[code].path` do manifesto
    (o padrão), ou None."""
    try:
        for entry in read_registry():
            if key(entry["root"]) == key(root):
                return Path(entry["code"])
    except (OSError, ValueError):
        pass
    return manifest_code_path(manifest or read_manifest(root))


def _section(manifest, name):
    value = (manifest or {}).get(name)
    return value if isinstance(value, dict) else {}


def _text(table, name, default):
    value = table.get(name)
    return value if isinstance(value, str) and value else default


def workspace_name(manifest):
    return _text(_section(manifest, "workspace"), "name", None)


def workspace_branch(manifest):
    """A branch da norma: `[workspace].branch`, ou `master` quando ausente."""
    return _text(_section(manifest, "workspace"), "branch", "master")


def workspace_remote(manifest):
    """O remote do workspace: `[workspace].remote`, ou `origin` quando ausente."""
    return _text(_section(manifest, "workspace"), "remote", "origin")


def code_name(manifest):
    return _text(_section(manifest, "code"), "name", None)


def plan_branch(manifest):
    """A expressão que tira o número do plano da branch do código, `[code].plan_branch` ou o padrão; a inválida cai
    no padrão."""
    pattern = _text(_section(manifest, "code"), "plan_branch", DEFAULT_PLAN_BRANCH)
    try:
        return re.compile(pattern)
    except re.error:
        return re.compile(DEFAULT_PLAN_BRANCH)


# A busca da raiz

class Workspace(NamedTuple):
    root: Path
    manifest: dict
    role: Optional[str]    # 'notes' | 'code' | None (o override apontou a raiz, e o cwd não é dela nem do código)
    top: Optional[Path]    # a raiz do worktree do cwd
    branch: Optional[str]  # a branch dele, só com `branch=True`


def _has_manifest(root):
    return os.path.isfile(os.path.join(str(root), MANIFEST))


def find(cwd, environ=None, branch=False):
    """O workspace do `cwd`, pela ordem do módulo, ou None. `branch=True` lê também a branch do worktree do cwd, mais
    uma chamada ao git."""
    env = os.environ if environ is None else environ
    dirs = _dirs(cwd)
    top, checkout = dirs if dirs else (None, None)
    root, role = None, None
    override = env.get("BRZA_WORKSPACE")
    if override and _has_manifest(os.path.expanduser(override)):
        root = Path(os.path.realpath(os.path.expanduser(override)))
        if checkout is not None and key(checkout) == key(root):
            role = "notes"
        elif checkout is not None and _registered(root, checkout):
            role = "code"
    elif checkout is not None and _has_manifest(checkout):
        root, role = checkout, "notes"
    elif checkout is not None:
        root = _linked_to(checkout)
        role = "code" if root else None
    if root is None:
        return None
    manifest = read_manifest(root)
    if manifest is None:
        return None
    return Workspace(root, manifest, role, top, branch_of(top) if branch and top and role else None)


def _entries():
    try:
        return read_registry()
    except (OSError, ValueError):
        return []


def _registered(root, checkout):
    return any(key(e["root"]) == key(root) and key(e["code"]) == key(checkout) for e in _entries())


def _linked_to(checkout):
    """A raiz do workspace ligado ao código `checkout` pelo registro, com o manifesto, ou None."""
    for entry in _entries():
        if key(entry["code"]) == key(checkout) and _has_manifest(entry["root"]):
            return Path(os.path.realpath(entry["root"]))
    return None


# O registro, para escrita

class RegisterError(Exception):
    """O registro recusa a entrada; a mensagem diz por quê."""


def register(root, code):
    """Acrescenta `{root, code}` ao registro e devolve True; False quando o par já estava lá. Recusa (RegisterError)
    o código já ligado a outro workspace, o workspace já ligado a outro código e o registro ilegível. `root` e `code`
    são gravados em caminho físico, absolutos."""
    root, code = os.path.realpath(os.path.expanduser(str(root))), os.path.realpath(os.path.expanduser(str(code)))
    path = registry_path()
    try:
        entries = read_registry()
    except (OSError, ValueError) as e:
        raise RegisterError("o registro %s está ilegível (%s); conserte à mão" % (path, e))
    for entry in entries:
        same_root, same_code = key(entry["root"]) == key(root), key(entry["code"]) == key(code)
        if same_root and same_code:
            return False
        if same_code:
            raise RegisterError("o código %s já está ligado ao workspace %s" % (entry["code"], entry["root"]))
        if same_root:
            raise RegisterError("o workspace %s já está ligado ao código %s" % (entry["root"], entry["code"]))
    block = "[[workspace]]\nroot = %s\ncode = %s\n" % (quote(root), quote(code))
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if not existing:
        text = REGISTRY_HEADER + block
    else:
        text = existing + ("" if existing.endswith("\n") else "\n") + "\n" + block
    path.write_text(text, encoding="utf-8")
    return True


# O esquema do manifesto (.brza/workspace.toml)

SCHEMA = 1
REQUIRED = object()  # a chave que o manifesto tem de trazer; as outras têm padrão

# tabela -> chave -> padrão (REQUIRED, ou o valor que a R3.3 e o D2 dão à chave ausente). A ordem é a do arquivo.
MANIFEST_SCHEMA = {
    "workspace": {"name": REQUIRED, "title": REQUIRED, "owner": REQUIRED, "branch": "master", "remote": "origin",
                  "born": REQUIRED},
    "author": {"name": REQUIRED, "email": REQUIRED, "login": REQUIRED},
    "code": {"name": REQUIRED, "path": REQUIRED, "github": REQUIRED, "branch": "master", "remote": "origin",
             "plan_branch": DEFAULT_PLAN_BRANCH},
    "core": {"ref": "master", "packages": []},
    "checker": {"worklogs_link_from": None},  # None: o dia de `workspace.born`
}
# O comentário que o `dump_manifest` põe depois do valor
MANIFEST_COMMENTS = {
    ("workspace", "name"): "{{workspace.name}}",
    ("workspace", "title"): "{{workspace.title}}",
    ("workspace", "owner"): "{{workspace.owner}}, quem decide",
    ("workspace", "branch"): "{{workspace.branch}}; ausente = master",
    ("workspace", "remote"): "{{workspace.remote}}; ausente = origin",
    ("workspace", "born"): "o dia em que o workspace nasceu; o checador cobra as notas daí em diante",
    ("code", "name"): "{{code.name}}",
    ("code", "path"): "{{code.path}}; o padrão do registro, não a verdade da máquina",
    ("code", "github"): "{{code.github}}",
    ("code", "plan_branch"): "tira o número do plano da branch do código",
    ("core", "ref"): "a branch do brza-workflow que o sync segue",
    ("core", "packages"): "pacotes opcionais do brza-workflow",
    ("checker", "worklogs_link_from"): "o dia a partir do qual a linha de worklog do plano leva o link",
}
MANIFEST_HEADER = ("# .brza/workspace.toml — o manifesto do workspace. O init o escreve; depois é local: o sync nunca o "
                   "toca.\n")
LOCK = ".brza/core.lock"
LOCK_HEADER = "# .brza/core.lock — gerado pelo brza sync; não edite\n"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def with_defaults(manifest):
    """Uma cópia do manifesto com o padrão de cada chave opcional ausente (R3.3: `master`, `origin`). Chave que o
    esquema não conhece segue como está; a obrigatória que falta não é inventada."""
    out = {name: (dict(table) if isinstance(table, dict) else table) for name, table in (manifest or {}).items()}
    for name, keys in MANIFEST_SCHEMA.items():
        table = out.get(name)
        if table is None:
            table = out[name] = {}
        if not isinstance(table, dict):
            continue
        for key_name, default in keys.items():
            if key_name not in table and default is not REQUIRED and default is not None:
                table[key_name] = list(default) if isinstance(default, list) else default
    ws, checker = out.get("workspace"), out.get("checker")
    if isinstance(checker, dict) and "worklogs_link_from" not in checker and isinstance(ws, dict) and "born" in ws:
        checker["worklogs_link_from"] = ws["born"]
    return out


def validate_manifest(manifest):
    """Os problemas do manifesto, um texto por problema, na ordem do esquema; [] quando está em ordem."""
    if not isinstance(manifest, dict):
        return ["o manifesto não é uma tabela"]
    problems = []
    if manifest.get("schema") != SCHEMA:
        problems.append("schema = %r, e este brza só lê o schema = %d" % (manifest.get("schema"), SCHEMA))
    for name, keys in MANIFEST_SCHEMA.items():
        table = manifest.get(name)
        if table is None:
            if any(default is REQUIRED for default in keys.values()):
                problems.append("falta a tabela [%s]" % name)
            continue
        if not isinstance(table, dict):
            problems.append("[%s] não é uma tabela" % name)
            continue
        for key_name, default in keys.items():
            where = "%s.%s" % (name, key_name)
            if key_name not in table:
                if default is REQUIRED:
                    problems.append("falta %s" % where)
                continue
            value = table[key_name]
            if key_name == "packages":
                if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
                    problems.append("%s não é uma lista de textos" % where)
            elif not (isinstance(value, str) and value):
                problems.append("%s não é um texto" % where)
            elif key_name in ("born", "worklogs_link_from") and not _DATE.fullmatch(value):
                problems.append("%s = %r não é uma data AAAA-MM-DD" % (where, value))
            elif key_name == "plan_branch":
                try:
                    re.compile(value)
                except re.error as e:
                    problems.append("%s não é uma expressão regular: %s" % (where, e))
    return problems


def _toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return quote(str(value))


def dump_manifest(manifest):
    """O texto do manifesto no layout do D2, na ordem do esquema, com o comentário de cada chave: só as chaves que
    `manifest` traz, e `parse_toml(dump_manifest(m)) == m`. O `brza init` o escreve; molde em templates/.brza/."""
    lines = [MANIFEST_HEADER, "schema = %d\n" % SCHEMA]
    for name, keys in MANIFEST_SCHEMA.items():
        table = manifest.get(name)
        if not isinstance(table, dict) or not any(k in table for k in keys):
            continue
        rows = [(k, _toml_value(table[k])) for k in keys if k in table]
        width = max(len(k) + 3 + len(v) for k, v in rows)
        lines.append("\n[%s]\n" % name)
        for k, v in rows:
            row = "%s = %s" % (k, v)
            comment = MANIFEST_COMMENTS.get((name, k))
            lines.append(row + (" " * (width - len(row) + 2) + "# " + comment if comment else "") + "\n")
    return "".join(lines)


# O lock (.brza/core.lock)

class LockError(ValueError):
    """O lock existe e não se lê; o sync não pode tratá-lo como ausente, porque então sobrescreveria edição local."""


def digest(data):
    """`sha256:<hex>` do texto (UTF-8) ou dos bytes: a forma que o lock guarda."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def hooks_digest(hooks):
    """O digest da chave `hooks` do settings.json, que o sync guarda como `.claude/settings.json#hooks`: o JSON canônico,
    sem depender da ordem das chaves."""
    return digest(json.dumps(hooks, indent=2, ensure_ascii=False, sort_keys=True))


def validate_lock(lock):
    """Os problemas do lock já lido, um texto por problema; [] quando está em ordem."""
    if not isinstance(lock, dict):
        return ["o lock não é uma tabela"]
    problems = []
    for name in ("commit", "ref", "synced"):
        if not (isinstance(lock.get(name), str) and lock[name]):
            problems.append("falta %s, ou não é um texto" % name)
    if isinstance(lock.get("synced"), str) and not _DATE.fullmatch(lock["synced"]):
        problems.append("synced = %r não é uma data AAAA-MM-DD" % lock["synced"])
    files = lock.get("files")
    if not isinstance(files, dict):
        problems.append("falta [files]")
    else:
        for path, value in files.items():
            if not (isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value)):
                problems.append("files[%r] não é um sha256:<64 hex>" % path)
    return problems


def dump_lock(commit, ref, synced, files):
    """O texto do lock: o commit do brza-workflow, a ref seguida, o dia do sync e o digest de cada caminho escrito,
    em ordem alfabética. `parse_toml(dump_lock(...))` o lê de volta."""
    lines = [LOCK_HEADER, "commit = %s\n" % quote(commit), "ref = %s\n" % quote(ref), "synced = %s\n" % quote(synced),
             "\n[files]\n"]
    for path in sorted(files):
        lines.append("%s = %s\n" % (quote(path), quote(files[path])))
    return "".join(lines)


def read_lock(root):
    """O lock do workspace em `root` como dict, ou None quando o arquivo não existe. Arquivo ilegível ou fora do
    esquema levanta LockError."""
    path = os.path.join(str(root), LOCK)
    try:
        with open(path, encoding="utf-8") as f:
            lock = parse_toml(f.read())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise LockError("%s ilegível: %s" % (path, e))
    problems = validate_lock(lock)
    if problems:
        raise LockError("%s fora do esquema: %s" % (path, "; ".join(problems)))
    return lock


# O render dos placeholders

PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)\s*\}\}")


class RenderError(ValueError):
    """Um placeholder sem valor no manifesto; a mensagem diz o caminho, a linha e a chave."""


def render(text, manifest, path="<texto>", escape=None):
    """Troca cada `{{tabela.chave}}` do `text` pelo valor do manifesto, com os padrões da R3.3 já aplicados. Sem
    condicional, laço nem escape de `{{`. Chave que falta, ou cujo valor não é texto, levanta RenderError com o
    `path` e a linha. `escape='json'` escapa o valor para dentro de uma string JSON (o molde do settings.json)."""
    values = with_defaults(manifest)

    def value(match):
        node = values
        for part in match.group(1).split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if not isinstance(node, str):
            line = text.count("\n", 0, match.start()) + 1
            why = "sem valor no manifesto" if node is None else "não é um texto no manifesto"
            raise RenderError("%s:%d: o placeholder {{%s}} %s" % (path, line, match.group(1), why))
        return json.dumps(node, ensure_ascii=False)[1:-1] if escape == "json" else node

    return PLACEHOLDER.sub(value, text)


def render_settings(template, manifest, path="settings.json"):
    """O molde do `.claude/settings.json` renderizado, com o valor escapado para JSON."""
    return render(template, manifest, path, escape="json")


def merge_hooks(settings_text, hooks_text):
    """O `settings.json` com a chave `hooks` trocada pela de `settings.hooks.json` — o sync é dono só dela —, e as
    outras chaves, na ordem, como estavam. `settings_text` vazio ou None parte de `{}`. Devolve o texto, 2 espaços e
    uma quebra no fim."""
    settings = json.loads(settings_text) if settings_text and settings_text.strip() else {}
    doc = json.loads(hooks_text)
    if not isinstance(settings, dict):
        raise ValueError("o settings.json não é um objeto")
    if not (isinstance(doc, dict) and isinstance(doc.get("hooks"), dict)):
        raise ValueError("o settings.hooks.json não traz a chave `hooks`")
    settings["hooks"] = doc["hooks"]
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
