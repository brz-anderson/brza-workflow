"""Testes do que o brza_root.py ganhou no P3 do PLN-006: o esquema do manifesto (`validate_manifest`, `with_defaults`,
`dump_manifest`), o lock (`dump_lock`, `read_lock`, `digest`), o `render` dos `{{tabela.chave}}` e o `merge_hooks`.

Sobe no Python 3.9, onde não há `tomllib`: a conferência contra ele pula.

    python3 -m unittest discover -s tests/hooks
"""

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "core", ".claude", "hooks"))

import brza_root as r  # noqa: E402

MANIFEST = {
    "schema": 1,
    "workspace": {"name": "acme-notes", "title": "Acme", "owner": "Ana", "born": "2026-10-26"},
    "author": {"name": "Ana Souza", "email": "ana@example.com", "login": "ana"},
    "code": {"name": "acme-app", "path": "~/Projects/acme-app", "github": "acme/acme-app"},
    "core": {"ref": "master", "packages": ["client"]},
}


def manifest(**changes):
    out = copy.deepcopy(MANIFEST)
    for dotted, value in changes.items():
        table, _, key = dotted.partition("__")
        if value is None:
            out[table].pop(key, None)
        else:
            out[table][key] = value
    return out


class RenderTest(unittest.TestCase):
    def test_each_placeholder_becomes_the_manifest_value(self):
        text = "{{workspace.name}} de {{workspace.owner}}: {{code.name}} em {{code.path}} ({{code.github}}), {{author.login}}"
        self.assertEqual(r.render(text, MANIFEST),
                         "acme-notes de Ana: acme-app em ~/Projects/acme-app (acme/acme-app), ana")

    def test_the_defaults_of_r3_3_apply_when_the_key_is_absent(self):
        out = r.render("`{{workspace.branch}}` {{workspace.remote}}/{{workspace.branch}} {{code.branch}} {{code.remote}}",
                       MANIFEST)
        self.assertEqual(out, "`master` origin/master master origin")
        custom = manifest(workspace__branch="main", workspace__remote="up", code__branch="trunk")
        self.assertEqual(r.render("{{workspace.remote}}/{{workspace.branch}} {{code.branch}}", custom), "up/main trunk")

    def test_spaces_inside_the_braces_are_allowed(self):
        self.assertEqual(r.render("{{ workspace.name }}", MANIFEST), "acme-notes")

    def test_a_missing_key_is_an_error_with_the_path_and_the_line(self):
        with self.assertRaises(r.RenderError) as ctx:
            r.render("linha 1\nlinha 2 {{code.nope}}\n", MANIFEST, "AGENTS.md")
        self.assertEqual(str(ctx.exception), "AGENTS.md:2: o placeholder {{code.nope}} sem valor no manifesto")

    def test_a_missing_table_and_a_required_key_without_value_are_errors(self):
        for text, manifest_ in (("{{outra.chave}}", MANIFEST), ("{{author.login}}", manifest(author__login=None)),
                                ("{{code.name}}", {"schema": 1})):
            with self.subTest(text=text):
                with self.assertRaises(r.RenderError):
                    r.render(text, manifest_)

    def test_a_value_that_is_not_text_is_an_error(self):
        with self.assertRaises(r.RenderError) as ctx:
            r.render("{{core.packages}}", MANIFEST, "x.md")
        self.assertIn("não é um texto", str(ctx.exception))
        with self.assertRaises(r.RenderError):  # desce além de um texto
            r.render("{{workspace.name.x}}", MANIFEST)

    def test_what_is_not_a_placeholder_stays(self):
        text = "${BRZA_PYTHON:-python3} {x} {{ }} {{Workspace.name}} {{workspace}} `{{` {{1.a}} {{a-b.c}}"
        self.assertEqual(r.render(text, MANIFEST), text)

    def test_the_value_is_not_rendered_again(self):
        out = r.render("{{workspace.title}}", manifest(workspace__title="{{code.name}}"))
        self.assertEqual(out, "{{code.name}}")

    def test_a_render_without_placeholder_is_the_identity(self):
        text = "# título\n\nnada a trocar, nem `{x}` nem {{\n"
        self.assertEqual(r.render(text, {}), text)

    def test_escape_json_escapes_for_a_string(self):
        out = r.render_settings('{"a": "{{author.name}}"}', manifest(author__name='Ana "A" \\ Souza\nx'))
        self.assertEqual(json.loads(out), {"a": 'Ana "A" \\ Souza\nx'})
        self.assertEqual(r.render("{{author.name}}", manifest(author__name='a"b')), 'a"b')

    def test_accents_survive_the_json_escape(self):
        out = r.render_settings('"{{author.name}}"', manifest(author__name="Ação Ç"))
        self.assertEqual(out, '"Ação Ç"')


