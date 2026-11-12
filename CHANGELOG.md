# Changelog

## 1.0.3
- UTF-16 `SKILL.md` files (with or without a BOM) are now recognised and reported as `ENCODING_UTF16`, and the
  rest of the audit still runs. BOM-less UTF-16 is detected by decoding and checking for control characters, so
  files that are mostly Korean (or other scripts without zero bytes) are caught too. Before, they showed as
  `NOT_UTF8` and nothing else was checked.
- New closing line with real numbers: skills whose origin is unknown, and skills that are copies of another skill.
  It only appears when a number is above zero. JSON output carries the same numbers in `origin_counts`.
- An `ORIGIN` / `ORIGIN.md` file in a skill folder is no longer reported as an unreferenced file.
- Removed `--format md`. Output formats are `text` and `json`.
- Console output never crashes on a console that cannot show a character (for example a cp949 or ASCII
  Windows console); such characters are replaced. All messages written by the tool are ASCII.
- Claude Code plugins: two plugins may each have a skill with the same name (Claude Code namespaces them), so
  `DUPLICATE_NAME` and `DUPLICATE_DESCRIPTION` now compare skills only within one plugin. Before, for example,
  `discord/access` and `telegram/access` both failed.
- Links that start with `${CLAUDE_SKILL_DIR}` or `${CLAUDE_PLUGIN_ROOT}` are resolved to the skill or plugin folder and
  checked there. Before, they were all reported as `BROKEN_LINK`. Other `${...}` variables are not checked.
- A plugin manifest (`.claude-plugin/plugin.json`) that names an author or a URL counts as a known origin.
- Tests: 65 (was 44). Every fix above has its own regression test.

## 1.0.2
- README: the sample output is now the real output of the tool on the sample skills in `examples/`.
- Added `examples/` (one passing, one warning, one failing skill) so the README result can be reproduced.
- README: Windows UTF-8 note; the tested-versions list names only what was actually run.
- No change to audit behaviour. (There is no 1.0.1 of this tool; the number was skipped to match the other kits.)

## 1.0.0
- First release.
