"""O teste de ouro do PLN-006/P3: o render do núcleo, com o manifesto do brza-workspace, reproduz a `master` do
brza-workspace@d85c783 — a cópia está em `fixtures/golden/master/` —, salvo a lista fechada de `golden_differences.py`.

Não depende do brza-workspace real: tudo o que o teste lê está no repositório.
"""

import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
CORE = os.path.join(ROOT, "core")
CLIENT_CORE = os.path.join(ROOT, "packages", "client", "core")
TEMPLATES = os.path.join(ROOT, "templates")
GOLDEN = os.path.join(HERE, "fixtures", "golden")
sys.path.insert(0, os.path.join(CORE, ".claude", "hooks"))
sys.path.insert(0, HERE)

import brza_root  # noqa: E402
from golden_differences import DIFFERENCES, WHY  # noqa: E402

# Os valores do brza-workspace, que o P8 grava no manifesto dele
BRZA_WORKSPACE = {
    "schema": 1,
    "workspace": {"name": "brza-workspace", "title": "BrzA Studio", "owner": "Anderson", "branch": "master",
                  "remote": "origin", "born": "2026-10-26"},
    "author": {"name": "Anderson Pereira Brzezinski", "email": "240569289+brz-anderson@users.noreply.github.com",
               "login": "brz-anderson"},
    "code": {"name": "brza-studio", "path": "~/Projects/brza-studio", "github": "brz-anderson/brza-studio",
             "branch": "master", "remote": "origin"},
    "core": {"ref": "master", "packages": ["client"]},
}
# Outro workspace, sem nenhum valor do brza-workspace
OTHER = {
    "schema": 1,
    "workspace": {"name": "casa-ws", "title": "Projeto Casa", "owner": "Beatriz", "born": "2026-11-03"},
    "author": {"name": "Beatriz Souza", "email": "bia@example.org", "login": "bia-souza"},
    "code": {"name": "casa-app", "path": "~/Code/casa-app", "github": "bia-souza/casa-app", "branch": "trunk"},
    "core": {"ref": "master"},
}
# O que é do brza-workspace e não pode sobrar no que o render faz com outro manifesto (R1.2, R8.2)
FORBIDDEN = ("brza-workspace", "brza-studio", "BrzA Studio", "Anderson", "brz-anderson", "Fixfy", "fixfy", "users.noreply.github.com",
             "`personal`", "Último PLN usado", "tools-kit", "claude-501")
# O texto do app (o chat, o scratch dele) mora no pacote do cliente; no núcleo e nos moldes não sobra (A8 do P9)
APP_TERMS = ("orca", "ORCA", "orca-visual")


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


def markdown_of(base):
    found = []
    for directory, _, files in os.walk(base):
        for name in files:
            if name.endswith(".md"):
                found.append(os.path.relpath(os.path.join(directory, name), base))
    return sorted(found)


def apply_differences(path, text):
    """O texto da master com as diferenças da lista fechada para `path`, cada uma casando uma vez."""
    for diff in DIFFERENCES:
        if diff.path == path:
            assert text.count(diff.old) == 1, "%s [%s]: o trecho casa %d vezes: %r" % (
                path, diff.why, text.count(diff.old), diff.old[:80])
            text = text.replace(diff.old, diff.new)
    return text