class ManifestSchemaTest(unittest.TestCase):
    def test_the_valid_manifest_has_no_problem(self):
        self.assertEqual(r.validate_manifest(MANIFEST), [])

    def test_each_problem_is_named(self):
        cases = {
            "schema": ({k: v for k, v in MANIFEST.items() if k != "schema"}, "schema = None"),
            "schema 2": (dict(MANIFEST, schema=2), "schema = 2"),
            "tabela": ({k: v for k, v in MANIFEST.items() if k != "code"}, "falta a tabela [code]"),
            "chave": (manifest(workspace__owner=None), "falta workspace.owner"),
            "login": (manifest(author__login=None), "falta author.login"),
            "tipo": (manifest(code__name=3), "code.name não é um texto"),
            "vazia": (manifest(code__name=""), "code.name não é um texto"),
            "data": (manifest(workspace__born="26/10/2026"), "workspace.born = '26/10/2026' não é uma data"),
            "link": (dict(MANIFEST, checker={"worklogs_link_from": "ontem"}),
                     "checker.worklogs_link_from = 'ontem'"),
            "regex": (manifest(code__plan_branch="(["), "code.plan_branch não é uma expressão regular"),
            "pacotes": (manifest(core__packages="client"), "core.packages não é uma lista de textos"),
            "tabela errada": (dict(MANIFEST, author="x"), "[author] não é uma tabela"),
        }
        for name, (value, expected) in cases.items():
            with self.subTest(name):
                self.assertTrue(any(expected in p for p in r.validate_manifest(value)), r.validate_manifest(value))
        self.assertEqual(r.validate_manifest([]), ["o manifesto não é uma tabela"])

    def test_an_absent_checker_and_core_are_fine(self):
        small = {k: v for k, v in MANIFEST.items() if k not in ("core", "checker")}
        self.assertEqual(r.validate_manifest(small), [])

    def test_with_defaults_fills_the_optional_keys_and_does_not_touch_the_input(self):
        small = {"schema": 1, "workspace": {"name": "a", "born": "2026-11-03"}}
        before = copy.deepcopy(small)
        out = r.with_defaults(small)
        self.assertEqual(small, before)
        self.assertEqual(out["workspace"]["branch"], "master")
        self.assertEqual(out["workspace"]["remote"], "origin")
        self.assertEqual(out["code"]["plan_branch"], r.DEFAULT_PLAN_BRANCH)
        self.assertEqual(out["core"], {"ref": "master", "packages": []})
        self.assertEqual(out["checker"]["worklogs_link_from"], "2026-11-03")  # o dia de `born`
        self.assertNotIn("name", out["code"])  # a obrigatória não se inventa

    def test_with_defaults_keeps_what_the_manifest_says(self):
        out = r.with_defaults(manifest(workspace__branch="main"))
        self.assertEqual(out["workspace"]["branch"], "main")
        self.assertEqual(out["core"]["packages"], ["client"])

    def test_dump_manifest_reads_back_what_was_dumped(self):
        full = manifest(workspace__branch="main", workspace__remote="up", code__branch="trunk",
                        code__plan_branch=r"[a-z]+/pln-(\d+)(-|$)")
        full["checker"] = {"worklogs_link_from": "2026-10-28"}
        text = r.dump_manifest(full)
        self.assertEqual(r.parse_toml(text), full)
        self.assertEqual(r.validate_manifest(r.parse_toml(text)), [])
        self.assertTrue(text.startswith("# .brza/workspace.toml — o manifesto do workspace."))
        self.assertEqual(r.dump_manifest(r.parse_toml(text)), text)

    def test_dump_manifest_writes_only_the_keys_given(self):
        text = r.dump_manifest(MANIFEST)
        self.assertNotIn("\nbranch =", text)
        self.assertNotIn("\nremote =", text)
        self.assertNotIn("[checker]", text)
        self.assertIn('packages = ["client"]', text)

    def test_dump_manifest_quotes_what_needs_it(self):
        value = manifest(author__name='Ana "A"\\ Souza')
        self.assertEqual(r.parse_toml(r.dump_manifest(value))["author"]["name"], 'Ana "A"\\ Souza')

    def test_the_template_is_what_dump_manifest_writes(self):
        path = os.path.join(ROOT, "templates", ".brza", "workspace.toml")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        parsed = r.parse_toml(text)
        self.assertEqual(r.validate_manifest(parsed), [])
        self.assertEqual(r.dump_manifest(parsed), text)
        self.assertEqual(parsed["workspace"]["branch"], "master")  # os padrões da R3.3, explícitos no molde
        self.assertEqual(parsed["workspace"]["remote"], "origin")
        self.assertEqual(parsed["core"]["packages"], ["client"])

    def test_the_template_has_nothing_of_the_brza_workspace(self):
        with open(os.path.join(ROOT, "templates", ".brza", "workspace.toml"), encoding="utf-8") as f:
            text = f.read()
        for word in ("brza-workspace", "brza-studio", "Anderson", "brz-anderson"):
            self.assertNotIn(word, text)

    @unittest.skipIf(tomllib is None, "sem tomllib (Python < 3.11): a conferência roda nas versões novas")
    def test_the_parser_reads_the_dumped_manifest_like_tomllib(self):
        text = r.dump_manifest(manifest(code__plan_branch=r"[a-z]+/pln-(\d+)(-|$)"))
        self.assertEqual(r.parse_toml(text), tomllib.loads(text))
        with open(os.path.join(ROOT, "templates", ".brza", "workspace.toml"), encoding="utf-8") as f:
            template = f.read()
        self.assertEqual(r.parse_toml(template), tomllib.loads(template))


