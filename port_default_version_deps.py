"""
port_default_version_deps.py


Sample usage:

$ cd /usr/local/libexec/freshports
$ python3 ./port_default_version_deps.py /jails/freshports/usr/ports/lang/python
/jails/freshports/usr/ports/lang/python: depends on PYTHON_DEFAULT (status=depends, probe=3.10)
  PORTVERSION:
    baseline:   3.12
    overridden: 3.10
  ...one entry per compared variable that moved

$ python3 ./port_default_version_deps.py /jails/freshports/usr/ports/net-mgmt/librenms
/jails/freshports/usr/ports/net-mgmt/librenms: no dependency on any of the 38 *_DEFAULT variable(s) checked
$

Why not just PORTVERSION: a *_DEFAULT change can alter what FreshPorts
shows for a port while leaving its version alone, e.g.

$ make -C /usr/ports/archivers/R-cran-zip -V BUILD_DEPENDS
R-cran-cli>=0:devel/R-cran-cli /usr/local/bin/R:math/R gfortran14:lang/gcc14 /usr/local/bin/as:devel/binutils
$ make -C /usr/ports/archivers/R-cran-zip -V BUILD_DEPENDS GCC_DEFAULT=1
R-cran-cli>=0:devel/R-cran-cli /usr/local/bin/R:math/R gfortran1:lang/gcc1 /usr/local/bin/as:devel/binutils

Determines whether anything FreshPorts extracts from a port with
`make -V` -- its version, but also its dependency lists, flavors,
master sites and the rest (see COMPARED_VARS) -- depends on a
*_DEFAULT variable (e.g. PYTHON_DEFAULT, GCC_DEFAULT), by asking
`make` itself, via differential evaluation, rather than parsing the
port's Makefile.

Technique:
    1. Run `make -C <port_dir> -V <each of COMPARED_VARS>` for a
       baseline.
    2. Run it again with the candidate variable overridden on the
       command line -- to a real alternate value where
       Mk/bsd.default-versions.mk documents one, else a fake probe.
    3. Compare. A difference in any compared variable (or a make
       failure caused by the probe value failing the port's own
       validation) means what FreshPorts shows for the port genuinely
       depends on that variable.

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
from dataclasses import dataclass, field
from typing import Optional

# The variables compared with and without an override. This mirrors what
# FreshPorts extracts with `make -V` when it refreshes a port -- the list in
# freshports scripts/Jail/scripts/make-port.sh, plus the -V-only extractions
# in make-master-sites-all.sh (_MASTER_SITES_ALL) and
# make-flavors-package-names.sh (FLAVORS). A *_DEFAULT change matters to
# FreshPorts whenever it changes something FreshPorts shows, not only the
# version: GCC_DEFAULT moves archivers/R-cran-zip's BUILD_DEPENDS from
# lang/gcc14 to another gcc while its PORTVERSION stays put.
#
# Keep this in step with make-port.sh. DISTVERSION isn't in that list
# (FreshPorts reads PORTVERSION) but is kept because
# port_default_version_variable stores it. Refresh steps that run targets
# rather than -V (showconfig, generate-plist, the pkg-message extract) are
# deliberately left out: they build/extract, which is far too expensive to
# run twice per variable.
COMPARED_VARS = (
    "PORTNAME", "PKGNAME", "DESCR", "CATEGORIES",
    "PORTVERSION", "PORTREVISION", "COMMENT", "COMMENTFILE",
    "MAINTAINER", "EXTRACT_SUFX", "BUILD_DEPENDS", "RUN_DEPENDS",
    "LIB_DEPENDS", "FORBIDDEN", "BROKEN", "DEPRECATED",
    "IGNORE", "MASTER_PORT", "LATEST_LINK", "NO_LATEST_LINK",
    "NO_PACKAGE", "PKGNAMEPREFIX", "PKGNAMESUFFIX", "PORTEPOCH",
    "RESTRICTED", "NO_CDROM", "EXPIRATION_DATE", "IS_INTERACTIVE",
    "ONLY_FOR_ARCHS", "NOT_FOR_ARCHS", "LICENSE", "FETCH_DEPENDS",
    "EXTRACT_DEPENDS", "PATCH_DEPENDS", "USES", "PKGMESSAGE",
    "DISTINFO_FILE", "_LICENSE_RESTRICTED", "MANUAL_PACKAGE_BUILD", "LICENSE_PERMS",
    "CONFLICTS", "CONFLICTS_BUILD", "CONFLICTS_INSTALL", "OPTIONS_NAME",
    "WWW", "TEST_DEPENDS", "BUILD_RUN_DEPENDS", "USE_RC_SUBR:ts:",
    # not in make-port.sh:
    "DISTVERSION", "_MASTER_SITES_ALL", "FLAVORS",
)
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
    changed_vars: list[str] = field(default_factory=list)   # COMPARED_VARS that moved, for 'depends'


def _run_make(
    port_dir: str,
    overrides: Optional[dict] = None,
    extra_vars: Optional[list] = None,
    portsdir: Optional[str] = None,
) -> tuple[int, dict, str]:
    """
    Run `make -C port_dir -V <each of COMPARED_VARS> [-V extra ...] [VAR=value ...] [PORTSDIR=portsdir]`.

    portsdir is passed as PORTSDIR, the way FreshPorts' own refresh
    (scripts/Jail/scripts/make-port.sh) invokes make, so the tree make
    resolves Mk/ and dependency origins against is the one being
    checked -- the values compared here are then the values FreshPorts
    would store, not ones evaluated against some other tree.

    extra_vars lets callers also query the *_DEFAULT variable's own
    current value in the same invocation (used to pick a real
    alternate probe value -- see port_depends_on_default_var).

    Returns (returncode, {varname: value}, stderr_text). On non-zero
    exit the values dict is empty -- callers check returncode before
    trusting it. A run that exits 0 but does not print exactly one
    line per -V flag is reported as returncode 1 as well, since the
    name->value pairing cannot be trusted in that case.
    """
    query_vars = list(COMPARED_VARS) + list(extra_vars or [])
    cmd = ["make", "-C", port_dir]
    for v in query_vars:
        cmd += ["-V", v]
    if overrides:
        for k, v in overrides.items():
            cmd.append(f"{k}={v}")
    if portsdir:
        cmd.append(f"PORTSDIR={portsdir}")

    if DEBUG:
        print(shlex.join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    lines = proc.stdout.splitlines()
    err = proc.stderr.strip()

    if proc.returncode == 0 and len(lines) != len(query_vars):
        # `make -V` prints exactly one line per -V flag, including an
        # empty line for an empty or undefined variable. Anything else
        # means the name->value pairing can't be trusted: zip() would
        # silently mis-pair the values or drop the tail, and callers
        # would then KeyError on a compared variable -- or, worse, compare a
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


def _choose_probe(
    possible_values: Optional[list[str]],
    current_value: Optional[str],
    fallback: str,
) -> tuple[Optional[str], bool]:
    """
    Pick the value to override a *_DEFAULT variable with. Returns
    (probe, used_real_alternate), or (None, False) if the variable is
    untestable.

    A real documented alternate is used whenever possible_values has one.
    If possible_values is known but holds nothing different from the
    current value (e.g. PYTHON2_DEFAULT's only documented value is 2.7 --
    Python 2 is frozen, there's nothing else to compare against), the
    variable genuinely cannot be differential-tested: a fake sentinel
    wouldn't test whether THIS port depends on it, it would just trip
    whatever validation the owning Uses/*.mk file does on the variable
    globally, for any port that touches that infrastructure at all. Only
    when the domain is unknown altogether does the fallback get used.
    """
    if possible_values:
        alternates = [v for v in possible_values if v != current_value]
        if alternates:
            return alternates[0], True
        return None, False
    return fallback, False


def _verdict(
    default_var: str,
    probe: str,
    used_real_alternate: bool,
    baseline: dict,
    rc: int,
    overridden: dict,
    err: str,
) -> DependencyResult:
    """Classify one variable from a make run with (at least) it overridden."""
    if rc != 0:
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
            probe_value=probe,
            detail=f"`make -V` with {default_var}={probe} failed (exit {rc}): {err} {note}".strip(),
        )

    changed = [k for k in COMPARED_VARS if overridden[k] != baseline[k]]
    if changed:
        return DependencyResult(
            status="depends",
            default_var=default_var,
            baseline=baseline,
            overridden=overridden,
            changed_vars=changed,
            probe_appears=any(_probe_appears_in(overridden[k], probe) for k in changed),
            probe_value=probe,
        )

    return DependencyResult(status="independent", default_var=default_var, baseline=baseline)


def port_depends_on_default_var(
    port_dir: str,
    default_var: str,
    probe_value: str = DEFAULT_PROBE_VALUE,
    possible_values: Optional[list[str]] = None,
    portsdir: Optional[str] = None,
) -> DependencyResult:
    """
    Check whether anything in COMPARED_VARS for the port at port_dir
    depends on the given *_DEFAULT variable. Standalone: runs its own
    baseline. check_port_dependencies() doesn't use this -- it shares one
    baseline across every variable.

    port_dir        - path to the port's directory (e.g. /usr/ports/lang/python)
    default_var     - e.g. 'PYTHON_DEFAULT'
    probe_value     - fallback fake value, used only if possible_values
                       isn't given. Some *_DEFAULT variables (notably
                       PYTHON_DEFAULT) are used to select a SIBLING PORT
                       DIRECTORY, not just compared against -- for those,
                       any made-up value fails regardless of formatting,
                       because the referenced port directory doesn't exist.
    possible_values - list of the variable's real legitimate values
                       (e.g. from extract_possible_values() against
                       Mk/bsd.default-versions.mk). When given, the
                       probe uses a REAL alternate value instead of a
                       fake one -- this is the reliable path, and
                       avoids the "Cannot open .../Makefile.version"
                       class of failure entirely, since a real value
                       corresponds to a real port/branch.
    portsdir        - ports tree root, passed to make as PORTSDIR (see
                       _run_make)
    """
    extra = [default_var] if possible_values else []
    rc, baseline_full, err = _run_make(port_dir, extra_vars=extra, portsdir=portsdir)
    if rc != 0:
        return DependencyResult(
            status="error",
            default_var=default_var,
            detail=f"baseline `make -V` failed (exit {rc}): {err}",
        )
    baseline = {k: baseline_full[k] for k in COMPARED_VARS}

    probe, used_real_alternate = _choose_probe(
        possible_values, baseline_full.get(default_var), probe_value
    )
    if probe is None:
        return DependencyResult(status="independent", default_var=default_var, baseline=baseline)

    rc2, overridden, err2 = _run_make(port_dir, overrides={default_var: probe}, portsdir=portsdir)
    return _verdict(default_var, probe, used_real_alternate, baseline, rc2, overridden, err2)


# Mk/bsd.default-versions.mk is stable for the duration of a run, but
# check_port_dependencies() is called once per port -- tens of thousands
# of times for a whole tree. Re-reading the file and re-scanning its full
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
    errors         - 'error' entries: variables make could not evaluate
                     individually. check_port_dependencies() shares one
                     baseline across all variables, so it never produces
                     these; kept for callers that report them.
    baseline_error - set when the port could not be evaluated AT ALL
                     (the no-overrides baseline call failed). Nothing
                     was checked, so checked_vars is empty.
    """
    results: list[DependencyResult] = field(default_factory=list)
    checked_vars: list[str] = field(default_factory=list)
    errors: list[DependencyResult] = field(default_factory=list)
    baseline_error: Optional[str] = None


def check_port_dependencies(
    port_dir: str,
    default_vars: list[str],
    repo_root: Optional[str] = None,
) -> PortCheckOutcome:
    """
    Check a port against a list of *_DEFAULT variable names (e.g. from
    a live parse of Mk/bsd.default-versions.mk) and report which ones
    change anything in COMPARED_VARS, which ones were conclusively
    checked, and whether the port could be evaluated at all.

    repo_root - if given, Mk/bsd.default-versions.mk is read once and
                used to look up real possible_values for each
                variable, so probing uses real alternates rather than
                the fake sentinel wherever possible. Strongly
                recommended -- omit only if you don't have a repo
                checkout available.

    PERFORMANCE: a port is checked in two `make` calls when it depends on
    nothing:

        1. One baseline call: every COMPARED_VARS value, plus every
           *_DEFAULT variable's current value (to pick real alternates).
        2. One combined call overriding every testable variable at once.
           If no compared variable moved, the port depends on none of them.

    If something moved (or the combined override broke make), the
    variables responsible are found by bisection: override each half of
    the group, and descend only into halves that moved, down to single
    variables. A port depending on one of 38 variables costs about a
    dozen further calls, against 76 for testing each variable alone with
    its own baseline. That matters now that COMPARED_VARS includes
    dependency lists: every USES=python/perl5/ssl/compiler port lands
    here, not just the rare port whose version moves.

    Caveat: a group that doesn't move is taken to mean every variable in
    it is independent. That assumes overriding several unrelated
    *_DEFAULT variables together can't cancel out a change one of them
    makes alone -- not expected, since each governs separate
    infrastructure. The fast path in step 2 has always rested on the
    same assumption. A group that fails only because of an interaction
    between its members, with every smaller group succeeding unchanged,
    likewise attributes nothing -- as testing each variable alone would.
    """
    possible_values_by_var = _possible_values(repo_root, default_vars) if repo_root else {}

    rc, baseline_full, err = _run_make(port_dir, extra_vars=default_vars, portsdir=repo_root)
    if rc != 0:
        # The baseline failed with NO overrides applied, so this is
        # the port (or the tree, or make) being unevaluable rather
        # than anything to do with a particular variable -- a missing
        # port directory, a broken Makefile, a tree that isn't fully
        # checked out. Report the failure; checked_vars stays empty,
        # which tells a caller reconciling a database to leave this
        # port's existing rows alone rather than treating it as
        # depending on nothing.
        return PortCheckOutcome(
            baseline_error=f"baseline `make -V` failed (exit {rc}): {err}",
        )
    baseline = {k: baseline_full[k] for k in COMPARED_VARS}

    # Untestable variables (see _choose_probe) are never overridden, and
    # are independent by definition -- so every variable counts as
    # conclusively checked once the baseline has succeeded.
    probes = {}
    for var in default_vars:
        probe, used_real_alternate = _choose_probe(
            possible_values_by_var.get(var), baseline_full.get(var), DEFAULT_PROBE_VALUE
        )
        if probe is not None:
            probes[var] = (probe, used_real_alternate)

    outcome = PortCheckOutcome(checked_vars=list(default_vars))

    def run(group: list[str]) -> tuple[int, dict, str]:
        return _run_make(port_dir, overrides={v: probes[v][0] for v in group}, portsdir=repo_root)

    def bisect(group: list[str], result: tuple[int, dict, str]) -> None:
        rc, overridden, err = result
        if rc == 0 and all(overridden[k] == baseline[k] for k in COMPARED_VARS):
            return
        if len(group) == 1:
            var = group[0]
            probe, used_real_alternate = probes[var]
            outcome.results.append(
                _verdict(var, probe, used_real_alternate, baseline, rc, overridden, err)
            )
            return
        mid = len(group) // 2
        for half in (group[:mid], group[mid:]):
            bisect(half, run(half))

    if probes:
        group = list(probes)
        bisect(group, run(group))
    return outcome


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

    outcome = check_port_dependencies(args.port_dir, varnames, repo_root=repo_root)

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
        print(f"{args.port_dir}: depends on {r.default_var} (status={r.status}, probe={r.probe_value})")
        if r.status == "depends_error":
            print(f"  {r.detail}")
        for k in r.changed_vars:
            print(f"  {k}:")
            print(f"    baseline:   {r.baseline[k]}")
            print(f"    overridden: {r.overridden[k]}")

    sys.exit(1 if outcome.errors else 0)
