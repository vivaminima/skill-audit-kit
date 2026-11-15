# Skill Audit Kit

*Part of the [Vivaminima tools](https://github.com/vivaminima) for Claude Code users.*

Find out why an agent skill does not load, does not trigger, or eats your context — before you ship it.

## New to this? Start here

**Who it's for:** you use Claude (desktop app or Claude Code) and you've added *skills* — the folders under `.claude/skills/` that teach Claude a task. A skill that never triggers, or quietly eats your context, gives you no error. This kit shows you which skill is broken and why.

**You don't need to know Python or the terminal.** Claude runs it for you.

1. Open the **Claude desktop app → Code tab** (Claude Code) and pick the project folder you want to check.
2. Download this repository (green **Code** button → **Download ZIP**) and unzip it anywhere.
3. Type the sentence below to Claude, with the path to the unzipped folder. Claude runs the script and explains the result in plain words.
4. If Claude says Python is missing, ask: *"Install Python 3 for me, step by step."*

> *"Run skill_audit.py from the skill-audit-kit folder on this project and tell me which skills are broken and how to fix them."*

What you get back: each skill marked PASS / WARN / FAIL, the exact reason (bad name, broken link, too long), and how many tokens your skills cost.

One Python file. No dependencies. Works offline. Never changes the folder it audits.

```
python skill_audit.py path/to/project
```

This is the real output of `python skill_audit.py examples` on the three sample skills shipped in `examples/`
(exit code 1):

```
Skill Audit Kit v1.0.3
3 skills: PASS 1  WARN 1  FAIL 1
Estimated tokens: 63 always loaded (names + descriptions), 5246 if every body loads

[FAIL] .claude/skills/Deploy_Tool  (3 lines, ~26 tokens)
  ERROR   NAME_INVALID: name 'Deploy_Tool' must use only lowercase letters, digits and single hyphens
  ERROR   BROKEN_LINK (line 9): Linked file does not exist: references/steps.md
[WARN] .claude/skills/release  (524 lines, ~5164 tokens)
  WARNING BODY_TOO_LONG: Body is 524 lines; keep SKILL.md under 500 and move detail into linked files
  WARNING DESCRIPTION_SHORT: description is 14 characters; too short to say what the skill does and when to use it
  INFO    NO_LICENSE_INFO: No 'license' field and no LICENSE file; reuse and resale rights are unknown
[PASS] .claude/skills/review  (7 lines, ~56 tokens)

Origin unknown: 3 skill(s), duplicated: 0 skill(s) - origin table and fix list are in the Skill Audit Pro report.
```

## What it checks

| Code | Level | Meaning |
|---|---|---|
| `MISSING_SKILL_MD`, `WRONG_FILENAME_CASE` | error | No `SKILL.md`, or it is named `skill.md` |
| `FRONTMATTER_MISSING`, `FRONTMATTER_UNCLOSED` | error | The `---` block is absent or never closed |
| `NAME_MISSING`, `NAME_INVALID`, `NAME_TOO_LONG` | error | `name` must be lowercase letters, digits, hyphens, max 64 |
| `DESCRIPTION_MISSING`, `DESCRIPTION_TOO_LONG` | error | `description` is required, max 1024 characters |
| `BROKEN_LINK` | error | A relative link points at a file that is not there (with line number) |
| `DUPLICATE_NAME` | error | Two skills share one name |
| `NOT_UTF8`, `OVERSIZE`, `UNREADABLE` | error | The file cannot be used as a skill |
| `ENCODING_UTF16` | error | `SKILL.md` was saved as UTF-16 (with or without a BOM); save it as UTF-8 |
| `NAME_DIR_MISMATCH`, `NAME_RESERVED` | warning | Name differs from its folder / contains a reserved word |
| `DESCRIPTION_SHORT`, `DESCRIPTION_XML_TAG` | warning | Description too thin to trigger reliably |
| `BODY_TOO_LONG`, `BODY_EMPTY` | warning | Over 500 lines, or almost nothing |
| `BACKSLASH_PATH`, `ABSOLUTE_PATH` | warning | Link breaks on another OS or machine |
| `DUPLICATE_DESCRIPTION`, `FRONTMATTER_SYNTAX`, `LICENSE_FILE_EMPTY` | warning | |
| `DESCRIPTION_NO_TRIGGER`, `DESCRIPTION_FIRST_PERSON` | info | Description style hints |
| `LINK_OUTSIDE_SKILL`, `UNREFERENCED_FILE` | info | Skill is not self-contained / carries unused files |
| `NO_LICENSE_INFO` | info | No `license` field and no LICENSE file — reuse rights unknown |

Only errors make a skill `FAIL`. Info never changes the status.

## Origin count

The last line counts skills whose origin is unknown (no `ORIGIN` file with a `source:`, no source URL in the
frontmatter or next to words such as *source*, *based on*, *forked from*) and skills whose `SKILL.md` is a copy of
another one (same text, or the same body under another name). The line only appears when a number is above zero.
In `--format json` the same numbers are in `origin_counts`.

Handles the things that trip simple checkers: UTF-8 BOM, Windows line endings, multi-line and
folded (`>`, `|`) descriptions, quoted values, nested `metadata`, links inside code blocks.

## Options

```
--format text|json        report format (json is for CI and scripts)
--output FILE             write to a file (refuses to overwrite unless --force)
--strict                  exit 1 on warnings too
--ignore CODE,CODE        drop findings you have decided to accept
```

Exit codes: `0` nothing failing, `1` at least one failing skill, `2` bad path or output error.

## Use in CI

```
python skill_audit.py . --strict --format json --output skill-audit.json --force
```

## What it does not do

- It does not run your skill or judge whether its instructions are good.
- Token numbers are estimates (about 4 characters per token). Real counts depend on the model.
- The frontmatter parser covers what skills use, not all of YAML.

## Windows

Use UTF-8 mode so non-English text prints correctly in the console:

```
python -X utf8 skill_audit.py path/to/project
```

## Requirements and what was tested

Python 3 standard library only - nothing to install.

- Tested: Windows 11, Python 3.10.11 (this release, 65 tests, including a cp949 console run).
- Version 1.0.2 was tested on Linux, Python 3.13; 1.0.0 was also run on Windows with Python 3.10 by an independent reviewer.
- Other versions and platforms have not been tested.

Run the tests yourself with `python -m unittest discover -s tests`.

MIT licence. See CHANGELOG.md for what changed.

## More from Vivaminima

- **Skill Audit Pro ($19)**, the paid edition of this tool, one-time payment on Gumroad: https://vivastory4.gumroad.com/l/skill-audit-pro

  <a href="https://vivastory4.gumroad.com/l/skill-audit-pro"><img src="https://raw.githubusercontent.com/vivaminima/vivaminima/main/img/skill-audit-kit.png" width="480" alt="Skill Audit Pro"></a>

- **Token Audit Kit ($19)** — see how many tokens your CLAUDE.md and its @imports cost before your first prompt, and a step-by-step guide to split it. Free script: https://github.com/vivaminima/context-file-audit · Paid kit (store): https://vivastory4.gumroad.com
- Free tools: [skill-audit-kit](https://github.com/vivaminima/skill-audit-kit) · [secret-map](https://github.com/vivaminima/secret-map) · [inbox-ledger](https://github.com/vivaminima/inbox-ledger)
