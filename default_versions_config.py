"""
default_versions_config.py

Config data for the set of "FOO_DEFAULT" variables defined in
Mk/bsd.default-versions.mk (freebsd/freebsd-ports, main branch).

This is a SNAPSHOT, not a generated/live list -- the upstream file
changes over time (vars get added/removed), so treat this as
something to periodically re-sync by hand against the real file,
not a permanent source of truth.

Each entry notes whether the variable is a plain, single-line
"VARNAME?= value" assignment (the shape detect_default_version_change
is built to parse cleanly) or something more complex (conditional
branches, multi-line, or computed from the installed binary) --
those are more likely to trip the 'anomaly' path and may need
bespoke handling if/when you watch them.

active=False means the variable is present in the file but currently
commented out upstream (e.g. EMACS_DEFAULT) -- not actually in
effect right now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DefaultVersionVar:
    name: str
    shape: str      # 'simple' | 'conditional' | 'complex'
    active: bool


_VARS: list[DefaultVersionVar] = [
    DefaultVersionVar("APACHE_DEFAULT",         "simple",      True),
    DefaultVersionVar("BDB_DEFAULT",            "simple",      True),
    DefaultVersionVar("COROSYNC_DEFAULT",       "simple",      True),
    DefaultVersionVar("EBUR128_DEFAULT",        "conditional", True),
    DefaultVersionVar("EMACS_DEFAULT",          "simple",      False),  # commented out upstream
    DefaultVersionVar("FIREBIRD_DEFAULT",       "conditional", True),
    DefaultVersionVar("FORTRAN_DEFAULT",        "simple",      True),
    DefaultVersionVar("FPC_DEFAULT",            "conditional", True),
    DefaultVersionVar("GCC_DEFAULT",            "simple",      True),
    DefaultVersionVar("GHOSTSCRIPT_DEFAULT",    "simple",      True),
    DefaultVersionVar("GL_DEFAULT",             "simple",      True),
    DefaultVersionVar("GO_DEFAULT",             "simple",      True),
    DefaultVersionVar("GUILE_DEFAULT",          "simple",      True),
    DefaultVersionVar("IMAGEMAGICK_DEFAULT",    "simple",      True),
    DefaultVersionVar("JAVA_DEFAULT",           "conditional", True),
    DefaultVersionVar("LAZARUS_DEFAULT",        "conditional", True),
    DefaultVersionVar("LIBRSVG2_DEFAULT",       "conditional", True),
    DefaultVersionVar("LINUX_DEFAULT",          "conditional", True),
    DefaultVersionVar("LLVM_DEFAULT",           "simple",      True),
    DefaultVersionVar("LUA_DEFAULT",            "simple",      True),
    DefaultVersionVar("LUAJIT_DEFAULT",         "conditional", True),
    DefaultVersionVar("MONO_DEFAULT",           "simple",      True),
    DefaultVersionVar("MYSQL_DEFAULT",          "simple",      True),
    DefaultVersionVar("NINJA_DEFAULT",          "simple",      True),
    DefaultVersionVar("NODEJS_DEFAULT",         "simple",      True),
    DefaultVersionVar("OPENLDAP_DEFAULT",       "simple",      True),
    DefaultVersionVar("PERL5_DEFAULT",          "complex",     True),  # conditional + binary-derived fallback
    DefaultVersionVar("PGSQL_DEFAULT",          "simple",      True),
    DefaultVersionVar("PHP_DEFAULT",            "simple",      True),
    DefaultVersionVar("PYCRYPTOGRAPHY_DEFAULT", "conditional", True),
    DefaultVersionVar("PYTHON_DEFAULT",         "simple",      True),
    DefaultVersionVar("PYTHON2_DEFAULT",        "simple",      True),
    DefaultVersionVar("RUBY_DEFAULT",           "simple",      True),
    DefaultVersionVar("RUST_DEFAULT",           "simple",      True),
    DefaultVersionVar("SAMBA_DEFAULT",          "simple",      True),
    DefaultVersionVar("SSL_DEFAULT",            "complex",     True),  # multi-branch, installed-lib detection
    DefaultVersionVar("SUDO_DEFAULT",           "simple",      True),  # NB: not in the upstream reserved-name guard loop
    DefaultVersionVar("TCLTK_DEFAULT",          "simple",      True),
    DefaultVersionVar("VARNISH_DEFAULT",        "simple",      True),
]


def default_version_vars(
    active_only: bool = True,
    shape: Optional[str] = None,
) -> list[str]:
    """
    Returns a list of variable-name strings (e.g. 'PYTHON_DEFAULT').

    active_only - exclude vars not currently in effect (default: True)
    shape       - restrict to one shape category: 'simple' | 'conditional' | 'complex'
                  (default: None, meaning all shapes)

    Examples:
        all_simple = default_version_vars(shape="simple")
        everything_active = default_version_vars()
    """
    result = [
        v for v in _VARS
        if (not active_only or v.active)
        and (shape is None or v.shape == shape)
    ]
    return [v.name for v in result]
