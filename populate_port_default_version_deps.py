#!/usr/bin/env python3
"""
populate_port_default_version_deps.py

Iterates over every active port in the tree and populates
port_default_version_variable with the result of checking whether that
port's PORTVERSION/DISTVERSION depends on any *_DEFAULT variable, via
differential `make -V` evaluation.

re: https://github.com/FreshPorts/freshports/issues/509
re: https://github.com/FreshPorts/freshports/issues/659

For each port this calls two existing modules directly (no subprocess/JSON
hop needed -- both are already plain importable Python):

    port_default_version_deps.check_port_dependencies()
        - runs the differential make -V checks for one port against every
          active *_DEFAULT variable, and reports which ones it depends on,
          which ones could be conclusively checked, and whether the port
          could be evaluated at all

    sync_port_default_version_variable.sync_port_dependencies()
        - upserts/removes the corresponding rows in
          port_default_version_variable for that port

Active ports come from `ports_active` (id, name, category), so a port's
directory is assumed to be <repo>/<category>/<name>.

Usage:
    python3 populate_port_default_version_deps.py --repo /jails/freshports/usr/ports [--dry-run]
    python3 populate_port_default_version_deps.py --repo /jails/freshports/usr/ports --category lang
    python3 populate_port_default_version_deps.py --repo /jails/freshports/usr/ports --port lang/python,net-p2p/litecoin

Requires psycopg2; connects with the same config.ini-derived parameters
as sync_default_version_variable.py, unless --dsn is given. Connections
require TLS -- see connection_params_from_config() there.
"""

from __future__ import annotations

import argparse
import configparser
import sys
import traceback
from pathlib import Path

from port_default_version_deps import check_port_dependencies
import port_default_version_deps
from sync_default_version_variable import (
    connection_params_from_config,
    parse_default_versions_file,
    read_target_file,
    require_ssl,
)
from sync_port_default_version_variable import get_variable_ids, sync_port_dependencies

PROGRESS_EVERY = 500
# Commit every N ports rather than once per port (one fsync per port
# over a whole tree) or once at the end (a transaction held open for
# the length of the run).
COMMIT_EVERY = 500


