#!/bin/sh
#
# find-external-portversion.sh
#
# Scans a local FreeBSD ports tree for ports whose PORTVERSION or
# DISTVERSION is NOT a literal, but a reference to a variable that is
# NOT defined in the port's own Makefile -- i.e. the version is
# controlled by some shared file elsewhere in the tree (classic
# example: lang/python's PORTVERSION coming from PYTHON_DEFAULT in
# Mk/bsd.default-versions.mk).
#
# Usage:
#   PORTSDIR=/usr/ports ./find-external-portversion.sh
#
# Output: tab-separated lines of:
#   PORT_ORIGIN   VAR_LINE   VARNAME   DEFINING_FILE(S)
#
# This is a first-pass STATIC heuristic (grep-based, no `make -V`
# evaluation). It's fast enough to run over the whole tree and is
# meant to produce a candidate list for review / for step 2 (the
# change-detection design), not a 100%-guaranteed-correct answer.
# Known limitations are noted inline below.

set -eu

PORTSDIR="${PORTSDIR:-/usr/ports}"

if [ ! -d "$PORTSDIR/Mk" ]; then
    echo "error: $PORTSDIR does not look like a ports tree (no Mk/ dir)" >&2
    exit 1
fi

# Build an index once: for every variable assigned anywhere under Mk/,
# record "VARNAME<TAB>file" so we can look up where a given variable
# comes from. This intentionally only looks at Mk/ (the shared
# infrastructure), not other ports, since "defined in some other
# port's Makefile" isn't a useful answer for this feature -- we care
# about shared files that fan out to many ports.
MKINDEX=$(mktemp)
trap 'rm -f "$MKINDEX"' EXIT

find "$PORTSDIR/Mk" -type f -name '*.mk' | while read -r mkfile; do
    # Matches lines like:  VARNAME?=   value   or  VARNAME=   value
    grep -E -o '^[A-Za-z_][A-Za-z0-9_]*[?:+]?=' "$mkfile" 2>/dev/null \
        | sed -E 's/[?:+]?=$//' \
        | while read -r varname; do
            printf '%s\t%s\n' "$varname" "$mkfile"
        done
done > "$MKINDEX"

# Now walk every port Makefile looking for PORTVERSION/DISTVERSION
# assignments whose right-hand side is a ${VARNAME} reference (not a
# literal).
find "$PORTSDIR" -mindepth 3 -maxdepth 3 -type f -name Makefile | while read -r makefile; do
    port_origin=$(echo "$makefile" | sed -E "s|^$PORTSDIR/||; s|/Makefile$||")

    # Grab PORTVERSION= / DISTVERSION= lines (allow ?= := += too).
    grep -E '^(PORTVERSION|DISTVERSION)[?:+]?=' "$makefile" 2>/dev/null | while IFS= read -r varline; do

        # Extract the variable name referenced on the RHS, if any.
        # Handles ${FOO} and ${FOO:modifier} forms. If there's no
        # ${...} at all, it's a literal -- skip it.
        refvar=$(echo "$varline" | grep -o '\${[A-Za-z_][A-Za-z0-9_]*' | head -n1 | sed 's/^\${//')

        if [ -z "$refvar" ]; then
            continue   # literal value, not our concern
        fi

        # Is refvar defined locally in this port's own Makefile?
        # (crude but effective: look for an assignment line for it
        # in the same file, excluding the PORTVERSION/DISTVERSION
        # line itself)
        if grep -E "^${refvar}[?:+]?=" "$makefile" | grep -v -E '^(PORTVERSION|DISTVERSION)[?:+]?=' >/dev/null 2>&1; then
            continue   # defined locally -- internal indirection, not external
        fi

        # Not defined locally -- look it up in the Mk/ index.
        sources=$(awk -F'\t' -v v="$refvar" '$1==v {print $2}' "$MKINDEX" | sort -u | paste -sd, -)

        if [ -n "$sources" ]; then
            printf '%s\t%s\t%s\t%s\n' "$port_origin" "$varline" "$refvar" "$sources"
        fi
        # If $sources is empty, refvar wasn't found in Mk/ either --
        # could be a USES-injected var (per-port, not shared) or
        # something set via .include of a non-Mk file. Worth a
        # second pass later, but not a false "external shared
        # dependency" candidate for now.
    done
done
