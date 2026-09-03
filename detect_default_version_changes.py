#!/usr/bin/env python3
"""
detect_default_version_changes.py

Command-line entry point launched by the freshports daemon when the
existing (Perl) commit-processing code flags a commit as having
touched Mk/bsd.default-versions.mk.

Invocation (example):

    detect_default_version_changes.py \\
        --commit 135741412e9fe53270e9078d14f71066a2aecaa6 \\
        --repo /usr/ports \\
        --output /var/freshports/work/135741412e9fe53270e9078d14f71066a2aecaa6.json

What it does:
    1. Fetches the diff for Mk/bsd.default-versions.mk for the given
       commit (on demand -- this script does the git subprocess call
       itself, the daemon doesn't need to hand it a diff).
    2. Runs detect_default_version_change() against every *_DEFAULT
       variable in default_versions_config (or a restricted subset,
       via --vars), using the real detection logic already validated
       against several real freebsd-ports commits.
    3. Writes a single JSON result file for the daemon to read.
       Written atomically (temp file + rename) so the daemon never
       sees a partially-written file.

Exit codes:
    0  - ran successfully; check the output file's "results" for
         what was actually found (changed / unchanged / anomaly per
         variable -- exit 0 does NOT mean "nothing changed", it means
         "the check itself completed").
    1  - the check itself failed (bad args, git failure, couldn't
         write output). A best-effort error JSON is still written to
         --output when possible, so the daemon has something to read
         even on failure -- but always check the exit code first.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from default_version_change import detect_default_version_change
from sync_default_version_variable import parse_default_versions_file, read_target_file

TARGET_FILE = "Mk/bsd.default-versions.mk"


def fetch_diff(repo: str, commit: str, path: str) -> str:
    """
    Run `git -C <repo> show <commit> -- <path>` and return its stdout.

    Args passed as a list (never a shell string), so commit/path
    values can't be interpreted by a shell even if they contained
    unexpected characters.
    """
    cmd = ["git", "-C", repo, "show", commit, "--", path]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git show exited {proc.returncode} for {commit} -- {path}: "
            f"{proc.stderr.strip()}"
        )
    return proc.stdout


def write_result_atomically(output_path: str, payload: dict) -> None:
    """
    Write JSON to output_path atomically: write to a temp file in the
    same directory, then os.replace() it into place. This guarantees
    the daemon never reads a half-written file, regardless of when it
    happens to poll.
    """
    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=out_dir, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_path, output_path)
    except Exception:
        # Clean up the temp file if we didn't get to the rename.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="Commit SHA to check")
    parser.add_argument("--repo", required=True, help="Path to local ports tree checkout")
    parser.add_argument("--output", required=True, help="Path to write the JSON result file")
    parser.add_argument(
        "--vars",
        default=None,
        help=(
            "Comma-separated list of *_DEFAULT variable names to check "
            "(default: all currently-active variables from "
            "default_versions_config)"
        ),
    )
    parser.add_argument(
        "--target-file",
        default=TARGET_FILE,
        help=f"Path within the repo to check (default: {TARGET_FILE})",
    )
    args = parser.parse_args()

    checked_at = datetime.now(timezone.utc).isoformat()

    varnames = (
        [v.strip() for v in args.vars.split(",") if v.strip()]
        if args.vars
        else [
            v.name for v in parse_default_versions_file(read_target_file(args.repo))
            if v.active
        ]
    )

    try:
        diff_text = fetch_diff(args.repo, args.commit, args.target_file)
    except RuntimeError as exc:
        payload = {
            "commit": args.commit,
            "target_file": args.target_file,
            "checked_at": checked_at,
            "error": str(exc),
        }
        try:
            write_result_atomically(args.output, payload)
        except OSError:
            pass  # we tried -- exit code is the authoritative failure signal
        print(f"error: {exc}", file=sys.stderr)
        return 1

    results = []
    for varname in varnames:
        r = detect_default_version_change(diff_text, varname, target_file=None)
        entry = {"var": varname, "status": r.status}
        if r.status == "changed":
            entry["old"] = r.old
            entry["new"] = r.new
        elif r.status == "anomaly":
            entry["detail"] = r.detail
            # Deliberately omit r.raw from the result file -- it's the
            # whole matched diff region, useful for interactive
            # debugging but noisy to persist for every anomaly.
        results.append(entry)

    changed = [r["var"] for r in results if r["status"] == "changed"]
    anomalies = [r["var"] for r in results if r["status"] == "anomaly"]

    payload = {
        "commit": args.commit,
        "target_file": args.target_file,
        "checked_at": checked_at,
        "checked_vars": varnames,
        "results": results,
        "changed": changed,
        "anomalies": anomalies,
    }

    try:
        write_result_atomically(args.output, payload)
    except OSError as exc:
        print(f"error: failed to write output file {args.output}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
