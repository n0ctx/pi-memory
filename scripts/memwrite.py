#!/usr/bin/env python3
"""memwrite — structured memory writes behind the pi-memory `memory` tool. Standard library only.

Usage: memwrite.py <note|report|update> --json '<object>' [--root DIR] [--project NAME] [--today YYYY-MM-DD]
  note    {"kind": "environment|preference|global", "text": "...", "replaces": "<part of an existing line>"}
          Appends "- YYYY-MM-DD · text"; with replaces, rewrites the one matching line (empty text deletes it).
  report  {"title", "summary", "status" (default open), "type", "tags": [], "open": [], "due": "YYYY-MM-DD",
           "related": ["<report file>"], "body"}
          Creates <slug>-YYYY-MM-DD.md with project set to --project. Refused when an unclosed report of the
          same project has the same title; that report should be updated instead.
  update  {"file", "status", "summary", "open": [], "due", "related": [], "change"}
          Sets the given fields and updated (related links are added to existing ones);
          "change" is appended under "## Subsequent Changes".
Prints what was written and lint errors for the written file. Exit code 1 on invalid input or lint errors.
Committing is left to memsave.py.
"""
import argparse, datetime, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memindex  # noqa: E402
import memq  # noqa: E402
from memoryio import write_text, writer_lock  # noqa: E402

NOTE_FILES = {
    'environment': ('ENVIRONMENT.md', '# ENVIRONMENT — Machine facts\n\n'
                    '> One line each: `- YYYY-MM-DD · <fact>`. Verify old entries against the live system.\n'),
    'preference': ('LESSONS.md', '# LESSONS — Preferences & Corrections\n\n'
                   '> Shown in every Pi request, newest first. One line each, in the user\'s words.\n\n(none yet)\n'),
    'global': ('STATE.md', '# STATE — Global notes\n\n'
               '> Shown in every Pi request. Cross-project reminders only, one line each; open work belongs in reports.\n\n'
               '(none yet)\n'),
}
CLOSED = ('done', 'abandoned', 'superseded')


class InputError(ValueError):
    pass


def read(path):
    return open(path, encoding='utf-8').read() if os.path.exists(path) else ''


