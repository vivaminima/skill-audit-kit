import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def skill(self, name="review", text=None, raw=None, dirname=None, base=(".claude", "skills")):
        d = self.root.joinpath(*base, dirname or name)
        d.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            (d / "SKILL.md").write_bytes(raw)
        else:
            (d / "SKILL.md").write_text(text if text is not None else GOOD.format(name=name), encoding="utf-8", newline="\n")
        return d

    def codes(self, report, index=0):
        return {f["code"] for f in report["skills"][index]["findings"]}

    def run_scan(self, **kw):
        return sa.scan(self.root, **kw)


class Encoding(Base):
    def test_clean_skill_passes(self):
        self.skill()
        r = self.run_scan()
        self.assertEqual(r["counts"], {"PASS": 1, "WARN": 0, "FAIL": 0})
        self.assertEqual(r["skills"][0]["findings"], [])

    def test_utf8_bom_is_not_a_failure(self):
        self.skill(raw=b"\xef\xbb\xbf" + GOOD.format(name="review").encode("utf-8"))
        self.assertEqual(self.run_scan()["counts"]["PASS"], 1)

    def test_crlf_is_not_a_failure(self):
        self.skill(raw=GOOD.format(name="review").replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(self.run_scan()["counts"]["PASS"], 1)

    def test_bom_and_crlf_together(self):
        self.skill(raw=b"\xef\xbb\xbf" + GOOD.format(name="review").replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(self.run_scan()["counts"]["PASS"], 1)

    def test_non_utf8_is_reported_not_crashed(self):
        self.skill(raw=b"---\nname: review\ndescription: \xff\xfe broken\n---\n")
        self.assertIn("NOT_UTF8", self.codes(self.run_scan()))

    def test_korean_description(self):
        self.skill(text="---\nname: review\ndescription: 풀 리퀘스트를 검토한다. 사용자가 코드 리뷰를 요청할 때 사용한다. 정확성과 테스트 범위를 본다.\nlicense: MIT\n---\n\n"
                        + "본문 지침을 여기에 충분히 길게 적는다. 변경 파일을 하나씩 보고 발견 사항을 한 줄씩 남긴다.\n")
        self.assertEqual(self.run_scan()["counts"]["PASS"], 1)


class Frontmatter(Base):
    def body(self, header):
        return "---\n" + header + "\n---\n\n" + "Instructions go here and are long enough to count as a real body for the skill.\n"

    def test_folded_scalar_description(self):
        self.skill(text=self.body("name: review\nlicense: MIT\ndescription: >\n  Review a pull request for correctness\n  and coverage. Use when a review is requested."))
        r = self.run_scan()
        self.assertEqual(r["counts"]["PASS"], 1, r["skills"][0]["findings"])

    def test_literal_scalar_description(self):
        self.skill(text=self.body("name: review\nlicense: MIT\ndescription: |-\n  Review a pull request for correctness.\n  Use when a review is requested."))
        self.assertEqual(self.run_scan()["counts"]["PASS"], 1)

    def test_plain_multiline_description(self):
        self.skill(text=self.body("name: review\nlicense: MIT\ndescription: Review a pull request for correctness\n  and coverage. Use when a review is requested."))
        self.assertEqual(self.run_scan()["counts"]["PASS"], 1)

    def test_quoted_values_and_colons(self):
        self.skill(text=self.body('name: "review"\nlicense: MIT\ndescription: "Review code: correctness, tests. Use when asked for a review."'))
        self.assertEqual(self.run_scan()["counts"]["PASS"], 1)

    def test_nested_metadata_is_tolerated(self):
        self.skill(text=self.body("name: review\nlicense: MIT\ndescription: Review a pull request. Use when a review is requested by the user.\nmetadata:\n  author: someone\n  tags:\n    - a\nallowed-tools: [Read, Grep]"))
        self.assertEqual(self.run_scan()["counts"]["PASS"], 1)

    def test_missing_frontmatter(self):
        self.skill(text="# just a heading\n\nsome text that is long enough to be a body for this particular test case.\n")
        self.assertIn("FRONTMATTER_MISSING", self.codes(self.run_scan()))

    def test_unclosed_frontmatter(self):
        self.skill(text="---\nname: review\ndescription: never closed\n\nbody text\n")
        self.assertIn("FRONTMATTER_UNCLOSED", self.codes(self.run_scan()))

    def test_missing_fields(self):
        self.skill(text=self.body("license: MIT"))
        self.assertTrue({"NAME_MISSING", "DESCRIPTION_MISSING"} <= self.codes(self.run_scan()))

    def test_empty_file(self):
        self.skill(text="")
        r = self.run_scan()
        self.assertEqual(r["counts"]["FAIL"], 1)


class NameAndDescription(Base):
    def make(self, name, desc, dirname=None):
        self.skill(name=name, dirname=dirname or name, text="---\nname: %s\ndescription: %s\nlicense: MIT\n---\n\n%s\n" % (name, desc, "x" * 80))
        return self.codes(self.run_scan())

    def test_invalid_names(self):
        for bad in ("Review_Skill", "review skill", "-review", "review--x", "REVIEW"):
            with self.subTest(bad=bad):
                with tempfile.TemporaryDirectory() as t:
                    self.root = Path(t)
                    self.assertIn("NAME_INVALID", self.make(bad, "Review things. Use when a review is requested by a user.", dirname="x"))

    def test_name_too_long(self):
        self.assertIn("NAME_TOO_LONG", self.make("a" * 65, "Review things. Use when a review is requested by a user."))

    def test_name_dir_mismatch(self):
        self.assertIn("NAME_DIR_MISMATCH", self.make("review", "Review things. Use when a review is requested by a user.", dirname="other"))

    def test_reserved_word(self):
        self.assertIn("NAME_RESERVED", self.make("claude-helper", "Review things. Use when a review is requested by a user."))

    def test_description_limits(self):
        self.assertIn("DESCRIPTION_SHORT", self.make("review", "short"))
        self.assertIn("DESCRIPTION_TOO_LONG", self.make("review", "Use when " + "x" * 1100))

    def test_description_without_trigger(self):
        self.assertIn("DESCRIPTION_NO_TRIGGER", self.make("review", "Comprehensive pull request reviewing capability with detailed output."))


class Links(Base):
    def with_body(self, body, files=()):
        d = self.skill(text="---\nname: review\ndescription: Review a pull request. Use when a review is requested by the user.\nlicense: MIT\n---\n\n" + body + "\n" + "padding " * 10)
        for f in files:
            p = d / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("content", encoding="utf-8")
        return self.run_scan()

    def test_existing_link_ok(self):
        r = self.with_body("See [checks](references/checks.md) and ![img](assets/a.png).", ["references/checks.md", "assets/a.png"])
        self.assertEqual(r["counts"]["PASS"], 1, r["skills"][0]["findings"])

    def test_broken_link_has_line_number(self):
        r = self.with_body("See [rules](rules.md).")
        f = [x for x in r["skills"][0]["findings"] if x["code"] == "BROKEN_LINK"][0]
        self.assertEqual(f["line"], 7)

    def test_urls_anchors_mailto_ignored(self):
        r = self.with_body("[a](https://example.com) [b](#section) [c](mailto:x@example.com) [d](checks.md#part)", ["checks.md"])
        self.assertEqual(r["counts"]["PASS"], 1, r["skills"][0]["findings"])

    def test_links_inside_code_are_ignored(self):
        r = self.with_body("```md\n[x](missing.md)\n```\nand `[y](also-missing.md)` inline")
        self.assertNotIn("BROKEN_LINK", self.codes(r))

    def test_outside_link_is_info_not_warning(self):
        (self.root / ".claude" / "shared").mkdir(parents=True)
        (self.root / ".claude" / "shared" / "rules.md").write_text("r", encoding="utf-8")
        r = self.with_body("See [shared](../../shared/rules.md).")
        self.assertIn("LINK_OUTSIDE_SKILL", self.codes(r))
        self.assertEqual(r["skills"][0]["status"], "PASS")

    def test_backslash_and_absolute(self):
        r = self.with_body("[a](references\\checks.md) [b](/etc/passwd) [c](C:/x/y.md)", ["references/checks.md"])
        self.assertTrue({"BACKSLASH_PATH", "ABSOLUTE_PATH"} <= self.codes(r))
        self.assertNotIn("BROKEN_LINK", self.codes(r))

    def test_unreferenced_file(self):
        r = self.with_body("See [checks](checks.md).", ["checks.md", "scripts/orphan.py"])
        msgs = [f["message"] for f in r["skills"][0]["findings"] if f["code"] == "UNREFERENCED_FILE"]
        self.assertEqual(len(msgs), 1)
        self.assertIn("scripts/orphan.py", msgs[0])


class Discovery(Base):
    def test_missing_skill_md(self):
        (self.root / ".claude" / "skills" / "empty").mkdir(parents=True)
        self.assertIn("MISSING_SKILL_MD", self.codes(self.run_scan()))

    def test_wrong_filename_case(self):
        d = self.root / ".claude" / "skills" / "review"
        d.mkdir(parents=True)
        (d / "skill.md").write_text(GOOD.format(name="review"), encoding="utf-8")
        r = self.run_scan()
        if (d / "SKILL.md").exists():
            self.skipTest("case-insensitive filesystem")
        self.assertIn("WRONG_FILENAME_CASE", self.codes(r))

    def test_single_skill_folder_as_root(self):
        d = self.skill()
        r = sa.scan(d)
        self.assertEqual((r["total_skills"], r["skills"][0]["skill"]), (1, "."))

    def test_skills_root_as_argument_and_plugin_layout(self):
        self.skill(base=("skills",))
        self.assertEqual(sa.scan(self.root)["total_skills"], 1)
        self.assertEqual(sa.scan(self.root / "skills")["total_skills"], 1)

    def test_ignores_vendor_dirs(self):
        self.skill()
        self.skill(name="vendored", base=("node_modules", "pkg"))
        self.assertEqual(self.run_scan()["total_skills"], 1)

    def test_duplicates(self):
        self.skill()
        self.skill(dirname="review-copy")
        r = self.run_scan()
        self.assertTrue(all("DUPLICATE_NAME" in {f["code"] for f in s["findings"]} for s in r["skills"]))
        self.assertTrue(all("DUPLICATE_DESCRIPTION" in {f["code"] for f in s["findings"]} for s in r["skills"]))

    def test_no_license_is_info_only(self):
        self.skill(text=GOOD.format(name="review").replace("license: MIT\n", ""))
        r = self.run_scan()
        self.assertIn("NO_LICENSE_INFO", self.codes(r))
        self.assertEqual(r["skills"][0]["status"], "PASS")

    def test_license_file_counts(self):
        d = self.skill(text=GOOD.format(name="review").replace("license: MIT\n", ""))
        (d / "LICENSE.txt").write_text("MIT", encoding="utf-8")
        r = self.run_scan()
        self.assertNotIn("NO_LICENSE_INFO", self.codes(r))
        self.assertEqual(r["skills"][0]["license"]["files"], ["LICENSE.txt"])

    def test_long_body_and_tokens(self):
        self.skill(text=GOOD.format(name="review") + "line\n" * 600)
        r = self.run_scan()
        self.assertIn("BODY_TOO_LONG", self.codes(r))
        self.assertGreater(r["tokens"]["all_bodies"], r["tokens"]["always_loaded"])

    def test_deterministic(self):
        for n in ("b-skill", "a-skill", "c-skill"):
            self.skill(name=n)
        self.assertEqual(self.run_scan(), self.run_scan())
        self.assertEqual([s["skill"] for s in self.run_scan()["skills"]], sorted(s["skill"] for s in self.run_scan()["skills"]))


class Cli(Base):
    def test_exit_codes(self):
        self.skill()
        self.assertEqual(sa.main([str(self.root), "--format", "json", "--output", str(self.root / "r.json")]), 0)
        self.assertEqual(sa.main([str(self.root), "--output", str(self.root / "r.json")]), 2)
        self.assertEqual(sa.main([str(self.root), "--force", "--output", str(self.root / "r.json")]), 0)
        self.assertEqual(sa.main([str(self.root / "nope")]), 2)

    def test_fail_and_strict_and_ignore(self):
        self.skill(text=GOOD.format(name="review").replace("Use when the user asks for a code review.", ""))
        out = str(self.root / "o.txt")
        self.assertEqual(sa.main([str(self.root), "--output", out]), 0)
        self.skill(name="other", dirname="mismatch")
        self.assertEqual(sa.main([str(self.root), "--force", "--output", out]), 0)
        self.assertEqual(sa.main([str(self.root), "--strict", "--force", "--output", out]), 1)
        self.assertEqual(sa.main([str(self.root), "--strict", "--ignore", "name_dir_mismatch", "--force", "--output", out]), 0)
        self.skill(name="Bad_Name", dirname="bad")
        self.assertEqual(sa.main([str(self.root), "--force", "--output", out]), 1)

    def test_category_folders_are_not_skills(self):
        self.skill(base=("skills", "public"))
        self.skill(name="other", base=("skills", "examples"))
        r = sa.scan(self.root)
        self.assertEqual((r["total_skills"], r["counts"]["FAIL"]), (2, 0))

    def test_helper_modules_and_mentioned_folders_are_not_orphans(self):
        d = self.skill(text=GOOD.format(name="review") + "Run `scripts/run.py`. Fonts live in the fonts/ folder.\n")
        for rel, body in (("scripts/run.py", "import helper\n"), ("scripts/helper.py", "x=1\n"), ("fonts/a.ttf", "bin"), ("stray.bin", "z")):
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(body, encoding="utf-8")
        msgs = [f["message"] for f in sa.scan(self.root)["skills"][0]["findings"] if f["code"] == "UNREFERENCED_FILE"]
        self.assertEqual(len(msgs), 1)
        self.assertIn("1 bundled file(s)", msgs[0])
        self.assertIn("stray.bin", msgs[0])

    def test_json_render(self):
        self.skill()
        out = self.root / "r.json"
        sa.main([str(self.root), "--format", "json", "--output", str(out)])
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["total_skills"], 1)

    def test_never_writes_into_audited_folder(self):
        d = self.skill()
        before = sorted(p.name for p in d.rglob("*"))
        sa.scan(self.root)
        self.assertEqual(before, sorted(p.name for p in d.rglob("*")))


if __name__ == "__main__":
    unittest.main()
