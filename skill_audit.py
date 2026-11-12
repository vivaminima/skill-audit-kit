#!/usr/bin/env python3
"""Skill Audit Kit - offline, read-only auditor for agent skill folders (SKILL.md).

Finds the problems that make a skill fail to load, fail to trigger, or waste
context: broken frontmatter, invalid names, weak descriptions, dead links,
oversized bodies, duplicate skills, and missing licence information.

Standard library only. Never writes inside the audited folder. Never uses the network.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

VERSION = "1.0.3"

MAX_FILE_BYTES = 1024 * 1024
NAME_MAX = 64
DESC_MAX = 1024
DESC_MIN = 40
BODY_MAX_LINES = 500
BODY_MIN_CHARS = 50
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache"}
SKILL_ROOTS = ((".claude", "skills"), ("skills",))
RESERVED_WORDS = ("anthropic", "claude")
TEXT_SUFFIXES = {".md", ".txt", ".py", ".js", ".ts", ".sh", ".json", ".yaml", ".yml", ".html", ".toml", ".xml", ".css"}
SEVERITIES = ("error", "warning", "info")
ORIGIN_STATES = ("KNOWN", "UNKNOWN", "DUPLICATED")

NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
KEY_RE = re.compile(r"^([A-Za-z_][\w.-]*)\s*:(?:\s+(.*)|\s*)$")
FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
LINK_RE = re.compile(r"!?\[[^\]\n]*\]\(\s*(<[^>\n]+>|[^)\s]+)(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)")
REFDEF_RE = re.compile(r"^\s{0,3}\[[^\]\n]+\]:\s*(<[^>\n]+>|\S+)", re.MULTILINE)
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
TRIGGER_RE = re.compile(
    r"\b(when|whenever|use|trigger|triggers|before|after)\b|때|경우|사용|場合|とき", re.IGNORECASE
)
FIRST_PERSON_RE = re.compile(r"\b(I can|I will|I'll|I am|I'm|you can use this)\b", re.IGNORECASE)
XML_TAG_RE = re.compile(r"<[A-Za-z/][^>]*>")

# origin (where a skill came from)
ORIGIN_FILE_RE = re.compile(r"^origin(?:\.(?:md|txt|ya?ml))?$", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>()\"'`\[\]{}|]+")
ORIGIN_CUE_RE = re.compile(
    r"\b(source|sources|origin|upstream|adapted|based on|forked|copied|credit|credits|original|author)\b"
    r"|출처|원본|원작|가져온", re.IGNORECASE)
ORIGIN_KEYS = ("source", "origin", "upstream", "repository", "repo", "homepage", "url")
ORIGIN_LINE_RE = re.compile(r"^\s*[-*]?\s*(source|origin|from|upstream|url)\s*:\s*(.*?)\s*$", re.IGNORECASE)
HEADER_URL_RE = re.compile(r"^\s*(?:" + "|".join(ORIGIN_KEYS) + r")\s*:\s*[\"']?(https?://\S+?)[\"']?\s*$",
                           re.IGNORECASE)


# --------------------------------------------------------------------------- text decoding

_TEXT_WS = "\t\n\r\f\v"


def sniff_utf16(raw):
    """Return 'utf-16-le' or 'utf-16-be' when BOM-less bytes are UTF-16 text, else None.

    Counting zero bytes alone misses text that is mostly Korean (or Chinese, Japanese):
    those characters have no zero byte. So the side that holds more zero bytes only picks
    the byte order; the decision is made by decoding and checking the result - almost no
    control or unassigned characters, and at least a little plain ASCII (the zero bytes
    came from somewhere).
    """
    if len(raw) < 2 or b"\x00" not in raw:
        return None
    sample = raw[:65536]
    if len(sample) % 2:
        sample = sample[:-1]
    even, odd = sample[0::2].count(0), sample[1::2].count(0)
    if even == odd:
        return None
    codec = "utf-16-le" if odd > even else "utf-16-be"
    text = None
    for cut in (sample, sample[:-2]):              # the sample may split a surrogate pair
        try:
            text = cut.decode(codec)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return None
    bad = plain = 0
    for ch in text:
        code = ord(ch)
        if code < 0x80:
            if ch in _TEXT_WS or 0x20 <= code < 0x7F:
                plain += 1
            else:
                bad += 1
        elif unicodedata.category(ch) in ("Cc", "Cn", "Co", "Cs"):
            bad += 1
    if bad > len(text) * 0.01 or plain < len(text) * 0.02:
        return None
    return codec


def decode_text(raw):
    """Return (text, encoding). encoding is 'utf-8', 'utf-16-le', 'utf-16-be' or None (not text)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    elif raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        codec = "utf-16-le" if raw.startswith(b"\xff\xfe") else "utf-16-be"
        try:
            return raw[2:].decode(codec), codec
        except UnicodeDecodeError:
            return None, None
    codec = sniff_utf16(raw)
    if codec:
        try:
            return raw.decode(codec), codec
        except UnicodeDecodeError:
            return None, None
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return None, None


