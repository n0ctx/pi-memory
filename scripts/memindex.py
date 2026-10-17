#!/usr/bin/env python3
"""memindex — generates INDEX.md from report frontmatter and runs lint. Standard library only.

Usage:
  memindex.py            Generate INDEX.md (no-op if unchanged, idempotent), then lint (exit code 1 if errors)
  memindex.py --lint     Lint only, no write (exit code 1 if errors)
  memindex.py --check    Exit code 1 if INDEX.md is out of sync with frontmatter (no write)
  --root DIR             Reports root directory (default $MEMQ_ROOT or ~/reports)

Frontmatter uses a restricted YAML subset: `key: scalar`, `key: "quoted"`, `key: [a, "b"]`,
`key:` followed by `  - item` block lists; `#` comments. Fields are listed in README.md.
"""
import json, os, re, sys

from pathlib import Path
from memoryio import ROOT, write_text, writer_lock
REF_DIR = None  # Set by --include-references for reference document directory
STATUSES = ['open', 'partial', 'plan', 'done', 'abandoned', 'superseded']
TYPES = ['fix', 'feature', 'audit', 'design', 'ops', 'handoff', 'research']
REQUIRED = ['title', 'date', 'updated', 'status', 'summary']
LIST_KEYS = ['systems', 'tags', 'open', 'related', 'attachments']
UNCLOSED = ('open', 'partial', 'plan')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
NAME_RE = re.compile(r'^.+-(\d{4}-\d{2}-\d{2})\.md$')
SECRET_PATTERNS = [
    ('telegram-bot-token', re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b')),
    ('openai/anthropic-key', re.compile(r'\bsk-[A-Za-z0-9_-]{20,}')),
    ('github-token', re.compile(r'\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{20,}')),
    ('bearer-literal', re.compile(r'Bearer\s+[A-Za-z0-9._~+/-]{20,}')),
    ('aws-key', re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ('private-key', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
]

# Line counts above which a _meta file is flagged (warning only; STATE and LESSONS are shown in every Pi request)
ROW_LIMITS = {
    'STATE.md': 150,
    'LESSONS.md': 250,
    'JOURNAL-': 500,
}


def _scalar(v):
    v = v.strip()
    if v.startswith('"'):
        return json.loads(v)
    if v.startswith("'") and v.endswith("'") and len(v) >= 2:
        return v[1:-1].replace("''", "'")
    v = re.sub(r'\s+#.*$', '', v)
    return v


def _split_flow(s):
    items, cur, q, i = [], '', None, 0
    while i < len(s):
        c = s[i]
        if q:
            cur += c
            if c == '\\' and q == '"' and i + 1 < len(s):
                cur += s[i + 1]; i += 1
            elif c == q:
                q = None
        elif c in '"\'':
            q = c; cur += c
        elif c == ',':
            items.append(cur); cur = ''
        else:
            cur += c
        i += 1
    if cur.strip():
        items.append(cur)
    return [_scalar(x) for x in items if x.strip()]


def split_frontmatter(text):
    """Return (frontmatter text | None, body, body start line number [1-based])."""
    if not text.startswith('---\n'):
        return None, text, 1
    end = text.find('\n---\n', 3)
    if end < 0:
        return None, text, 1
    fm = text[4:end + 1]
    body = text[end + 5:]
    start = text[:end + 5].count('\n') + 1
    if body.startswith('\n'):
        body = body[1:]; start += 1
    return fm, body, start


def parse_frontmatter(fm):
    data, key = {}, None
    for n, raw in enumerate(fm.splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        m = re.match(r'^\s+-\s+(.*)$', line)
        if m and key is not None and isinstance(data.get(key), list):
            data[key].append(_scalar(m.group(1)))
            continue
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*):(?:\s+(.*))?$', line)
        if not m:
            raise ValueError(f'frontmatter line {n} unparseable: {raw!r}')
        key, val = m.group(1), (m.group(2) or '').strip()
        v_nc = re.sub(r'\s+#.*$', '', val) if not val.startswith(('"', "'")) else val
        if v_nc == '':
            data[key] = []
        elif v_nc.startswith('['):
            if not v_nc.endswith(']'):
                raise ValueError(f'frontmatter line {n} unclosed list')
            data[key] = _split_flow(v_nc[1:-1])
        else:
            data[key] = _scalar(val)
    for k in LIST_KEYS:
        if k in data and not isinstance(data[k], list):
            data[k] = [data[k]] if data[k] else []
    return data


def format_value(v):
    if isinstance(v, list):
        return '[' + ', '.join(json.dumps(str(x), ensure_ascii=False) for x in v) + ']'
    v = str(v)
    return v if DATE_RE.match(v) or re.fullmatch(r'[a-z]+', v) else json.dumps(v, ensure_ascii=False)


def set_field(text, key, value):
    """Set one frontmatter field (replacing its block list items, if any); other lines stay unchanged."""
    lines = text.split('\n')
    end = lines.index('---', 1)
    new = f'{key}: {format_value(value)}'
    for i in range(1, end):
        if re.match(rf'^{re.escape(key)}:(\s|$)', lines[i]):
            j = i + 1
            while j < end and re.match(r'^\s+-\s', lines[j]):
                j += 1
            lines[i:j] = [new]
            return '\n'.join(lines)
    lines.insert(end, new)
    return '\n'.join(lines)


def load_reports(root=ROOT):
    reps = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if not name.endswith('.md') or name == 'INDEX.md' or not os.path.isfile(p):
            continue
        text = open(p, encoding='utf-8').read()
        fm, body, start = split_frontmatter(text)
        rep = {'file': name, 'path': p, 'text': text, 'body': body, 'body_start': start, 'fm': None, 'error': None}
        if fm is None:
            rep['error'] = 'Missing frontmatter'
        else:
            try:
                rep['fm'] = parse_frontmatter(fm)
            except (ValueError, json.JSONDecodeError) as e:
                rep['error'] = f'frontmatter parse error: {e}'
        reps.append(rep)
    return reps


def load_references(ref_dir):
    """Load reference documents (type: reference): have frontmatter, read-only, independent of task reports."""
    refs = []
    if not ref_dir or not os.path.isdir(ref_dir):
        return refs
    for name in sorted(os.listdir(ref_dir)):
        p = os.path.join(ref_dir, name)
        if not name.endswith('.md') or not os.path.isfile(p):
            continue
        text = open(p, encoding='utf-8').read()
        fm, body, start = split_frontmatter(text)
        ref = {'file': name, 'path': p, 'text': text, 'body': body, 'body_start': start,
               'fm': None, 'error': None, 'ref_dir': ref_dir}
        if fm is None:
            continue  # .md files without frontmatter are not reference docs
        try:
            ref['fm'] = parse_frontmatter(fm)
        except (ValueError, json.JSONDecodeError) as e:
            ref['error'] = f'frontmatter parse error: {e}'
        refs.append(ref)
    return refs


def g(rep, key, default=''):
    fm = rep.get('fm') or {}
    v = fm.get(key, default)
    return v if v is not None else default


def sort_key(rep):
    st = g(rep, 'status')
    sunk = 1 if st in ('superseded', 'abandoned') else 0
    # updated/date descending, filename ascending
    return (sunk, _neg(g(rep, 'updated')), _neg(g(rep, 'date')), rep['file'])


def _neg(s):
    return tuple(-ord(c) for c in str(s))


def cell(s):
    return str(s).replace('|', '\\|').replace('\n', ' ')


def link(name):
    return f'[{cell(name)}](<{name}>)'


def reference_link(rep):
    try:
        target = Path(os.path.relpath(rep['path'], ROOT)).as_posix()
    except ValueError:
        target = Path(rep['path']).resolve().as_uri()
    return f"[{cell(rep['file'])}](<{target}>)"


def is_unclosed(rep):
    return g(rep, 'status') in UNCLOSED or bool(g(rep, 'open', []))


def render_index(reps, refs=None):
    ok = sorted([r for r in reps if r['fm']], key=sort_key)
    bad = [r for r in reps if not r['fm']]
    unclosed = [r for r in ok if is_unclosed(r)]
    journals = sorted(f for f in os.listdir(os.path.join(ROOT, '_meta')) if f.startswith('JOURNAL-')) \
        if os.path.isdir(os.path.join(ROOT, '_meta')) else []
    out = ['<!-- Auto-generated by pi-memory/scripts/memindex.py — do not edit manually; re-run memindex after editing report frontmatter. -->',
           '# Reports INDEX', '',
           f'{len(ok) + len(bad)} total reports · {len(unclosed)} unclosed']
    if refs:
        out[2] = f'{out[2]} · {len(refs)} reference documents'
    out.append(f'· Metadata files: [LESSONS](<_meta/LESSONS.md>)')
    for j in journals:
        out[-1] = f'{out[-1]} · [{j[:-3]}](<_meta/{j}>)'
    out += ['', '## Reference Documents', '']
    if refs:
        out += ['| File | Updated | Status | Systems | Summary |', '|---|---|---|---|---|']
        for r in sorted(refs, key=lambda x: g(x, 'updated')):
            ref_link = reference_link(r)
            out.append(f"| {ref_link} | {g(r, 'updated')} | {g(r, 'status')} | "
                       f"{cell(', '.join(g(r, 'systems', [])))} | {cell(g(r, 'summary'))} |")
    else:
        out.append('(none)')
    out += ['', '## Unclosed', '']
    if unclosed:
        out += ['| File | Status | Updated | Open Items |', '|---|---|---|---|']
        for r in unclosed:
            items = '; '.join(g(r, 'open', [])) or '(see body)'
            out.append(f"| {link(r['file'])} | {g(r, 'status')} | {g(r, 'updated')} | {cell(items)} |")
    else:
        out.append('(none)')
    out += ['', '## All Reports', '', '| File | Date | Updated | Status | Type | Systems | Summary |', '|---|---|---|---|---|---|---|']
    for r in ok:
        out.append(f"| {link(r['file'])} | {g(r, 'date')} | {g(r, 'updated')} | {g(r, 'status')} | {g(r, 'type')} | "
                   f"{cell(', '.join(g(r, 'systems', [])))} | {cell(g(r, 'summary'))} |")
    for r in bad:
        out.append(f"| {link(r['file'])} | | | ⚠️ {cell(r['error'])} | | | |")
    out += ['', '## Grouped by System', '']
    groups = {}
    for r in ok:
        for s in g(r, 'systems', []):
            groups.setdefault(s, []).append(r)
    if refs:
        for r in refs:
            for s in g(r, 'systems', []):
                groups.setdefault(s, []).append(r)
    for s in sorted(groups):
        out.append(f'### {s}')
        for r in groups[s]:
            if 'ref_dir' in r:
                ref_link = reference_link(r)
                out.append(f"- {ref_link} · reference · {g(r, 'updated')}")
            else:
                out.append(f"- {link(r['file'])} · {g(r, 'status')} · {g(r, 'updated')}")
        out.append('')
    return '\n'.join(out).rstrip('\n') + '\n'


def scan_secrets(text):
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for name, rx in SECRET_PATTERNS:
            if rx.search(line):
                hits.append((n, name))
    return hits


def lint(reps):
    errs, warns = [], []
    names = {r['file'] for r in reps}
    for r in reps:
        f = r['file']
        E = lambda m: errs.append(f'{f}: {m}')
        W = lambda m: warns.append(f'{f}: {m}')
        for n, kind in scan_secrets(r['text']):
            E(f'Line {n}: possible secret ({kind})')
        if not r['fm']:
            E(r['error']); continue
        fm = r['fm']
        for k in REQUIRED:
            if k not in fm or fm[k] in ('', []):
                E(f'Missing required field {k}')
        st, ty = fm.get('status', ''), fm.get('type', '')
        if st and st not in STATUSES: E(f'Invalid status: {st}')
        if ty and ty not in TYPES: E(f'Invalid type: {ty}')
        for k in ('date', 'updated'):
            if fm.get(k) and not DATE_RE.match(str(fm[k])): E(f'{k} not YYYY-MM-DD: {fm[k]}')
        if DATE_RE.match(str(fm.get('date', ''))) and DATE_RE.match(str(fm.get('updated', ''))) \
                and fm['updated'] < fm['date']:
            E('updated is before date')
        if st == 'superseded' and not fm.get('superseded_by'): E('status=superseded but superseded_by is empty')
        if fm.get('due') and not DATE_RE.match(str(fm['due'])): E(f"due not YYYY-MM-DD: {fm['due']}")
        if st in ('done', 'superseded', 'abandoned') and fm.get('open'):
            W(f'status={st} but open is non-empty (contradiction)')
        if len(str(fm.get('summary', ''))) > 80: W(f"summary exceeds 80 characters ({len(str(fm['summary']))})")
        if len(fm.get('tags', [])) < 3: W('fewer than 3 tags')
        m = NAME_RE.match(f)
        if not m: W('filename does not match topic-YYYY-MM-DD.md')
        elif m.group(1) != str(fm.get('date', '')): W(f"filename date {m.group(1)} ≠ date {fm.get('date')}")
        for k in ('related', 'attachments'):
            for x in fm.get(k, []):
                if not os.path.exists(os.path.join(ROOT, x)): W(f'{k} points to non-existent path: {x}')
        sb = fm.get('superseded_by')
        if sb and sb not in names: W(f'superseded_by points to non-existent report: {sb}')
    meta = os.path.join(ROOT, '_meta')
    if not os.path.isdir(meta):
        warns.append('_meta/ does not exist')
    else:
        for name in sorted(os.listdir(meta)):
            p = os.path.join(meta, name)
            if not os.path.isfile(p) or not name.endswith('.md'):
                continue
            text = open(p, encoding='utf-8').read()
            for n, kind in scan_secrets(text):
                errs.append(f'_meta/{name}: Line {n}: possible secret ({kind})')
            limit = next((v for k, v in ROW_LIMITS.items() if name.startswith(k)), None)
            count = len(text.splitlines())
            if limit and count > limit:
                warns.append(f'_meta/{name}: {count} lines exceeds {limit}; consider removing outdated lines')
    return errs, warns


def main(argv):
    global ROOT, REF_DIR
    if '--root' in argv:
        ROOT = os.path.expanduser(argv[argv.index('--root') + 1])
    if '--include-references' in argv:
        REF_DIR = argv[argv.index('--include-references') + 1]
    if '--lint' in argv or '--check' in argv:
        return run(argv)
    with writer_lock(ROOT):
        return run(argv)


def run(argv):
    refs = load_references(REF_DIR) if REF_DIR else []
    reps = load_reports(ROOT)
    idx = os.path.join(ROOT, 'INDEX.md')
    if '--lint' in argv:
        return report_lint(reps)
    new = render_index(reps, refs)
    old = open(idx, encoding='utf-8').read() if os.path.exists(idx) else None
    if '--check' in argv:
        print('INDEX.md is up to date' if old == new else 'INDEX.md is stale, re-run memindex')
        return 0 if old == new else 1
    if old == new:
        print(f'INDEX.md unchanged ({len(reps)} reports{f" + {len(refs)} references" if refs else ""})')
    else:
        write_text(idx, new)
        print(f'INDEX.md updated ({len(reps)} reports{f" + {len(refs)} references" if refs else ""})')
    return report_lint(reps)


def report_lint(reps):
    errs, warns = lint(reps)
    for e in errs: print('ERROR', e)
    for w in warns: print('WARN ', w)
    print(f'lint: {len(reps)} reports, {len(errs)} errors, {len(warns)} warnings')
    return 1 if errs else 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
