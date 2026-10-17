#!/usr/bin/env python3
"""memcard — the memory card the pi-memory extension adds to every Pi request. Standard library only.

Usage: memcard.py [--root DIR] [--project NAME] [--today YYYY-MM-DD] [--budget 2000]

Within the character budget, in priority order: unclosed reports of the project (overdue first),
one-line preferences from LESSONS (newest first), unclosed reports without a project,
global notes from STATE, and a count of unclosed reports in other projects. Omitted lines are counted.
"""
import argparse, datetime, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memindex  # noqa: E402
import memq  # noqa: E402

DUE_RE = re.compile(r'\[due (\d{4}-\d{2}-\d{2})\]')


def due_mark(due, today):
    if not due:
        return ''
    try:
        overdue = datetime.date.fromisoformat(str(due)) < today
    except ValueError:
        return ''
    return f'⚠️ overdue {due} · ' if overdue else f'due {due} · '


def report_line(rep, today):
    g = lambda k, d='': memindex.g(rep, k, d)
    items = '; '.join(g('open', []))
    return (f"- {due_mark(g('due'), today)}{g('title') or rep['file']} [{g('status')}]"
            + (f' — open: {items}' if items else '') + f" · {rep['file']}")


def report_order(rep, today):
    due = str(memindex.g(rep, 'due') or '9999-99-99')
    return (due >= today.isoformat(), due, memindex._neg(memindex.g(rep, 'updated')))


def preference_lines(root):
    lines = [line for _, _, line in reversed(memq.note_lines(root, {'LESSONS.md'}))]
    for head, content, _, _, _ in reversed(memq.lessons(root)):
        if content:
            lines.append(f"- {content} ({head.rsplit(' · ', 1)[-1]})")
    return lines


def note_lines(root, today):
    out = []
    for _, _, line in memq.note_lines(root, {'STATE.md'}):
        m = DUE_RE.search(line)
        if m and m.group(1) < today.isoformat():
            line = line.replace(m.group(0), f'⚠️ overdue {m.group(1)}', 1)
        out.append(line)
    return out


def build(root, project, today, budget):
    unclosed = [r for r in memindex.load_reports(root) if r['fm'] and memindex.is_unclosed(r)] \
        if os.path.isdir(root) else []
    unclosed.sort(key=lambda r: report_order(r, today))
    mine = [r for r in unclosed if project and memindex.g(r, 'project') == project]
    loose = [r for r in unclosed if not memindex.g(r, 'project')]
    others = {}
    for r in unclosed:
        p = memindex.g(r, 'project')
        if p and p != project:
            others[p] = others.get(p, 0) + 1
    sections = [
        (f'Open work in {project}:', [report_line(r, today) for r in mine]),
        ('Preferences and corrections (_meta/LESSONS.md):', preference_lines(root)),
        ('Open work without a project:', [report_line(r, today) for r in loose]),
        ('Notes (_meta/STATE.md):', note_lines(root, today)),
    ]
    out, used = [], 0
    for title, items in sections:
        if not items:
            continue
        block, omitted = [title], 0
        size = used + len(title) + 1
        for item in items:
            if size + len(item) + 1 > budget - 60:
                omitted += 1
                continue
            block.append(item)
            size += len(item) + 1
        if len(block) == 1:
            block.append(f'- ({omitted} lines omitted)')
        elif omitted:
            block.append(f'- (+{omitted} more; use memory search)')
        block_text = '\n'.join(block)
        if used + len(block_text) + 2 > budget:
            break
        out.append(block_text)
        used += len(block_text) + 2
    if others:
        line = 'Other projects with open work: ' + ', '.join(f'{p} ({n})' for p, n in sorted(others.items()))
        if used + len(line) <= budget:
            out.append(line)
    return '\n\n'.join(out) if out else '(no open work, preferences, or notes recorded yet)'


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', default=memindex.ROOT)
    parser.add_argument('--project', default='')
    parser.add_argument('--today', type=datetime.date.fromisoformat, default=datetime.date.today())
    parser.add_argument('--budget', type=int, default=2000)
    args = parser.parse_args(argv)
    memindex.ROOT = os.path.expanduser(args.root)
    print(build(memindex.ROOT, args.project, args.today, args.budget))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except OSError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
