"""Regression tests for the 1.0.3 changes (one or more per fix)."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import skill_audit as sa

GOOD = """---
name: {name}
description: Review a pull request for correctness and test coverage. Use when the user asks for a code review.
license: MIT
---

# Review

Walk through the diff file by file and record one finding per line.
Stop and ask the author when a check cannot be answered from the diff alone.
"""


def korean_text(lines=40):
    """Text that is more than 80% Hangul, with a little ASCII (spaces, digits, newlines)."""
    row = "가나다라마바사아자차카타파하대한민국"           # includes U+AC00 and U+B300 (low byte 0x00)
    text = "".join("%s %d\n" % (row, i % 10) for i in range(lines))
    hangul = sum(1 for c in text if "가" <= c <= "힣")
    assert hangul / len(text) >= 0.8, hangul / len(text)
    return text


def rule_102(raw):
    """The 1.0.2 heuristic (zero-byte ratio only), kept here to show why it missed."""
    head = raw[:8192]
    even, odd = head[0::2].count(0), head[1::2].count(0)
    half = max(1, len(head) // 2)
    return odd > half * 0.6 and even < half * 0.05


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def skill(self, name="review", text=None, raw=None, base=(".claude", "skills"), extra=None):
        d = self.root.joinpath(*base, name)
        d.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            (d / "SKILL.md").write_bytes(raw)
        else:
            (d / "SKILL.md").write_text(text if text is not None else GOOD.format(name=name), encoding="utf-8")
        for rel, body in (extra or {}).items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(body, encoding="utf-8")
        return d

    def detail(self):
        return {r["skill"].split("/")[-1]: r for r in sa.scan(self.root, origin_detail=True)["skills"]}


class Utf16(Base):
    def test_bomless_utf16_mostly_korean_is_detected(self):
        text = korean_text()
        for codec in ("utf-16-le", "utf-16-be"):
            with self.subTest(codec=codec):
                raw = text.encode(codec)
                self.assertEqual(sa.sniff_utf16(raw), codec)
                self.assertEqual(sa.decode_text(raw), (text, codec))
        self.assertFalse(rule_102(text.encode("utf-16-le")))   # the 1.0.2 rule missed exactly this file

    def test_utf16_skill_is_reported_and_still_audited(self):
        body = GOOD.format(name="review") + korean_text(10)
        self.skill(raw=body.replace("\n", "\r\n").encode("utf-16-le"))
        r = sa.scan(self.root)["skills"][0]
        codes = {f["code"] for f in r["findings"]}
        self.assertIn("ENCODING_UTF16", codes)
        self.assertNotIn("NOT_UTF8", codes)
        self.assertNotIn("NAME_MISSING", codes)                 # frontmatter was read
        self.assertEqual(r["status"], "FAIL")

    def test_cp949_and_binary_are_not_mistaken_for_utf16(self):
        self.assertIsNone(sa.sniff_utf16(korean_text().encode("cp949")))
        self.assertIsNone(sa.sniff_utf16(b"\x00\x01\x02\x03\xff\xfe" * 50))
        self.assertIsNone(sa.sniff_utf16("plain ascii".encode("utf-8")))
        self.skill(raw=("---\nname: review\ndescription: " + korean_text(1)).encode("cp949"))
        self.assertIn("NOT_UTF8", {f["code"] for f in sa.scan(self.root)["skills"][0]["findings"]})


class Origin(Base):
    def test_origin_file_with_source_is_known(self):
        self.skill(extra={"ORIGIN.md": "source: self\nauthor: team\n"})
        self.assertEqual(self.detail()["review"]["origin"]["status"], "KNOWN")

    def test_blank_origin_template_is_unknown(self):
        self.skill(extra={"ORIGIN.md": "source:\nauthor:\nlicense:\n"})
        o = self.detail()["review"]["origin"]
        self.assertEqual((o["status"], o["origin_file"], o["origin_filled"]), ("UNKNOWN", "ORIGIN.md", False))

    def test_source_url_next_to_an_origin_word_is_known(self):
        self.skill(text=GOOD.format(name="review") + "\nBased on https://example.org/skills/review (MIT).\n")
        o = self.detail()["review"]["origin"]
        self.assertEqual(o["status"], "KNOWN")
        self.assertEqual(o["source_urls"][0]["url"], "https://example.org/skills/review")
        self.assertTrue(o["source_urls"][0]["where"].startswith("SKILL.md line "))

    def test_frontmatter_source_key_is_known(self):
        self.skill(text=GOOD.format(name="review").replace("license: MIT", "license: MIT\nsource: https://example.org/r"))
        self.assertEqual(self.detail()["review"]["origin"]["status"], "KNOWN")

    def test_ordinary_doc_link_and_license_alone_stay_unknown(self):
        self.skill(text=GOOD.format(name="review") + "\nSee https://example.org/docs for the API.\n")
        o = self.detail()["review"]["origin"]
        self.assertEqual((o["status"], o["license"]), ("UNKNOWN", True))

    def test_copies_are_duplicated_including_renamed_copies(self):
        self.skill("review")
        self.skill("review", base=("plugins", "x", "skills"))                 # identical copy
        self.skill("review-2", text=GOOD.format(name="review-2"))            # same body, new name
        self.skill("other", text=GOOD.format(name="other").replace("Walk through", "Read"))
        d = sa.scan(self.root, origin_detail=True)
        states = sorted(r["origin"]["status"] for r in d["skills"])
        self.assertEqual(states, ["DUPLICATED", "DUPLICATED", "DUPLICATED", "UNKNOWN"])
        self.assertEqual(d["origin_counts"], {"KNOWN": 0, "UNKNOWN": 1, "DUPLICATED": 3})

    def test_modified_date_is_reported(self):
        d = self.skill()
        os.utime(d / "SKILL.md", (1767225600, 1767225600))                     # 2026-01-01 UTC
        self.assertEqual(self.detail()["review"]["origin"]["modified"], "2026-01-01")

    def test_origin_file_is_not_an_unreferenced_file(self):
        self.skill(extra={"ORIGIN.md": "source: self\n"})
        self.assertNotIn("UNREFERENCED_FILE", {f["code"] for f in sa.scan(self.root)["skills"][0]["findings"]})

    def test_free_json_keeps_counts_only(self):
        self.skill()
        r = sa.scan(self.root)
        self.assertNotIn("origin", r["skills"][0])
        self.assertEqual(r["origin_counts"]["UNKNOWN"], 1)


class Plugins(Base):
    def plugin(self, name, manifest):
        d = self.root / "plugins" / name
        (d / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (d / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        return d

    def test_same_skill_name_in_two_plugins_is_not_a_conflict(self):
        # found on a real folder: discord/access and telegram/access failed as DUPLICATE_NAME,
        # but Claude Code namespaces plugin skills (discord:access, telegram:access)
        for name in ("chat-a", "chat-b"):
            base = self.plugin(name, {"name": name, "author": {"name": "Someone"}})
            self.skill("access", text=GOOD.format(name="access").replace("Walk", name), base=(str(base), "skills"))
        r = sa.scan(self.root)
        self.assertEqual(r["counts"]["FAIL"], 0, r["skills"])

    def test_same_name_inside_one_plugin_still_fails(self):
        base = self.plugin("p", {"name": "p", "author": "Someone"})
        self.skill("access", base=(str(base), "skills"))
        self.skill("access", text=GOOD.format(name="access").replace("Walk", "Go"), base=(str(base), "skills", "more"))
        self.assertIn("DUPLICATE_NAME", {f["code"] for s in sa.scan(self.root)["skills"] for f in s["findings"]})

    def test_plugin_manifest_with_author_or_url_makes_origin_known(self):
        base = self.plugin("p", {"name": "p", "author": {"name": "Someone", "url": "https://example.org/p"}})
        self.skill("one", base=(str(base), "skills"))
        bare = self.plugin("q", {"name": "q"})
        self.skill("two", text=GOOD.format(name="two").replace("Walk", "Go"), base=(str(bare), "skills"))
        d = self.detail()
        self.assertEqual((d["one"]["origin"]["status"], d["two"]["origin"]["status"]), ("KNOWN", "UNKNOWN"))
        self.assertEqual(d["one"]["origin"]["plugin"], {"name": "p", "author": "Someone", "urls": ["https://example.org/p"]})


    def test_claude_code_path_variables_are_resolved(self):
        # found on a real folder: ${CLAUDE_SKILL_DIR}/jobs/x.md was reported as a broken link
        base = self.plugin("p", {"name": "p", "author": "Someone"})
        (base / "scripts").mkdir()
        body = ("\nSee [a](${CLAUDE_SKILL_DIR}/jobs/a.md), [s](${CLAUDE_PLUGIN_ROOT}/scripts), "
                "[gone](${CLAUDE_SKILL_DIR}/jobs/gone.md) and [o](${OTHER_VAR}/x.md).\n")
        self.skill("one", text=GOOD.format(name="one") + body, base=(str(base), "skills"), extra={"jobs/a.md": "x"})
        broken = [f["message"] for f in sa.scan(self.root)["skills"][0]["findings"] if f["code"] == "BROKEN_LINK"]
        self.assertEqual(broken, ["Linked file does not exist: ${CLAUDE_SKILL_DIR}/jobs/gone.md"])


class ClosingLine(Base):
    def run_main(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = sa.main([str(self.root)] + list(args))
        return code, buf.getvalue()

    def test_line_carries_the_real_numbers(self):
        self.skill("review")
        self.skill("review", base=("plugins", "x", "skills"))
        self.skill("solo", text=GOOD.format(name="solo").replace("Walk through", "Read"))
        code, out = self.run_main()
        last = out.strip().splitlines()[-1]
        self.assertIn("Origin unknown: 1 skill(s), duplicated: 2 skill(s)", last)

    def test_no_line_when_there_is_nothing_to_report(self):
        self.skill(extra={"ORIGIN.md": "source: self\n"})
        code, out = self.run_main()
        self.assertNotIn("Origin unknown", out)
        self.assertNotIn("Pro", out)

    def test_markdown_format_is_gone(self):
        self.skill()
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()):
                import contextlib
                with contextlib.redirect_stderr(io.StringIO()):
                    sa.main([str(self.root), "--format", "md"])


class Console(Base):
    def test_cp949_and_ascii_consoles_exit_cleanly(self):
        # SKU01 broke on an em dash in a cp949 console. Names, descriptions and paths with
        # characters the console cannot show must never crash the run.
        self.skill("review", text=GOOD.format(name="review").replace("correctness", "correctness — ✓ 리뷰"))
        self.skill("Bad_Name", text=GOOD.format(name="Bad_Name"), base=(".claude", "skills", "—스킬"))
        for enc in ("cp949", "ascii"):
            with self.subTest(enc=enc):
                env = dict(os.environ, PYTHONIOENCODING=enc, PYTHONUTF8="0")
                p = subprocess.run([sys.executable, "-I", str(HERE / "skill_audit.py"), str(self.root)],
                                   capture_output=True, env=env)
                self.assertEqual(p.returncode, 1, p.stderr)            # 1 = a failing skill, not a crash
                self.assertNotIn(b"Traceback", p.stderr)
                self.assertIn(b"FAIL", p.stdout)
        p = subprocess.run([sys.executable, "-I", str(HERE / "skill_audit.py"), str(self.root / ".claude" / "skills" / "review")],
                           capture_output=True, env=dict(os.environ, PYTHONIOENCODING="cp949", PYTHONUTF8="0"))
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_tool_messages_are_ascii(self):
        src = (HERE / "skill_audit.py").read_text(encoding="utf-8")
        for line in src.splitlines():
            if "finding(" in line or "lines.append" in line or "print(" in line or "return (" in line:
                self.assertTrue(all(ord(c) < 128 for c in line), line)


if __name__ == "__main__":
    unittest.main()