# --------------------------------------------------------------------------- frontmatter

def split_frontmatter(text):
    """Return (header_lines, body, error_code). `text` must already have BOM removed."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text, "FRONTMATTER_MISSING"
    for i in range(1, len(lines)):
        if lines[i].rstrip() in ("---", "..."):
            return lines[1:i], "\n".join(lines[i + 1:]), None
    return None, text, "FRONTMATTER_UNCLOSED"


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else inner.replace('\\"', '"')
    if " #" in value:  # trailing YAML comment on a plain scalar
        value = value.split(" #", 1)[0].rstrip()
    return value


def parse_frontmatter(header_lines):
    """Parse the subset of YAML that skill frontmatter uses.

    Handles plain, quoted, multi-line plain, literal (|) and folded (>) scalars.
    Nested maps and lists are kept as raw text and flagged as complex.
    Returns (fields, complex_keys, problems).
    """
    fields, complex_keys, problems = {}, set(), []
    i, n = 0, len(header_lines)
    while i < n:
        line = header_lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[0] in " \t":
            problems.append("unexpected indentation at frontmatter line %d" % (i + 1))
            i += 1
            continue
        match = KEY_RE.match(line)
        if not match:
            problems.append("cannot parse frontmatter line %d" % (i + 1))
            i += 1
            continue
        key, value = match.group(1), (match.group(2) or "").strip()
        block = []
        j = i + 1
        while j < n and (not header_lines[j].strip() or header_lines[j][0] in " \t"):
            block.append(header_lines[j])
            j += 1
        while block and not block[-1].strip():
            block.pop()
        if key in fields:
            problems.append("duplicate key %r" % key)
        if re.fullmatch(r"[|>][+-]?\d*", value):
            content = [b.strip() for b in block]
            if value[0] == "|":
                fields[key] = "\n".join(content).strip()
            else:
                fields[key] = re.sub(r"[ \t]*\n[ \t]*", " ", "\n".join(content)).strip()
        elif value == "" and block:
            first = next((b.strip() for b in block if b.strip()), "")
            if first.startswith("- ") or first == "-" or KEY_RE.match(first):
                complex_keys.add(key)
                fields[key] = "\n".join(block)
            else:
                fields[key] = " ".join(b.strip() for b in block if b.strip())
        elif value.startswith(("[", "{")):
            complex_keys.add(key)
            fields[key] = value
        else:
            extra = [b.strip() for b in block if b.strip()]
            fields[key] = _unquote(" ".join([value] + extra) if extra else value)
        i = j
    return fields, complex_keys, problems


# --------------------------------------------------------------------------- helpers

def estimate_tokens(text):
    """Rough token estimate: ~4 chars per token for ASCII, ~1.5 for other scripts."""
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return int(round(ascii_chars / 4.0 + (len(text) - ascii_chars) / 1.5))


def mask_code(markdown):
    """Blank out fenced code blocks and inline code, keeping line numbers stable."""
    out, fence = [], None
    for line in markdown.split("\n"):
        m = FENCE_RE.match(line)
        if fence:
            if m and m.group(1) == fence:
                fence = None
            out.append("")
        elif m:
            fence = m.group(1)
            out.append("")
        else:
            out.append(INLINE_CODE_RE.sub("", line))
    return "\n".join(out)


def extract_links(markdown):
    """Yield (line_number, target) for local-looking markdown links."""
    masked = mask_code(markdown)
    found = []
    for regex in (LINK_RE, REFDEF_RE):
        for m in regex.finditer(masked):
            target = m.group(1).strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1].strip()
            found.append((masked.count("\n", 0, m.start()) + 1, target))
    return sorted(found)


def read_text(path):
    """Return (text, error_code).

    Accepts UTF-8 with or without BOM and any newline style. UTF-16 (with or without a
    BOM) is decoded so the rest of the audit can run, and reported as ENCODING_UTF16.
    """
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None, "OVERSIZE"
        raw = path.read_bytes()
    except OSError:
        return None, "UNREADABLE"
    text, encoding = decode_text(raw)
    if text is None:
        return None, "NOT_UTF8"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, ("ENCODING_UTF16" if encoding != "utf-8" else None)


def finding(severity, code, message, line=None):
    item = {"severity": severity, "code": code, "message": message}
    if line:
        item["line"] = line
    return item


def _normal_hash(text):
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return hashlib.sha256("\n".join(lines).strip().encode("utf-8")).hexdigest()


def _newest_mtime(skill_dir):
    newest, seen = 0.0, 0
    for base, dirs, names in os.walk(skill_dir, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            seen += 1
            if seen > 2000:
                break
            try:
                newest = max(newest, os.lstat(os.path.join(base, name)).st_mtime)
            except OSError:
                pass
    if not newest:
        return None
    return datetime.datetime.fromtimestamp(newest, datetime.timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- plugins

def plugin_of(skill_dir, root):
    """Return (plugin_dir, manifest) for the Claude Code plugin a skill belongs to, or (None, None).

    A plugin is the nearest folder at or above the skill (but not above root) that holds
    .claude-plugin/plugin.json. Plugin skills are namespaced by their plugin, so two plugins
    may each have a skill with the same name.
    """
    root = Path(root)
    for folder in [skill_dir] + list(skill_dir.parents):
        manifest = folder / ".claude-plugin" / "plugin.json"
        if manifest.is_file():
            data = None
            try:
                if manifest.stat().st_size <= MAX_FILE_BYTES:
                    text, _enc = decode_text(manifest.read_bytes())
                    data = json.loads(text) if text else None
            except (OSError, ValueError):
                data = None
            return folder, data if isinstance(data, dict) else {}
        if folder == root or root not in folder.parents:
            break
    return None, None


def _plugin_origin(manifest):
    """KNOWN-origin evidence from a plugin manifest: its name plus an author or a URL."""
    if not manifest or not isinstance(manifest.get("name"), str):
        return None
    author = manifest.get("author")
    if isinstance(author, dict):
        author_name, author_url = author.get("name"), author.get("url")
    else:
        author_name, author_url = author, None
    urls = []
    for value in (manifest.get("homepage"), manifest.get("repository"), author_url):
        if isinstance(value, dict):
            value = value.get("url")
        if isinstance(value, str):
            urls += URL_RE.findall(value)
    if not (isinstance(author_name, str) and author_name.strip()) and not urls:
        return None
    return {"name": manifest["name"][:100], "author": author_name[:100] if isinstance(author_name, str) else None,
            "urls": urls[:3]}


# --------------------------------------------------------------------------- origin

def origin_evidence(skill_dir, entries, fields, header, body, body_offset, text, licensed, plugin=None):
    """Collect what tells us where a skill came from. Reads only; never writes."""
    ev = {"origin_file": None, "origin_filled": False, "origin_source": None, "source_urls": [],
          "plugin": _plugin_origin(plugin),
          "license": bool(licensed), "modified": _newest_mtime(skill_dir),
          "sha256": _normal_hash(text) if text is not None else None,
          "body_sha256": _normal_hash(body) if body is not None and len(body.strip()) >= BODY_MIN_CHARS else None}
    urls = []

    def add(url, where):
        url = url.rstrip(".,;:!?")
        if url and url not in [u["url"] for u in urls]:
            urls.append({"url": url[:300], "where": where})

    for name in sorted(entries):
        p = entries[name]
        if ORIGIN_FILE_RE.match(name) and p.is_file() and not p.is_symlink():
            ev["origin_file"] = name
            otext, _err = read_text(p)
            for line in (otext or "").split("\n"):
                m = ORIGIN_LINE_RE.match(line)
                if m and m.group(2) and not m.group(2).startswith("#"):
                    ev["origin_filled"] = True
                    ev["origin_source"] = m.group(2)[:200]
                for u in URL_RE.findall(line):
                    add(u, name)
            if urls:
                ev["origin_filled"] = True
            break
    for key in ORIGIN_KEYS:
        value = fields.get(key)
        if isinstance(value, str):
            for u in URL_RE.findall(value):
                add(u, "frontmatter %s" % key)
    for line in header or []:
        m = HEADER_URL_RE.match(line)
        if m:
            add(m.group(1), "frontmatter")
    if body:
        for number, line in enumerate(mask_code(body).split("\n"), 1):
            if ORIGIN_CUE_RE.search(line):
                for u in URL_RE.findall(line):
                    add(u, "SKILL.md line %d" % (number + body_offset))
    ev["source_urls"] = urls[:5]
    return ev


def classify_origins(results):
    """Set result['origin']['status'] to KNOWN / UNKNOWN / DUPLICATED, across all given results.

    DUPLICATED: the same SKILL.md text (or the same body under another name) exists elsewhere.
    KNOWN: an ORIGIN file with a source, a source URL in the frontmatter or next to an
    origin word (source, based on, forked from, ...) in SKILL.md, or a Claude Code plugin
    manifest (plugin.json) naming an author or a URL. Everything else is UNKNOWN.
    A LICENSE alone says how a skill may be used, not where it came from, so it does not make
    a skill KNOWN.
    """
    groups = {}
    for r in results:
        o = r.get("origin")
        if not o:
            continue
        for key in ("sha256", "body_sha256"):
            if o.get(key):
                groups.setdefault((key, o[key]), []).append(r)
    for r in results:
        o = r.get("origin")
        if not o:
            continue
        copies = []
        for key in ("sha256", "body_sha256"):
            for other in groups.get((key, o.get(key)), []) if o.get(key) else []:
                label = other.get("where", other["skill"])
                if other is not r and label not in copies:
                    copies.append(label)
        o["copies"] = copies
        if copies:
            o["status"] = "DUPLICATED"
        elif o["origin_filled"] or o["source_urls"] or o.get("plugin"):
            o["status"] = "KNOWN"
        else:
            o["status"] = "UNKNOWN"
    return {k: sum(1 for r in results if r.get("origin") and r["origin"]["status"] == k) for k in ORIGIN_STATES}


# --------------------------------------------------------------------------- one skill

def audit_skill(skill_dir, root):
    """Audit one skill directory. Returns a result dict."""
    findings = []
    result = {
        "skill": skill_dir.relative_to(root).as_posix() if skill_dir != root else ".",
        "name": None, "description": None, "status": "PASS", "findings": findings,
        "tokens": {"always_loaded": 0, "body": 0}, "body_lines": 0, "license": None, "origin": None,
    }
    plugin_dir, manifest = plugin_of(skill_dir, root)
    result["_ns"] = str(plugin_dir) if plugin_dir else ""
    entries = {}
    try:
        for p in skill_dir.iterdir():
            entries[p.name] = p
    except OSError:
        findings.append(finding("error", "UNREADABLE", "Cannot list the skill directory"))
        return result

    skill_md = entries.get("SKILL.md")
    if skill_md is None:
        wrong = [n for n in entries if n.lower() == "skill.md"]
        if wrong:
            findings.append(finding("error", "WRONG_FILENAME_CASE",
                                    "Found %r; the file must be named exactly SKILL.md" % wrong[0]))
            skill_md = entries[wrong[0]]
        else:
            findings.append(finding("error", "MISSING_SKILL_MD", "No SKILL.md in this skill directory"))
            return result
    if skill_md.is_symlink() or not skill_md.is_file():
        findings.append(finding("error", "MISSING_SKILL_MD", "SKILL.md is a symlink or not a regular file"))
        return result

    text, err = read_text(skill_md)
    if err == "ENCODING_UTF16":
        findings.append(finding("error", err, "SKILL.md is saved as UTF-16; skills must be UTF-8"))
    elif err:
        messages = {"OVERSIZE": "SKILL.md is larger than 1 MiB", "UNREADABLE": "SKILL.md cannot be read",
                    "NOT_UTF8": "SKILL.md is not valid UTF-8"}
        findings.append(finding("error", err, messages[err]))
        result["license"] = _check_license({}, entries, [])
        result["origin"] = origin_evidence(skill_dir, entries, {}, None, None, 0, None, result["license"], manifest)
        if err == "NOT_UTF8":
            try:
                result["origin"]["sha256"] = hashlib.sha256(skill_md.read_bytes()).hexdigest()
            except OSError:
                pass
        return result

    header, body, fm_err = split_frontmatter(text)
    fields, complex_keys = {}, set()
    if fm_err == "FRONTMATTER_MISSING":
        findings.append(finding("error", fm_err, "SKILL.md must start with a '---' YAML frontmatter block", 1))
    elif fm_err == "FRONTMATTER_UNCLOSED":
        findings.append(finding("error", fm_err, "Frontmatter block is never closed with '---'", 1))
    else:
        fields, complex_keys, problems = parse_frontmatter(header)
        for problem in problems:
            findings.append(finding("warning", "FRONTMATTER_SYNTAX", "Frontmatter: " + problem))
        _check_name(fields, complex_keys, skill_dir, findings)
        _check_description(fields, complex_keys, findings)
        always = "\n".join(str(fields.get(k, "")) for k in ("name", "description"))
        result["tokens"]["always_loaded"] = estimate_tokens(always)
        result["name"] = fields.get("name") if "name" not in complex_keys else None
        result["description"] = fields.get("description") if "description" not in complex_keys else None

    body_offset = (len(header) + 2) if header is not None else 0
    body_lines = body.strip("\n").split("\n") if body.strip() else []
    result["body_lines"] = len(body_lines)
    result["tokens"]["body"] = estimate_tokens(body)
    if len(body.strip()) < BODY_MIN_CHARS:
        findings.append(finding("warning", "BODY_EMPTY", "SKILL.md has almost no instructions after the frontmatter"))
    if len(body_lines) > BODY_MAX_LINES:
        findings.append(finding("warning", "BODY_TOO_LONG",
                                "Body is %d lines; keep SKILL.md under %d and move detail into linked files"
                                % (len(body_lines), BODY_MAX_LINES)))

    referenced_text = [text]
    _check_links(body, body_offset, skill_dir, root, findings, plugin_dir)
    _check_unreferenced(skill_dir, skill_md, referenced_text, findings)
    result["license"] = _check_license(fields, entries, findings)
    result["origin"] = origin_evidence(skill_dir, entries, fields, header, body, body_offset, text, result["license"],
                                       manifest)

    if any(f["severity"] == "error" for f in findings):
        result["status"] = "FAIL"
    elif any(f["severity"] == "warning" for f in findings):
        result["status"] = "WARN"
    return result


def _check_name(fields, complex_keys, skill_dir, findings):
    name = fields.get("name")
    if not name or "name" in complex_keys:
        findings.append(finding("error", "NAME_MISSING", "Frontmatter needs a non-empty 'name'"))
        return
    if len(name) > NAME_MAX:
        findings.append(finding("error", "NAME_TOO_LONG", "name is %d characters; the limit is %d" % (len(name), NAME_MAX)))
    if not NAME_RE.fullmatch(name):
        findings.append(finding("error", "NAME_INVALID",
                                "name %r must use only lowercase letters, digits and single hyphens" % name))
    for word in RESERVED_WORDS:
        if word in name.lower():
            findings.append(finding("warning", "NAME_RESERVED", "name contains the reserved word %r" % word))
    if name != skill_dir.name:
        findings.append(finding("warning", "NAME_DIR_MISMATCH",
                                "name %r does not match its directory %r" % (name, skill_dir.name)))


def _check_description(fields, complex_keys, findings):
    desc = fields.get("description")
    if not desc or "description" in complex_keys:
        findings.append(finding("error", "DESCRIPTION_MISSING", "Frontmatter needs a non-empty 'description'"))
        return
    if len(desc) > DESC_MAX:
        findings.append(finding("error", "DESCRIPTION_TOO_LONG",
                                "description is %d characters; the limit is %d" % (len(desc), DESC_MAX)))
    if len(desc) < DESC_MIN:
        findings.append(finding("warning", "DESCRIPTION_SHORT",
                                "description is %d characters; too short to say what the skill does and when to use it"
                                % len(desc)))
    elif not TRIGGER_RE.search(desc):
        findings.append(finding("info", "DESCRIPTION_NO_TRIGGER",
                                "description does not say when to use the skill (no 'use when ...' style cue)"))
    if FIRST_PERSON_RE.search(desc):
        findings.append(finding("info", "DESCRIPTION_FIRST_PERSON",
                                "description is written in first/second person; third person triggers more reliably"))
    if XML_TAG_RE.search(desc):
        findings.append(finding("warning", "DESCRIPTION_XML_TAG", "description contains XML-like tags"))


LINK_VARS = ("${CLAUDE_SKILL_DIR}", "${CLAUDE_PLUGIN_ROOT}")


def _check_links(body, offset, skill_dir, root, findings, plugin_dir=None):
    skill_real = skill_dir.resolve()
    seen = set()
    for line, target in extract_links(body):
        path_part = target.split("#", 1)[0].split("?", 1)[0]
        if path_part.startswith("${"):
            # Claude Code expands ${CLAUDE_SKILL_DIR} and ${CLAUDE_PLUGIN_ROOT} when the skill runs.
            base = {"${CLAUDE_SKILL_DIR}": skill_dir, "${CLAUDE_PLUGIN_ROOT}": plugin_dir}.get(path_part.split("/", 1)[0])
            if base is None or path_part in seen:
                continue                       # another variable, or no plugin: cannot be checked offline
            seen.add(path_part)
            rest = path_part.split("/", 1)[1] if "/" in path_part else ""
            if not (Path(base) / rest).exists():
                findings.append(finding("error", "BROKEN_LINK", "Linked file does not exist: %s" % path_part, line + offset))
            continue
        if not path_part or SCHEME_RE.match(path_part) and not re.match(r"^[A-Za-z]:[\\/]", path_part):
            continue
        if path_part in seen:
            continue
        seen.add(path_part)
        where = line + offset
        if "\\" in path_part:
            findings.append(finding("warning", "BACKSLASH_PATH",
                                    "Link %r uses backslashes; use forward slashes so it works on every OS" % path_part, where))
            path_part = path_part.replace("\\", "/")
        if path_part.startswith("/") or re.match(r"^[A-Za-z]:/", path_part):
            findings.append(finding("warning", "ABSOLUTE_PATH",
                                    "Link %r is an absolute path and will break on another machine" % path_part, where))
            continue
        try:
            resolved = (skill_dir / path_part).resolve()
        except (OSError, ValueError):
            findings.append(finding("error", "BROKEN_LINK", "Link %r cannot be resolved" % path_part, where))
            continue
        if not resolved.exists():
            findings.append(finding("error", "BROKEN_LINK", "Linked file does not exist: %s" % path_part, where))
        elif resolved != skill_real and skill_real not in resolved.parents:
            findings.append(finding("info", "LINK_OUTSIDE_SKILL",
                                    "Link %r points outside the skill folder; it breaks if the skill is copied alone"
                                    % path_part, where))


def _check_unreferenced(skill_dir, skill_md, texts, findings):
    """Report bundled files that nothing in the skill mentions (one summary finding)."""
    files = []
    for base, dirs, names in os.walk(skill_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            p = Path(base) / name
            if p == skill_md or name.startswith(".") or name == "__init__.py":
                continue
            if name.upper().startswith(("LICENSE", "LICENCE", "NOTICE", "COPYING")):
                continue
            if Path(base) == skill_dir and ORIGIN_FILE_RE.match(name):
                continue
            files.append(p)
    if not files or len(files) > 2000:
        return
    budget = 5 * 1024 * 1024
    sources = {}
    for p in files:
        if p.suffix.lower() in TEXT_SUFFIXES and budget > 0:
            extra, _err = read_text(p)
            if extra:
                sources[p] = extra
                budget -= len(extra)
    skill_text = "\n".join(texts)
    orphans = []
    for p in files:
        rel = p.relative_to(skill_dir)
        others = skill_text + "\n" + "\n".join(t for q, t in sources.items() if q != p)
        names = {rel.as_posix(), rel.as_posix().replace("/", "\\"), p.name}
        if p.suffix.lower() in (".py", ".js", ".ts", ".sh"):
            names.add(p.stem)
        if any(n in others for n in names):
            continue
        if any((part + "/") in skill_text or re.search(r"\b%s\b" % re.escape(part), skill_text) for part in rel.parts[:-1]):
            continue
        orphans.append(rel.as_posix())
    if orphans:
        shown = ", ".join(orphans[:5]) + (" (+%d more)" % (len(orphans) - 5) if len(orphans) > 5 else "")
        findings.append(finding("info", "UNREFERENCED_FILE",
                                "%d bundled file(s) are never mentioned anywhere in the skill: %s" % (len(orphans), shown)))


def _check_license(fields, entries, findings):
    declared = fields.get("license")
    files = sorted(n for n in entries if n.upper().split(".")[0] in ("LICENSE", "LICENCE", "COPYING"))
    for name in files:
        try:
            if entries[name].is_file() and entries[name].stat().st_size == 0:
                findings.append(finding("warning", "LICENSE_FILE_EMPTY", "%s is empty" % name))
        except OSError:
            pass
    if not declared and not files:
        findings.append(finding("info", "NO_LICENSE_INFO",
                                "No 'license' field and no LICENSE file; reuse and resale rights are unknown"))
        return None
    return {"declared": declared or None, "files": files}


# --------------------------------------------------------------------------- discovery

def discover(root):
    """Return sorted skill directories under root, without following symlinks."""
    found = set()
    if any(n.lower() == "skill.md" for n in _names(root)):
        found.add(root)
    for base, dirs, names in os.walk(root, followlinks=False):
        base_path = Path(base)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (base_path / d).is_symlink())
        if any(n.lower() == "skill.md" for n in names):
            found.add(base_path)
        parts = base_path.relative_to(root).parts
        for marker in SKILL_ROOTS:
            nested = parts[-len(marker):] == marker if len(marker) > 1 else parts == marker
            if nested or (base_path == root and root.name == "skills" and marker == ("skills",)):
                for d in dirs:
                    if not _contains_skill(base_path / d):
                        found.add(base_path / d)
    return sorted(found)


def _contains_skill(path):
    """True if a SKILL.md exists anywhere below path (path is then a category folder)."""
    for _base, dirs, names in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if any(n.lower() == "skill.md" for n in names):
            return True
    return False


def _names(path):
    try:
        return [p.name for p in path.iterdir()]
    except OSError:
        return []


def scan(root, ignore=(), origin_detail=False):
    """Audit every skill under root.

    origin_detail=False (the free report) keeps only the KNOWN / UNKNOWN / DUPLICATED
    counts; True keeps each skill's origin evidence as well.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Path is not an existing directory: %s" % root)
    results = [audit_skill(d, root) for d in discover(root)]

    by_name, by_desc = {}, {}
    for r in results:
        if r["name"]:
            by_name.setdefault((r["_ns"], r["name"]), []).append(r)
        if r["description"]:
            by_desc.setdefault((r["_ns"], " ".join(r["description"].lower().split())), []).append(r)
    for (_ns, name), group in by_name.items():
        if len(group) > 1:
            for r in group:
                others = ", ".join(o["skill"] for o in group if o is not r)
                r["findings"].append(finding("error", "DUPLICATE_NAME", "name %r is also used by: %s" % (name, others)))
    for group in by_desc.values():
        if len(group) > 1:
            for r in group:
                others = ", ".join(o["skill"] for o in group if o is not r)
                r["findings"].append(finding("warning", "DUPLICATE_DESCRIPTION",
                                             "description is identical to: %s" % others))
    origin_counts = classify_origins(results)

    ignore = {c.strip().upper() for c in ignore if c.strip()}
    for r in results:
        r["findings"] = [f for f in r["findings"] if f["code"] not in ignore]
        r["findings"].sort(key=lambda f: (SEVERITIES.index(f["severity"]), f.get("line", 0), f["code"]))
        sev = {f["severity"] for f in r["findings"]}
        r["status"] = "FAIL" if "error" in sev else "WARN" if "warning" in sev else "PASS"
        r.pop("description", None)
        r.pop("_ns", None)
        if not origin_detail:
            r.pop("origin", None)

    counts = {k: sum(1 for r in results if r["status"] == k) for k in ("PASS", "WARN", "FAIL")}
    return {
        "tool": "Skill Audit Kit", "version": VERSION, "root": str(root),
        "total_skills": len(results), "counts": counts, "origin_counts": origin_counts,
        "tokens": {"always_loaded": sum(r["tokens"]["always_loaded"] for r in results),
                   "all_bodies": sum(r["tokens"]["body"] for r in results),
                   "note": "estimates (about 4 characters per token); real counts vary by model"},
        "skills": results,
    }


