"""
port_default_version_deps.py


Sample usage:

$ cd /usr/local/libexec/freshports
$ python3 ./port_default_version_deps.py /jails/freshports/usr/ports/lang/python
/jails/freshports/usr/ports/lang/python: depends on PYTHON_DEFAULT (status=depends)
  baseline:   {'PORTVERSION': '3.12', 'DISTVERSION': '3.12'}
  overridden: {'PORTVERSION': '3.10', 'DISTVERSION': '3.10'}

$ python3 ./port_default_version_deps.py /jails/freshports/usr/ports/net-mgmt/librenms
/jails/freshports/usr/ports/net-mgmt/librenms: no dependency on any checked *_DEFAULT variable
$

Determines whether a given port's PORTVERSION/DISTVERSION depends on
a *_DEFAULT variable (e.g. PYTHON_DEFAULT) -- by asking `make` itself,
via differential evaluation, rather than parsing the port's Makefile.

Technique:
    1. Run `make -C <port_dir> -V PORTVERSION -V DISTVERSION` for a
       baseline.
    2. Run it again with the candidate variable overridden on the
       command line to an obviously-fake probe value.
    3. Compare. A difference (or a make failure caused by the probe
       value failing the port's own validation) means the port's
       version genuinely depends on that variable.

This deliberately does not parse Makefile text at all -- `make`
evaluates the real .include chain (Mk/bsd.default-versions.mk,
Uses/*.mk, conditionals, etc.), so this is authoritative in a way
regex-based scanning can't be.

Note: this has not been run against a real FreeBSD ports tree (this
environment has neither bmake nor a ports checkout available) --
only sanity-checked for subprocess/argument-handling correctness
against a fake `make` stand-in. Please validate it against your real
tree before relying on it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Optional

VERSION_VARS = ("PORTVERSION", "DISTVERSION")
DEFAULT_PROBE_VALUE = "999999.9999"
# Numeric-shaped on purpose: some Uses/*.mk files (e.g. python.mk) run the
# *_DEFAULT value through their own internal numeric version comparisons
# (.for loops using "<" etc.) regardless of which variable is actually
# being probed, and a non-numeric sentinel (the previous value here,
# "999999.freshports-probe") makes those comparisons fatal-error out --
# a false "depends_error" that has nothing to do with an actual
# dependency. This value is still obviously out-of-range for any real
# *_DEFAULT variable while being safe to compare numerically.
#
# This is still only a fallback: it does NOT help variables validated
# against a whitelist of names rather than compared numerically (e.g.
# SSL_DEFAULT's list of provider names) -- those need a real alternate
# from possible_values, which is why extract_possible_values() finding
# real values is the primary fix and this is the last-resort path for
# variables with no "Possible values:" comment to draw from at all.

# Module-level debug flag. When run as a script, __main__ below sets
# this from --debug. When imported (e.g. by populate_port_default_version_deps.py),
# it defaults to False -- callers that want debug output from a bulk
# run can opt in explicitly with:
#     import port_default_version_deps
#     port_default_version_deps.DEBUG = True
DEBUG = False


@dataclass
class DependencyResult:
    status: str                     # 'depends' | 'depends_error' | 'independent' | 'error'
    default_var: str
    baseline: Optional[dict] = None
    overridden: Optional[dict] = None
    probe_appears: Optional[bool] = None
    probe_value: Optional[str] = None
    detail: Optional[str] = None


def _run_make(port_dir: str, overrides: Optional[dict] = None, extra_vars: Optional[list] = None) -> tuple[int, dict, str]:
    """
    Run `make -C port_dir -V PORTVERSION -V DISTVERSION [-V extra ...] [VAR=value ...]`.

    extra_vars lets callers also query the *_DEFAULT variable's own
    current value in the same invocation (used to pick a real
    alternate probe value -- see port_depends_on_default_var).

    Returns (returncode, {varname: value}, stderr_text). On non-zero
    exit, the values dict may be incomplete/empty -- callers check
    returncode before trusting it.
    """
    query_vars = list(VERSION_VARS) + list(extra_vars or [])
    cmd = ["make", "-C", port_dir]
    for v in query_vars:
        cmd += ["-V", v]
    if overrides:
        for k, v in overrides.items():
            cmd.append(f"{k}={v}")

    if DEBUG:
        print(cmd)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    lines = proc.stdout.splitlines()
    values = dict(zip(query_vars, lines)) if proc.returncode == 0 else {}
    return proc.returncode, values, proc.stderr.strip()


def port_depends_on_default_var(
    port_dir: str,
    default_var: str,
    probe_value: str = DEFAULT_PROBE_VALUE,
    possible_values: Optional[list[str]] = None,
) -> DependencyResult:
    """
    Check whether the port at port_dir has a PORTVERSION/DISTVERSION
    that depends on the given *_DEFAULT variable.

    port_dir        - path to the port's directory (e.g. /usr/ports/lang/python)
    default_var     - e.g. 'PYTHON_DEFAULT'
    probe_value     - fallback fake value, used only if possible_values
                       isn't given or has no usable alternate. Some
                       *_DEFAULT variables (notably PYTHON_DEFAULT) are
                       used to select a SIBLING PORT DIRECTORY, not
                       just compared against -- for those, any made-up
                       value fails regardless of formatting, because
                       the referenced port directory doesn't exist.
    possible_values - list of the variable's real legitimate values
                       (e.g. from extract_possible_values() against
                       Mk/bsd.default-versions.mk). When given, the
                       probe uses a REAL alternate value instead of a
                       fake one -- this is the reliable path, and
                       avoids the "Cannot open .../Makefile.version"
                       class of failure entirely, since a real value
                       corresponds to a real port/branch.
    """
    extra = [default_var] if possible_values else []
    rc, baseline_full, err = _run_make(port_dir, extra_vars=extra)
    if rc != 0:
        return DependencyResult(
            status="error",
            default_var=default_var,
            detail=f"baseline `make -V` failed (exit {rc}): {err}",
        )
    baseline = {k: baseline_full[k] for k in VERSION_VARS}

    chosen_probe = None
    used_real_alternate = False
    if possible_values:
        current_value = baseline_full.get(default_var)
        alternates = [v for v in possible_values if v != current_value]
        if alternates:
            chosen_probe = alternates[0]
            used_real_alternate = True
    if chosen_probe is None:
        chosen_probe = probe_value

    rc2, overridden, err2 = _run_make(port_dir, overrides={default_var: chosen_probe})

    if rc2 != 0:
        # The override itself broke the build. If we were using a REAL
        # alternate value and it still failed, that's a genuinely
        # unusual/notable case worth flagging as such in the detail --
        # a legitimate value shouldn't normally break the build.
        note = (
            "(used a real alternate value from possible_values, not a fake sentinel -- "
            "unexpected that this still failed)"
            if used_real_alternate else ""
        )
        return DependencyResult(
            status="depends_error",
            default_var=default_var,
            baseline=baseline,
            probe_value=chosen_probe,
            detail=f"`make -V` with {default_var}={chosen_probe} failed (exit {rc2}): {err2} {note}".strip(),
        )

    if overridden != baseline:
        probe_appears = any(chosen_probe in v for v in overridden.values())
        return DependencyResult(
            status="depends",
            default_var=default_var,
            baseline=baseline,
            overridden=overridden,
            probe_appears=probe_appears,
            probe_value=chosen_probe,
        )

    return DependencyResult(status="independent", default_var=default_var, baseline=baseline)


def find_all_dependencies(
    port_dir: str,
    default_vars: list[str],
    repo_root: Optional[str] = None,
) -> list[DependencyResult]:
    """
    Check a port against a list of *_DEFAULT variable names (e.g. from
    a live parse of Mk/bsd.default-versions.mk) and return only the
    ones it actually depends on ('depends' or 'depends_error').

    repo_root - if given, Mk/bsd.default-versions.mk is read once and
                used to look up real possible_values for each
                variable, so probing uses real alternates rather than
                the fake sentinel wherever possible. Strongly
                recommended -- omit only if you don't have a repo
                checkout available.

    PERFORMANCE: most ports depend on NONE of the *_DEFAULT variables
    (empirically: 0 out of 252 in a real `biology` category run). So
    rather than testing each variable one at a time (2 `make` calls
    per variable -- a baseline plus an override -- each requiring a
    full bsd.port.mk/Uses/*.mk evaluation), this first tries ONE
    combined call that overrides every variable in `default_vars`
    simultaneously:

        1. One combined baseline call (PORTVERSION, DISTVERSION, and
           every variable's current value, all in one `make -V`
           invocation).
        2. One combined override call (same, but every variable set
           to its probe value at once).
        3. If PORTVERSION/DISTVERSION didn't move, the port depends
           on NONE of them -- done, in 2 calls total instead of
           2 * len(default_vars).

    Only if that combined check shows a difference (or errors) does
    it fall back to testing each variable individually, to correctly
    attribute which one(s) are responsible. That fallback is exactly
    the original one-at-a-time algorithm, so a port that DOES have a
    dependency gets identical results either way -- this only changes
    how many `make` calls the common (independent) case costs, not
    what any port is reported as depending on.

    Caveat: the combined-override step assumes overriding multiple
    unrelated *_DEFAULT variables at once doesn't interact in some
    surprising way for a given port (e.g. one variable's fallback
    sentinel colliding with an unrelated check elsewhere). This is
    not expected in practice -- each *_DEFAULT variable governs
    independent, unrelated infrastructure -- and even if it happened,
    the result would be a false "might depend on something" that
    triggers the accurate fallback path, not a wrong final answer.
    """
    possible_values_by_var = {}
    if repo_root:
        from sync_default_version_variable import extract_possible_values, read_target_file
        mk_text = read_target_file(repo_root)
        for var in default_vars:
            vals = extract_possible_values(mk_text, var)
            if vals:
                possible_values_by_var[var] = vals

    def _slow_path() -> list[DependencyResult]:
        results = []
        for var in default_vars:
            r = port_depends_on_default_var(
                port_dir, var, possible_values=possible_values_by_var.get(var)
            )
            if r.status in ("depends", "depends_error"):
                results.append(r)
        return results

    # Combined baseline: PORTVERSION/DISTVERSION plus every variable's
    # current value, all in one call.
    rc, baseline_full, err = _run_make(port_dir, extra_vars=default_vars)
    if rc != 0:
        # Baseline itself failed -- can't determine anything from the
        # fast path. Fall back (matches the original per-variable
        # behavior, where a baseline failure for a given variable
        # produces an 'error' result that find_all_dependencies
        # silently excludes from its return value).
        return _slow_path()
    baseline = {k: baseline_full[k] for k in VERSION_VARS}

    # Pick a probe value for every variable up front: a real
    # alternate when possible_values gives us one, else the fallback
    # sentinel.
    combined_overrides = {}
    for var in default_vars:
        possible = possible_values_by_var.get(var)
        chosen = None
        if possible:
            current_value = baseline_full.get(var)
            alternates = [v for v in possible if v != current_value]
            if alternates:
                chosen = alternates[0]
        combined_overrides[var] = chosen if chosen is not None else DEFAULT_PROBE_VALUE

    rc2, combined_overridden, err2 = _run_make(port_dir, overrides=combined_overrides)

    if rc2 == 0:
        overridden = {k: combined_overridden[k] for k in VERSION_VARS}
        if overridden == baseline:
            # Fast path: overriding EVERY candidate variable at once
            # changed nothing -- this port depends on none of them.
            return []

    # Something moved (or the combined override broke the build) --
    # fall back to testing each variable individually to attribute
    # the effect correctly. Only reached for ports that actually have
    # a dependency, so the extra cost is rare in aggregate.
    return _slow_path()


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    from sync_default_version_variable import parse_default_versions_file, read_target_file

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port_dir", help="Path to the port directory")
    parser.add_argument(
        "--vars",
        default=None,
        help="Comma-separated *_DEFAULT vars to check (default: all active vars, parsed live from the repo's Mk/bsd.default-versions.mk)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Path to the ports tree root, used only to find Mk/bsd.default-versions.mk for the default var list. "
             "Defaults to two levels up from port_dir (the standard <repo>/category/port layout).",
    )
    parser.add_argument(
        "--debug",
        action='store_true',
        help="Prints additional information.",
    )
    args = parser.parse_args()

    DEBUG = args.debug

    repo_root = args.repo or str(Path(args.port_dir).resolve().parent.parent)

    if args.vars:
        varnames = [v.strip() for v in args.vars.split(",") if v.strip()]
    else:
        varnames = [
            v.name for v in parse_default_versions_file(read_target_file(repo_root))
            if v.active
        ]

    deps = find_all_dependencies(args.port_dir, varnames, repo_root=repo_root)
    if not deps:
        print(f"{args.port_dir}: no dependency on any checked *_DEFAULT variable")
        sys.exit(0)

    for r in deps:
        print(f"{args.port_dir}: depends on {r.default_var} (status={r.status})")
        if r.baseline:
            print(f"  baseline:   {r.baseline}")
        if r.overridden:
            print(f"  overridden: {r.overridden}")
