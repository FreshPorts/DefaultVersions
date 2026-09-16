"""
port_default_version_deps.py


Sample usage:

$ cd /usr/local/libexec/freshports
$ python3 ./port_default_version_deps.py /jails/freshports/usr/ports/lang/python
/jails/freshports/usr/ports/lang/python: depends on PYTHON_DEFAULT (status=depends)
  baseline:   {'PORTVERSION': '3.12', 'DISTVERSION': '3.12'}
  overridden: {'PORTVERSION': '3.10', 'DISTVERSION': '3.10'}

$ python3 ./port_default_version_deps.py /jails/freshports/usr/ports/net-mgmt/librenms
/jails/freshports/usr/ports/net-mgmt/librenms: no dependency on any of the 38 *_DEFAULT variable(s) checked
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

import re
import shlex
import subprocess
from dataclasses import dataclass
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


class MakeError(RuntimeError):
    """The port's own `make -V` failed -- nothing can be determined about it."""


class MakeOutputError(RuntimeError):
    """`make -V` exited 0 but its output can't be mapped back to the
    variables that were queried (see _run_make)."""


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
    exit the values dict is empty -- callers check returncode before
    trusting it. A run that exits 0 but does not print exactly one
    line per -V flag is reported as returncode 1 as well, since the
    name->value pairing cannot be trusted in that case.
    """
    query_vars = list(VERSION_VARS) + list(extra_vars or [])
    cmd = ["make", "-C", port_dir]
    for v in query_vars:
        cmd += ["-V", v]
    if overrides:
        for k, v in overrides.items():
            cmd.append(f"{k}={v}")

    if DEBUG:
        print(shlex.join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    lines = proc.stdout.splitlines()
<<<<<<< HEAD
    if proc.returncode == 0 and len(lines) != len(query_vars):
        # `make -V` prints exactly one line per -V, so a different count
        # means the port's evaluation put something else on stdout (a
        # bmake `.info`, a Mk/*.mk notice, ...). Zipping names to lines
        # positionally would then silently shift every value by one --
        # e.g. recording a deprecation notice as this port's PORTVERSION
        # and reporting a bogus dependency off the back of it -- so
        # refuse to map them at all rather than return plausible garbage.
        raise MakeOutputError(
            f"{shlex.join(cmd)}: {len(query_vars)} variable(s) queried but "
            f"make printed {len(lines)} line(s): {lines!r}"
        )
=======
    err = proc.stderr.strip()

    if proc.returncode == 0 and len(lines) != len(query_vars):
        # `make -V` prints exactly one line per -V flag, including an
        # empty line for an empty or undefined variable. Anything else
        # means the name->value pairing can't be trusted: zip() would
        # silently mis-pair the values or drop the tail, and callers
        # would then KeyError on PORTVERSION -- or, worse, compare a
        # shifted value against the baseline and call it a difference.
        # Report it as a failure. The usual cause is a `make` that
        # isn't bmake, or a wrapper that adds output of its own.
        err = "; ".join(x for x in (
            err,
            f"expected {len(query_vars)} line(s) of `make -V` output, got {len(lines)}",
        ) if x)
        if DEBUG:
            print(f"  -> rc=0 but got {len(lines)} line(s) for {len(query_vars)} -V flag(s); treating as failure")
        return 1, {}, err

>>>>>>> c36729c6b46dd2dece2c7974e2b5fc8dfe6b1e8c
    values = dict(zip(query_vars, lines)) if proc.returncode == 0 else {}
    if DEBUG:
        print(f"  -> rc={proc.returncode} values={values} stderr={err!r}")
    return proc.returncode, values, err


def _probe_appears_in(version: str, probe: str) -> bool:
    """
    Did `probe` actually show up as a component of `version`?

    Deliberately not a plain substring test. With a real alternate
    value like PHP_DEFAULT's '8.1', `'8.1' in '18.15'` is true purely
    by coincidence, and reports a probe value that never appeared.
    Require the match to begin at a component boundary (not mid-digit,
    not partway through a longer dotted run) and not to run on into a
    longer identifier -- so '8.1' matches '8.1', '8.1.2' and
    'py38-8.1', but not '18.15' or '1.8.1'.
    """
    return re.search(
        rf"(?<![0-9A-Za-z.]){re.escape(probe)}(?![0-9A-Za-z])",
        version,
    ) is not None


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
        else:
            # possible_values is the FULL documented domain for this
            # variable, and it contains no value different from the
            # current one (e.g. PYTHON2_DEFAULT's only documented
            # value is 2.7 -- Python 2 is frozen, there's nothing
            # else to compare against). This variable genuinely
            # cannot be differential-tested: falling back to a fake
            # sentinel here wouldn't test whether THIS port depends
            # on it, it would just trip whatever validation the
            # owning Uses/*.mk file does on the variable globally,
            # for any port that happens to touch that infrastructure
            # at all. Treat as untestable rather than report a
            # depends_error that doesn't actually mean what it looks
            # like it means.
            return DependencyResult(status="independent", default_var=default_var, baseline=baseline)
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
        probe_appears = any(_probe_appears_in(v, chosen_probe) for v in overridden.values())
        return DependencyResult(
            status="depends",
            default_var=default_var,
            baseline=baseline,
            overridden=overridden,
            probe_appears=probe_appears,
            probe_value=chosen_probe,
        )

    return DependencyResult(status="independent", default_var=default_var, baseline=baseline)


<<<<<<< HEAD
# Mk/bsd.default-versions.mk is stable for the duration of a run, but
# find_all_dependencies() is called once per port -- tens of thousands of
# times for a whole tree. Re-reading the file and re-scanning its full
# text once per variable per port (38 scans x 30k ports) is pure
# overhead, so the extraction is memoised per repo root. Clear this dict
# (or restart) if the file is edited mid-run.
_POSSIBLE_VALUES_CACHE: dict = {}


def _possible_values(repo_root: str, default_vars: list[str]) -> dict:
    """{var: [real documented values]} for vars that document any, read
    from repo_root's Mk/bsd.default-versions.mk at most once per var."""
    from sync_default_version_variable import extract_possible_values, read_target_file

    cached = _POSSIBLE_VALUES_CACHE.setdefault(repo_root, {})
    missing = [v for v in default_vars if v not in cached]
    if missing:
        mk_text = read_target_file(repo_root)
        for var in missing:
            cached[var] = extract_possible_values(mk_text, var)
    return {v: cached[v] for v in default_vars if cached.get(v)}