class LockTest(unittest.TestCase):
    FILES = {"AGENTS.md": r.digest("a"), ".claude/settings.json#hooks": r.hooks_digest({"Stop": []}),
             "plans/README.md": r.digest(b"b")}

    def test_digest_is_the_sha256_of_the_utf8_text(self):
        self.assertEqual(r.digest("ação"), "sha256:" + hashlib.sha256("ação".encode("utf-8")).hexdigest())
        self.assertEqual(r.digest(b"x"), r.digest("x"))

    def test_hooks_digest_ignores_the_order_of_the_keys(self):
        self.assertEqual(r.hooks_digest({"a": 1, "b": [1, 2]}), r.hooks_digest({"b": [1, 2], "a": 1}))
        self.assertNotEqual(r.hooks_digest({"a": 1}), r.hooks_digest({"a": 2}))

    def test_dump_lock_reads_back_and_sorts_the_files(self):
        text = r.dump_lock("a" * 40, "master", "2026-11-03", self.FILES)
        lock = r.parse_toml(text)
        self.assertEqual(lock, {"commit": "a" * 40, "ref": "master", "synced": "2026-11-03", "files": self.FILES})
        self.assertEqual(r.validate_lock(lock), [])
        self.assertEqual(list(lock["files"]), sorted(self.FILES))
        self.assertTrue(text.startswith("# .brza/core.lock — gerado pelo brza sync; não edite\n"))

    @unittest.skipIf(tomllib is None, "sem tomllib (Python < 3.11): a conferência roda nas versões novas")
    def test_the_parser_reads_the_lock_like_tomllib(self):
        text = r.dump_lock("b" * 40, "feat/x", "2026-11-03", self.FILES)
        self.assertEqual(r.parse_toml(text), tomllib.loads(text))

    def test_read_lock_returns_none_without_the_file(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(r.read_lock(root))

    def test_read_lock_reads_the_file(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, ".brza"))
            with open(os.path.join(root, r.LOCK), "w", encoding="utf-8") as f:
                f.write(r.dump_lock("c" * 40, "master", "2026-11-03", self.FILES))
            self.assertEqual(r.read_lock(root)["files"], self.FILES)

    def test_read_lock_does_not_treat_a_broken_lock_as_absent(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, ".brza"))
            path = os.path.join(root, r.LOCK)
            for text, expected in (("commit = \n", "ilegível"), ('commit = "x"\n', "fora do esquema"),
                                   (r.dump_lock("c", "m", "2026-11-03", {"a": "md5:00"}), "files['a']")):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                with self.assertRaises(r.LockError) as ctx:
                    r.read_lock(root)
                self.assertIn(expected, str(ctx.exception))

    def test_validate_lock_names_each_problem(self):
        good = r.parse_toml(r.dump_lock("a" * 40, "master", "2026-11-03", self.FILES))
        for change, expected in ((("commit", None), "falta commit"), (("synced", "hoje"), "synced = 'hoje'"),
                                 (("files", None), "falta [files]"), (("ref", ""), "falta ref")):
            lock = dict(good)
            if change[1] is None:
                lock.pop(change[0])
            else:
                lock[change[0]] = change[1]
            with self.subTest(change=change):
                self.assertTrue(any(expected in p for p in r.validate_lock(lock)), r.validate_lock(lock))


