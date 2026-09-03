"""
default_version_change.py

Detects a change to a specific "FOO_DEFAULT?=" style variable
assignment (e.g. PYTHON_DEFAULT) by inspecting a unified diff
(patch text) for a commit -- not by evaluating make or diffing
whole-file content.

Intended use: your commit pipeline already knows a given commit
touches Mk/bsd.default-versions.mk. Hand this module the diff text
for that commit (or just the diff for that one file, if you've
already isolated it) and ask whether a specific variable's value
changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ChangeResult:
    status: str                        # 'changed' | 'unchanged' | 'anomaly'
    old: Optional[str] = None
    new: Optional[str] = None
    detail: Optional[str] = None
    raw: Optional[str] = None

    def __bool__(self) -> bool:
        # Convenience: `if result:` is True only when a real change was found.
        return self.status == "changed"


def detect_default_version_change(
    diff_text: str,
    varname: str,
    target_file: Optional[str] = None,
) -> ChangeResult:
    """
    diff_text    - the patch/diff text for the commit (may contain
                   multiple files' hunks; we isolate target_file)
    varname      - e.g. 'PYTHON_DEFAULT'
    target_file  - path as it appears in the diff headers, e.g.
                   'Mk/bsd.default-versions.mk'. Optional -- if
                   omitted, the whole diff_text is scanned as-is
                   (use this if you've already pre-scoped the diff
                   to just that file).

    Returns a ChangeResult:
        ChangeResult(status='changed',   old='3.11', new='3.12')
        ChangeResult(status='unchanged')                                  # var line not touched
        ChangeResult(status='anomaly',   detail='...', raw='...')         # removed w/o added, or vice versa, or >1 pair
    """
    if not varname:
        raise ValueError("varname required")

    scoped = _isolate_file_diff(diff_text, target_file) if target_file else diff_text

    if not scoped:
        return ChangeResult(status="unchanged")

    quoted = re.escape(varname)
    removed_re = re.compile(rf"^-(?!--)\s*{quoted}\??=\s*(\S+)")
    added_re = re.compile(rf"^\+(?!\+\+)\s*{quoted}\??=\s*(\S+)")

    removed: list[str] = []
    added: list[str] = []

    for line in scoped.splitlines():
        m = removed_re.match(line)
        if m:
            removed.append(_strip_comment(m.group(1)))
            continue
        m = added_re.match(line)
        if m:
            added.append(_strip_comment(m.group(1)))

    if not removed and not added:
        return ChangeResult(status="unchanged")

    if len(removed) == 1 and len(added) == 1:
        if removed[0] == added[0]:
            # Line was touched (e.g. whitespace, comment) but the
            # actual value didn't change.
            return ChangeResult(status="unchanged")
        return ChangeResult(status="changed", old=removed[0], new=added[0])

    # Anything else -- 0/1 mismatch, or multiple assignment lines in
    # one commit -- don't guess, surface it for a human to check.
    return ChangeResult(
        status="anomaly",
        detail=(
            f"expected exactly one removed and one added {varname} line; "
            f"got {len(removed)} removed, {len(added)} added"
        ),
        raw=scoped,
    )


def _strip_comment(val: str) -> str:
    """
    Strip a trailing make comment ("# ...") and surrounding whitespace
    from a captured value, e.g. "3.12" from "3.12   # supported until 2028".
    Note: the capture regex above already stops at \\s+ before a
    comment in the common case, but this guards against values that
    were captured with an attached comment due to no space, or tabs.
    """
    val = re.sub(r"\s*#.*$", "", val)
    return val.rstrip()


def _isolate_file_diff(diff_text: str, target_file: str) -> str:
    """
    Cut a multi-file diff down to just the hunks for target_file.
    Handles standard git diff headers:
        diff --git a/Mk/bsd.default-versions.mk b/Mk/bsd.default-versions.mk
        ...
        @@ -a,b +c,d @@
        <hunk lines>
        diff --git a/<next file> ...   <- stop here
    """
    quoted = re.escape(target_file)
    start_re = re.compile(rf"^diff --git a/{quoted} b/{quoted}\b")
    any_diff_re = re.compile(r"^diff --git ")

    out: list[str] = []
    in_target = False

    for line in diff_text.splitlines():
        if start_re.match(line):
            in_target = True
            continue
        elif any_diff_re.match(line):
            in_target = False
            continue

        if in_target:
            out.append(line)

    return "\n".join(out)
