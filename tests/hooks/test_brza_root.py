"""Testes do brza_root.py: o parser TOML do subconjunto, conferido contra o `tomllib`, e a busca da raiz do workspace
(override, common dir com o manifesto, registro da máquina), em repositórios git temporários.

Nada lê o ~/.config real: o registro de cada teste é o do BRZA_REGISTRY. O módulo sobe no Python 3.9, onde não há
`tomllib` e o teste de conferência pula.

    python3 -m unittest discover -s tests/hooks
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.normpath(os.path.join(HERE, "..", "..", "core", ".claude", "hooks"))
IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

MANIFEST = r'''# .brza/workspace.toml — o manifesto
schema = 1

[workspace]
name = "acme-notes"
title = "Acme"
owner = "Ana"
branch = "trunk"
remote = "upstream"
born = "2026-10-26"

[author]
name = "Ana Souza"
email = "ana@example.com"

[code]
name = "acme-app"
path = "~/Projects/acme-app"
github = "acme/acme-app"
branch = "main"
remote = "origin"
plan_branch = "[a-z]+/pln-(\\d+)(-|$)"

[core]
ref = "master"
packages = ["client"]
'''


def load_root():
    spec = importlib.util.spec_from_file_location("brza_root", os.path.join(HOOKS, "brza_root.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["brza_root"] = module
    spec.loader.exec_module(module)
    return module


def git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env={**os.environ, **IDENTITY})
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def make_repo(path, branch, files=None):
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", branch)
    for name, text in (files or {"README.md": "x\n"}).items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "base")
    return path


class RootCase(unittest.TestCase):
    """Cada teste tem o próprio BRZA_REGISTRY, e nenhum tem BRZA_WORKSPACE."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(os.path.realpath(self.tmp.name))
        self.saved = {k: os.environ.pop(k, None) for k in ("BRZA_WORKSPACE", "BRZA_REGISTRY", "XDG_CONFIG_HOME")}
        self.addCleanup(self.restore)
        self.registry = self.base / "config" / "workspaces.toml"
        os.environ["BRZA_REGISTRY"] = str(self.registry)
        self.root = load_root()

    def restore(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        sys.modules.pop("brza_root", None)

    def register(self, root, code):
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        with open(self.registry, "a", encoding="utf-8") as f:
            f.write(f'[[workspace]]\nroot = "{root}"\ncode = "{code}"\n')


GOOD = [
    MANIFEST,
    '# registro\n[[workspace]]\nroot = "/a/ws"\ncode = "/a/code"\n\n[[workspace]]\nroot = "/b/ws"\ncode = "/b/code"\n',
    "",
    "# só um comentário\n\n",
    'a = "x"   # comentário no fim\nb = \'literal \\n sem escape\'\nc = "tab\\t aspas \\" barra \\\\ \\u00e9 \\U0001F600"\n',
    "n = 42\nneg = -7\nbig = 1_000\nzero = 0\nyes = true\nno = false\n",
    'l = []\nm = ["a", "b"]\nn = ["a", "b",]\no = [\n  "um",   # o primeiro\n  "dois",\n  # sozinho\n]\np = [["a"], ["b", "c"]]\nq = [1, 2, 3]\n',
    '[a.b.c]\nx = 1\n[a.b]\ny = 2\n[a]\nz = 3\n',
    '[a]\nx = 1\n[[a.items]]\nname = "um"\n[a.items.sub]\nk = "v"\n[[a.items]]\nname = "dois"\n',
    '"quoted key" = 1\n\'lit key\' = 2\n[ "t" . "u" ]\nv = 3\n[ x . y ]\nw = 4\n',
    '[t]\r\nk = "v"\r\n# c\r\nl = [\r\n"a",\r\n"b"\r\n]\r\n',
    'pt = "ç ã é — 日本"\n',
    '[a]\n[b]\nk = 1\n',
    'k = "valor"\n[[w]]\n[[w]]\nx = 1\n',
]

BAD = [
    'a = 1\na = 2\n',                 # chave repetida
    '[a]\n[a]\n',                      # tabela repetida
    'a = "aberta\n',                   # string sem fim
    'a = "x" "y"\n',                   # lixo depois do valor
    'a =\n',                           # sem valor
    '= 1\n',                           # sem chave
    'a = [1, 2\n',                     # lista sem fim
    'a = [1 2]\n',                     # sem vírgula
    '[a\nb = 1\n',                     # cabeçalho sem fim
    '[[a]\n',                          # cabeçalho de lista sem fim
    'a = "\\q"\n',                     # escape desconhecido
    'a = "\\ud800"\n',                 # escape fora do escalar Unicode
    'a = 01\n',                        # zero à esquerda
    'a = tru\n',                       # booleano quebrado
    'a = 1\n[a]\n',                    # tabela sobre valor
    '[[a]]\n[a]\n',                    # tabela sobre lista de tabelas
    'a = "x\ty"\n' + 'b = "\x01"\n',   # controle dentro da string
]

OUTSIDE_THE_SUBSET = [
    'a = 1.5\n',                       # float
    'a = 1979-05-27\n',                # data
    'a = { b = 1 }\n',                 # tabela em linha
    'a = """x\ny"""\n',                # string de várias linhas
    "a = '''x\ny'''\n",                # literal de várias linhas
    'a.b = 1\n',                       # chave pontilhada
    'a = 0x10\n',                      # hexadecimal
    'a = inf\n',                       # especial
]


@unittest.skipIf(tomllib is None, "sem tomllib (Python < 3.11): a conferência roda nas versões novas")
class ParserAgainstTomllibTest(RootCase):
    def test_good_documents_parse_to_what_tomllib_parses(self):
        for text in GOOD:
            with self.subTest(text=text[:60]):
                self.assertEqual(self.root.parse_toml(text), tomllib.loads(text))

    def test_what_tomllib_rejects_the_parser_rejects(self):
        for text in BAD:
            with self.subTest(text=text[:60]):
                with self.assertRaises(tomllib.TOMLDecodeError):
                    tomllib.loads(text)
                with self.assertRaises(self.root.TomlError):
                    self.root.parse_toml(text)

    def test_the_real_manifest_example_has_the_expected_values(self):
        data = self.root.parse_toml(MANIFEST)
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["workspace"]["branch"], "trunk")
        self.assertEqual(data["core"]["packages"], ["client"])
        self.assertEqual(data["code"]["plan_branch"], r"[a-z]+/pln-(\d+)(-|$)")