# --------------------------------------------------------------------------- output

def origin_line(report):
    """One closing line with the real origin numbers. Empty when there is nothing to say."""
    o = report.get("origin_counts") or {}
    unknown, dup = o.get("UNKNOWN", 0), o.get("DUPLICATED", 0)
    if not unknown and not dup:
        return ""
    return ("Origin unknown: %d skill(s), duplicated: %d skill(s) - origin table and fix list are in the "
            "Skill Audit Pro report." % (unknown, dup))


def render_text(report):
    c = report["counts"]
    lines = ["Skill Audit Kit v%s" % report["version"],
             "%d skills: PASS %d  WARN %d  FAIL %d" % (report["total_skills"], c["PASS"], c["WARN"], c["FAIL"]),
             "Estimated tokens: %d always loaded (names + descriptions), %d if every body loads"
             % (report["tokens"]["always_loaded"], report["tokens"]["all_bodies"]), ""]
    if not report["skills"]:
        lines.append("No skills found. Point this at a folder that contains SKILL.md files.")
    for r in report["skills"]:
        lines.append("[%s] %s  (%d lines, ~%d tokens)" % (r["status"], r["skill"], r["body_lines"], r["tokens"]["body"]))
        for f in r["findings"]:
            loc = " (line %d)" % f["line"] if f.get("line") else ""
            lines.append("  %-7s %s%s: %s" % (f["severity"].upper(), f["code"], loc, f["message"]))
    extra = origin_line(report)
    if extra:
        lines += ["", extra]
    return "\n".join(lines)


