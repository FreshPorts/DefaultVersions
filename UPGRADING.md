# Upgrading: `*_DEFAULT` dependency checks beyond `PORTVERSION`

This release changes what a "dependency" means in
`port_default_version_variable`. The check no longer looks only at
`PORTVERSION`/`DISTVERSION`: it compares everything FreshPorts extracts from a
port with `make -V` (`COMPARED_VARS` in `port_default_version_deps.py` — the
list from FreshPorts' `scripts/Jail/scripts/make-port.sh`, plus
`_MASTER_SITES_ALL`, `FLAVORS` and `DISTVERSION`).

The case that motivated it:

```
% make -C /usr/ports/archivers/R-cran-zip -V BUILD_DEPENDS
R-cran-cli>=0:devel/R-cran-cli /usr/local/bin/R:math/R gfortran14:lang/gcc14 /usr/local/bin/as:devel/binutils
% make -C /usr/ports/archivers/R-cran-zip -V BUILD_DEPENDS GCC_DEFAULT=1
R-cran-cli>=0:devel/R-cran-cli /usr/local/bin/R:math/R gfortran1:lang/gcc1 /usr/local/bin/as:devel/binutils
```

`GCC_DEFAULT` changes what FreshPorts shows for that port. The old check called
it independent, because `PORTVERSION` never moved.

**Expect many more rows than the table holds today.** Every
`USES=python`/`perl5`/`ssl`/compiler port is a candidate now, not just the rare
port whose version is derived from a `*_DEFAULT` variable.

Also in this release:

- `changed_vars text[]` records which compared variables moved, e.g.
  `{BUILD_DEPENDS}`.
- Every `make` call passes `PORTSDIR=<repo>`, the way `make-port.sh` does, so
  values are evaluated against the tree being checked.
- `check_port_dependencies()` shares one baseline across all variables and
  bisects to attribute a change, instead of testing each variable separately
  with its own baseline.

## Before you start

- **Back up the database**, or at least `port_default_version_variable`.
- Note your ports tree path (the `--repo` value, e.g.
  `/jails/freshports/usr/ports`) and confirm `/usr/local/etc/freshports/config.ini`
  still has the `database` section these scripts read.
- **None of this has been run against a real ports tree or a real Postgres
  instance.** It was verified against a fake `make` and a mock DB connection.
  Treat the first run as a test.

## 1. Deploy the code

Deploy together — the writer and the checker changed in step:

- `port_default_version_deps.py`
- `sync_port_default_version_variable.py`
- `populate_port_default_version_deps.py`
- `sync_default_version_variable.py`

## 2. Apply the migration

```bash
psql -f PostgreSQL/DDL/0300-port_default_version_variable_changed_vars.sql
```

Additive: it adds `changed_vars` and refreshes the table comments. The
`baseline_*`/`overridden_*` version columns stay and are still populated. It
uses `ADD COLUMN IF NOT EXISTS`, so it is a no-op on a database built from
`0200-port_default_version_variable.sql`, which already includes the column.

Run it against a copy first if you can. The Python side passes a list for
`changed_vars` and relies on psycopg2 adapting it to `text[]`; that path has not
been exercised against real Postgres.

## 3. Refresh the variable catalog

`port_default_version_variable` rows can only be written for variables already
in `default_version_variable`:

```bash
python3 sync_default_version_variable.py --repo /jails/freshports/usr/ports --dry-run
python3 sync_default_version_variable.py --repo /jails/freshports/usr/ports
```

## 4. Spot-check one port

No database involved:

```bash
python3 port_default_version_deps.py --repo /jails/freshports/usr/ports \
    /jails/freshports/usr/ports/archivers/R-cran-zip
```

Expect `depends on GCC_DEFAULT`, with a `BUILD_DEPENDS:` block showing the
baseline and overridden values. If it reports no dependency, stop — the rest of
the upgrade rests on this working. Add `--debug` to see each `make` command and
its result.

## 5. Dry run a category

```bash
python3 populate_port_default_version_deps.py \
    --repo /jails/freshports/usr/ports --category archivers --dry-run
```

`--dry-run` runs every `make` check but writes nothing. This is where you find
out what the run costs and whether the results look sane. Compare against
`--category lang` or `--category devel` too; they exercise different
frameworks.

Useful flags: `--port cat/name[,cat/name...]` for specific ports, `--limit N`
to cut the run short, `--debug` for every `make` invocation.

## 6. Full run

```bash
python3 populate_port_default_version_deps.py --repo /jails/freshports/usr/ports
```

It prints progress every 500 ports and a summary at the end:

```
checked:  N ports (E could not be checked at all, P partially)
depends:  D ports had at least one *_DEFAULT dependency
upserted: U port/variable rows
removed:  R port/variable rows (now independent)
```

Exit status is 1 if any port could not be checked at all, 0 otherwise. A port
that `make` could not evaluate is skipped rather than recorded: its existing
rows are left alone, never deleted on the strength of a failed check.

This run is slower than before. A port that depends on nothing still costs two
`make` calls, but each port that now counts as dependent costs roughly a dozen
more.

## 7. Verify

Rows that came from the new check, by variable:

```sql
SELECT dvv.name, count(*) AS ports, count(*) FILTER (WHERE 'PORTVERSION' = ANY(pdvv.changed_vars)) AS version_moved
  FROM port_default_version_variable pdvv
  JOIN default_version_variable dvv ON dvv.id = pdvv.default_version_variable_id
 GROUP BY dvv.name
 ORDER BY ports DESC;
```

What moved, across all rows:

```sql
SELECT unnest(changed_vars) AS variable, count(*)
  FROM port_default_version_variable
 GROUP BY 1 ORDER BY 2 DESC;
```

Rows not yet re-checked (should be 0 after a full run):

```sql
SELECT count(*) FROM port_default_version_variable
 WHERE changed_vars IS NULL AND status = 'depends';
```

**Watch for a variable that matches nearly every port.** Variables with no
`# Possible values:` comment in `Mk/bsd.default-versions.mk` get probed with a
fake sentinel, which can trip a framework's own validation for every port that
uses it — showing up as a tree-wide dependency, often via `IGNORE`. If one
variable dominates the first query, suspect the probe rather than the ports:
check `probe_value` and `detail` on those rows, and whether
`extract_possible_values()` finds values for that variable.

## Rollback

Revert the code. Leaving `changed_vars` in place is harmless — the previous
writer never referenced it, and the column is nullable.

Rolling back the *data* is a restore from backup: the new run will have deleted
rows for ports that are no longer dependent and added rows for ports that now
are.

## Keeping it correct afterwards

`COMPARED_VARS` is a hand-maintained copy of the `make -V` list in FreshPorts'
`scripts/Jail/scripts/make-port.sh`. If that list changes, update
`COMPARED_VARS` to match, or the check will stop covering something FreshPorts
displays.