class MergeHooksTest(unittest.TestCase):
    HOOKS = json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}})

    def test_the_hooks_key_is_replaced_in_place(self):
        settings = json.dumps({"env": {"A": "1"}, "hooks": {"Old": []}, "z": True})
        out = json.loads(r.merge_hooks(settings, self.HOOKS))
        self.assertEqual(list(out), ["env", "hooks", "z"])
        self.assertEqual(out["hooks"], json.loads(self.HOOKS)["hooks"])
        self.assertEqual(out["env"], {"A": "1"})

    def test_the_hooks_key_is_added_at_the_end_when_absent(self):
        out = json.loads(r.merge_hooks(json.dumps({"env": {}}), self.HOOKS))
        self.assertEqual(list(out), ["env", "hooks"])

    def test_an_empty_settings_starts_from_an_empty_object(self):
        for empty in (None, "", "  \n"):
            self.assertEqual(list(json.loads(r.merge_hooks(empty, self.HOOKS))), ["hooks"])

    def test_the_text_has_two_spaces_and_a_final_newline(self):
        out = r.merge_hooks("{}", self.HOOKS)
        self.assertTrue(out.endswith("}\n"))
        self.assertTrue(out.startswith('{\n  "hooks": {\n    "Stop": ['))

    def test_the_merge_is_idempotent(self):
        once = r.merge_hooks('{"env": {"A": "á"}}', self.HOOKS)
        self.assertEqual(r.merge_hooks(once, self.HOOKS), once)
        self.assertIn("á", once)

    def test_a_hooks_file_without_the_key_is_refused(self):
        for bad in ("{}", '{"hooks": []}', "[]"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    r.merge_hooks("{}", bad)
        with self.assertRaises(ValueError):
            r.merge_hooks("[]", self.HOOKS)


if __name__ == "__main__":
    unittest.main()