class GoldenRenderTest(unittest.TestCase):
    def test_the_manifest_of_the_golden_is_valid(self):
        self.assertEqual(brza_root.validate_manifest(BRZA_WORKSPACE), [])
        self.assertEqual(brza_root.validate_manifest(OTHER), [])

    def test_every_markdown_of_the_core_has_its_copy_of_the_master(self):
        self.assertEqual(markdown_of(CORE), markdown_of(os.path.join(GOLDEN, "master")))

    def test_the_render_reproduces_the_master_but_for_the_closed_list(self):
        for path in markdown_of(CORE):
            with self.subTest(path=path):
                expected = apply_differences(path, read(GOLDEN, "master", path))
                self.assertEqual(brza_root.render(read(CORE, path), BRZA_WORKSPACE, path), expected)

    def test_the_closed_list_names_files_that_exist_and_reasons_it_knows(self):
        files = set(markdown_of(CORE))
        for diff in DIFFERENCES:
            self.assertIn(diff.path, files)
            self.assertIn(diff.why, WHY)
            self.assertNotEqual(diff.old, diff.new)
        self.assertEqual(set(d.why for d in DIFFERENCES), set(WHY), "motivo que nenhuma diferença usa")

    def test_a_difference_the_render_does_not_make_fails(self):
        # a lista é fechada nos dois sentidos: tirar uma entrada reprova o ouro
        path = "plans/README.md"
        without = [d for d in DIFFERENCES if d.path == path][1:]
        text = read(GOLDEN, "master", path)
        for diff in without:
            text = text.replace(diff.old, diff.new)
        self.assertNotEqual(brza_root.render(read(CORE, path), BRZA_WORKSPACE, path), text)

    def test_nothing_of_the_brza_workspace_survives_another_manifest(self):
        for base, paths in ((CORE, markdown_of(CORE)), (CLIENT_CORE, markdown_of(CLIENT_CORE)),
                            (TEMPLATES, markdown_of(TEMPLATES))):
            for path in paths:
                with self.subTest(path=path):
                    out = brza_root.render(read(base, path), OTHER, path)
                    self.assertNotIn("{{", out)
                    for word in FORBIDDEN + (() if base == CLIENT_CORE else APP_TERMS):
                        self.assertNotIn(word, out, "%s deixou %r" % (path, word))

    def test_the_other_manifest_reaches_the_text(self):
        out = brza_root.render(read(CORE, "AGENTS.md"), OTHER, "AGENTS.md")
        self.assertTrue(out.startswith("# AGENTS.md — casa-ws\n"))
        self.assertIn("workspace pessoal de Beatriz para o **Projeto Casa**", out)
        self.assertIn("`~/Code/casa-app`", out)
        self.assertIn("(`bia-souza/casa-app`)", out)
        self.assertIn("do `casa-app`, na `trunk`.", out)  # a branch do código, e não a do workspace

    def test_the_norm_says_the_common_norm_is_edited_in_the_flow_core_and_arrives_by_the_sync(self):  # A3 do P9
        for path in ("AGENTS.md", ".claude/skills/agent-instructions-writer/SKILL.md"):
            with self.subTest(path=path):
                text = " ".join(read(CORE, path).split())
                self.assertIn("`.brza/core.lock`", text)
                self.assertIn("no `core/` do `brza-workflow`", text)
                self.assertIn("`brza sync`", text)
                self.assertIn("`.claude/rules/workspace.md`", text)
                self.assertIn("`README.md` local", text)
                self.assertIn("{{workspace.branch}}", text)
        self.assertNotIn("Norma se grava na `{{workspace.branch}}`", read(CORE, "AGENTS.md"))
        self.assertNotIn("A norma mora na `{{workspace.branch}}`", read(CORE, ".claude/skills/agent-instructions-writer/SKILL.md"))

    def test_the_claude_md_starts_by_importing_the_agents_md(self):
        for text in (read(CORE, "CLAUDE.md"),
                     brza_root.render(read(CORE, "CLAUDE.md"), BRZA_WORKSPACE, "CLAUDE.md")):
            self.assertEqual(text.split("\n")[:3], ["@AGENTS.md", "", "# Adaptador do Claude Code"])
        self.assertNotIn("quando a opção\n`claude-md-and-agents-md` está ativa", read(CORE, "CLAUDE.md"))

    def test_the_client_rules_hold_what_left_the_claude_md(self):
        out = brza_root.render(read(CLIENT_CORE, ".claude", "rules", "brza-client.md"), BRZA_WORKSPACE, "brza-client.md")
        self.assertIn("## A configuração chega pela função do shell", out)
        self.assertIn("`~/.local/bin/claude-brza`", out)
        self.assertIn("não é herdado** pelo `brza-studio`", out)
        self.assertNotIn("Fixfy", out)
        left = [d for d in DIFFERENCES if d.why == "rules-client"]
        self.assertEqual(len(left), 1)
        self.assertNotIn("## A configuração chega pela função do shell", read(CORE, "CLAUDE.md"))

    def test_the_client_rules_hold_the_app_scratch_and_the_chat_artboards(self):
        out = brza_root.render(read(CLIENT_CORE, ".claude", "rules", "brza-client.md"), BRZA_WORKSPACE, "brza-client.md")
        for text in ("ORCA_CHAT_SCRATCH_DIR", "orca-chat-visuals", '::orca-visual{file="<arquivo>.html"',
                     "chat-scratch/<hash>", "brza shot <artboard>.html <scratch>/<letra>.png --width 736"):
            self.assertIn(text, out)
        self.assertNotIn("claude-501", out)  # o uid desta máquina não vai a nenhum pacote
        for text in ("ORCA_CHAT_SCRATCH_DIR", "orca-chat-visuals", "::orca-visual", "chat-scratch"):
            self.assertNotIn(text, read(CORE, "CLAUDE.md") + read(CORE, ".claude", "skills", "plan-runner", "references",
                                                                 "open-and-plan.md"))
        self.assertIn(".claude/rules/brza-client.md", read(CORE, "CLAUDE.md"))
        self.assertIn(".claude/rules/brza-client.md", read(CORE, ".claude", "skills", "plan-runner", "references",
                                                            "open-and-plan.md"))

    def test_the_templates_render_with_the_manifest(self):
        for path in markdown_of(TEMPLATES):
            with self.subTest(path=path):
                out = brza_root.render(read(TEMPLATES, path), BRZA_WORKSPACE, path)
                self.assertNotIn("{{", out)
        readme = brza_root.render(read(TEMPLATES, "README.md"), OTHER, "README.md")
        self.assertTrue(readme.startswith("# casa-ws\n"))
        self.assertIn("gh auth switch --user bia-souza", readme)

    def test_the_python_of_the_core_has_no_placeholder(self):
        # só o .md e o molde do settings são renderizados (D4): o .py é cópia e lê o manifesto em execução
        for directory, _, files in os.walk(CORE):
            for name in files:
                if name.endswith(".py") and name != "brza_root.py":  # o brza_root documenta a sintaxe
                    with self.subTest(name=name):
                        self.assertIsNone(re.search(r"\{\{(workspace|author|code)\.[a-z_]+\}\}", read(directory, name)))


