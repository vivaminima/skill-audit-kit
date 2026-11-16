---
name: skill-audit-kit
description: Audits the Claude Code skills in a project: broken names, links and frontmatter, duplicates, and token cost per skill. Use when the user asks, for example, "audit my skills", "which skills are broken", "check my .claude/skills", "스킬 점검해 줘".
---

# skill-audit-kit

Audits the Claude Code skills in a project: broken names, links and frontmatter, duplicates, and token cost per skill. A small Python tool shipped in this plugin: standard library only, nothing to install, runs locally.

## Run

Use `python` (Windows: `py -3` also works; macOS/Linux: `python3`). On Windows add `-X utf8` if the console shows broken characters.

    python "${CLAUDE_PLUGIN_ROOT}/skill_audit.py" <project-folder>
    python "${CLAUDE_PLUGIN_ROOT}/skill_audit.py" <project-folder> --strict --format json --output skill-audit.json --force

Run it from the user's project folder and pass the path they mean. If no path is given, ask which folder or file to check.

## Read the result

Lead with the fix list in priority order, then the per-skill table.

Show the tool's own output (shortened if long) and then a two- or three-line summary. Do not change the user's files unless they ask.