class ParserTest(RootCase):
    """Sem o tomllib: roda também no Python 3.9."""

    def test_values_the_manifest_and_the_registry_use(self):
        data = self.root.parse_toml(MANIFEST)
        self.assertEqual(data["workspace"], {"name": "acme-notes", "title": "Acme", "owner": "Ana", "branch": "trunk",
                                             "remote": "upstream", "born": "2026-10-26"})
        self.assertEqual(data["core"]["packages"], ["client"])
        reg = self.root.parse_toml(GOOD[1])
        self.assertEqual(reg, {"workspace": [{"root": "/a/ws", "code": "/a/code"}, {"root": "/b/ws", "code": "/b/code"}]})

    def test_arrays_of_tables_keep_the_subtable_of_the_last_item(self):
        data = self.root.parse_toml(GOOD[8])
        self.assertEqual(data, {"a": {"x": 1, "items": [{"name": "um", "sub": {"k": "v"}}, {"name": "dois"}]}})

    def test_escapes_and_literals(self):
        data = self.root.parse_toml(GOOD[4])
        self.assertEqual(data["a"], "x")
        self.assertEqual(data["b"], "literal \\n sem escape")
        self.assertEqual(data["c"], 'tab\t aspas " barra \\ \u00e9 \U0001F600')

    def test_malformed_documents_raise_with_the_line(self):
        for text in BAD:
            with self.subTest(text=text[:60]):
                with self.assertRaises(self.root.TomlError):
                    self.root.parse_toml(text)
        with self.assertRaises(self.root.TomlError) as caught:
            self.root.parse_toml('a = 1\nb = \n')
        self.assertIn("linha 2", str(caught.exception))

    def test_what_is_outside_the_subset_is_refused_and_not_guessed(self):
        for text in OUTSIDE_THE_SUBSET:
            with self.subTest(text=text[:60]):
                with self.assertRaises(self.root.TomlError):
                    self.root.parse_toml(text)

    def test_quote_writes_what_the_parser_reads_back(self):
        for value in ("simples", 'com "aspas"', "barra \\ e\ttab", "ação — 日本", "/Users/x/Projects/a b"):
            with self.subTest(value=value):
                self.assertEqual(self.root.parse_toml(f"k = {self.root.quote(value)}\n"), {"k": value})