def find_all_dependencies(
=======
@dataclass
class PortCheckOutcome:
    """
    Everything one port's check produced, as opposed to
    find_all_dependencies()'s convenience list of just the hits.

    results        - the 'depends'/'depends_error' entries
    checked_vars   - the variables CONCLUSIVELY evaluated this run. A
                     variable appears here only if make actually
                     answered for it, which is what lets a caller
                     reconciling a database tell "checked, and now
                     independent" (drop the stale row) apart from
                     "could not be checked" (leave the row alone).
                     This is exactly what sync_port_dependencies()'s
                     checked_vars argument wants.
    errors         - the 'error' entries: variables make could not
                     evaluate, individually
    baseline_error - set when the port could not be evaluated AT ALL
                     (the no-overrides baseline call failed). Nothing
                     was checked, so checked_vars is empty.
    """
    results: list[DependencyResult] = field(default_factory=list)
    checked_vars: list[str] = field(default_factory=list)
    errors: list[DependencyResult] = field(default_factory=list)
    baseline_error: Optional[str] = None


def check_port_dependencies(
>>>>>>> c36729c6b46dd2dece2c7974e2b5fc8dfe6b1e8c
    port_dir: str,
    default_vars: list[str],
    repo_root: Optional[str] = None,
) -> PortCheckOutcome:
    """
    Check a port against a list of *_DEFAULT variable names (e.g. from
    a live parse of Mk/bsd.default-versions.mk) and report which ones
    it depends on, which ones were conclusively checked, and whether
    the port could be evaluated at all.

    Raises MakeError if the port's own baseline `make -V` fails, and
    MakeOutputError if make's output can't be mapped to the variables
    queried -- in both cases the port is unevaluable, which is NOT the
    same as "depends on nothing" and must not be reported as such.

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
    possible_values_by_var = _possible_values(repo_root, default_vars) if repo_root else {}

    def _slow_path() -> PortCheckOutcome:
        outcome = PortCheckOutcome()
        for var in default_vars:
            r = port_depends_on_default_var(
                port_dir, var, possible_values=possible_values_by_var.get(var)
            )
            if r.status == "error":
                # make couldn't answer for this variable, so we know
                # nothing about it -- deliberately NOT recorded as
                # checked, so callers don't read the absence of a
                # 'depends' result as "independent".
                outcome.errors.append(r)
                continue
            outcome.checked_vars.append(var)
            if r.status in ("depends", "depends_error"):
                outcome.results.append(r)
        return outcome

    # Combined baseline: PORTVERSION/DISTVERSION plus every variable's
    # current value, all in one call.
    rc, baseline_full, err = _run_make(port_dir, extra_vars=default_vars)
    if rc != 0:
<<<<<<< HEAD
        # The port's own unmodified `make -V` failed, so nothing at all
        # can be concluded about it -- least of all "independent".
        # Returning [] here (which is what falling back to the slow path
        # amounted to: every per-variable baseline runs the very same
        # command, fails the same way, and yields an 'error' result that
        # gets filtered out) is actively harmful, because callers read an
        # empty result as "checked, depends on nothing" and DELETE the
        # port's existing rows -- see sync_port_dependencies(). A broken
        # port, an unreadable tree or a transient failure would silently
        # wipe known-good data. Fail loudly instead; bulk callers already
        # catch this per port, count it, and leave the DB untouched.
        raise MakeError(f"baseline `make -V` failed for {port_dir} (exit {rc}): {err}")
=======
        # The baseline failed with NO overrides applied, so this is
        # the port (or the tree, or make) being unevaluable rather
        # than anything to do with a particular variable -- a missing
        # port directory, a broken Makefile, a tree that isn't fully
        # checked out. Running the slow path here would just re-issue
        # the same doomed command once per variable, so report the
        # failure instead. checked_vars stays empty, which tells a
        # caller reconciling a database to leave this port's existing
        # rows alone rather than treating it as depending on nothing.
        return PortCheckOutcome(
            baseline_error=f"baseline `make -V` failed (exit {rc}): {err}",
        )
>>>>>>> c36729c6b46dd2dece2c7974e2b5fc8dfe6b1e8c
    baseline = {k: baseline_full[k] for k in VERSION_VARS}

    # Pick a probe value for every variable up front: a real
    # alternate when possible_values gives us one, the fallback
    # sentinel when we don't know the domain at all (e.g. SSL_DEFAULT).
    # Variables where possible_values is known but has NO usable
    # alternate (e.g. PYTHON2_DEFAULT's only documented value is 2.7)
    # are OMITTED from the combined override entirely -- including
    # them with a known-invalid sentinel risks tripping that
    # variable's own validation for every port that touches its
    # infrastructure, poisoning the combined test and forcing an
    # unnecessary fallback to the slow path for ports that don't
    # actually depend on the untestable variable at all.
    combined_overrides = {}
    for var in default_vars:
        possible = possible_values_by_var.get(var)
        if possible:
            current_value = baseline_full.get(var)
            alternates = [v for v in possible if v != current_value]
            if alternates:
                combined_overrides[var] = alternates[0]
            # else: no usable alternate -- omit, untestable
        else:
            combined_overrides[var] = DEFAULT_PROBE_VALUE

    rc2, combined_overridden, err2 = _run_make(port_dir, overrides=combined_overrides)

    if rc2 == 0:
        overridden = {k: combined_overridden[k] for k in VERSION_VARS}
        if overridden == baseline:
            # Fast path: overriding every TESTABLE candidate variable
            # at once changed nothing -- this port depends on none of
            # them. The untestable ones omitted from the override
            # above are independent by definition (the slow path
            # reports 'independent' for them too, without running
            # anything), so every variable in default_vars counts as
            # conclusively checked here.
            return PortCheckOutcome(checked_vars=list(default_vars))

    # Something moved (or the combined override broke the build) --
    # fall back to testing each variable individually to attribute
    # the effect correctly. Only reached for ports that actually have
    # a dependency, so the extra cost is rare in aggregate.
    return _slow_path()


def find_all_dependencies(
    port_dir: str,
    default_vars: list[str],
    repo_root: Optional[str] = None,
) -> list[DependencyResult]:
    """
    Convenience wrapper over check_port_dependencies(): just the
    'depends'/'depends_error' results for a port, as a list.

    NOTE: this collapses "checked, and depends on nothing" and "could
    not be checked at all" into the same empty list. Anything that
    writes these results to a database must call
    check_port_dependencies() directly and honour its checked_vars,
    or a port whose `make` calls failed will look independent and
    have its existing rows deleted.
    """
    return check_port_dependencies(port_dir, default_vars, repo_root=repo_root).results


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

<<<<<<< HEAD
    try:
        deps = find_all_dependencies(args.port_dir, varnames, repo_root=repo_root)
    except (MakeError, MakeOutputError) as exc:
        print(f"{args.port_dir}: {exc}", file=sys.stderr)
        sys.exit(2)

    if not deps:
        print(f"{args.port_dir}: no dependency on any checked *_DEFAULT variable")
        sys.exit(0)
=======
    outcome = check_port_dependencies(args.port_dir, varnames, repo_root=repo_root)
>>>>>>> c36729c6b46dd2dece2c7974e2b5fc8dfe6b1e8c

    if outcome.baseline_error:
        # Nothing was checked -- don't let this read as "no dependencies".
        print(f"{args.port_dir}: could not be checked: {outcome.baseline_error}", file=sys.stderr)
        sys.exit(2)

    for r in outcome.errors:
        print(f"{args.port_dir}: could not check {r.default_var}: {r.detail}", file=sys.stderr)

    if not outcome.results:
        print(
            f"{args.port_dir}: no dependency on any of the "
            f"{len(outcome.checked_vars)} *_DEFAULT variable(s) checked"
        )
    for r in outcome.results:
        print(f"{args.port_dir}: depends on {r.default_var} (status={r.status})")
        if r.baseline:
            print(f"  baseline:   {r.baseline}")
        if r.overridden:
            print(f"  overridden: {r.overridden}")

    sys.exit(1 if outcome.errors else 0)