def note(root, data, today):
    check(data, ('kind', 'text', 'replaces'))
    kind, text = data.get('kind'), str(data.get('text') or '').strip()
    replaces = str(data.get('replaces') or '').strip()
    if kind not in NOTE_FILES:
        raise InputError(f'kind must be one of {", ".join(NOTE_FILES)}')
    if not text and not replaces:
        raise InputError('text is required')
    if '\n' in text:
        raise InputError('text must be one line')
    name, header = NOTE_FILES[kind]
    path = os.path.join(root, '_meta', name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    content = read(path) or header
    lines = content.split('\n')
    entry = f'- {today} · {text}'
    if replaces:
        hits = [i for i, line in enumerate(lines) if replaces in line]
        if len(hits) != 1:
            raise InputError(f'replaces matched {len(hits)} lines in {path}; give text that occurs in exactly one line')
        i = hits[0]
        if text:
            lines[i] = entry
        else:
            del lines[i]
        write_text(path, '\n'.join(lines))
        return f'{"Updated" if text else "Deleted"} {path}:{i + 1}'
    key = memq.norm(text).strip()
    for i, line in enumerate(lines):
        if key and key in memq.norm(line):
            return f'Already recorded at {path}:{i + 1}: {line.strip()}'
    content = re.sub(r'\n+\(.*\)\s*$', '', content.rstrip())  # drop a trailing "(none yet)" placeholder
    gap = '\n' if content.rsplit('\n', 1)[-1].startswith('- ') else '\n\n'
    write_text(path, f'{content}{gap}{entry}\n')
    return f'Recorded in {path}'


def slug(title, tags):
    for source in [title, *tags]:
        s = re.sub(r'[^a-z0-9]+', '-', str(source).lower()).strip('-')[:50].strip('-')
        if s:
            return s
    return 'note'


def check(data, keys):
    status = data.get('status')
    if status is not None and status not in memindex.STATUSES:
        raise InputError(f'status must be one of {", ".join(memindex.STATUSES)}')
    if data.get('type') and data['type'] not in memindex.TYPES:
        raise InputError(f'type must be one of {", ".join(memindex.TYPES)}')
    if data.get('due') and not memindex.DATE_RE.match(str(data['due'])):
        raise InputError('due must be YYYY-MM-DD')
    for k in ('tags', 'open', 'related'):
        if k in data and not isinstance(data[k], list):
            raise InputError(f'{k} must be a list of strings')
    unknown = set(data) - set(keys)
    if unknown:
        raise InputError(f'{", ".join(sorted(unknown))} not accepted here; allowed: {", ".join(keys)}')


def related_files(root, data, own=''):
    """Basenames of existing reports to link; unknown names are an input error."""
    names = list(dict.fromkeys(os.path.basename(str(x)) for x in data.get('related', [])))
    if own in names:
        raise InputError(f'related cannot include the report itself ({own})')
    missing = [n for n in names if not os.path.isfile(os.path.join(root, n))]
    if missing:
        raise InputError(f'related must name existing reports; not found: {", ".join(missing)}')
    return names


def title_key(title):
    return ' '.join(memq.norm(title).split())


def open_duplicate(root, title, project):
    key = title_key(title)
    for rep in memindex.load_reports(root):
        if rep['fm'] and memindex.is_unclosed(rep) and str(memindex.g(rep, 'project')) == project \
                and title_key(memindex.g(rep, 'title')) == key:
            return rep
    return None


def report(root, data, today, project):
    check(data, ('title', 'summary', 'status', 'type', 'tags', 'open', 'due', 'related', 'body'))
    title, summary = str(data.get('title') or '').strip(), str(data.get('summary') or '').strip()
    if not title or not summary:
        raise InputError('title and summary are required')
    dup = open_duplicate(root, title, project)
    if dup:
        raise InputError(
            f'{dup["file"]} is an unclosed report with the same title in {"project " + project if project else "no project"} '
            f'(status {memindex.g(dup, "status")}). Use action "update" with file "{dup["file"]}" instead; '
            'for a separate task, use a different title.')
    related = related_files(root, data)
    base = slug(title, data.get('tags', []))
    name, n = f'{base}-{today}.md', 2
    while os.path.exists(os.path.join(root, name)):
        name, n = f'{base}-{n}-{today}.md', n + 1
    fields = [('title', title), ('date', today), ('updated', today), ('status', data.get('status') or 'open')]
    if data.get('type'): fields.append(('type', data['type']))
    if project: fields.append(('project', project))
    fields += [('tags', data.get('tags', [])), ('summary', summary), ('open', data.get('open', []))]
    if data.get('due'): fields.append(('due', data['due']))
    if related: fields.append(('related', related))
    fm = '\n'.join(f'{k}: {memindex.format_value(v)}' for k, v in fields)
    body = str(data.get('body') or '').strip()
    write_text(os.path.join(root, name), f'---\n{fm}\n---\n\n# {title}\n' + (f'\n{body}\n' if body else ''))
    return name, f'Created {os.path.join(root, name)}'


def update(root, data, today):
    check(data, ('file', 'status', 'summary', 'open', 'due', 'related', 'change'))
    name = os.path.basename(str(data.get('file') or ''))
    path = os.path.join(root, name)
    if not name.endswith('.md') or name == 'INDEX.md' or not os.path.isfile(path):
        raise InputError(f'no report named {name!r} in {root}')
    text = read(path)
    fm = memindex.split_frontmatter(text)[0]
    if fm is None:
        raise InputError(f'{name} has no frontmatter')
    if data.get('related'):
        try:
            existing = memindex.parse_frontmatter(fm).get('related', [])
        except (ValueError, json.JSONDecodeError):
            existing = []
        text = memindex.set_field(text, 'related', list(dict.fromkeys([*existing, *related_files(root, data, name)])))
    for k in ('status', 'summary', 'open', 'due'):
        if k in data and data[k] is not None:
            text = memindex.set_field(text, k, data[k])
    if data.get('status') in CLOSED and 'open' not in data:
        text = memindex.set_field(text, 'open', [])
    text = memindex.set_field(text, 'updated', today)
    change = str(data.get('change') or '').strip()
    if change:
        lines = text.rstrip('\n').split('\n')
        try:
            start = lines.index('## Subsequent Changes')
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('## ')), len(lines))
            while end > start + 1 and not lines[end - 1].strip():
                end -= 1
            lines.insert(end, f'- {today}: {change}')
        except ValueError:
            lines += ['', '## Subsequent Changes', '', f'- {today}: {change}']
        text = '\n'.join(lines)
    write_text(path, text.rstrip('\n') + '\n')
    return name, f'Updated {path}'


def file_errors(name):
    errs, _ = memindex.lint(memindex.load_reports(memindex.ROOT))
    return [e for e in errs if e.startswith(f'{name}: ')]


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['note', 'report', 'update'])
    parser.add_argument('--json', required=True)
    parser.add_argument('--root', default=memindex.ROOT)
    parser.add_argument('--project', default='')
    parser.add_argument('--today', type=datetime.date.fromisoformat, default=datetime.date.today())
    args = parser.parse_args(argv)
    root = memindex.ROOT = os.path.expanduser(args.root)
    today = args.today.isoformat()
    try:
        data = json.loads(args.json)
        if not isinstance(data, dict):
            raise InputError('--json must be an object')
        with writer_lock(root):
            if args.action == 'note':
                print(note(root, data, today))
                return 0
            name, msg = report(root, data, today, args.project) if args.action == 'report' \
                else update(root, data, today)
        print(msg)
        errs = file_errors(name)
        for e in errs:
            print('ERROR', e)
        return 1 if errs else 0
    except (InputError, json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}')
        return 1


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, RuntimeError) as exc:
        print(f'ERROR: {exc}')
        sys.exit(1)
