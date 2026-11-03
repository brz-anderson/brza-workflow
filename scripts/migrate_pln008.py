#!/usr/bin/env python3
"""Script de migração do PLN-008 (D4, D8, usando D3 e D7).

Modos:
- Prévia por padrão (sem `--apply`): inspeciona os workstreams selecionados, valida colisões de bootstrap no índice,
  calcula as renomeações de D4 (`REV-001`..`REV-004`), a criação da `MIG-001` e o recálculo de `refs:` / remoção de
  `date:` nos worklogs (D3), reportando arquivos e alterações sem tocar no disco nem commitar.
- Aplicação explícita (`--apply`):
  1. Reserva atomicamente `REV-001`..`REV-004` e `MIG-001` no índice (`bootstrap_pln008`) e cria a nota `MIG-001`
     no molde de D1 em `workspace`.
  2. Renomeia os quatro pareceres históricos conforme o mapa D4, trocando `REV:` por `REV-NNN:` no H1 e atualizando os
     links relativos nas árvores donas via `move_note.py`.
  3. Recalcula `refs:` dos worklogs pelo extrator de D3 (`extract_worklog_refs`) e remove `date:` preservando `tags:` e
     os demais campos do frontmatter (`set_frontmatter_refs(..., remove_date=True)`).
  4. Confere cada workstream com o checador (`check_notes.py`), commita por caminhos modificados e reconstrói o índice
     (`index --rebuild`).

Worktree ausente é reportado sem criar workstreams novos. Segunda execução com `--apply` é idempotente (zero bytes
alterados e zero linhas acrescentadas ao índice).

Uso: python3 scripts/migrate_pln008.py --workstream workspace
A aplicação exige --apply e seleção explícita; cada sessão seleciona apenas os workstreams sob sua responsabilidade.
A MIG nasce aberta e sem datas históricas inferidas. Sua conclusão depende das evidências das sessões donas.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NOTE_BIN = ROOT / 'bin' / 'brza-note'
MOVE_SCRIPT = ROOT / 'core' / '.claude' / 'skills' / 'notes-checker' / 'scripts' / 'move_note.py'

REV_MAP: tuple[tuple[str, str, str, str], ...] = (
    ('tools-kit', 'REV-001', 'work/reviews/pln-002-os-comandos-brza.md', 'work/reviews/REV-001-pln-002-os-comandos-brza.md'),
    ('workspace', 'REV-002', 'work/reviews/pln-005-a-confianca-dos-fatos.md', 'work/reviews/REV-002-pln-005-a-confianca-dos-fatos.md'),
    ('workspace', 'REV-003', 'work/reviews/pln-006-o-brza-init.md', 'work/reviews/REV-003-pln-006-o-brza-init.md'),
    ('workspace', 'REV-004', 'work/reviews/pln-006-a-migracao.md', 'work/reviews/REV-004-pln-006-a-migracao.md'),
)

MIG_001_REL = 'work/issues/MIG-001-renumeracao-rev-e-refs-pln-008.md'
MIG_001_WORKSTREAM = 'workspace'
MIG_001_CONTENT = """---
status: open
type: migration
description: Renomeia os quatro pareceres históricos para REV-001..REV-004 e recalcula refs dos worklogs.
scope:
  - workspace
tags:
  - tooling
---

# MIG-001: Renumeração dos pareceres REV-001..REV-004 e backfill de refs nos worklogs

## Estado-alvo

Conforme `PLN-008` (D3, D4, D7 e D8):

- Os quatro pareceres históricos seguem `work/reviews/REV-NNN-<slug>.md` e H1 `# REV-NNN: <título>`:
  - `tools-kit`: `work/reviews/REV-001-pln-002-os-comandos-brza.md`
  - `workspace`: `work/reviews/REV-002-pln-005-a-confianca-dos-fatos.md`
  - `workspace`: `work/reviews/REV-003-pln-006-o-brza-init.md`
  - `workspace`: `work/reviews/REV-004-pln-006-a-migracao.md`
- Os worklogs trazem `refs:` calculado pelas menções e links do corpo (D3), sem o campo `date:` no frontmatter.

## Estado atual

- Pareceres históricos sem prefixo `REV-NNN-` no caminho e com H1 `# REV: <título>`.
- Worklogs históricos sem `refs:` consolidado pelo extrator D3 ou com `date:` no frontmatter.

