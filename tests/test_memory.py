import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
INIT_SCRIPT = SCRIPTS / "init-memory.py"


class MemoryRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "memory"
        self.root.mkdir()

    def run_script(self, script, *args, env=None):
        process_env = os.environ.copy()
        process_env["PYTHONPATH"] = str(SCRIPTS)
        process_env["PYTHONUTF8"] = "1"
        process_env["GIT_CONFIG_GLOBAL"] = os.devnull
        process_env["GIT_CONFIG_NOSYSTEM"] = "1"
        if env:
            process_env.update({key: str(value) for key, value in env.items()})
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), *map(str, args)],
            cwd=PROJECT_ROOT,
            env=process_env,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )

    def test_initializer_creates_memory_and_is_idempotent_without_staging_user_files(self):
        (self.root / "existing.txt").write_text("keep me\n", encoding="utf-8")
        subprocess.run(["git", "init", str(self.root)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(self.root), "add", "existing.txt"],
            check=True,
            capture_output=True,
        )

        result = self.run_script(
            "init-memory.py", "--root", self.root, "--name", "Test User", "--email", "test@example.invalid"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "_meta/STATE.md").is_file())
        self.assertTrue((self.root / "_meta/LESSONS.md").is_file())
        self.assertTrue((self.root / "INDEX.md").is_file())

        status = subprocess.run(
            ["git", "-C", str(self.root), "status", "--porcelain"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        self.assertIn("A  existing.txt", status)
        tracked = subprocess.run(
            ["git", "-C", str(self.root), "show", "--pretty=format:", "--name-only", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.splitlines()
        self.assertNotIn("existing.txt", tracked)
        self.assertTrue({"INDEX.md", "_meta/STATE.md", "_meta/LESSONS.md"}.issubset(set(tracked)))

        count = subprocess.run(
            ["git", "-C", str(self.root), "rev-list", "--count", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        repeated = self.run_script("init-memory.py", "--root", self.root)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        after = subprocess.run(
            ["git", "-C", str(self.root), "rev-list", "--count", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        self.assertEqual(after, count)
        for key, expected in (("user.name", "Test User"), ("user.email", "test@example.invalid")):
            value = subprocess.run(
                ["git", "-C", str(self.root), "config", "--local", "--get", key],
                check=True, text=True, capture_output=True,
            ).stdout.strip()
            self.assertEqual(value, expected)

    def test_initializer_uses_memq_root_when_root_argument_is_omitted(self):
        default_root = self.root / "from-env"
        result = self.run_script("init-memory.py", env={"MEMQ_ROOT": default_root})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((default_root / "_meta/STATE.md").is_file())

    def test_default_root_is_reports_under_home_and_explicit_root_takes_priority(self):
        home = self.root / "home"
        home.mkdir()
        env = {"HOME": home, "USERPROFILE": home, "MEMQ_ROOT": ""}
        process_env = os.environ.copy()
        process_env.update({key: str(value) for key, value in env.items()})
        process_env.pop("MEMQ_ROOT")
        process_env["PYTHONUTF8"] = "1"
        process_env["GIT_CONFIG_GLOBAL"] = os.devnull
        process_env["GIT_CONFIG_NOSYSTEM"] = "1"
        result = subprocess.run(
            [sys.executable, str(INIT_SCRIPT)], env=process_env,
            text=True, encoding="utf-8", capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((home / "reports/_meta/STATE.md").is_file())
        indexed = self.run_script("memindex.py", env={"MEMQ_ROOT": home / "reports"})
        self.assertEqual(indexed.returncode, 0, indexed.stderr)
        linted = self.run_script("memindex.py", "--lint", env={"MEMQ_ROOT": home / "reports"})
        self.assertEqual(linted.returncode, 0, linted.stderr)
        explicit = self.root / "explicit"
        overridden = self.run_script("init-memory.py", "--root", explicit, env={"MEMQ_ROOT": self.root / "unused"})
        self.assertEqual(overridden.returncode, 0, overridden.stderr)
        self.assertTrue((explicit / "_meta/STATE.md").is_file())
        self.assertFalse((self.root / "unused").exists())

    def test_search_finds_english_and_chinese_content_and_uses_memq_root_default(self):
        (self.root / "bilingual-2025-01-01.md").write_text(
            "---\ntitle: Offline indexing\ndate: 2025-01-01\nupdated: 2025-01-01\n"
            "status: open\ntype: research\nsystems: [reports]\ntags: [search, 中文, 索引]\n"
            "summary: bilingual memory retrieval\n---\n离线搜索与中文索引验证。\n",
            encoding="utf-8",
        )
        env = {"MEMQ_ROOT": self.root}
        english = self.run_script("memq.py", "offline", env=env)
        chinese = self.run_script("memq.py", "中文", env=env)
        self.assertEqual(english.returncode, 0, english.stderr)
        self.assertEqual(chinese.returncode, 0, chinese.stderr)
        self.assertIn("bilingual-2025-01-01.md", english.stdout)
        self.assertIn("bilingual-2025-01-01.md", chinese.stdout)

    def test_search_covers_meta_notes_beyond_journal(self):
        meta = self.root / "_meta"
        meta.mkdir()
        (meta / "ENVIRONMENT.md").write_text(
            "# Environment\n\n> notes\n- Clash 代理端口由 7890 改为 7897\n", encoding="utf-8"
        )
        result = self.run_script("memq.py", "7897", "--root", self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{meta / 'ENVIRONMENT.md'}:4 · - Clash 代理端口由 7890 改为 7897", result.stdout)

    def test_report_without_systems_passes_lint(self):
        (self.root / "_meta").mkdir()
        (self.root / "free-2025-01-01.md").write_text(
            "---\ntitle: Free\ndate: 2025-01-01\nupdated: 2025-01-01\nstatus: done\ntype: fix\n"
            "tags: [a, b, c]\nsummary: ok\n---\nbody\n",
            encoding="utf-8",
        )
        result = self.run_script("memindex.py", "--lint", "--root", self.root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("0 errors, 0 warnings", result.stdout)

    def test_lesson_search_excludes_retired_entries_and_keeps_active_statuses(self):
        meta = self.root / "_meta"
        meta.mkdir()
        (meta / "LESSONS.md").write_text(
            "# LESSONS\n\n"
            "### L-0001 · convention · retired\n- 内容：部署使用 old-tool\n- source: retired-evidence.md\n\n"
            "### L-0002 · convention · candidate\n- 内容：部署尝试 new-tool\n\n"
            "### L-0003 · convention · promoted\n- 内容：部署核验输出\n",
            encoding="utf-8",
        )
        result = self.run_script("memq.py", "部署", "--root", self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("== Lessons (2 hits)", result.stdout)
        self.assertNotIn("L-0001", result.stdout)
        self.assertNotIn("old-tool", result.stdout)
        self.assertNotIn("retired-evidence.md", result.stdout)
        self.assertIn("L-0002 · convention · candidate", result.stdout)
        self.assertIn("L-0003 · convention · promoted", result.stdout)

    def test_lesson_search_shows_location_and_all_recorded_sources(self):
        meta = self.root / "_meta"
        meta.mkdir()
        lessons = meta / "LESSONS.md"
        lessons.write_text(
            "# LESSONS\n\nIntroductory text.\n\n"
            "### L-0001 · correction · retired\n- 内容：旧部署流程\n\n"
            "### L-0002 · correction · candidate\n- 内容：部署前核验目标\n"
            "- 来源：[[deployment-2026-01-01.md]]，用户原话\n"
            "- source: session-2026-01-02.jsonl:42\n\n"
            "### L-0003 · correction · promoted\n- content: deployment needs verification\n"
            "- sources:\n  - report-a.md:12\n  - report-b.md:23\n"
            "  Context note, not a source.\n  - not-a-source.md\n"
            "- hits: 2\n",
            encoding="utf-8",
        )
        result = self.run_script("memq.py", "部署", "deployment", "--root", self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{lessons}:8", result.stdout)
        self.assertIn(f"{lessons}:13", result.stdout)
        self.assertIn("[[deployment-2026-01-01.md]]，用户原话", result.stdout)
        self.assertIn("session-2026-01-02.jsonl:42", result.stdout)
        self.assertIn("report-a.md:12", result.stdout)
        self.assertIn("report-b.md:23", result.stdout)
        self.assertIn("deployment needs verification", result.stdout)
        candidate, promoted = result.stdout.split("- L-0003 · correction · promoted", 1)
        self.assertNotIn("report-a.md:12", candidate)
        self.assertNotIn("session-2026-01-02.jsonl:42", promoted)
        self.assertNotIn("source: hits: 2", result.stdout)
        self.assertNotIn("source: Context note", result.stdout)
        self.assertNotIn("source: - not-a-source.md", result.stdout)

    def test_lesson_search_marks_missing_sources_without_inventing_evidence(self):
        meta = self.root / "_meta"
        meta.mkdir()
        lessons = meta / "LESSONS.md"
        lessons.write_text(
            "# LESSONS\n\n"
            "### L-0001 · pitfall · candidate\n- 内容：部署需要回读\n"
            "- 来源：\n- hits: 1\n\n"
            "### L-0002 · pitfall · candidate\n- 内容：部署失败先诊断\n",
            encoding="utf-8",
        )
        result = self.run_script("memq.py", "部署", "--root", self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{lessons}:3", result.stdout)
        self.assertIn(f"{lessons}:8", result.stdout)
        self.assertEqual(result.stdout.count("source: (not recorded)"), 2)
        self.assertNotIn("source: hits: 1", result.stdout)

    def test_reference_links_are_relative_to_selected_root(self):
        ref_dir = self.root / "references"
        ref_dir.mkdir()
        (ref_dir / "guide.md").write_text(
            "---\ntitle: Guide\nupdated: 2025-01-01\nstatus: done\nsystems: [reports]\n"
            "summary: Reference\n---\n",
            encoding="utf-8",
        )
        result = self.run_script(
            "memindex.py", "--root", self.root, "--include-references", ref_dir
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        index = (self.root / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("[guide.md](<references/guide.md>)", index)

    def test_index_generation_also_lints_and_reports_errors(self):
        meta = self.root / "_meta"
        meta.mkdir()
        (meta / "STATE.md").write_text("- item\n" * 151 + "key sk-abcdefghijklmnopqrstuvwxyz123\n", encoding="utf-8")
        result = self.run_script("memindex.py", "--root", self.root)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("WARN  _meta/STATE.md: 152 lines exceeds 150", result.stdout)
        self.assertIn("ERROR _meta/STATE.md: Line 152: possible secret", result.stdout)
        self.assertTrue((self.root / "INDEX.md").is_file())

    def test_writer_lock_conflict_fails_without_changing_index(self):
        (self.root / "INDEX.md").write_text("original index\n", encoding="utf-8")
        lock = self.root / ".task-memory.lock"
        lock.mkdir()
        result = self.run_script("memindex.py", "--root", self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual((self.root / "INDEX.md").read_text(encoding="utf-8"), "original index\n")
        meta = self.root / "_meta"
        meta.mkdir()
        written = self.run_script("memwrite.py", "note", "--root", self.root, "--json", '{"kind": "global", "text": "x"}')
        self.assertNotEqual(written.returncode, 0)
        self.assertFalse((meta / "STATE.md").exists())
        initialized = self.run_script("init-memory.py", "--root", self.root)
        self.assertNotEqual(initialized.returncode, 0)
        self.assertNotIn("Traceback", initialized.stderr)
        self.assertFalse((self.root / ".git").exists())
        self.assertTrue(lock.is_dir())

    def init_root(self):
        result = self.run_script("init-memory.py", "--root", self.root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def write(self, action, data, *args):
        return self.run_script("memwrite.py", action, "--root", self.root, "--today", "2026-10-18",
                               "--json", json.dumps(data, ensure_ascii=False), *args)

    def save(self, *args):
        result = self.run_script("memsave.py", "--root", self.root, "--today", "2026-10-18", *args)
        return result, json.loads(result.stdout)

    def git_lines(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, text=True,
                              capture_output=True).stdout.splitlines()

    def test_notes_append_dedupe_replace_and_delete(self):
        self.init_root()
        env = self.root / "_meta/ENVIRONMENT.md"
        self.assertEqual(self.write("note", {"kind": "environment", "text": "Clash 端口 7897"}).returncode, 0)
        again = self.write("note", {"kind": "environment", "text": "Clash 端口 7897"})
        self.assertIn("Already recorded", again.stdout)
        self.assertEqual(env.read_text(encoding="utf-8").count("Clash"), 1)
        self.write("note", {"kind": "environment", "text": "Clash 端口 7899", "replaces": "7897"})
        self.assertIn("- 2026-10-18 · Clash 端口 7899\n", env.read_text(encoding="utf-8"))
        self.assertNotIn("7897", env.read_text(encoding="utf-8"))
        missing = self.write("note", {"kind": "environment", "text": "x", "replaces": "absent"})
        self.assertEqual(missing.returncode, 1)
        self.write("note", {"kind": "environment", "text": "", "replaces": "7899"})
        self.assertNotIn("Clash", env.read_text(encoding="utf-8"))
        self.write("note", {"kind": "preference", "text": "回复不要用表格"})
        lessons = (self.root / "_meta/LESSONS.md").read_text(encoding="utf-8")
        self.assertTrue(lessons.endswith("\n\n- 2026-10-18 · 回复不要用表格\n"))
        self.assertNotIn("(none yet)", lessons)

    def test_report_create_update_and_close(self):
        self.init_root()
        created = self.write("report", {"title": "calc handoff", "summary": "sub done", "status": "partial",
                                         "tags": ["calc"], "open": ["mul", "div"], "due": "2026-10-19"},
                             "--project", "calc")
        self.assertEqual(created.returncode, 0, created.stdout)
        report = self.root / "calc-handoff-2026-10-18.md"
        text = report.read_text(encoding="utf-8")
        for line in ('title: "calc handoff"', "date: 2026-10-18", "status: partial", 'project: calc',
                     'open: ["mul", "div"]', "due: 2026-10-19", "# calc handoff"):
            self.assertIn(line, text)
        second = self.write("report", {"title": "calc handoff", "summary": "again"})
        self.assertIn("calc-handoff-2-2026-10-18.md", second.stdout)
        self.write("update", {"file": "calc-handoff-2026-10-18.md", "status": "done", "change": "mul and div added"})
        self.write("update", {"file": "calc-handoff-2026-10-18.md", "change": "verified"})
        text = report.read_text(encoding="utf-8")
        self.assertIn("status: done", text)
        self.assertIn("open: []", text)
        self.assertIn("## Subsequent Changes\n\n- 2026-10-18: mul and div added\n- 2026-10-18: verified\n", text)
        bad = self.write("report", {"title": "x", "summary": "y", "status": "weird"})
        self.assertEqual(bad.returncode, 1)
        self.assertIn("status must be one of", bad.stdout)

    def test_report_with_open_same_title_in_project_is_refused(self):
        self.init_root()
        self.write("report", {"title": "calc 交接", "summary": "s", "status": "partial"}, "--project", "calc")
        dup = self.write("report", {"title": "Calc  交接", "summary": "again"}, "--project", "calc")
        self.assertEqual(dup.returncode, 1)
        self.assertIn('Use action "update" with file "calc-2026-10-18.md"', dup.stdout)
        self.assertFalse((self.root / "calc-2-2026-10-18.md").exists())
        other = self.write("report", {"title": "calc 交接", "summary": "web"}, "--project", "web")
        self.assertEqual(other.returncode, 0, other.stdout)
        self.write("update", {"file": "calc-2026-10-18.md", "status": "done"})
        after = self.write("report", {"title": "calc 交接", "summary": "new round"}, "--project", "calc")
        self.assertEqual(after.returncode, 0, after.stdout)

    def test_related_links_are_validated_and_merged(self):
        self.init_root()
        self.write("report", {"title": "first", "summary": "s"})
        self.write("report", {"title": "second", "summary": "s"})
        missing = self.write("report", {"title": "third", "summary": "s", "related": ["nope.md"]})
        self.assertEqual(missing.returncode, 1)
        self.assertIn("not found: nope.md", missing.stdout)
        made = self.write("report", {"title": "third", "summary": "s", "related": ["first-2026-10-18.md"]})
        self.assertEqual(made.returncode, 0, made.stdout)
        third = self.root / "third-2026-10-18.md"
        self.assertIn('related: ["first-2026-10-18.md"]', third.read_text(encoding="utf-8"))
        self.write("update", {"file": third.name, "related": ["first-2026-10-18.md", "second-2026-10-18.md"]})
        self.assertIn('related: ["first-2026-10-18.md", "second-2026-10-18.md"]', third.read_text(encoding="utf-8"))
        own = self.write("update", {"file": third.name, "related": [third.name]})
        self.assertEqual(own.returncode, 1)
        lint = self.run_script("memindex.py", "--lint", "--root", self.root)
        self.assertNotIn("related points to non-existent", lint.stdout)

    def test_save_fixes_derivable_fields_and_commits_without_the_lock(self):
        self.init_root()
        (self.root / "hand-2026-10-01.md").write_text(
            '---\nstatus: open\nsummary: "hand edited"\n---\n\n# Hand title\n', encoding="utf-8")
        result, out = self.save("--project", "demo")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(out["committed"])
        text = (self.root / "hand-2026-10-01.md").read_text(encoding="utf-8")
        for line in ('title: "Hand title"', "date: 2026-10-01", "updated: 2026-10-18", 'project: demo'):
            self.assertIn(line, text)
        self.assertEqual(self.git_lines("status", "--porcelain"), [])
        self.assertNotIn(".task-memory.lock/owner", self.git_lines("ls-files"))
        self.assertIn("hand-2026-10-01.md", (self.root / "INDEX.md").read_text(encoding="utf-8"))
        self.assertEqual(self.save()[1]["changed"], [])
        (self.root / "hand-2026-10-01.md").unlink()
        result, out = self.save()
        self.assertTrue(out["committed"], out)
        self.assertNotIn("hand-2026-10-01.md", self.git_lines("ls-files"))
        self.assertEqual(self.git_lines("status", "--porcelain"), [])

    def test_save_blocks_only_on_errors_in_changed_files(self):
        self.init_root()
        (self.root / "old-2026-01-01.md").write_text("no frontmatter\n", encoding="utf-8")
        self.save()
        self.git_lines("add", "-A")
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "old"], check=True,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})
        self.write("note", {"kind": "global", "text": "renew domain"})
        result, out = self.save()
        self.assertTrue(out["committed"], out)
        self.assertIn("old-2026-01-01.md: Missing frontmatter", out["warnings"])
        stats = self.root / "_meta/STATE.md"
        stats.write_text(stats.read_text(encoding="utf-8") + "- key sk-abcdefghijklmnopqrstuvwxyz123\n", encoding="utf-8")
        result, out = self.save()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(out["committed"])
        self.assertIn("_meta/STATE.md: Line", out["errors"][0])
        self.assertIn("M _meta/STATE.md", self.git_lines("status", "--porcelain")[0])

    def test_card_shows_project_work_first_and_respects_budget(self):
        self.init_root()
        self.write("report", {"title": "mine", "summary": "s", "open": ["finish it"], "due": "2026-10-01"},
                   "--project", "calc")
        self.write("report", {"title": "theirs", "summary": "s", "open": ["x"]}, "--project", "web")
        self.write("report", {"title": "loose", "summary": "s", "status": "plan"})
        self.write("report", {"title": "closed", "summary": "s", "status": "done"}, "--project", "calc")
        self.write("note", {"kind": "preference", "text": "older preference"})
        self.write("note", {"kind": "preference", "text": "newer preference"})
        self.write("note", {"kind": "global", "text": "[due 2026-10-10] renew domain"})
        card = self.run_script("memcard.py", "--root", self.root, "--project", "calc", "--today", "2026-10-18").stdout
        self.assertIn("Open work in calc:\n- ⚠️ overdue 2026-10-01 · mine [open] — open: finish it", card)
        self.assertNotIn("closed", card)
        self.assertNotIn("theirs", card)
        self.assertIn("Other projects with open work: web (1)", card)
        self.assertIn("loose [plan]", card)
        self.assertLess(card.index("newer preference"), card.index("older preference"))
        self.assertIn("⚠️ overdue 2026-10-10 renew domain", card)
        for i in range(60):
            self.write("note", {"kind": "preference", "text": f"preference number {i} with some padding text"})
        small = self.run_script("memcard.py", "--root", self.root, "--project", "calc", "--budget", "600").stdout
        self.assertLessEqual(len(small.strip()), 600)
        self.assertIn("mine", small)
        self.assertRegex(small, r"\(\+\d+ more; use memory search\)")

    def test_stale_writer_lock_is_recovered(self):
        (self.root / "_meta").mkdir()
        lock = self.root / ".task-memory.lock"
        lock.mkdir()
        dead = subprocess.Popen([sys.executable, "-c", ""])
        dead.wait()
        (lock / "owner").write_text(f"{dead.pid}\n{socket.gethostname()}\n", encoding="utf-8")
        written = self.write("note", {"kind": "global", "text": "after dead owner"})
        self.assertEqual(written.returncode, 0, written.stdout)
        self.assertFalse(lock.exists())
        lock.mkdir()
        old = time.time() - 3600
        os.utime(lock, (old, old))
        written = self.write("note", {"kind": "global", "text": "after old lock"})
        self.assertEqual(written.returncode, 0, written.stdout)

    def test_search_reads_one_line_lessons_and_flags_old_notes(self):
        meta = self.root / "_meta"
        meta.mkdir()
        (meta / "LESSONS.md").write_text(
            "# LESSONS\n\n### L-0001 · convention · retired\n- content: 部署 legacy-tool\n\n"
            "## Other\n\n- 2026-10-01 · 部署 new-tool 前先备份\n", encoding="utf-8")
        (meta / "ENVIRONMENT.md").write_text("- 2025-01-01 · 部署 host 端口 22\n", encoding="utf-8")
        out = self.run_script("memq.py", "部署", "--root", self.root, "--today", "2026-10-18").stdout
        self.assertIn("部署 new-tool 前先备份", out)
        self.assertNotIn("legacy-tool", out)
        self.assertIn("ENVIRONMENT.md:1 · - 2025-01-01 · 部署 host 端口 22 ⚠️ 655 days old", out)

    def test_atomic_replace_failure_preserves_original_text(self):
        sys.path.insert(0, str(SCRIPTS))
        try:
            import memoryio

            target = self.root / "atomic.txt"
            target.write_text("original\n", encoding="utf-8")
            with patch.object(memoryio.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    memoryio.write_text(target, "replacement\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
            self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["atomic.txt"])
        finally:
            sys.path.remove(str(SCRIPTS))
            sys.modules.pop("memoryio", None)


if __name__ == "__main__":
    unittest.main()
