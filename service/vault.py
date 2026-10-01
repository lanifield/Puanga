"""Scans the Mahara vault for selectable project/course options.

Every Projects/<name>/ folder has a <name>_frontmatter.md (built from
project_frontmatter.md) declaring a `client:` field; every Courses/<name>/
folder has a <name>_overview.md (built from course_overview.md) declaring a
`provider:` field instead — which serves the same role for time-tracking
purposes (who/what this work is for). Folders without a matching file yet
aren't real entries, so they're skipped.
"""

import os
import re
from pathlib import Path

VAULT_ROOT = Path(os.environ.get("PUANGA_VAULT_ROOT", r"G:\Mahara"))

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---", re.DOTALL)


def _extract_field(frontmatter_body, field):
    match = re.search(rf"^{re.escape(field)}:[ \t]*(.*)$", frontmatter_body, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip().strip('"').strip("'")
    return value or None


def _read_frontmatter(path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _FRONTMATTER_RE.match(text)
    return match.group(1) if match else None


def scan_options():
    """Returns a list of {value, label, client} dicts, one per project/course
    folder that has its frontmatter file with a non-blank name and client/provider."""
    options = []
    sources = [
        ("Projects", "*_frontmatter.md", "project_name", "client"),
        ("Courses", "*_overview.md", "course_name", "provider"),
    ]
    for subdir, file_pattern, name_field, client_field in sources:
        base = VAULT_ROOT / subdir
        if not base.is_dir():
            continue
        for folder in sorted(base.iterdir()):
            if not folder.is_dir():
                continue
            matches = sorted(folder.glob(file_pattern))
            if not matches:
                continue
            fm_body = _read_frontmatter(matches[0])
            if fm_body is None:
                continue
            label = _extract_field(fm_body, name_field)
            client = _extract_field(fm_body, client_field)
            if not label or not client:
                continue  # incomplete frontmatter — not usable as a time-tracking target yet
            options.append({"value": folder.name, "label": label, "client": client})
    return options