## Passos

- [ ] P1 — Conferir as reservas `REV-001`..`REV-004` e `MIG-001` no índice da master.
- [ ] P2 — Provar os quatro nomes novos, a ausência dos antigos e os links resolvidos nos workstreams donos.
- [ ] P3 — Conferir refs dos worklogs e remoção de date com tags e demais campos preservados.
- [ ] P4 — Registrar os comandos, resultados e commits antes de concluir esta MIG.

## Critério de conclusão

Rodar no worktree `workspace`, com os dois worktrees presentes:

```bash
python3 .claude/skills/notes-checker/scripts/check_notes.py
python3 .claude/skills/notes-checker/scripts/check_notes.py --root ../tools-kit
python3 - <<'PY'
from pathlib import Path
mapa = {
    '../tools-kit/work/reviews': [('001', 'pln-002-os-comandos-brza')],
    'work/reviews': [('002', 'pln-005-a-confianca-dos-fatos'),
                     ('003', 'pln-006-o-brza-init'), ('004', 'pln-006-a-migracao')],
}
for pasta, revisoes in mapa.items():
    for numero, slug in revisoes:
        antigo = Path(pasta) / (slug + '.md')
        novo = Path(pasta) / ('REV-' + numero + '-' + slug + '.md')
        assert novo.is_file(), str(novo)
        assert not antigo.exists(), str(antigo)
print('Mapa D4 conferido: quatro destinos presentes e quatro origens ausentes.')
PY
```