def safe_stdio():
    """Never crash on a console that cannot show a character (cp949, ascii): replace it instead."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and (stream.encoding or "").lower().replace("-", "").replace("_", "") != "utf8":
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def write_out(text):
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))


def main(argv=None):
    safe_stdio()
    parser = argparse.ArgumentParser(prog="skill_audit", description="Offline, read-only audit of agent skill folders.")
    parser.add_argument("path", help="project root, skills folder, or a single skill folder")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", help="write the report to this file instead of stdout")
    parser.add_argument("--force", action="store_true", help="allow --output to replace an existing file")
    parser.add_argument("--strict", action="store_true", help="exit 1 on warnings as well as errors")
    parser.add_argument("--ignore", default="", help="comma-separated finding codes to drop, e.g. NO_LICENSE_INFO")
    parser.add_argument("--version", action="version", version="Skill Audit Kit " + VERSION)
    args = parser.parse_args(argv)
    try:
        report = scan(args.path, ignore=args.ignore.split(","))
        if args.format == "text":
            rendered = render_text(report)
        else:
            rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w" if args.force else "x", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered + "\n")
        else:
            write_out(rendered)
    except FileExistsError:
        print("ERROR: output file already exists (use --force to replace it)", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    counts = report["counts"]
    return 1 if counts["FAIL"] or (args.strict and counts["WARN"]) else 0


if __name__ == "__main__":
    sys.exit(main())
