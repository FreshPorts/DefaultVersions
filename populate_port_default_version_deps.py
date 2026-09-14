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

    port_default_version_deps.find_all_dependencies()
        - runs the differential make -V checks for one port against every
          active *_DEFAULT variable, returns DependencyResult objects for
          only the ones it actually depends on

    sync_port_default_version_variable.sync_port_dependencies()
        - upserts/removes the corresponding rows in
          port_default_version_variable for that port

Active ports come from `ports_active` (id, name, category), so a port's
directory is assumed to be <repo>/<category>/<name>.

Usage:
    python3 populate_port_default_version_deps.py --repo /jails/freshports/usr/ports [--dry-run]

Requires psycopg2; connects using the same config.ini-derived DSN as
sync_default_version_variable.py, unless --dsn is given.
"""

from __future__ import annotations

import argparse
import configparser
import re
import sys
import traceback
from pathlib import Path

from port_default_version_deps import find_all_dependencies
from sync_default_version_variable import parse_default_versions_file, read_target_file
from sync_port_default_version_variable import sync_port_dependencies

PROGRESS_EVERY = 500


def get_active_ports(conn, category: str = None, name: str = None) -> list[dict]:
    """
    Returns [{'id': ..., 'name': ..., 'category': ...}, ...] from
    ports_active, optionally filtered down to a single category and/or
    port name at the SQL level (rather than pulling the whole table and
    filtering in Python -- ports_active can be tens of thousands of rows).
    """
    sql = "SELECT id, name, category FROM ports_active"
    conditions = []
    params = []
    if category:
        conditions.append("category = %s")
        params.append(category)
    if name:
        conditions.append("name = %s")
        params.append(name)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    cur = conn.cursor()
    cur.execute(sql, params)
    return [
        {"id": row[0], "name": row[1], "category": row[2]}
        for row in cur.fetchall()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Path to local ports tree checkout")
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: derived from config.ini, same as sync_default_version_variable.py)")
    parser.add_argument("--dry-run", action="store_true", help="Run the make -V checks but don't write to the DB")
    parser.add_argument("--category", default=None, help="Only process ports in this category (e.g. lang) -- useful for testing")
    parser.add_argument("--port", default=None, help="Only process a single port, given as category/name (e.g. net-p2p/litecoin) -- overrides --category")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N ports -- useful for testing")
    args = parser.parse_args()

    port_category, port_name = None, None
    if args.port:
        if "/" not in args.port:
            print(f"--port must be given as category/name (e.g. net-p2p/litecoin), got: {args.port}", file=sys.stderr)
            return 1
        port_category, port_name = args.port.split("/", 1)

    mk_text = read_target_file(args.repo)
    varnames = [v.name for v in parse_default_versions_file(mk_text) if v.active]
    print(f"checking against {len(varnames)} active *_DEFAULT variables: {', '.join(varnames)}")

    import psycopg2  # imported lazily, same convention as sync_default_version_variable.py

    if args.dsn:
        dsn = args.dsn
    else:
        config = configparser.ConfigParser()
        config.read('/usr/local/etc/freshports/config.ini')
        dsn = (
            'host=' + config['database']['HOST']
            + ' dbname=' + config['database']['DBNAME']
            + ' user=' + config['database']['DEFAULTS_DBUSER']
            + ' password=' + re.escape(config['database']['DEFAULTS_PASSWORD'])
            + ' sslcertmode=disable'
        )

    conn = psycopg2.connect(dsn)

    try:
        ports = get_active_ports(conn, category=port_category or args.category, name=port_name)
        if args.limit:
            ports = ports[: args.limit]

        print(f"found {len(ports)} active ports to check")
        if args.port and not ports:
            print(f"warning: no active port found matching {args.port} -- check spelling/category", file=sys.stderr)

        checked_count = 0
        depends_count = 0
        upserted_total = 0
        removed_total = 0
        error_count = 0

        for i, port in enumerate(ports, start=1):
            port_id = port["id"]
            port_dir = str(Path(args.repo) / port["category"] / port["name"])

            try:
                deps = find_all_dependencies(port_dir, varnames, repo_root=args.repo)
            except Exception as exc:
                print(f"error: make -V check failed for {port_dir}: {exc}", file=sys.stderr)
                print(traceback.format_exc(), file=sys.stderr)
                error_count += 1
                continue

            checked_count += 1

            if deps:
                depends_count += 1
                names = ", ".join(f"{r.default_var}({r.status})" for r in deps)
                print(f"{port_dir}: {names}")

            if not args.dry_run:
                summary = sync_port_dependencies(conn, port_id, varnames, deps)
                upserted_total += len(summary["upserted"])
                removed_total += len(summary["removed"])
                if summary["skipped_unknown_variable"]:
                    print(
                        f"  warning: unknown variable(s) for port_id {port_id}, "
                        f"run sync_default_version_variable.py first: "
                        f"{summary['skipped_unknown_variable']}",
                        file=sys.stderr,
                    )

            if i % PROGRESS_EVERY == 0:
                print(f"... {i}/{len(ports)} ports checked")

        print(
            f"\nchecked:  {checked_count} ports ({error_count} errored)\n"
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
