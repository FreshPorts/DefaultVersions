#!/usr/bin/env python3
"""
sync_default_version_variable.py

Brings the default_version_variable table into sync with the live
Mk/bsd.default-versions.mk file. Distinct from
detect_default_version_changes.py (which reacts to a single commit's
diff) -- this reads the file's CURRENT full content and reconciles
the table's whole state against it. Can be run at any time, not tied
to a commit event.

What "sync" means here:
    - Every *_DEFAULT variable found in the file gets upserted
      (inserted if new, updated if shape/active changed) with
      last_synced_at set to now().
    - Any variable currently marked active=true in the table but NOT
      found in this file scan gets soft-deactivated (active=false,
      last_synced_at=now()) -- NOT deleted. Hard deletion is a
      separate, deliberate decision (see --hard-delete), not an
      automatic side effect of a routine sync, since other tables may
      reference rows here by id.

How variables are classified (derived from the actual file content,
not a hand-maintained guess):
    - active: true if at least one non-commented assignment line
      exists for the variable anywhere in the file.
    - shape:
        'complex'     - any assignment uses the ':=' or '!=' operator
                         (computed/derived value, e.g. PERL5_DEFAULT,
                         SSL_DEFAULT)
        'conditional' - more than one assignment line for the
                         variable (branched under .if/.elif/.else),
                         all using '?=' or '='
        'simple'      - exactly one assignment line, unconditioned

Usage:
    python3 sync_default_version_variable.py --repo /usr/ports [--dry-run]

Requires psycopg2 for actual DB execution; connection parameters come
from /usr/local/etc/freshports/config.ini unless --dsn is given.
Connections require TLS -- see connection_params_from_config().
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# for FreshPorts database connection
import configparser # for config.ini parsing
import re           # for escaping the database passwords


TARGET_RELATIVE_PATH = "Mk/bsd.default-versions.mk"

# Matches an assignment line (after stripping any leading '#' + whitespace
# for comment lines) whose LHS is a bareword ending in _DEFAULT -- this
# naturally excludes computed LHS forms like ${lang}_DEFAULT or
# ${_l:tu}_DEFAULT used by the reserved-name-guard .for loop, since those
# start with '$', not a letter/underscore.
ASSIGNMENT_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*_DEFAULT)\s*(\?=|:=|\+=|!=|=)"
)

IF_RE = re.compile(r"^\.\s*(if|ifdef|ifndef)\b")
ELIF_ELSE_RE = re.compile(r"^\.\s*(elif|else)\b")
ENDIF_RE = re.compile(r"^\.\s*endif\b")


@dataclass
class ParsedVar:
    name: str
    shape: str      # 'simple' | 'conditional' | 'complex'
    active: bool


@dataclass
class _Occurrence:
    op: str
    depth: int
    active: bool


def parse_default_versions_file(text: str) -> list[ParsedVar]:
    """
    Parse the full content of Mk/bsd.default-versions.mk (or an
    equivalent file with the same conventions) and return one
    ParsedVar per *_DEFAULT variable found.

    Pure function, no I/O -- easy to unit test against fixture text.
    """
    occurrences: dict[str, list[_Occurrence]] = {}
    depth = 0

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue

        # Track .if/.elif/.else/.endif nesting. Directive lines are
        # matched against the stripped line with leading dots/spaces
        # normalized (the ports tree convention adds extra spaces
        # after the dot per nesting level purely for readability --
        # normalize by removing whitespace right after the leading
        # dot before matching).
        directive = re.sub(r"^\.\s*", ".", stripped)

        if IF_RE.match(directive):
            depth += 1
            continue
        if ELIF_ELSE_RE.match(directive):
            continue
        if ENDIF_RE.match(directive):
            depth = max(0, depth - 1)
            continue
        if directive.startswith(".for") or directive.startswith(".endfor"):
            continue  # not relevant -- see ASSIGNMENT_RE docstring note

        is_comment = stripped.startswith("#")
        content = stripped[1:].strip() if is_comment else stripped

        m = ASSIGNMENT_RE.match(content)
        if not m:
            continue

        varname, op = m.group(1), m.group(2)
        occurrences.setdefault(varname, []).append(
            _Occurrence(op=op, depth=depth, active=not is_comment)
        )

    results = []
    for varname, occs in occurrences.items():
        active = any(o.active for o in occs)
        if any(o.op in (":=", "!=") for o in occs):
            shape = "complex"
        elif len(occs) > 1:
            shape = "conditional"
        else:
            shape = "simple"
        results.append(ParsedVar(name=varname, shape=shape, active=active))

    return sorted(results, key=lambda v: v.name)


POSSIBLE_VALUES_RE = re.compile(r"possible[_ ]values?:\s*(.+)", re.IGNORECASE)

# Matches a comment line that's purely comma-separated tokens (no internal
# whitespace within a token), used to detect when a "Possible values:" list
# wraps onto the following comment line(s) vs. hitting unrelated prose.
CONTINUATION_LIST_RE = re.compile(r"^[A-Za-z0-9_.\-]+(?:\s*,\s*[A-Za-z0-9_.\-]+)*,?$")


def extract_possible_values(text: str, varname: str) -> list[str]:
    """
    Scan the file for a "# Possible values: a, b, c" (or "Possible
    value:", or "Possible_values:") style comment immediately
    preceding an assignment line for `varname`, and return the listed
    tokens.

    This is what lets differential probing use a REAL alternate value
    instead of a made-up sentinel -- necessary for *_DEFAULT variables
    (like PYTHON_DEFAULT) whose value selects a sibling port directory
    rather than just flowing into a string comparison. A fake value
    can't work for those no matter how it's formatted; a real
    alternate always can.

    Best-effort text scan, not a full parser -- if no matching comment
    is found (or the format doesn't match), returns an empty list and
    callers should fall back to sentinel probing.
    """
    lines = text.splitlines()
    for i, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped.startswith("#"):
            continue

        content = stripped[1:].strip()
        m = POSSIBLE_VALUES_RE.match(content)
        if not m:
            continue

        # The list itself may wrap onto subsequent comment lines (e.g.
        # SSL_DEFAULT's "base, openssl, ..." list continues on the next
        # line as "openssl36, libressl, libressl-devel"). Merge those
        # continuation lines in, but only ones that look like pure
        # comma-separated tokens -- an unrelated prose comment (like the
        # "If no preference was set..." explanation that often follows)
        # won't match this and correctly stops the merge.
        values_text = m.group(1)
        j = i + 1
        while j < len(lines):
            nxt = lines[j].strip()
            if not nxt.startswith("#"):
                break
            nxt_content = nxt[1:].strip()
            if CONTINUATION_LIST_RE.match(nxt_content):
                values_text += " " + nxt_content
                j += 1
                continue
            break

        # Does this comment actually precede an assignment for varname?
        # The real assignment may be nested behind .if/.elif guards -- e.g.
        # SSL_DEFAULT checks for an already-installed provider before
        # falling back to its default -- so keep scanning forward past
        # directives, backslash-continued condition lines, blank lines,
        # and other comments. Only stop early if we hit an assignment for
        # a DIFFERENT *_DEFAULT variable, since that means we've wandered
        # into the next variable's block without finding ours.
        LOOKAHEAD_WINDOW = 40
        for lookahead in lines[i + 1 : i + 1 + LOOKAHEAD_WINDOW]:
            la_stripped = lookahead.strip()
            if not la_stripped:
                continue
            if la_stripped.startswith("#"):
                continue  # multi-line comment block, keep looking

            la_match = ASSIGNMENT_RE.match(la_stripped)
            if la_match:
                if la_match.group(1) == varname:
                    # Strip a trailing parenthetical note, e.g.
                    # "1.20, 1.21 (Any other version is unsupported)".
                    final_values_text = re.split(r"\s*\(", values_text)[0]
                    tokens = [t.strip() for t in re.split(r"[,\s]+", final_values_text) if t.strip()]
                    return tokens
                break  # assignment for a different variable -- not ours

            # anything else -- .if/.elif/.endif directives, backslash-
            # continued condition lines, etc. -- keep looking, the real
            # assignment may be further down inside this nesting

    return []


# Connections to the database must be encrypted. libpq's own default
# sslmode is 'prefer', which negotiates TLS when the server offers it
# and silently continues in PLAINTEXT when it doesn't -- so a server
# that stops offering TLS, or a connection redirected somewhere else,
# downgrades without anything being logged or raised. Pinning 'require'
# makes that a connection failure instead.
#
# Note what 'require' does not do: it encrypts, but it does not
# authenticate the server, so it doesn't stop an attacker who can
# redirect the connection from presenting their own certificate. If the
# server's CA certificate is available to these scripts, 'verify-full'
# (plus sslrootcert=/path/to/ca.crt) is the setting that actually rules
# out a man in the middle.
DEFAULT_SSLMODE = 'require'


def connection_params_from_config(config) -> dict:
    """
    libpq connection parameters from a freshports config.ini.

    Returned as keyword arguments for psycopg2.connect(**params) rather
    than as a DSN string, so psycopg2 quotes each value for libpq. In
    particular the password: it can't be safely pasted into a
    hand-assembled DSN, and re.escape() is not the escaper for the job
    -- that's a REGEX escaper, and it leaves libpq's own metacharacter,
    a leading single quote, untouched.

    sslcertmode='disable' is unrelated to sslmode: it says we never send
    a CLIENT certificate, which is still true.
    """
    return {
        'host': config['database']['HOST'],
        'dbname': config['database']['DBNAME'],
        'user': config['database']['DEFAULTS_DBUSER'],
        'password': config['database']['DEFAULTS_PASSWORD'],
        'sslmode': DEFAULT_SSLMODE,
        'sslcertmode': 'disable',
    }


def require_ssl(dsn: str) -> str:
    """
    Add sslmode to a caller-supplied --dsn that doesn't set one, so an
    operator-supplied DSN can't quietly connect in the clear either.

    A DSN that DOES name an sslmode is left alone -- 'verify-full' is
    stronger than what we'd impose, and a deliberate 'disable' (a local
    test instance, say) is the operator's call to make explicitly.
    """
    from psycopg2.extensions import make_dsn, parse_dsn

    params = parse_dsn(dsn)
    params.setdefault('sslmode', DEFAULT_SSLMODE)
    return make_dsn(**params)


def read_target_file(repo: str) -> str:
    path = Path(repo) / TARGET_RELATIVE_PATH
    return path.read_text()


@dataclass
class SyncPlan:
    to_insert: list[ParsedVar]
    to_update: list[tuple[str, dict, dict]]   # (name, old_values, new_values)
    to_deactivate: list[str]
    unchanged: list[str]


def compute_sync_plan(conn, records: list[ParsedVar]) -> SyncPlan:
    """
    Read-only: compares `records` (freshly parsed current file state)
    against the table's current contents and returns what WOULD
    change, without writing anything. Shared by both the actual sync
    (which executes this plan) and --check (which only reports it).
    """
    cur = conn.cursor()
    cur.execute("SELECT name, shape, active FROM default_version_variable")
    existing = {row[0]: {"shape": row[1], "active": bool(row[2])} for row in cur.fetchall()}

    scanned_names = {r.name for r in records}
    to_insert, to_update, unchanged = [], [], []

    for r in records:
        if r.name not in existing:
            to_insert.append(r)
            continue
        old = existing[r.name]
        new = {"shape": r.shape, "active": r.active}
        if old != new:
            to_update.append((r.name, old, new))
        else:
            unchanged.append(r.name)

    to_deactivate = sorted(
        name for name, vals in existing.items()
        if vals["active"] and name not in scanned_names
    )

    return SyncPlan(
        to_insert=to_insert,
        to_update=to_update,
        to_deactivate=to_deactivate,
        unchanged=unchanged,
    )


def sync_table(conn, records: list[ParsedVar], hard_delete: bool = False) -> dict:
    """
    Reconcile the default_version_variable table against `records`
    (the freshly parsed current state of the file). Computes a plan
    via compute_sync_plan(), then executes only the real changes in
    it -- rows that already match the file are left untouched
    entirely (no-op UPDATEs are skipped, not just cheap-but-issued).

    Returns a summary dict: {inserted: [...], updated: [...], deactivated: [...], deleted: [...]}
    """
    plan = compute_sync_plan(conn, records)
    cur = conn.cursor()

    for r in plan.to_insert:
        cur.execute(
            "INSERT INTO default_version_variable (name, shape, active, last_synced_at) "
            "VALUES (%s, %s, %s, now())",
            (r.name, r.shape, r.active),
        )

    for name, _old, new in plan.to_update:
        cur.execute(
            "UPDATE default_version_variable "
            "SET shape = %s, active = %s, last_synced_at = now() "
            "WHERE name = %s",
            (new["shape"], new["active"], name),
        )

    deleted = []
    deactivated = []
    for name in plan.to_deactivate:
        if hard_delete:
            cur.execute("DELETE FROM default_version_variable WHERE name = %s", (name,))
            deleted.append(name)
        else:
            cur.execute(
                "UPDATE default_version_variable "
                "SET active = false, last_synced_at = now() "
                "WHERE name = %s AND active = true",
                (name,),
            )
            if cur.rowcount:
                deactivated.append(name)

    conn.commit()
    return {
        "inserted": [r.name for r in plan.to_insert],
        "updated": [name for name, _o, _n in plan.to_update],
        "deactivated": deactivated,
        "deleted": deleted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Path to local ports tree checkout")
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: libpq env vars)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print, don't touch the DB")
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Connect read-only, compare against the table, and report drift "
            "without writing anything. Exit code 0 = in sync, 1 = drift found "
            "(or a real error) -- suitable for a monitoring/alerting job."
        ),
    )
    parser.add_argument(
        "--hard-delete",
        action="store_true",
        help="Delete rows for vars no longer in the file, instead of soft-deactivating them",
    )
    args = parser.parse_args()

    text = read_target_file(args.repo)
    records = parse_default_versions_file(text)

    if args.dry_run:
        for r in records:
            print(f"{r.name}\tshape={r.shape}\tactive={r.active}")
        print(f"\n{len(records)} variables parsed from {args.repo}/{TARGET_RELATIVE_PATH}")
        return 0

    import psycopg2  # imported lazily -- not needed for --dry-run

    config = configparser.ConfigParser()
    config.read('/usr/local/etc/freshports/config.ini')

    SCRIPT_DIR = config['filesystem']['SCRIPT_DIR']

    conn_params = connection_params_from_config(config)

    if args.check:
        conn = psycopg2.connect(require_ssl(args.dsn)) if args.dsn else psycopg2.connect(**conn_params)
        try:
            plan = compute_sync_plan(conn, records)
        finally:
            conn.close()

        drift = bool(plan.to_insert or plan.to_update or plan.to_deactivate)

        for r in plan.to_insert:
            print(f"WOULD INSERT   {r.name}\t(shape={r.shape}, active={r.active})")
        for name, old, new in plan.to_update:
            print(f"WOULD UPDATE   {name}\t{old} -> {new}")
        for name in plan.to_deactivate:
            print(f"WOULD DEACTIVATE {name}")

        print(
            f"\n{len(plan.unchanged)} unchanged, "
            f"{len(plan.to_insert)} to insert, "
            f"{len(plan.to_update)} to update, "
            f"{len(plan.to_deactivate)} to deactivate"
        )
        if not drift:
            print("table is in sync with the file")

        return 1 if drift else 0

    conn = psycopg2.connect(require_ssl(args.dsn)) if args.dsn else psycopg2.connect(**conn_params)
    try:
        summary = sync_table(conn, records, hard_delete=args.hard_delete)
    finally:
        conn.close()

    print(f"inserted:    {len(summary['inserted'])} {summary['inserted']}")
    print(f"updated:     {len(summary['updated'])}")
    print(f"deactivated: {len(summary['deactivated'])} {summary['deactivated']}")
    print(f"deleted:     {len(summary['deleted'])} {summary['deleted']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
