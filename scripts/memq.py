#!/usr/bin/env python3
"""memq — pi-memory search (report frontmatter + body + LESSONS + other _meta notes such as JOURNAL and ENVIRONMENT). Standard library only.

Usage: memq.py <keywords...> [-n 5] [--root DIR] [--project NAME] [--today YYYY-MM-DD]
  Example: memq.py Qwen offline 8080      memq.py slash menu setMyCommands

Scoring: keyword hits in title/tags/filename/systems/summary get high weight, body hits low weight (capped by count);
bonus for all keywords matching and full-phrase matches; status in {open,partial,plan} and reports of --project get a boost,
superseded/abandoned are down-weighted. Dated note lines (`- YYYY-MM-DD · ...`) older than 90 days are marked for verification.
Output: up to N reports (path · status · updated · staleness mark · summary + ≤2 hit snippets),
N non-retired lessons (status, content, path:line, recorded sources), N matching _meta note lines (path:line).
"""
import datetime, math, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memindex  # noqa: E402

FIELD_W = {'title': 5.0, 'tags': 5.0, 'file': 4.0, 'systems': 4.0, 'summary': 3.0}
BODY_W, BODY_CAP = 0.6, 12


def norm(s):
    return re.sub(r'[-_/]+', ' ', str(s).lower())


def token_weight(t):
    # Very short pure-ASCII words (like out, ok) have low discriminative power
    return 0.3 if re.fullmatch(r'[a-z0-9 ]{1,3}', t) else 1.0


def fields(rep):
    fm = rep['fm'] or {}
    return {'title': norm(fm.get('title', '')), 'tags': norm(' | '.join(fm.get('tags', []))),
            'file': norm(rep['file']), 'systems': norm(' '.join(fm.get('systems', []))),
            'summary': norm(fm.get('summary', ''))}


def score_text(tokens, phrase, fl, body):
    score, matched = 0.0, 0
    for t in tokens:
        w = token_weight(t)
        s = sum(FIELD_W[k] for k, v in fl.items() if t in v)
        c = body.count(t)
        s += BODY_W * math.log2(1 + min(c, BODY_CAP)) * 2 if c else 0
        if s:
            matched += 1
        score += w * s
    if not score:
        return 0.0, 0
    if len(tokens) > 1:
        score *= (matched / len(tokens)) ** 2
        if matched == len(tokens):
            score += 3
        if phrase and any(phrase in v for v in fl.values()):
            score += 6
        elif phrase and phrase in body:
            score += 3
    return score, matched


def snippets(rep, tokens, limit=2):
    out = []
    for i, line in enumerate(rep['body'].splitlines()):
        nl = norm(line)
        hit = [t for t in tokens if t in nl]
        if hit:
            out.append((len(hit), -i, i + rep['body_start'], line.strip()))
    out.sort(reverse=True)
    res = []
    for _, _, ln, text in out[:limit]:
        nl = norm(text)
        pos = min(nl.find(t) for t in tokens if t in nl)
        start = max(0, pos - 30)
        frag = text[start:start + 90]
        res.append(f'L{ln}: ' + ('…' if start else '') + frag + ('…' if start + 90 < len(text) else ''))
    return res


def stale_mark(updated, today):
    try:
        d = (today - datetime.date.fromisoformat(str(updated))).days
    except ValueError:
        return '⚠️ updated missing'
    if d > 90:
        return f'⚠️ {d} days ago, facts may have drifted — verify before citing'
    if d > 30:
        return f'({d} days ago)'
    return ''


def lessons(root):
    p = os.path.join(root, '_meta', 'LESSONS.md')
    if not os.path.exists(p):
        return []
    text = open(p, encoding='utf-8').read()
    headers = list(re.finditer(r'(?m)^### L-\d{4}[^\n]*', text))
    res = []
    for i, header in enumerate(headers):
        head = header.group()[4:].strip()
        if head.endswith(' · retired'):
            continue
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        b = text[header.start():end].split('\n---')[0].split('\n## ')[0].strip()
        # Legacy entries may use Chinese field labels (内容 = content, 来源 = source) and full-width colons.
        content = re.search(r'(?mi)^- (?:内容|content)\s*[:：](.*)$', b)
        sources, in_source = [], False
        for line in b.splitlines()[1:]:
            source = re.match(r'^- (?:来源|sources?)\s*[:：]\s*(.*)$', line, re.I)
            if source:
                in_source = True
                if source.group(1).strip():
                    sources.append(source.group(1).strip())
            elif in_source and re.match(r'^[ \t]+-[ \t]+\S', line):
                sources.append(line.strip())
            elif line.strip():
                in_source = False
        lineno = text.count('\n', 0, header.start()) + 1
        res.append((head, content.group(1).strip() if content else '', norm(b), lineno, sources))
    return res