class FindTest(RootCase):
    def setUp(self):
        super().setUp()
        self.notes = make_repo(self.base / "acme-notes", "trunk", {".brza/workspace.toml": MANIFEST})
        git(self.notes, "branch", "workstream/a")
        self.tree = self.notes / ".worktrees" / "a"
        git(self.notes, "worktree", "add", "-q", str(self.tree), "workstream/a")
        self.code = make_repo(self.base / "acme-app", "main")
        self.far = self.base / "elsewhere" / "feat"
        git(self.code, "worktree", "add", "-q", "-b", "feat/pln-001-x", str(self.far))
        self.register(self.notes, self.code)

    def test_the_workspace_checkout_and_its_worktrees_are_notes(self):
        for cwd in (self.notes, self.tree, self.notes / ".brza"):
            with self.subTest(cwd=cwd):
                found = self.root.find(str(cwd))
                self.assertEqual((found.root, found.role), (self.notes, "notes"))
        self.assertEqual(self.root.find(str(self.tree), branch=True).branch, "workstream/a")
        self.assertEqual(self.root.find(str(self.tree)).top, self.tree)
        self.assertEqual(self.root.find(str(self.notes)).manifest["workspace"]["name"], "acme-notes")

    def test_the_code_checkout_and_a_worktree_outside_its_directory_are_code_by_the_registry(self):
        for cwd in (self.code, self.far):
            with self.subTest(cwd=cwd):
                found = self.root.find(str(cwd))
                self.assertEqual((found.root, found.role), (self.notes, "code"))
        self.assertEqual(self.root.find(str(self.far), branch=True).branch, "feat/pln-001-x")
        self.assertEqual(self.root.find(str(self.far)).top, self.far)

    def test_a_worktree_outside_the_code_directory_with_no_registry_entry_has_no_workspace(self):
        self.registry.unlink()
        for cwd in (self.code, self.far):
            self.assertIsNone(self.root.find(str(cwd)))
        self.assertIsNotNone(self.root.find(str(self.tree)))  # o workspace não depende do registro

    def test_an_entry_for_another_code_does_not_link_this_one(self):
        self.registry.unlink()
        other = make_repo(self.base / "other-app", "main")
        self.register(self.notes, other)
        self.assertIsNone(self.root.find(str(self.far)))
        self.assertEqual(self.root.find(str(other)).role, "code")

    def test_the_entry_matches_ignoring_case_and_symlinks(self):
        self.registry.unlink()
        self.register(self.notes, str(self.code).upper())
        self.assertEqual(self.root.find(str(self.far)).role, "code")
        self.registry.unlink()
        link = self.base / "link"
        link.symlink_to(self.code)
        self.register(self.notes, link)
        self.assertEqual(self.root.find(str(self.far)).role, "code")

    def test_an_entry_whose_root_has_no_manifest_is_ignored(self):
        self.registry.unlink()
        bare = make_repo(self.base / "no-manifest", "master")
        self.register(bare, self.code)
        self.assertIsNone(self.root.find(str(self.far)))

    def test_a_repo_that_is_not_in_the_registry_nor_a_workspace_has_none(self):
        other = make_repo(self.base / "fixfy", "master")
        plain = self.base / "plain"
        plain.mkdir()
        for cwd in (other, plain, self.base / "missing", "", None):
            with self.subTest(cwd=cwd):
                self.assertIsNone(self.root.find(cwd if cwd is None else str(cwd)))

    def test_the_override_picks_the_root_but_the_role_still_comes_from_the_cwd(self):
        environ = {"BRZA_WORKSPACE": str(self.notes)}
        found = self.root.find(str(self.far), environ=environ)
        self.assertEqual((found.root, found.role), (self.notes, "code"))
        unrelated = make_repo(self.base / "fixfy", "master")
        found = self.root.find(str(unrelated), environ=environ)
        self.assertEqual((found.root, found.role), (self.notes, None))
        found = self.root.find(str(self.base), environ=environ)
        self.assertEqual((found.root, found.role), (self.notes, None))

    def test_foreign_cwd_refuses_the_cwd_in_another_git_repo_and_names_both_paths(self):  # A1
        environ = {"BRZA_WORKSPACE": str(self.notes)}
        other = make_repo(self.base / "other-notes", "main", {".brza/workspace.toml": 'schema = 1\n[workspace]\nname = "o"\n'})
        message = self.root.foreign_cwd(self.root.find(str(other / ".brza"), environ=environ))
        self.assertIn(str(other), message)
        self.assertIn(str(self.notes), message)
        self.assertIn("BRZA_WORKSPACE", message)
        unrelated = make_repo(self.base / "fixfy", "master")
        self.assertIn(str(unrelated), self.root.foreign_cwd(self.root.find(str(unrelated), environ=environ)))

    def test_foreign_cwd_lets_the_workspace_its_worktrees_the_code_and_the_outside_through(self):  # A1
        environ = {"BRZA_WORKSPACE": str(self.notes)}
        for cwd in (self.notes, self.tree, self.code, self.far, self.base):  # o `base` não é repositório git
            with self.subTest(cwd=cwd):
                self.assertIsNone(self.root.foreign_cwd(self.root.find(str(cwd), environ=environ)))
        self.assertIsNone(self.root.foreign_cwd(None))
        self.assertIsNone(self.root.foreign_cwd(self.root.find(str(self.tree))))  # sem a variável

    def test_the_override_wins_over_the_cwd(self):
        other = make_repo(self.base / "other-notes", "main", {".brza/workspace.toml": 'schema = 1\n[workspace]\nname = "o"\n'})
        found = self.root.find(str(self.tree), environ={"BRZA_WORKSPACE": str(other)})
        self.assertEqual((found.root, found.role), (other, None))

    def test_the_override_without_a_manifest_is_ignored(self):
        found = self.root.find(str(self.tree), environ={"BRZA_WORKSPACE": str(self.base)})
        self.assertEqual((found.root, found.role), (self.notes, "notes"))

    def test_a_broken_manifest_or_registry_finds_nothing_and_does_not_raise(self):
        self.registry.write_text("[[workspace\n", encoding="utf-8")
        self.assertIsNone(self.root.find(str(self.far)))
        (self.notes / ".brza" / "workspace.toml").write_text("isto = = não\n", encoding="utf-8")
        self.assertIsNone(self.root.find(str(self.tree)))

    def test_the_registry_path_comes_from_brza_registry_then_xdg_then_home(self):
        self.assertEqual(self.root.registry_path(), self.registry)
        del os.environ["BRZA_REGISTRY"]
        os.environ["XDG_CONFIG_HOME"] = "/x/cfg"
        self.assertEqual(self.root.registry_path(), Path("/x/cfg/brza/workspaces.toml"))
        del os.environ["XDG_CONFIG_HOME"]
        self.assertEqual(self.root.registry_path(), Path.home() / ".config" / "brza" / "workspaces.toml")

    def test_git_failure_is_no_workspace(self):
        self.root.git = lambda *a, **k: None
        self.assertIsNone(self.root.find(str(self.tree)))