A execução parcial em um workstream não conclui a MIG; a sessão dona registra os comandos, resultados e commits.
"""

REV_H1_PREFIX = re.compile(r'^REV\s*:\s*')


def load_note_globals() -> dict:
    # run_path não acrescenta o diretório do executável ao sys.path.
    sys.path.insert(0, str(NOTE_BIN.parent))
    return runpy.run_path(str(NOTE_BIN))['main'].__globals__


def load_move_note():
    spec = importlib.util.spec_from_file_location('move_note', str(MOVE_SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replace_rev_h1(old_h1: str | None, rev_id: str) -> str | None:
    """D4: troca somente `REV:` pelo `<REV-NNN>:` correspondente no H1."""
    if not old_h1:
        return None
    if old_h1.startswith(f'{rev_id}:'):
        return old_h1
    if REV_H1_PREFIX.match(old_h1):
        return REV_H1_PREFIX.sub(f'{rev_id}: ', old_h1, count=1)
    raise ValueError(f'H1 inesperado para {rev_id}: {old_h1}')


def ordered_workstreams(ws_root: Path, requested: list[str] | None) -> list[str]:
    """Ordena workstreams com `tools-kit` e `workspace` primeiro para que as notas D4 existam nas branches ao
    processar os demais workstreams."""
    if requested:
        raw = []
        for item in requested:
            for part in item.split(','):
                name = part.strip()
                if name and name != 'all' and name not in raw:
                    raw.append(name)
        if raw:
            priority = {'tools-kit': 0, 'workspace': 1}
            return sorted(raw, key=lambda n: (priority.get(n, 2), raw.index(n)))
        return []

    trees_dir = ws_root / '.worktrees'
    discovered: set[str] = {'tools-kit', 'workspace'}
    if trees_dir.is_dir():
        for child in sorted(trees_dir.iterdir()):
            if child.is_dir() and (child / '.git').exists():
                discovered.add(child.name)
    priority = {'tools-kit': 0, 'workspace': 1}
    return sorted(discovered, key=lambda n: (priority.get(n, 2), n))


def check_bootstrap_preview(note_globals: dict, entries: tuple[dict, ...] | list[dict] | None = None) -> None:
    """Em modo prévia, confere no índice disponível se há colisão divergente com `PLN008_BOOTSTRAP`."""
    entries = entries or note_globals['PLN008_BOOTSTRAP']
    Refused = note_globals['Refused']
    runtime = note_globals['runtime']
    try:
        repo = note_globals['index_repo']()
        text = ''
        remote, branch = note_globals['remote_and_branch']()
        for ref in (f'{remote}/{branch}', branch):
            try:
                if runtime.git('ls-tree', ref, '--', note_globals['INDEX_PATH'], cwd=repo).strip():
                    text = runtime.git('show', f'{ref}:{note_globals["INDEX_PATH"]}', cwd=repo)
                    break
            except Exception:
                continue
    except Exception as exc:
        raise Refused(f'não foi possível ler o índice para a prévia: {exc}') from exc
    if not text and not any(note_globals['ref_has'](ref, note_globals['INDEX_PATH'], repo)
                            for ref in (f'{remote}/{branch}', branch)):
        raise Refused('índice indisponível para a prévia')
    state = note_globals['current_state'](text)
    by_path = {line.get('path'): id_ for id_, line in state.items() if isinstance(line.get('path'), str)}
    for entry in entries:
        id_, kind, ws, path = entry['id'], entry['kind'], entry['workstream'], entry['path']
        if id_ in state:
            existing = state[id_]
            if (existing.get('kind'), existing.get('workstream'), existing.get('path')) != (kind, ws, path):
                raise Refused(
                    f'colisão no bootstrap de {id_}: índice tem ({existing.get("kind")}, '
                    f'{existing.get("workstream")}, {existing.get("path")}), esperado ({kind}, {ws}, {path})'
                )
            if entry.get('type') and existing.get('type') not in (None, entry['type']):
                raise Refused(f'colisão no bootstrap de {id_}: tipo {existing.get("type")} != {entry["type"]}')
            continue
        if path in by_path and by_path[path] != id_:
            raise Refused(f'colisão no bootstrap de {id_}: caminho {path} já pertence a {by_path[path]}')


def snapshot_md_files(wt_root: Path, mn_module) -> dict[str, str]:
    rel_map = mn_module.real_rel_map(str(wt_root))
    out: dict[str, str] = {}
    for rel, full in sorted(rel_map.items()):
        if rel.endswith('.md'):
            out[rel] = Path(full).read_text(encoding='utf-8')
    return out


def simulate_or_apply_rev_moves(
    ws_name: str,
    wt_root: Path,
    apply: bool,
    mn_module,
) -> tuple[list[tuple[str, str, str, str, str]], list[str], set[str], dict[str, str]]:
    """Executa ou simula o passo 2 (renomeações D4, troca do H1 e reescrita de links na árvore dona).

    Devolve `(renamed, link_updates, changed_paths, simulated_texts)`.
    """
    renamed: list[tuple[str, str, str, str, str]] = []
    link_updates: set[str] = set()
    changed_paths: set[str] = set()
    texts = snapshot_md_files(wt_root, mn_module)

    ws_entries = [row for row in REV_MAP if row[0] == ws_name]
    for _, rev_id, old_rel, new_rel in ws_entries:
        if old_rel in texts and new_rel in texts:
            raise ValueError(f'origem e destino coexistem: {old_rel}, {new_rel}')
        if old_rel in texts:
            old_text = texts[old_rel]
            old_h1 = mn_module.h1_of(old_text) or ''
            new_h1 = replace_rev_h1(old_h1, rev_id) or old_h1
            renamed.append((old_rel, new_rel, rev_id, old_h1, new_h1))
            changed_paths.add(old_rel)
            changed_paths.add(new_rel)
            if apply:
                before = dict(texts)
                mn_module.move_note(str(wt_root), old_rel, new_rel, dry_run=False, title=new_h1)
                texts = snapshot_md_files(wt_root, mn_module)
                for rel, content in texts.items():
                    if rel == new_rel:
                        continue
                    if before.get(rel) != content:
                        link_updates.add(rel)
                        changed_paths.add(rel)
            else:
                moves = {old_rel: new_rel}
                labels = {old_rel: (old_h1, new_h1)} if old_h1 else {}
                next_texts: dict[str, str] = {}
                for rel, content in sorted(texts.items()):
                    target_rel = moves.get(rel, rel)
                    rewritten, _, _ = mn_module.rewrite_links_in_text(content, rel, target_rel, moves, labels)
                    if rel == old_rel and new_h1:
                        rewritten = mn_module.set_h1(rewritten, new_h1)
                    if rel != old_rel and rewritten != content:
                        link_updates.add(target_rel)
                        changed_paths.add(target_rel)
                    next_texts[target_rel] = rewritten
                texts = next_texts
        elif new_rel in texts:
            cur_text = texts[new_rel]
            old_h1 = mn_module.h1_of(cur_text) or ''
            new_h1 = replace_rev_h1(old_h1, rev_id) or old_h1
            labels = {old_rel: (old_h1, new_h1), new_rel: (old_h1, new_h1)} if old_h1 != new_h1 else {}
            if old_h1 != new_h1:
                renamed.append((new_rel, new_rel, rev_id, old_h1, new_h1))
                changed_paths.add(new_rel)
            moves = {old_rel: new_rel}
            for rel, content in list(sorted(texts.items())):
                rewritten, _, _ = mn_module.rewrite_links_in_text(content, rel, rel, moves, labels)
                if rel == new_rel and old_h1 != new_h1:
                    rewritten = mn_module.set_h1(rewritten, new_h1)
                if rewritten != content:
                    if rel != new_rel:
                        link_updates.add(rel)
                    changed_paths.add(rel)
                    texts[rel] = rewritten
                    if apply:
                        (wt_root / rel).write_text(rewritten, encoding='utf-8')

    return renamed, sorted(link_updates), changed_paths, texts


def augment_preview_catalog(catalog: dict, ws_root: Path, targets: list[str]) -> None:
    """Na prévia (ou antes do commit de outro workstream), garante que os alvos D4/MIG-001 existentes em disco sejam
    reconhecidos no catálogo de referências."""
    for owner_ws, rev_id, old_rel, new_rel in REV_MAP:
        wt = ws_root / '.worktrees' / owner_ws
        if (owner_ws in targets and (wt / old_rel).is_file()) or (wt / new_rel).is_file():
            catalog['valid_refs'].add(rev_id)
            catalog['all_rels'].add(old_rel)
            catalog['all_rels'].add(new_rel)
            catalog['id_by_rel'][old_rel] = rev_id
            catalog['id_by_rel'][new_rel] = rev_id
    mig_wt = ws_root / '.worktrees' / MIG_001_WORKSTREAM
    if ((MIG_001_WORKSTREAM in targets and (mig_wt / '.git').exists())
            or (mig_wt / MIG_001_REL).is_file()):
        catalog['valid_refs'].add('MIG-001')
        catalog['all_rels'].add(MIG_001_REL)
        catalog['id_by_rel'][MIG_001_REL] = 'MIG-001'


def migrate(
    ws_root: Path | None = None,
    workstreams: list[str] | None = None,
    apply: bool = False,
    note_globals: dict | None = None,
) -> tuple[int, dict]:
    """Executa a prévia ou aplicação da migração PLN-008 e devolve `(exit_code, report)`."""
    ng = note_globals or load_note_globals()
    mn = load_move_note()
    Refused = ng['Refused']
    runtime = ng['runtime']

    if ws_root is None:
        ws_root = runtime.require_workspace()
    ws_root = Path(ws_root).resolve()

    targets = ordered_workstreams(ws_root, workstreams)
    report: dict = {
        'mode': 'apply' if apply else 'preview',
        'missing_worktrees': [],
        'workstreams': {},
    }

    try:
        if apply and (not targets or not workstreams or any('all' in [p.strip() for p in item.split(',')] for item in workstreams)):
            raise Refused('--apply exige seleção explícita por --workstream')
        # Validar TODAS as árvores selecionadas antes de reservar ou escrever: D3 não permite apagar refs inválidos.
        for ws_name in targets:
            wt = ws_root / '.worktrees' / ws_name
            if wt.parent != ws_root / '.worktrees' or ws_name in ('.', '..'):
                raise Refused(f'workstream inválido: {ws_name}')
            if not (wt / '.git').exists():
                continue
            if apply and runtime.git('status', '--porcelain', '--', 'plans', 'work', 'wiki', cwd=wt).strip():
                raise Refused(f'notas com alterações sem commit em {ws_name}; preserve e grave o trabalho antes de aplicar')
            if ng['branch_of'](wt) != f'workstream/{ws_name}':
                raise Refused(f'branch incompatível com workstream: {ws_name}')
            catalog = ng['build_target_catalog'](wt)
            for rel, content in snapshot_md_files(wt, mn).items():
                if ng['WORKLOG_NOTE'].fullmatch(rel):
                    for ref_id in ng['as_list'](ng['parse_frontmatter'](content).get('refs')):
                        if ref_id not in catalog['valid_refs']:
                            raise Refused(f'ref-not-found: {ws_name}:{rel} — {ref_id}')
            # Simular também para detectar destino duplicado/H1 inesperado antes de qualquer escrita.
            simulate_or_apply_rev_moves(ws_name, wt, False, mn)
        if apply and any((ws_root / '.worktrees' / name / '.git').exists() for name in targets):
            ng['bootstrap_pln008']()
        else:
            check_bootstrap_preview(ng)
    except (Refused, runtime.BrzaError, ValueError) as e:
        print(f'recusado: {e}')
        return 1, report

    any_committed = False
    for ws_name in targets:
        wt_root = ws_root / '.worktrees' / ws_name
        if not (wt_root / '.git').exists():
            report['missing_worktrees'].append(ws_name)
            print(f'aviso: worktree ausente: {ws_name} ({wt_root})')
            continue

        ws_info: dict = {
            'created': [],
            'renamed': [],
            'link_updates': [],
            'worklogs_updated': [],
            'changed_paths': [],
        }
        changed_paths: set[str] = set()

        # Passo 1: criar MIG-001 no workstream `workspace` se ainda não existir com o conteúdo esperado
        if ws_name == MIG_001_WORKSTREAM:
            mig_path = wt_root / MIG_001_REL
            existing_mig = mig_path.read_text(encoding='utf-8') if mig_path.is_file() else None
            if existing_mig is None:
                ws_info['created'].append(MIG_001_REL)
                changed_paths.add(MIG_001_REL)
                if apply:
                    mig_path.parent.mkdir(parents=True, exist_ok=True)
                    mig_path.write_text(MIG_001_CONTENT, encoding='utf-8')

        # Passo 2: renomear pareceres de D4, atualizar H1 e corrigir links na árvore dona
        renamed, link_updates, rev_changed, texts = simulate_or_apply_rev_moves(ws_name, wt_root, apply, mn)
        ws_info['renamed'] = renamed
        ws_info['link_updates'] = link_updates
        changed_paths |= rev_changed

        # Um destino já materializado em outra branch substitui o caminho antigo antes do checador desta árvore.
        ready_moves, ready_labels = {}, {}
        for owner, rev_id, old_rel, new_rel in REV_MAP:
            destination = ws_root / '.worktrees' / owner / new_rel
            if owner == ws_name or not destination.is_file():
                continue
            new_h1 = mn.h1_of(destination.read_text(encoding='utf-8')) or ''
            ready_moves[old_rel] = new_rel
            ready_labels[old_rel] = ('REV:' + new_h1.split(':', 1)[1], new_h1)
        for rel, content in list(texts.items()):
            rewritten, _, _ = mn.rewrite_links_in_text(content, rel, rel, ready_moves, ready_labels)
            if rewritten != content:
                texts[rel] = rewritten
                changed_paths.add(rel)
                if rel not in ws_info['link_updates']:
                    ws_info['link_updates'].append(rel)
                if apply:
                    (wt_root / rel).write_text(rewritten, encoding='utf-8')

        # Passo 3: recalcular `refs:` dos worklogs pelo D3 e remover `date:` preservando `tags:` e demais campos
        catalog = ng['build_target_catalog'](wt_root)
        if not apply:
            augment_preview_catalog(catalog, ws_root, targets)

        worklog_pattern = ng['WORKLOG_NOTE']
        for rel in sorted(texts):
            if not worklog_pattern.fullmatch(rel):
                continue
            cur_text = (wt_root / rel).read_text(encoding='utf-8') if apply else texts[rel]
            refs, unknown = ng['extract_worklog_refs'](cur_text, wt_root / rel, wt_root, catalog)
            for lineno, ref_id in unknown:
                print(f'aviso: unknown-ref-mention: {ws_name}:{rel}:{lineno} — {ref_id}')
            updated = ng['set_frontmatter_refs'](cur_text, refs, remove_date=True)
            if updated != cur_text:
                ws_info['worklogs_updated'].append(rel)
                changed_paths.add(rel)
                if apply:
                    (wt_root / rel).write_text(updated, encoding='utf-8')

        ws_info['changed_paths'] = sorted(changed_paths)
        report['workstreams'][ws_name] = ws_info

        mode_tag = '[APLICADO]' if apply else '[PRÉVIA]'
        print(
            f'{mode_tag} {ws_name}: {len(ws_info["created"])} criado(s), '
            f'{len(ws_info["renamed"])} parecer(es) renomeado(s), '
            f'{len(ws_info["link_updates"])} nota(s) com links reescritos, '
            f'{len(ws_info["worklogs_updated"])} worklog(s) atualizado(s)'
        )
        for c in ws_info['created']:
            print(f'  + {c}')
        for old_r, new_r, rev_id, _, new_h1 in ws_info['renamed']:
            print(f'  ~ {old_r} -> {new_r} ({rev_id}: #{new_h1})')
        for l_rel in ws_info['link_updates']:
            print(f'  * links: {l_rel}')
        for w_rel in ws_info['worklogs_updated']:
            print(f'  * worklog: {w_rel}')

        # Passo 4: conferir workstream e commitar por caminhos
        if apply:
            if ws_info['changed_paths']:
                rc = ng['commit'](
                    wt_root,
                    ws_info['changed_paths'],
                    f'docs(pln-008): migração MIG-001 em {ws_name}',
                    do_push=True,
                )
                if rc != 0:
                    return rc, report
                any_committed = True
            elif ng['check'](wt_root) != 0:
                print(f'recusado: o checador achou erro em {ws_name}')
                return 1, report

    # O mover atualiza apenas a árvore dona. Depois que os destinos estão nas branches,
    # reescrever os links cruzados nas árvores selecionadas, sem editar workstreams alheios.
    moves, labels = {}, {}
    for owner, rev_id, old_rel, new_rel in REV_MAP:
        wt = ws_root / '.worktrees' / owner
        source = wt / old_rel if not apply and owner in targets else wt / new_rel
        if not source.is_file():
            source = wt / new_rel
        if not source.is_file():
            continue
        h1 = mn.h1_of(source.read_text(encoding='utf-8')) or ''
        new_h1 = replace_rev_h1(h1, rev_id)
        old_h1 = 'REV:' + new_h1.split(':', 1)[1] if new_h1 else h1
        moves[old_rel] = new_rel
        labels[old_rel] = (old_h1, new_h1)
    for ws_name, info in report['workstreams'].items():
        wt = ws_root / '.worktrees' / ws_name
        changed = []
        catalog = ng['build_target_catalog'](wt)
        for rel, content in snapshot_md_files(wt, mn).items():
            rewritten, _, _ = mn.rewrite_links_in_text(content, rel, rel, moves, labels)
            if apply and ng['WORKLOG_NOTE'].fullmatch(rel):
                refs, _ = ng['extract_worklog_refs'](rewritten, wt / rel, wt, catalog)
                rewritten = ng['set_frontmatter_refs'](rewritten, refs, remove_date=True)
            if rewritten != content:
                changed.append(rel)
                if apply:
                    (wt / rel).write_text(rewritten, encoding='utf-8')
        for rel in changed:
            if rel not in info['link_updates']:
                info['link_updates'].append(rel)
            if rel not in info['changed_paths']:
                info['changed_paths'].append(rel)
            print(f'  * links cruzados: {ws_name}:{rel}')
        if apply and changed:
            rc = ng['commit'](wt, changed, f'docs(pln-008): links cruzados em {ws_name}', do_push=True)
            if rc:
                return rc, report
            any_committed = True

    if apply and any_committed:
        try:
            ng['rebuild']()
        except (Refused, runtime.BrzaError) as e:
            print(f'aviso: o commit nos workstreams foi feito, mas não reconstruí o índice ({e})')
            return 1, report

    return 0, report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog='migrate_pln008.py',
        description='Migração do PLN-008 (D4, D8): prévia por padrão e aplicação explícita com --apply.',
    )
    ap.add_argument('--apply', action='store_true', help='aplica as mudanças, commita por caminhos e reconstrói o índice')
    ap.add_argument(
        '--workstream',
        action='append',
        default=[],
        help='filtra o(s) workstream(s) alvo (pode repetir ou separar por vírgula; padrão: todos)',
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    code, _ = migrate(
        workstreams=args.workstream or None,
        apply=args.apply,
    )
    return code


if __name__ == '__main__':
    sys.exit(main())