def note_lines(root, names=None):
    """Content lines of _meta notes (ENVIRONMENT, STATE, JOURNAL, one-line LESSONS entries, ...);
    skips headings, > notes, placeholders, and legacy `### L-NNNN` lesson blocks (searched by lessons())."""
    d = os.path.join(root, '_meta')
    if not os.path.isdir(d):
        return []
    res = []
    for f in sorted(os.listdir(d), reverse=True):
        if not f.endswith('.md') or names is not None and f not in names:
            continue
        path = os.path.join(d, f)
        in_block = False
        for n, line in enumerate(open(path, encoding='utf-8'), 1):
            line = line.strip()
            if line.startswith('#') or line == '---':
                in_block = line.startswith('### L-')
                continue
            if line and not in_block and not line.startswith('>') and not re.fullmatch(r'\(.*\)', line):
                res.append((path, n, line))
    return res


def note_age_mark(line, today):
    m = re.match(r'- (\d{4}-\d{2}-\d{2})\b', line)
    if not m:
        return ''
    try:
        d = (today - datetime.date.fromisoformat(m.group(1))).days
    except ValueError:
        return ''
    return f' ⚠️ {d} days old — verify before relying on it' if d > 90 else ''


def main(argv):
    n, root, project, today, words = 5, memindex.ROOT, '', datetime.date.today(), []
    it = iter(argv)
    for a in it:
        if a == '-n': n = int(next(it))
        elif a == '--root': root = os.path.expanduser(next(it))
        elif a == '--project': project = next(it)
        elif a == '--today': today = datetime.date.fromisoformat(next(it))
        elif a in ('-h', '--help'): print(__doc__); return 0
        else: words.append(a)
    if not words:
        print(__doc__); return 2
    memindex.ROOT = root
    tokens = [t for t in dict.fromkeys(norm(w).strip() for w in words) if t]
    phrase = ' '.join(tokens) if len(tokens) > 1 else ''
    reps = memindex.load_reports(root)
    scored = []
    for r in reps:
        fl = fields(r) if r['fm'] else {'file': norm(r['file'])}
        fl = {k: v for k, v in fl.items()}
        s, m = score_text(tokens, phrase, {k: fl.get(k, '') for k in FIELD_W}, norm(r['body']))
        if not s:
            continue
        st = memindex.g(r, 'status')
        if st in memindex.UNCLOSED: s *= 1.15
        elif st in ('superseded', 'abandoned'): s *= 0.6
        if project and memindex.g(r, 'project') == project: s *= 1.2
        scored.append((s, r))
    scored.sort(key=lambda x: (-x[0], x[1]['file']))
    print(f'== Reports ({len(scored)}/{len(reps)} hits, showing top {min(n, len(scored))}) — keywords: {" / ".join(tokens)}')
    for i, (s, r) in enumerate(scored[:n], 1):
        mark = stale_mark(memindex.g(r, 'updated'), today)
        parts = [r['path'], memindex.g(r, 'status') or '?', str(memindex.g(r, 'updated') or '?')]
        if memindex.g(r, 'project'): parts.append('@' + str(memindex.g(r, 'project')))
        if mark: parts.append(mark)
        parts.append(memindex.g(r, 'summary') or r['error'] or '')
        print(f'{i}. ' + ' · '.join(parts) + f'  [{s:.1f}]')
        for sn in snippets(r, tokens):
            print('     ' + sn)
    ls = []
    for head, content, text, lineno, sources in lessons(root):
        s = sum(token_weight(t) for t in tokens if t in text)
        if s: ls.append((s, head, content, lineno, sources))
    ls.sort(key=lambda x: -x[0])
    print(f'== Lessons ({len(ls)} hits)')
    for s, head, content, lineno, sources in ls[:n]:
        print(f'- {head} · {content[:120]}' + ('…' if len(content) > 120 else ''))
        print(f'     {os.path.join(root, "_meta", "LESSONS.md")}:{lineno}')
        for source in sources or ['(not recorded)']:
            print(f'     source: {source}')
    ns = []
    for path, lineno, line in note_lines(root):
        nl = norm(line)
        s = sum(token_weight(t) for t in tokens if t in nl)
        if s: ns.append((s, path, lineno, line))
    ns.sort(key=lambda x: -x[0])
    print(f'== Notes ({len(ns)} hits)')
    for s, path, lineno, line in ns[:n]:
        print(f'- {path}:{lineno} · {line[:200]}' + ('…' if len(line) > 200 else '') + note_age_mark(line, today))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except OSError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
