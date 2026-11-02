"""Um repositório git de mentira, com um worktree, para o teste da raiz do `fact_outcome.py` e do
`summarize_measurements.py`: a raiz vem do `git rev-parse --git-common-dir`, e só um repositório de verdade a prova."""

import os
import subprocess

GIT = ["git", "-c", "user.name=teste", "-c", "user.email=teste@example.org", "-c", "commit.gpgsign=false"]


def run(args, cwd):
    subprocess.run(GIT + args, cwd=cwd, check=True, capture_output=True, text=True)


def make(directory, files):
    """(principal, worktree), ambos por realpath: o checkout da `master` com `files` ({caminho: conteúdo}) no commit, e
    o worktree da branch `workstream/x` ao lado, em `<directory>/wt`."""
    main = os.path.join(os.path.realpath(directory), "main")
    os.makedirs(main)
    run(["init", "-q", "-b", "master"], main)
    for name, content in files.items():
        target = os.path.join(main, name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
    run(["add", "-A"], main)
    run(["commit", "-q", "-m", "base"], main)
    tree = os.path.join(os.path.realpath(directory), "wt")
    run(["worktree", "add", "-q", "-b", "workstream/x", tree], main)
    return main, tree


def clean_env(**extra):
    """O ambiente de um terminal do worktree: sem a BRZA_WORKSPACE nem a MEASURE_DIR."""
    env = {k: v for k, v in os.environ.items() if k not in ("BRZA_WORKSPACE", "MEASURE_DIR")}
    return {**env, **extra}
