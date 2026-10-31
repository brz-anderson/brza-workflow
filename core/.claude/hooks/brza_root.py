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

Só a stdlib. O `render` dos placeholders do manifesto entra neste arquivo no passo seguinte do plano.
"""

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


# O `render` dos placeholders do manifesto ({{tabela.chave}}, D4 do PLN-006) entra aqui, no P3, no mesmo arquivo.