def get_active_ports(conn, category: str = None, ports: list[tuple] = None) -> list[dict]:
    """
    Returns [{'id': ..., 'name': ..., 'category': ...}, ...] from
    ports_active, optionally narrowed at the SQL level (rather than
    pulling the whole table and filtering in Python -- ports_active can
    be tens of thousands of rows).

    ports    - [(category, name), ...]: fetch exactly these ports, in
               one round trip. Matched as a row value, so
               lang/python and net-p2p/python stay distinct -- which
               separate "category IN (...) AND name IN (...)" lists
               would not.
    category - a whole category, used only when `ports` isn't given.
    """
    sql = "SELECT id, name, category FROM ports_active"
    params = []
    if ports:
        placeholders = ", ".join(["(%s, %s)"] * len(ports))
        sql += f" WHERE (category, name) IN ({placeholders})"
        for port_category, port_name in ports:
            params.extend((port_category, port_name))
    elif category:
        sql += " WHERE category = %s"
        params.append(category)

    cur = conn.cursor()
    cur.execute(sql, params)
    return [
        {"id": row[0], "name": row[1], "category": row[2]}
        for row in cur.fetchall()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Path to local ports tree checkout")
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: derived from config.ini, same as sync_default_version_variable.py). sslmode=require is added unless the DSN sets one")
    parser.add_argument("--dry-run", action="store_true", help="Run the make -V checks but don't write to the DB")
    parser.add_argument("--category", default=None, help="Only process ports in this category (e.g. lang) -- useful for testing")
    parser.add_argument(
        "--port",
        default=None,
        help="Only process these ports: a comma-separated list of category/name "
             "(e.g. lang/python,net-p2p/litecoin) -- overrides --category",
    )
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N ports -- useful for testing")
    parser.add_argument("--debug", action="store_true", help="Print every make command run and its result (rc/values/stderr)")
    args = parser.parse_args()

    port_default_version_deps.DEBUG = args.debug

    requested_ports = []
    if args.port:
        for entry in args.port.split(","):
            entry = entry.strip()
            if not entry:
                continue  # tolerate a trailing or doubled comma
            if "/" not in entry:
                print(
                    f"--port entries must be given as category/name "
                    f"(e.g. net-p2p/litecoin), got: {entry}",
                    file=sys.stderr,
                )
                return 1
            port_category, _, port_name = entry.partition("/")
            requested_ports.append((port_category, port_name))
        if not requested_ports:
            print("--port was given but lists no ports", file=sys.stderr)
            return 1

    mk_text = read_target_file(args.repo)
    varnames = [v.name for v in parse_default_versions_file(mk_text) if v.active]
    print(f"checking against {len(varnames)} active *_DEFAULT variables: {', '.join(varnames)}")

    import psycopg2  # imported lazily, same convention as sync_default_version_variable.py

    if args.dsn:
        conn = psycopg2.connect(require_ssl(args.dsn))
    else:
        config = configparser.ConfigParser()
        config.read('/usr/local/etc/freshports/config.ini')
        conn = psycopg2.connect(**connection_params_from_config(config))

    try:
        ports = get_active_ports(conn, category=args.category, ports=requested_ports)
        if args.limit is not None:
            ports = ports[: args.limit]

        print(f"found {len(ports)} active ports to check")
        if requested_ports:
            # Name the ones that didn't come back, rather than only
            # noticing when NONE of them did -- asking for five and
            # silently checking three is the easy mistake here.
            found = {(p["category"], p["name"]) for p in ports}
            missing = [f"{c}/{n}" for c, n in requested_ports if (c, n) not in found]
            if missing:
                print(
                    f"warning: {len(missing)} requested port(s) not found in ports_active "
                    f"-- check spelling/category: {', '.join(missing)}",
                    file=sys.stderr,
                )

        # The variable catalog can't change while this runs, so look it
        # up once instead of once per port -- otherwise it's one
        # identical SELECT of the same ~38 rows for every port in the
        # tree. Likewise warn about unknown variables once here rather
        # than repeating the same warning tens of thousands of times.
        var_id_by_name = {} if args.dry_run else get_variable_ids(conn, varnames)
        if not args.dry_run:
            unknown_vars = [v for v in varnames if v not in var_id_by_name]
            if unknown_vars:
                print(
                    f"warning: {len(unknown_vars)} variable(s) not in default_version_variable "
                    f"and will be skipped, run sync_default_version_variable.py first: "
                    f"{', '.join(unknown_vars)}",
                    file=sys.stderr,
                )

        checked_count = 0
        depends_count = 0
        upserted_total = 0
        removed_total = 0
        error_count = 0
        partial_count = 0

        for i, port in enumerate(ports, start=1):
            port_id = port["id"]
            port_dir = str(Path(args.repo) / port["category"] / port["name"])

            try:
                outcome = check_port_dependencies(port_dir, varnames, repo_root=args.repo)
            except Exception as exc:
                print(f"error: make -V check failed for {port_dir}: {exc}", file=sys.stderr)
                print(traceback.format_exc(), file=sys.stderr)
                error_count += 1
                continue

            if outcome.baseline_error:
                # The port couldn't be evaluated at all -- a missing
                # directory, a broken Makefile, a tree that isn't fully
                # checked out. We know NOTHING about it, so skip the
                # sync entirely: reconciling on an empty result would
                # read as "depends on nothing" and delete this port's
                # existing, correct rows.
                print(f"error: {port_dir}: {outcome.baseline_error}", file=sys.stderr)
                error_count += 1
                continue

            checked_count += 1
            deps = outcome.results

            if outcome.errors:
                # Some variables couldn't be evaluated. They're absent
                # from outcome.checked_vars, so sync leaves their rows
                # alone rather than deleting them on a failed check.
                partial_count += 1
                failed = ", ".join(r.default_var for r in outcome.errors)
                print(
                    f"warning: {port_dir}: could not check {failed} "
                    f"-- leaving any existing rows for those alone",
                    file=sys.stderr,
                )

            if deps:
                depends_count += 1
                names = ", ".join(f"{r.default_var}({r.status})" for r in deps)
                print(f"{port_dir}: {names}")

            if not args.dry_run:
                summary = sync_port_dependencies(
                    conn,
                    port_id,
                    outcome.checked_vars,
                    deps,
                    var_id_by_name=var_id_by_name,
                    commit=False,
                )
                upserted_total += len(summary["upserted"])
                removed_total += len(summary["removed"])
                if i % COMMIT_EVERY == 0:
                    conn.commit()

            if i % PROGRESS_EVERY == 0:
                print(f"... {i}/{len(ports)} ports checked")

        if not args.dry_run:
            conn.commit()

        print(
            f"\nchecked:  {checked_count} ports ({error_count} could not be checked at all, "
            f"{partial_count} partially)\n"
            f"depends:  {depends_count} ports had at least one *_DEFAULT dependency\n"
            f"upserted: {upserted_total} port/variable rows\n"
            f"removed:  {removed_total} port/variable rows (now independent)"
        )
        if args.dry_run:
            print("(--dry-run: no database writes were made)")

        return 1 if error_count else 0

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