class SettingsGoldenTest(unittest.TestCase):
    def master(self):
        return json.loads(read(GOLDEN, "master-settings.json"))

    def test_the_hooks_file_carries_the_hooks_key_of_the_master_and_only_it(self):
        doc = json.loads(read(CORE, ".claude", "settings.hooks.json"))
        self.assertEqual(list(doc), ["hooks"])
        self.assertEqual(doc["hooks"], self.master()["hooks"])

    def test_the_template_and_the_merge_give_the_master_settings_but_for_the_ssh_command(self):
        template = read(TEMPLATES, ".claude", "settings.json")
        rendered = brza_root.render_settings(template, BRZA_WORKSPACE)
        merged = brza_root.merge_hooks(rendered, read(CORE, ".claude", "settings.hooks.json"))
        expected = self.master()
        del expected["env"]["GIT_SSH_COMMAND"]  # a chave é do usuário: R3.4 só a documenta
        self.assertEqual(json.loads(merged), expected)
        self.assertEqual(list(json.loads(merged)), list(expected), "a ordem das chaves")
        self.assertEqual(merged, json.dumps(expected, indent=2, ensure_ascii=False) + "\n")

    def test_the_merge_replaces_only_the_hooks_key(self):
        template = brza_root.render_settings(read(TEMPLATES, ".claude", "settings.json"), OTHER)
        settings = json.loads(template)
        settings["hooks"] = {"Stop": []}
        settings["permissions"] = {"allow": ["Bash(ls)"]}
        merged = json.loads(brza_root.merge_hooks(json.dumps(settings), read(CORE, ".claude", "settings.hooks.json")))
        self.assertEqual(merged["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(merged["env"], settings["env"])
        self.assertEqual(merged["hooks"], json.loads(read(CORE, ".claude", "settings.hooks.json"))["hooks"])

    def test_the_author_goes_into_the_env_escaped_for_json(self):
        manifest = json.loads(json.dumps(OTHER))
        manifest["author"]["name"] = 'Bia "B" \\ Souza'
        out = json.loads(brza_root.render_settings(read(TEMPLATES, ".claude", "settings.json"), manifest))
        self.assertEqual(out["env"]["GIT_AUTHOR_NAME"], 'Bia "B" \\ Souza')
        self.assertEqual(out["env"]["GIT_COMMITTER_EMAIL"], "bia@example.org")


if __name__ == "__main__":
    unittest.main()
