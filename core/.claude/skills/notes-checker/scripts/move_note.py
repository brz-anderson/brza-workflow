#!/usr/bin/env python3
"""Move ou renomeia uma nota do acervo e reescreve os links Markdown relativos que apontam para ela ou saem dela.

A origem é uma nota ou a pasta de um plano, que se move inteira:
  1. Reescreve nas demais notas do acervo (`work/`, `plans/` e `wiki/`) todo link relativo
     `[texto](caminho.md)` que aponta para `<origem>`, atualizando-o para `<destino>` (preservando `#âncora` e o
     rótulo).
  2. Se `<origem>` mudou de pasta, reescreve dentro da própria nota movida os links relativos que saem dela para outros
     arquivos do workspace.
  3. Move o arquivo com `git mv` (ou `os.replace` quando fora de um repositório git, como nos testes).
  4. Com `--title`, troca o H1 da nota — na pasta de plano, o do `PLN-NNN-plan.md` — e o rótulo de todo link para ela
     que era igual ao H1 antigo; o rótulo diferente fica e é contado na saída.

Quando o foco da nota muda, o nome, o H1 e os links mudam juntos (work/README.md, "Como funciona").

O script lê e reescreve só a árvore atual. O link de uma nota de outro workstream (outra branch `workstream/*`, que o
check_notes.py aceita) não é reescrito aqui: o rótulo e o caminho dele mudam na branch do outro workstream, com o
move_note.py rodado no worktree dele, e quem aponta de fora para a nota movida acusa `unresolved-link` até lá.

Uso:
  python3 .claude/skills/notes-checker/scripts/move_note.py <origem> <destino> [--title "H1 novo"] [--root DIR] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import quote, unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_notes as cn

INLINE_CODE = re.compile(r"`[^`\n]*`")
MD_LINK = re.compile(r"(!?\[(?:\\.|[^\[\]])*\]\()([^)\s]+)(\))")


def real_rel_map(root: str) -> dict[str, str]:
    """Retorna {real_rel_posix: full_path} para todos os arquivos do acervo."""
    on_disk = cn.files(root)
    out = {}
    for _, full in on_disk.items():
        rel = os.path.relpath(full, root).replace(os.sep, "/")
        out[rel] = full
    return out


def split_href(href: str) -> tuple[str, str]:
    """Separa `caminho.md#secao` em (`caminho.md`, `#secao`)."""
    if "#" in href:
        path_part, frag = href.split("#", 1)
        return path_part, "#" + frag
    return href, ""


def is_relative_path(href: str) -> bool:
    if not href or href.startswith(("#", "/")):
        return False
    # Todo esquema de URI fica como está — http, mailto, e também o file:/// que nota antiga ainda tem
    return not cn.URI_SCHEME.match(href)


def format_rel(dst_rel: str, src_dir: str) -> str:
    rel = os.path.relpath(dst_rel, src_dir if src_dir else ".").replace(os.sep, "/")
    return "/".join(quote(p, safe="-_.~()") for p in rel.split("/"))


def rewrite_links_in_text(
    text: str,
    note_old_rel: str,
    note_new_rel: str,
    moves: dict[str, str],
    labels: dict[str, tuple[str, str]] | None = None,
) -> tuple[str, int, int]:
    """Reescreve links Markdown relativos em `text` fora de blocos cercados de código.

    `moves` leva cada caminho antigo ao novo; `labels` leva o caminho antigo de uma nota a (H1 antigo, H1 novo), e o
    rótulo igual ao H1 antigo passa ao novo. Devolve o texto, os links reescritos e os rótulos trocados.
    """
    count = 0
    relabeled = 0
    labels = labels or {}
    moving_self = note_old_rel != note_new_rel
    note_dir = os.path.dirname(note_old_rel)
    new_note_dir = os.path.dirname(note_new_rel)

    out_lines = []
    fenced = False
    for line in text.split("\n"):
        if cn.FENCE.match(line.strip()):
            fenced = not fenced
            out_lines.append(line)
            continue
        if fenced:
            out_lines.append(line)
            continue

        # Link cujo caminho cai dentro de crases inline (`...`) é exemplo, não link; crase no rótulo não conta
        spans = [(c.start(), c.end()) for c in INLINE_CODE.finditer(line)]
        def repl(m: re.Match[str]) -> str:
            nonlocal count, relabeled
            prefix, href, suffix = m.group(1), m.group(2), m.group(3)
            if any(a <= m.start(2) < b for a, b in spans) or not is_relative_path(href):
                return m.group(0)
            path_part, frag = split_href(href)
            decoded = unquote(path_part)
            resolved = os.path.normpath(os.path.join(note_dir, decoded)).replace(os.sep, "/")
            if not moving_self and resolved not in moves and resolved not in labels:
                return m.group(0)

            new_href = href
            if moving_self or resolved in moves:
                target_rel = moves.get(resolved, resolved)
                if moving_self and target_rel == note_new_rel and frag:
                    # Link da nota para ela mesma, com âncora: fica só a âncora
                    new_href = frag
                else:
                    new_href = format_rel(target_rel, new_note_dir) + frag
                if new_href != href:
                    count += 1

            new_prefix = prefix
            if resolved in labels:
                old_title, new_title = labels[resolved]
                bang = "!" if prefix.startswith("!") else ""
                if prefix[len(bang) + 1 : -2].strip() == old_title:
                    new_prefix = f"{bang}[{new_title}]("
                    relabeled += 1
            return f"{new_prefix}{new_href}{suffix}"

        line = MD_LINK.sub(repl, line)
        out_lines.append(line)

    return "\n".join(out_lines), count, relabeled


def h1_of(text: str) -> str | None:
    """O H1 da nota, depois do frontmatter."""
    for line in body_lines(text):
        if line.startswith("# "):
            return line[2:].strip()
    return None


def body_lines(text: str):
    lines = text.split("\n")
    start = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                start = j + 1
                break
    return lines[start:]


def set_h1(text: str, new_title: str) -> str:
    lines = text.split("\n")
    offset = len(lines) - len(body_lines(text))
    for j in range(offset, len(lines)):
        if lines[j].startswith("# "):
            lines[j] = f"# {new_title}"
            return "\n".join(lines)
    raise ValueError("a nota não tem H1 para trocar")


def git_mv_or_move(root: str, src_full: str, dst_full: str) -> None:
    os.makedirs(os.path.dirname(dst_full), exist_ok=True)
    # Tenta git mv se o diretório estiver sob controle do git e o arquivo for rastreado
    src_dir = os.path.dirname(src_full)
    dst_dir = os.path.dirname(dst_full)
    try:
        top_src = subprocess.check_output(
            ["git", "-C", src_dir, "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        top_dst = subprocess.check_output(
            ["git", "-C", dst_dir, "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if top_src and top_src == top_dst:
            subprocess.run(
                ["git", "-C", top_src, "ls-files", "--error-unmatch", os.path.relpath(src_full, top_src)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["git", "-C", top_src, "mv", os.path.relpath(src_full, top_src), os.path.relpath(dst_full, top_src)],
                check=True,
            )
            return
    except Exception:
        pass
    shutil.move(src_full, dst_full)


def plan_file(folder_full: str) -> str:
    """O `PLN-NNN-plan.md` da pasta de plano: é ele que carrega o H1."""
    found = [f for f in os.listdir(folder_full) if f.endswith("-plan.md")]
    if len(found) != 1:
        raise ValueError(f"a pasta precisa de exatamente um *-plan.md para o --title; achei {len(found)}")
    return os.path.join(folder_full, found[0])


def move_note(
    root: str, src_arg: str, dst_arg: str, dry_run: bool = False, title: str | None = None
) -> tuple[int, int, int, int]:
    """Move a nota ou a pasta de plano e, com `title`, troca o H1 e os rótulos iguais ao H1 antigo.

    Devolve (arquivos atualizados, links reescritos, rótulos trocados, rótulos que ficaram diferentes do H1 novo).
    """
    root = os.path.abspath(root)
    src_full = os.path.abspath(os.path.join(root, src_arg) if not os.path.isabs(src_arg) else src_arg)
    dst_full = os.path.abspath(os.path.join(root, dst_arg) if not os.path.isabs(dst_arg) else dst_arg)
    src_is_dir = os.path.isdir(src_full)
    same = src_full == dst_full

    if not same and not src_is_dir and (os.path.isdir(dst_full) or dst_arg.endswith(("/", os.sep))):
        dst_full = os.path.join(dst_full, os.path.basename(src_full))
        same = src_full == dst_full

    if not os.path.exists(src_full):
        raise FileNotFoundError(f"origem não encontrada: {src_arg}")
    if same and not title:
        raise ValueError("origem e destino iguais: sem --title não há o que fazer")
    if not same and os.path.exists(dst_full):
        raise FileExistsError(f"destino já existe: {dst_arg}")

    def rel(path: str) -> str:
        return os.path.relpath(path, root).replace(os.sep, "/")

    # Cada arquivo que muda de lugar: um, ou todos os da pasta de plano
    moves: dict[str, str] = {}
    if src_is_dir:
        for dirpath, _, names in os.walk(src_full):
            for name in names:
                full = os.path.join(dirpath, name)
                moves[rel(full)] = rel(os.path.join(dst_full, os.path.relpath(full, src_full)))
    else:
        moves[rel(src_full)] = rel(dst_full)
    if same:
        moves = {}

    titled_old = rel(plan_file(src_full) if src_is_dir else src_full) if title else None
    labels: dict[str, tuple[str, str]] = {}
    titled_text = None
    if titled_old:
        with open(os.path.join(root, titled_old), encoding="utf-8") as f:
            titled_text = f.read()
        old_title = h1_of(titled_text)
        if old_title is None:
            raise ValueError(f"{titled_old} não tem H1 para trocar")
        labels[titled_old] = (old_title, title)

    all_notes = real_rel_map(root)
    if src_is_dir or titled_old:
        # Pasta de plano: os arquivos de dentro também podem apontar para o que muda
        for r in moves or ([titled_old] if titled_old else []):
            all_notes.setdefault(r, os.path.join(root, r))
    rewritten_files = rewritten_links = relabeled = 0
    pending_writes: dict[str, str] = {}

    for note_rel, full in sorted(all_notes.items()):
        if not note_rel.endswith(".md") or note_rel in cn.SKILL_TEMPLATES:
            continue
        with open(full, encoding="utf-8") as f:
            text = f.read()
        new_rel = moves.get(note_rel, note_rel)
        new_text, n, k = rewrite_links_in_text(text, note_rel, new_rel, moves, labels)
        if note_rel == titled_old:
            new_text = set_h1(new_text, title)
        if new_text != text:
            rewritten_files += 1
            rewritten_links += n
            relabeled += k
            pending_writes[os.path.join(root, new_rel)] = new_text

    if not dry_run:
        if not same:
            git_mv_or_move(root, src_full, dst_full)
        for full, content in pending_writes.items():
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)

    # Rótulo que não era igual ao H1 antigo fica como estava; conta para quem renomeou conferir
    stale = 0
    if titled_old:
        new_titled = moves.get(titled_old, titled_old)
        for note_rel, full in sorted(all_notes.items()):
            if not note_rel.endswith(".md"):
                continue
            new_rel = moves.get(note_rel, note_rel)
            text = pending_writes.get(os.path.join(root, new_rel))
            if text is None:
                with open(full, encoding="utf-8") as f:
                    text = f.read()
            fenced = False
            for line in text.split("\n"):
                if cn.FENCE.match(line.strip()):
                    fenced = not fenced
                if fenced or cn.FENCE.match(line.strip()):
                    continue
                spans = [(c.start(), c.end()) for c in INLINE_CODE.finditer(line)]
                for m in MD_LINK.finditer(line):
                    path_part, _ = split_href(m.group(2))
                    if any(a <= m.start(2) < b for a, b in spans) or not is_relative_path(path_part):
                        continue
                    target = os.path.normpath(os.path.join(os.path.dirname(new_rel), unquote(path_part)))
                    if target.replace(os.sep, "/") == new_titled and m.group(1).lstrip("!")[1:-2].strip() != title:
                        stale += 1

    return rewritten_files, rewritten_links, relabeled, stale


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("src", help="caminho atual da nota, ou a pasta do plano")
    parser.add_argument("dst", help="novo caminho (ou diretório de destino) da nota; igual à origem para só trocar o H1")
    parser.add_argument("--title", help="H1 novo; os rótulos iguais ao H1 antigo passam a ele")
    parser.add_argument("--root", default=cn.DEFAULT_ROOT, help="raiz do workspace")
    parser.add_argument("--dry-run", action="store_true", help="mostra quantos links mudariam sem tocar no disco")
    args = parser.parse_args()

    try:
        files_n, links_n, labels_n, stale_n = move_note(
            args.root, args.src, args.dst, dry_run=args.dry_run, title=args.title
        )
    except Exception as exc:
        print(f"erro: {exc}", file=sys.stderr)
        sys.exit(1)

    prefix = "[dry-run] " if args.dry_run else ""
    line = f"{prefix}{args.src} -> {args.dst} | arquivos atualizados: {files_n} | links reescritos: {links_n}"
    if args.title:
        line += f" | rótulos trocados: {labels_n} | rótulos diferentes do H1 novo: {stale_n}"
    print(line)


if __name__ == "__main__":
    main()
