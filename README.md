# pi-memory

English | [简体中文](README.zh-CN.md)

Cross-session memory for [Pi](https://pi.dev). Markdown files in a local git repository; Python 3 standard library only.

## Requirements

- Pi 1.1.0 or later
- Python 3 (`python3` on macOS and Linux, `py -3` on Windows)
- git

## Install

```bash
pi install git:github.com/n0ctx/pi-memory   # update later with: pi update git:github.com/n0ctx/pi-memory
pi install /path/to/pi-memory               # or a local checkout, loaded in place
```

Nothing else is required. The memory root is `$MEMQ_ROOT`, default `~/reports`; it is created and put under git on the first Pi session.

## How it works

The extension does the bookkeeping so the agent only decides what is worth remembering.

- **Before each request** it adds a memory card (at most about 2,000 characters) to the system prompt: unclosed reports of the current project (the git repository name, else the directory name) with overdue items marked, one-line preferences and corrections, unclosed reports without a project, global notes, and a count of open work in other projects.
- **The `memory` tool** gives the agent four actions: `search`, `note` (a one-line fact: `environment`, `preference`, or `global`), `report` (create a task record), and `update` (change a report and log what changed). Filenames, dates, project, and frontmatter are generated; `related` links other reports. Creating a report is refused while an unclosed report of the same project has the same title, so the agent updates that one instead.
- **At the end of each run** it fills derivable fields in changed reports (title, date, updated, project), lints the changed files, regenerates `INDEX.md`, and commits everything under the root. Problems in files that were not changed are only warnings.
- **One follow-up at most per run**: the agent gets one more turn to fix or record when changed files fail lint (for example a possible secret); when the run changed machine configuration (installs, services, home dotfiles) or the user's message reads like a lasting preference and nothing was recorded; or when project files were edited, the user's message leaves part of the work for later ("the rest tomorrow", "for now"), and no report was created or updated. Both wording checks recognize English and Chinese.
- **Subagents** (`PI_SUBAGENT_DEPTH` set) get neither the card nor the tool; the main agent records their results.
- A writer lock (`<root>/.task-memory.lock`) serializes automatic writes; a lock left by a process that has exited, or older than ten minutes, is taken over.

## Layout

| Path under the root | Contents |
|---|---|
| `<topic>-YYYY-MM-DD.md` | Report: a decision and its rationale, or a handoff of unfinished work |
| `_meta/STATE.md` | Global notes, one line each (`[due YYYY-MM-DD]` marks a deadline) |
| `_meta/LESSONS.md` | Preferences and corrections, one line each |
| `_meta/ENVIRONMENT.md` | Machine facts: tools, ports, services, models, config paths |
| `INDEX.md` | Generated catalog of reports |

Report frontmatter (restricted YAML: scalars, double-quoted strings, `[a, "b"]` lists, `#` comments):

```yaml
---
title: "calc.py handoff"
date: 2026-10-18          # creation date; matches the filename
updated: 2026-10-18
status: partial           # open | partial | plan | done | abandoned | superseded
type: handoff             # optional: fix | feature | audit | design | ops | handoff | research
project: calc             # optional; reports without one are shown in every project
tags: [calc, div, ValueError]
summary: "sub done; mul and div remain"
open: ["mul", "div raises ValueError on zero"]
due: 2026-10-19           # optional
related: []               # optional: other report filenames
superseded_by: ""         # required when status is superseded
---
```

Older memory roots keep working: `### L-NNNN` lesson entries and JOURNAL files are still searched, and non-retired lessons appear on the card.

## Scripts

All take `--root` (default `$MEMQ_ROOT`, then `~/reports`). Use `python3`, `python`, or `py -3`.

| Script | Purpose |
|---|---|
| `memcard.py [--project P]` | Print the memory card |
| `memq.py <keywords...> [--project P]` | Search reports, lessons, and `_meta` notes |
| `memwrite.py <note\|report\|update> --json '{...}'` | Structured write used by the `memory` tool |
| `memsave.py [--project P]` | Fix, lint, index, and commit pending changes |
| `memindex.py [--lint\|--check]` | Regenerate INDEX and lint the whole root |
| `init-memory.py` | Create the root and its git repository |

## Verification

```bash
python3 -m unittest discover -s tests -v
node --test tests/*.test.ts
```
