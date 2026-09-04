"""
sync_port_default_version_variable.py

Persists the results of find_all_dependencies() (port_default_version_deps.py)
into the port_default_version_variable table.

Distinct from the actual dependency CHECK itself (that's
port_default_version_deps.py, which just runs `make -V` and returns
Python objects) -- this module's only job is writing those results
into the DB in a way that stays correct on repeated runs: a variable
that WAS a dependency and no longer is (e.g. a port's Makefile
changed to set PORTVERSION directly) gets its row removed, not left
behind as stale data.

Since this table only stores rows for actual dependencies (see the
table comment in port_default_version_variable.sql), "no row" means
"independent" -- so bringing it into sync after a recheck means:
inserting/updating rows for variables now found dependent, and
deleting rows for variables that were dependent before but are now
independent among the ones actually checked this run.
"""

from __future__ import annotations

from port_default_version_deps import DependencyResult


def get_variable_ids(conn, names: list[str]) -> dict[str, int]:
    """Look up default_version_variable.id for each name. Names not
    found in the table are silently omitted from the result -- callers
    should have run sync_default_version_variable.py first so the
    catalog is current."""
    if not names:
        return {}
    cur = conn.cursor()
    placeholders = ",".join(["%s"] * len(names))
    cur.execute(
        f"SELECT id, name FROM default_version_variable WHERE name IN ({placeholders})",
        tuple(names),
    )
    return {name: vid for vid, name in cur.fetchall()}


def sync_port_dependencies(
    conn,
    port_id: int,
    checked_vars: list[str],
    results: list[DependencyResult],
) -> dict:
    """
    port_id       - the port's id in the ports table
    checked_vars  - the FULL list of *_DEFAULT variable names that were
                    checked this run (not just the ones found dependent
                    -- this is what lets us tell "checked and now
                    independent" apart from "not checked this run,
                    leave alone")
    results       - find_all_dependencies()'s return value: only the
                    'depends'/'depends_error' entries

    Returns {'upserted': [...names...], 'removed': [...names...], 'skipped_unknown_variable': [...names...]}
    """
    var_id_by_name = get_variable_ids(conn, checked_vars)
    skipped_unknown = [v for v in checked_vars if v not in var_id_by_name]

    result_by_var = {r.default_var: r for r in results}

    cur = conn.cursor()
    cur.execute(
        "SELECT dvv.name, pdvv.default_version_variable_id "
        "FROM port_default_version_variable pdvv "
        "JOIN default_version_variable dvv ON dvv.id = pdvv.default_version_variable_id "
        "WHERE pdvv.port_id = %s",
        (port_id,),
    )
    existing_var_id_by_name = {name: vid for name, vid in cur.fetchall()}

    upserted, removed = [], []

    for varname in checked_vars:
        vid = var_id_by_name.get(varname)
        if vid is None:
            continue  # not in the catalog -- run sync_default_version_variable.py first

        r = result_by_var.get(varname)

        if r is None:
            # Checked this run, came back independent. If we had a
            # stale row claiming a dependency, remove it.
            if varname in existing_var_id_by_name:
                cur.execute(
                    "DELETE FROM port_default_version_variable "
                    "WHERE port_id = %s AND default_version_variable_id = %s",
                    (port_id, vid),
                )
                removed.append(varname)
            continue

        baseline = r.baseline or {}
        overridden = r.overridden or {}
        cur.execute(
            """
            INSERT INTO port_default_version_variable
                (port_id, default_version_variable_id, status,
                 baseline_portversion, baseline_distversion,
                 overridden_portversion, overridden_distversion,
                 probe_value, detail, checked_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (port_id, default_version_variable_id) DO UPDATE
            SET status = EXCLUDED.status,
                baseline_portversion = EXCLUDED.baseline_portversion,
                baseline_distversion = EXCLUDED.baseline_distversion,
                overridden_portversion = EXCLUDED.overridden_portversion,
                overridden_distversion = EXCLUDED.overridden_distversion,
                probe_value = EXCLUDED.probe_value,
                detail = EXCLUDED.detail,
                checked_at = now()
            """,
            (
                port_id, vid, r.status,
                baseline.get("PORTVERSION"), baseline.get("DISTVERSION"),
                overridden.get("PORTVERSION"), overridden.get("DISTVERSION"),
                r.probe_value, r.detail,
            ),
        )
        upserted.append(varname)

    conn.commit()
    return {"upserted": upserted, "removed": removed, "skipped_unknown_variable": skipped_unknown}
