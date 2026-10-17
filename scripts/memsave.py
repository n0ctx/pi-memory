#!/usr/bin/env python3
"""memsave — commit pending memory changes; run by the pi-memory extension at the end of each Pi run.

Usage: memsave.py [--root DIR] [--project NAME] [--today YYYY-MM-DD]

For changed reports, fills what can be derived: title (from the first # heading), date (from the
filename, else today), updated (today), and project (new reports only). Lint errors in changed files
block the commit; problems in unchanged files are reported as warnings. Otherwise regenerates INDEX
and commits every change under the root. Prints JSON:
{"changed": [... including deletions], "committed": bool, "errors": [...], "warnings": [...], "locked": bool}
"""
import argparse, datetime, json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memindex  # noqa: E402
from memoryio import LockBusy, write_text, writer_lock  # noqa: E402

PATHS = ['--', '.', ':(exclude).task-memory.lock']  # the root, minus the writer lock held while committing


def git(root, *args):
    return subprocess.run(['git', '-C', root, *args], check=True, capture_output=True, text=True,
                          encoding='utf-8').stdout


def pending(root):
    """Paths relative to root: changed (added or modified), the untracked subset, and deleted."""
    prefix = git(root, 'rev-parse', '--show-prefix').strip()
    out = git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all', *PATHS).split('\0')
    changed, untracked, deleted, i = [], set(), [], 0
    while i < len(out):
        entry = out[i]
        i += 1
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if 'R' in code or 'C' in code:
            i += 1  # the next field is the rename source
        path = path[len(prefix):] if path.startswith(prefix) else path
        if 'D' in code:
            deleted.append(path)
            continue
        changed.append(path)
        if code == '??':
            untracked.add(path)
    return changed, untracked, deleted


def autofix(root, name, today, project, new):
    path = os.path.join(root, name)
    text = open(path, encoding='utf-8').read()
    fm_text, body, _ = memindex.split_frontmatter(text)
    if fm_text is None:
        return
    try:
        fm = memindex.parse_frontmatter(fm_text)
    except (ValueError, json.JSONDecodeError):
        return
    fixed = text
    heading = re.search(r'(?m)^# (.+)$', body)
    if not fm.get('title') and heading:
        fixed = memindex.set_field(fixed, 'title', heading.group(1).strip())
    if not fm.get('date'):
        m = memindex.NAME_RE.match(name)
        fixed = memindex.set_field(fixed, 'date', m.group(1) if m else today)
    updated = str(fm.get('updated', ''))
    if not memindex.DATE_RE.match(updated) or updated < today:
        fixed = memindex.set_field(fixed, 'updated', today)
    if new and project and not fm.get('project'):
        fixed = memindex.set_field(fixed, 'project', project)
    if fixed != text:
        write_text(path, fixed)


def save(root, project, today):
    result = {'changed': [], 'committed': False, 'errors': [], 'warnings': [], 'locked': False}
    changed, untracked, deleted = pending(root)
    result['changed'] = changed + deleted
    if not result['changed']:
        return result
    try:
        with writer_lock(root):
            commit(root, changed, untracked, project, today, result)
    except LockBusy:
        result['locked'] = True
    return result


def commit(root, changed, untracked, project, today, result):
    for name in changed:
        if '/' not in name and name.endswith('.md') and name != 'INDEX.md':
            autofix(root, name, today, project, name in untracked)
    reps = memindex.load_reports(root)
    errs, warns = memindex.lint(reps)
    own = lambda issue: issue.split(': ', 1)[0] in changed
    result['errors'] = [e for e in errs if own(e)]
    result['warnings'] = [w for w in warns if own(w)] + [e for e in errs if not own(e)]
    if result['errors']:
        return
    index = os.path.join(root, 'INDEX.md')
    new_index = memindex.render_index(reps)
    if not os.path.exists(index) or open(index, encoding='utf-8').read() != new_index:
        write_text(index, new_index)
    now_changed, _, now_deleted = pending(root)
    names = sorted(now_changed + now_deleted)
    git(root, 'add', '-A', *PATHS)
    message = 'pi-memory: ' + ', '.join(names[:3]) + (f' +{len(names) - 3} more' if len(names) > 3 else '')
    git(root, 'commit', '-q', '-m', message, *PATHS)
    result['committed'] = True


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', default=memindex.ROOT)
    parser.add_argument('--project', default='')
    parser.add_argument('--today', type=datetime.date.fromisoformat, default=datetime.date.today())
    args = parser.parse_args(argv)
    root = memindex.ROOT = os.path.expanduser(args.root)
    try:
        result = save(root, args.project, args.today.isoformat())
    except subprocess.CalledProcessError as exc:
        result = {'changed': [], 'committed': False, 'errors': [f'git failed: {(exc.stderr or exc).strip()}'],
                  'warnings': [], 'locked': False}
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except OSError as exc:
        print(json.dumps({'changed': [], 'committed': False, 'errors': [str(exc)], 'warnings': [], 'locked': False}))
        sys.exit(1)
