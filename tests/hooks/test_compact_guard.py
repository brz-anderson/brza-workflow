"""Testes do compact_guard.py: a trava de fechamento antes do /compact.

Cada teste roda num repositório git temporário, que faz as vezes do worktree do workstream, com o
measure.resolve_workstream trocado pelo teste e a medição no MEASURE_DIR.

    python3 -m unittest discover -s .claude/hooks/tests
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.normpath(os.path.join(HERE, "..", "..", "core", ".claude", "hooks"))
TODAY = date(2026, 10, 30)
NOW = 1_800_000_000.0


def load_guard(directory):
    os.environ["MEASURE_DIR"] = directory
    hooks = HOOKS
    for name in ("measure", "compact_guard"):  # o measure.py é lido de novo para pegar o MEASURE_DIR do teste
        spec = importlib.util.spec_from_file_location(name, os.path.join(hooks, f"{name}.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def run(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


class CompactGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.guard = load_guard(os.path.join(self.tmp.name, "measure"))
        self.tree = os.path.realpath(os.path.join(self.tmp.name, "tree"))
        os.makedirs(os.path.join(self.tree, "work", "worklogs"))
        run("init", "-q", "-b", "workstream/w", cwd=self.tree)
        self.write("work/worklogs/2026-10-30-o-assunto.md")
        run("add", "-A", cwd=self.tree)
        run("commit", "-q", "-m", "base", cwd=self.tree)
        self.where = ("w", self.tree)
        self.resolve = mock.patch.object(self.guard.measure, "resolve_workstream", lambda cwd: self.where)
        self.resolve.start()

    def tearDown(self):
        self.resolve.stop()
        self.tmp.cleanup()
        os.environ.pop("MEASURE_DIR", None)
        sys.modules.pop("measure", None)
        sys.modules.pop("compact_guard", None)

    def write(self, path, text="x\n"):
        full = os.path.join(self.tree, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)

    def logged(self):
        try:
            with open(self.guard.measure.LOG, encoding="utf-8") as f:
                return [r for r in map(json.loads, f) if r["event"] == "guard"]
        except OSError:
            return []

    def pre(self, cwd=None, trigger="manual", instructions="", session="s1"):
        return self.guard.pre({"session_id": session, "cwd": cwd or self.tree, "trigger": trigger,
                               "custom_instructions": instructions}, today=TODAY, clock=lambda: NOW)

    def post(self, cwd=None, session="s1", at=NOW):
        return self.guard.post({"session_id": session, "cwd": cwd or self.tree, "source": "compact"},
                               clock=lambda: at)

    def test_closed_task_passes_and_is_measured(self):
        self.assertIsNone(self.pre())
        (row,) = self.logged()
        self.assertEqual((row["kind"], row["decision"], row["workstream"], row["worklog"]),
                         ("compact", "passed", "w", "2026-10-30-o-assunto.md"))

    def test_each_note_path_blocks_when_dirty(self):
        for path in ("work/issues/a.md", "plans/PLN-001-x/PLN-001-plan.md", "wiki/guides/g.md", "memory/m.md",
                     ".claude/agent-memory/collector/m.md"):
            with self.subTest(path=path):
                self.write(path)
                out = self.pre()
                self.assertEqual(out["decision"], "block")
                self.assertIn(path, out["reason"])
                os.remove(os.path.join(self.tree, path))

    def test_dirty_outside_the_notes_passes(self):
        self.write("scripts/x.py")
        self.assertIsNone(self.pre())

    def test_tracked_change_blocks_and_points_to_close_task(self):
        self.write("work/worklogs/2026-10-30-o-assunto.md", "mudou\n")
        out = self.pre()
        self.assertIn("1 arquivo(s) sem commit nas notas do workstream w", out["reason"])
        self.assertIn("rode /close-task", out["reason"])
        self.assertIn("/compact sem-fechar", out["reason"])
        (row,) = self.logged()
        self.assertEqual((row["decision"], row["dirty"], row["studio"]), ("blocked", 1, False))

    def test_missing_worklog_of_today_blocks(self):
        run("rm", "-q", "work/worklogs/2026-10-30-o-assunto.md", cwd=self.tree)
        run("commit", "-q", "-m", "sem worklog", cwd=self.tree)
        self.write("work/worklogs/2026-10-29-ontem.md")
        run("add", "-A", cwd=self.tree)
        run("commit", "-q", "-m", "ontem", cwd=self.tree)
        out = self.pre()
        self.assertIn("falta o worklog de hoje, work/worklogs/2026-10-30-<assunto>.md", out["reason"])
        self.assertNotIn("sem commit", out["reason"])

    def test_many_dirty_files_are_cut_in_the_message(self):
        for i in range(7):
            self.write(f"memory/m{i}.md")
        self.assertIn("e mais 2", self.pre()["reason"])

    def test_sem_fechar_releases_and_silences_the_post(self):
        self.write("memory/m.md")
        self.assertIsNone(self.pre(instructions="foque no plano sem-fechar"))
        self.assertIsNone(self.post())
        self.assertEqual([r["decision"] for r in self.logged()], ["released"])
        self.assertIsNotNone(self.post(at=NOW + 31 * 60))  # passada a janela, volta a avisar

    def test_word_must_stand_alone(self):
        self.write("memory/m.md")
        self.assertIsNotNone(self.pre(instructions="nao-sem-fechar-isso"))

    def test_auto_compaction_is_never_blocked(self):
        self.write("memory/m.md")
        self.assertIsNone(self.pre(trigger="auto"))
        self.assertEqual(self.logged(), [])

    def test_master_and_no_workstream_pass(self):
        self.write("memory/m.md")
        for where in (("master", None), (None, None), ("w", None)):
            with self.subTest(where=where):
                self.where = where
                self.assertIsNone(self.pre())
                self.assertIsNone(self.post())

    def test_git_failure_never_blocks(self):
        self.where = ("w", os.path.join(self.tmp.name, "nao-e-repo"))
        os.makedirs(self.where[1])
        self.assertIsNone(self.pre())

    def test_studio_session_gets_the_steps_instead_of_the_skill(self):
        studio = os.path.join(self.tmp.name, "studio")
        os.makedirs(studio)
        self.write("memory/m.md")
        out = self.pre(cwd=studio)
        self.assertIn(f"no worktree do workstream w ({self.tree})", out["reason"])
        self.assertIn("brza note commit", out["reason"])
        self.assertNotIn("/close-task", out["reason"])
        self.assertTrue(self.logged()[-1]["studio"])

    def test_post_injects_the_order_when_notes_are_dirty(self):
        self.write("work/issues/a.md")
        out = self.post()["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        self.assertIn("work/issues/a.md", out["additionalContext"])
        self.assertIn("atualize o worklog de hoje a partir do resumo", out["additionalContext"])
        self.assertEqual(self.logged()[-1]["decision"], "injected")

    def test_post_is_silent_when_notes_are_clean(self):
        self.assertIsNone(self.post())
        self.assertEqual(self.logged(), [])

    def test_main_passes_outside_the_workspace(self):
        self.write("memory/m.md")
        script = os.path.join(HOOKS, "compact_guard.py")
        # Sem o mock, o processo novo resolve o workstream pelo git: o repositório temporário não é do workspace, e passa.
        done = subprocess.run([sys.executable, script, "pre"], input=json.dumps({"cwd": self.tree, "trigger": "manual"}),
                              capture_output=True, text=True,
                              env={**os.environ, "MEASURE_DIR": self.tmp.name,
                                   "BRZA_REGISTRY": os.path.join(self.tmp.name, "sem-registro.toml")})
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_main_survives_bad_input(self):
        script = os.path.join(HOOKS, "compact_guard.py")
        done = subprocess.run([sys.executable, script, "pre"], input="não é json", capture_output=True, text=True,
                              env={**os.environ, "MEASURE_DIR": self.tmp.name})
        self.assertEqual((done.returncode, done.stdout), (0, ""))


class OtherWorkspaceTest(unittest.TestCase):
    """O hook inteiro, em processo, com o resolvedor de verdade: um workspace de outro nome (`acme-notes`, na branch
    `trunk`) e o código dele (`acme-app`) ligados pelo registro, com o worktree do código fora do diretório dele."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = os.path.realpath(self.tmp.name)
        self.notes, self.code = os.path.join(base, "acme-notes"), os.path.join(base, "acme-app")
        for repo, branch in ((self.notes, "trunk"), (self.code, "main")):
            os.makedirs(repo)
            run("init", "-q", "-b", branch, cwd=repo)
        os.makedirs(os.path.join(self.notes, ".brza"))
        with open(os.path.join(self.notes, ".brza", "workspace.toml"), "w", encoding="utf-8") as f:
            f.write('schema = 1\n[workspace]\nname = "acme-notes"\nbranch = "trunk"\n[code]\nname = "acme-app"\n')
        for repo in (self.notes, self.code):
            with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as f:
                f.write("x\n")
            run("add", "-A", cwd=repo)
            run("commit", "-q", "-m", "base", cwd=repo)
        run("branch", "workstream/w", cwd=self.notes)
        self.tree = os.path.join(self.notes, ".worktrees", "w")
        run("worktree", "add", "-q", self.tree, "workstream/w", cwd=self.notes)
        os.makedirs(os.path.join(self.tree, "plans", "PLN-001-x"))
        with open(os.path.join(self.tree, "plans", "PLN-001-x", "PLN-001-plan.md"), "w", encoding="utf-8") as f:
            f.write("# plano\n")
        run("add", "-A", cwd=self.tree)
        run("commit", "-q", "-m", "plano", cwd=self.tree)
        self.far = os.path.join(base, "elsewhere", "feat")
        run("worktree", "add", "-q", "-b", "feat/pln-001-x", self.far, cwd=self.code)
        self.registry = os.path.join(base, "registry.toml")
        with open(self.registry, "w", encoding="utf-8") as f:
            f.write(f'[[workspace]]\nroot = "{self.notes}"\ncode = "{self.code}"\n')
        os.makedirs(os.path.join(self.tree, "memory"))
        with open(os.path.join(self.tree, "memory", "m.md"), "w", encoding="utf-8") as f:
            f.write("x\n")

    def pre(self, cwd, registry=True):
        env = {k: v for k, v in os.environ.items() if k != "BRZA_WORKSPACE"}
        env.update({"MEASURE_DIR": os.path.join(self.tmp.name, "measure"),
                    "BRZA_REGISTRY": self.registry if registry else os.path.join(self.tmp.name, "nada.toml")})
        done = subprocess.run([sys.executable, os.path.join(HOOKS, "compact_guard.py"), "pre"],
                              input=json.dumps({"cwd": cwd, "trigger": "manual"}), capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout) if done.stdout else None

    def test_the_workspace_worktree_is_blocked_with_the_skill(self):
        out = self.pre(self.tree)
        self.assertEqual(out["decision"], "block")
        self.assertIn("workstream w", out["reason"])
        self.assertIn("/close-task", out["reason"])

    def test_the_code_worktree_outside_its_directory_is_blocked_with_the_steps_by_the_registry(self):
        out = self.pre(self.far)
        self.assertEqual(out["decision"], "block")
        self.assertIn(f"no worktree do workstream w ({self.tree})", out["reason"])
        self.assertNotIn("/close-task", out["reason"])
        self.assertNotIn("brza-", out["reason"].replace(self.tmp.name, ""))

    def test_without_the_registry_entry_the_code_worktree_is_not_checked(self):
        self.assertIsNone(self.pre(self.far, registry=False))

    def test_the_norm_branch_checkout_and_the_literal_master_are_not_checked(self):
        self.assertIsNone(self.pre(self.notes))  # trunk
        run("checkout", "-q", "-b", "master", cwd=self.notes)
        self.assertIsNone(self.pre(self.notes))  # com o manifesto em trunk, master é uma branch qualquer


if __name__ == "__main__":
    unittest.main()