class ManifestValuesTest(RootCase):
    def test_branch_and_remote_default_to_master_and_origin(self):
        self.assertEqual(self.root.workspace_branch({}), "master")
        self.assertEqual(self.root.workspace_remote({}), "origin")
        self.assertEqual(self.root.workspace_branch(self.root.parse_toml(MANIFEST)), "trunk")
        self.assertEqual(self.root.workspace_remote(self.root.parse_toml(MANIFEST)), "upstream")

    def test_the_names_come_from_the_manifest(self):
        manifest = self.root.parse_toml(MANIFEST)
        self.assertEqual(self.root.workspace_name(manifest), "acme-notes")
        self.assertEqual(self.root.code_name(manifest), "acme-app")
        self.assertIsNone(self.root.code_name({}))

    def test_the_plan_branch_pattern_comes_from_the_manifest_with_a_default(self):
        default = self.root.plan_branch({})
        self.assertEqual(default.match("feat/pln-007-x").group(1), "007")
        self.assertIsNone(default.match("master"))
        custom = self.root.plan_branch({"code": {"plan_branch": r"plan/(\d+)"}})
        self.assertEqual(custom.match("plan/12").group(1), "12")
        broken = self.root.plan_branch({"code": {"plan_branch": "("}})
        self.assertEqual(broken.match("fix/pln-1-x").group(1), "1")

    def test_the_code_path_is_the_registry_entry_and_then_the_manifest_default(self):
        manifest = self.root.parse_toml(MANIFEST)
        self.assertEqual(self.root.code_path(self.base / "ws", manifest), Path.home() / "Projects" / "acme-app")
        self.register(self.base / "ws", self.base / "app")
        self.assertEqual(self.root.code_path(self.base / "ws", manifest), self.base / "app")
        self.assertIsNone(self.root.code_path(self.base / "outro", {}))


if __name__ == "__main__":
    unittest.main()
