# FreshPorts `*_DEFAULT` Variable Tracking

Technical documentation for the code implementing [FreshPorts issue #659](https://github.com/FreshPorts/freshports/issues/659):
detecting when `*_DEFAULT` variables in `Mk/bsd.default-versions.mk` change, and
identifying which ports depend on them.

## Problem

Traditionally, FreshPorts determines a port version via `make -V PORTVERSION`

For each commit against a given port, that command is run and the current value
is obtained.

However, for some ports, the commit which affects the `PORTVERSION` value does not
touch the port.  FreshPorts does not run that command for the port[s] because it
does not know the details.

This code hopes to parse changes to Mk/bsd.default-versions.mk, detect any changes
which might affect PORTVERSION, and then update the affected ports.

See https://github.com/FreshPorts/freshports/issues/659 for more background.

Some ports (e.g. `lang/python`) don't set `PORTVERSION`/`DISTVERSION` as a literal
in their own `Makefile`. Instead they derive it from a shared variable defined in
`Mk/bsd.default-versions.mk` (e.g. `PYTHON_DEFAULT`). FreshPorts' existing
per-port commit tracking misses this: a commit to `Mk/bsd.default-versions.mk`
touches a file outside any port's own directory, so the affected ports never get
flagged for reprocessing.

This is two distinct problems, handled by two independent pieces of code:

1. **Commit-triggered detection** — when a commit touches
   `Mk/bsd.default-versions.mk`, determine whether a specific `*_DEFAULT`
   variable's *value* actually changed (not just the file).
2. **Port-level dependency detection** — determine whether a given port's
   `PORTVERSION`/`DISTVERSION` depends on a given `*_DEFAULT` variable at all.

## Language split

New code is Python, with Perl used only as glue into the existing FreshPorts
daemon (which is Perl). One deliberate exception: `PortDefaultVersionDeps.pm`
is Perl by explicit choice, in parallel with its Python equivalent
`port_default_version_deps.py`.

## Architecture

```
                    +-----------------------------------+
                    |  Existing Perl commit pipeline     |
                    |  (already tracks per-port          |
                    |   changed-file lists)              |
                    +------------------+------------------+
                                       | commit touches
                                       | Mk/bsd.default-versions.mk?
                                       v
                    +-----------------------------------+
                    |      DefaultVersionsFlag.pm        |
                    |  sets a flag for the daemon        |
                    +------------------+------------------+
                                       |
                                       v
                    +-----------------------------------+
                    |     DefaultVersionsRunner.pm       |
                    |  (daemon) launches the Python      |
                    |  script as a subprocess, reads     |
                    |  back its JSON result              |
                    +------------------+------------------+
                                       | subprocess
                                       v
                    +-----------------------------------+
                    | detect_default_version_changes.py  |
                    |  fetches the commit's diff,        |
                    |  runs detection, writes JSON       |
                    +------------------+------------------+
                                       | uses
                                       v
                    +-----------------------------------+
                    |    default_version_change.py       |
                    |  core diff-parsing logic           |
                    +-----------------------------------+

  Separately, on demand or on a schedule:

    sync_default_version_variable.py  -->  default_version_variable table
    (parses the live file, reconciles the table)

    port_default_version_deps.py      -->  sync_port_default_version_variable.py
    (per-port make -V check)               --> port_default_version_variable table
```

---

## Active files

### `default_version_change.py`

Core detection logic: given a unified diff (patch text) for a commit, determines
whether a specific variable (e.g. `PYTHON_DEFAULT`) changed value.

- `detect_default_version_change(diff_text, varname, target_file=None) -> ChangeResult`
  — scans `+`/`-` lines in the diff for a `VARNAME?=` assignment, compares old vs.
  new. Ignores comment-only changes near the variable (a real risk: commits touch
  `Mk/bsd.default-versions.mk` for unrelated reasons — comment edits, other
  variables — far more often than they change the variable you care about).
- `ChangeResult.status` is one of `changed`, `unchanged`, `anomaly` (e.g. a
  removed line with no corresponding added line — surfaced for manual review
  rather than guessed at).

Validated against four real commits from `freebsd/freebsd-ports`: a genuine
`PYTHON_DEFAULT` change, an unrelated-variable-in-the-same-file commit, a
comment-only commit, and an adversarial comment-only edit sitting directly
adjacent to (and textually mentioning) `PYTHON_DEFAULT`. All four behaved
correctly.

### `detect_default_version_changes.py`

CLI entry point launched by the FreshPorts daemon (via `DefaultVersionsRunner.pm`)
when a commit is flagged as touching `Mk/bsd.default-versions.mk`.

```
detect_default_version_changes.py --commit <sha> --repo <path> --output <json-path> [--vars VAR1,VAR2] [--target-file path]
```

- Fetches the diff for `Mk/bsd.default-versions.mk` itself (`git show <sha> --
  <path>`), via subprocess with list-form args (no shell interpolation).
- Runs `detect_default_version_change` for every variable in `--vars`, or (by
  default) every currently-active variable, sourced live from
  `sync_default_version_variable.parse_default_versions_file` against the given
  `--repo` — not a hardcoded list.
- Writes a single JSON result file **atomically** (temp file + `os.replace`), so
  the daemon never reads a half-written file.
- Exit code `0` = the check ran (regardless of what it found); `1` = the check
  itself failed (bad commit, git error, can't write output). A best-effort error
  JSON is still written on failure when possible.

### `sync_default_version_variable.py`

Two related but distinct jobs, both built around a shared parser:

1. **Parser** (`parse_default_versions_file`) — reads the live content of
   `Mk/bsd.default-versions.mk` and extracts every `*_DEFAULT` variable, with:
   - `active` — false if the only occurrence is commented out (e.g.
     `#EMACS_DEFAULT?=...`)
   - `shape` — `simple` (one plain assignment), `conditional` (multiple
     `?=`/`=` assignments under `.if`/`.elif`/`.else` branches), or `complex`
     (any assignment uses `:=` or `!=` — computed/derived, e.g.
     `PERL5_DEFAULT`, `SSL_DEFAULT`)
   - Tracks `.if`/`.elif`/`.else`/`.endif` nesting depth properly rather than
     guessing from indentation.
   - `extract_possible_values(text, varname)` — separately parses the
     `# Possible values: a, b, c` comment above a variable's assignment. Used
     by `port_default_version_deps.py` to probe with a real value instead of
     a fake one (see below — this exists because of a real production crash).

2. **Table sync** (`sync_table` / `compute_sync_plan`) — reconciles the
   `default_version_variable` table against a fresh parse:
   - Inserts new variables, updates changed ones, **skips no-op writes** for
     rows that already match (so routine syncs don't touch untouched rows).
   - **Soft-deactivates** (`active=false`) variables no longer found in the
     file, rather than deleting — other tables (like
     `port_default_version_variable`) reference rows here by id.
     `--hard-delete` opts into real deletion if you want it.

CLI:
```
sync_default_version_variable.py --repo <path> [--dry-run | --check] [--hard-delete] [--dsn <postgres-dsn>]
```
- `--dry-run` — parse only, print results, touch nothing (no DB connection at all).
- `--check` — connect read-only, report drift, exit `1` if any (or a real error),
  `0` if in sync. Suitable for a cron/monitoring job.
- *(neither)* — actually reconcile the table.

Supersedes the earlier hand-maintained `default_versions_config.py`/
`DefaultVersionsConfig.pm` (see **Superseded** below) — that list contained
real classification errors (`GO_DEFAULT`, `JAVA_DEFAULT`, `FIREBIRD_DEFAULT`
were guessed as `conditional`; live parsing against the real file shows all
three are `simple`).

Validated: parser tested against real current file content pulled from
`freebsd/freebsd-ports`; the insert/update/soft-deactivate reconciliation logic
tested end-to-end against a real database engine (SQLite standing in for
Postgres, since no Postgres instance was available in the dev sandbox — the
literal Postgres connection and `%s`/`now()` syntax has **not** been run
against real Postgres and should be smoke-tested before production use).

### `port_default_version_deps.py`

Determines whether a **specific port's** `PORTVERSION`/`DISTVERSION` depends on
a given `*_DEFAULT` variable — by asking `make` itself via differential
evaluation, not by parsing the port's Makefile.

Technique:
1. Baseline: `make -C <port_dir> -V PORTVERSION -V DISTVERSION`.
2. Override: same command, plus `VARNAME=<probe value>` on the command line.
3. Compare. A difference means the port's version depends on the variable.

**Probe value strategy matters.** The first version used a fake sentinel
(`999999.freshports-probe`) for every check. Against real `lang/python`, this
crashed `make` in two distinct ways: a numeric-comparison operator in
`python.mk` choked on a non-numeric value, and — the more fundamental issue —
`python.mk` uses `PYTHON_DEFAULT` to select a **sibling port directory**
(`lang/python312/Makefile.version`), so *any* fake value fails regardless of
formatting, because it doesn't correspond to a real port on disk.

The fix: `port_depends_on_default_var` now prefers a **real alternate value**,
pulled from `Mk/bsd.default-versions.mk`'s `# Possible values:` comment via
`extract_possible_values()` (see `sync_default_version_variable.py`), falling
back to the fake sentinel only when no real alternate is available (e.g.
`SSL_DEFAULT`, whose comment isn't positioned where the extractor can associate
it with the assignment).

- `port_depends_on_default_var(port_dir, default_var, probe_value=..., possible_values=...) -> DependencyResult`
  — `status` is `depends` (value flowed through), `depends_error` (override
  broke the build — still evidence of a dependency, just not of the resulting
  value), `independent`, or `error` (baseline call itself failed).
- `find_all_dependencies(port_dir, default_vars, repo_root=...) -> list[DependencyResult]`
  — checks a port against a list of variables, auto-loading `possible_values`
  for each from `repo_root` when given. Returns only `depends`/`depends_error`
  entries.

CLI:
```
port_default_version_deps.py <port_dir> [--repo <path>] [--vars VAR1,VAR2]
```
`--repo` defaults to two directories up from `port_dir` (the standard
`<repo>/category/port` layout).

Validated against the real crash (reproduced with the old sentinel approach,
confirmed fixed with a real alternate, using a fake `make` modeling the actual
`python.mk` failure modes), and against the real ports tree directly:
`lang/python` correctly shows `depends` on `PYTHON_DEFAULT` (`3.12` -> `3.10`),
`net-mgmt/librenms` correctly shows no dependency, as a control.

### `sync_port_default_version_variable.py`

Persists `find_all_dependencies()` results into the `port_default_version_variable`
table.

- `get_variable_ids(conn, names) -> dict[str, int]` — looks up
  `default_version_variable.id` for a list of names.
- `sync_port_dependencies(conn, port_id, checked_vars, results) -> dict` —
  upserts rows for variables now found dependent, and **removes** rows for
  variables that were dependent on a previous run but aren't anymore among the
  ones actually rechecked this run. (Real `DELETE`, not soft — this table is
  purely derived/recomputable, unlike `default_version_variable`.)

**Important:** `checked_vars` must be the *complete* list of variables actually
checked this run, not just the ones that came back dependent — that's what lets
the function distinguish "checked, now independent" (row removed) from "not
checked this run" (row left alone). Passing a partial list risks deleting real
dependencies for variables that were never re-evaluated.

Validated end-to-end (real dependency-check results from the fake `make`,
persisted into and reconciled against a SQLite-simulated schema), including the
stale-dependency-removal path specifically.

### `DefaultVersionsFlag.pm` (Perl)

The Perl-side detection half. Given a commit your pipeline is already
processing (with its changed-file list already available), checks whether it
touched `Mk/bsd.default-versions.mk` and, if so, sets a flag for the daemon.
Deliberately does *no* diff fetching or detection itself — that all lives in
Python now.

- `flag_if_default_versions_touched($commit)` — two `TODO`s left for your
  actual implementations: `_changed_files_for` (your existing accessor) and
  `_set_flag` (however your pipeline persists "check this commit" — DB row,
  queue entry, etc.).

### `DefaultVersionsRunner.pm` (Perl)

The daemon-side half. Launches `detect_default_version_changes.py` as a
subprocess for a flagged commit SHA, waits for it, reads back its JSON result.

- `run_default_version_check($commit_sha) -> \%result` — builds the command
  line as a list (never a shell string), runs it via `system(@cmd)`, reads and
  JSON-decodes the output file. Dies with the script's own error detail
  bubbled through on failure.
- Config via package variables (not constants, so tests/callers can override):
  `$PYTHON_BIN`, `$SCRIPT_PATH`, `$REPO_DIR`, `$OUTPUT_DIR`.

Validated end-to-end: genuinely launches the real Python script as a
subprocess (not mocked) and correctly parses its real JSON output, for both a
successful commit check and a failing one (bad SHA, error detail correctly
propagated into the Perl `die` message).

### `PortDefaultVersionDeps.pm` (Perl — explicit exception to the Python-first strategy)

Perl port of `port_default_version_deps.py`, kept in parallel by deliberate
request. Same technique, same real-alternate-value fix, same API shape
(adjusted for Perl idiom — an options hashref as the third argument instead of
Python keyword arguments).

- `port_depends_on_default_var($port_dir, $default_var, \%opts)` — `\%opts`:
  `probe_value` (fallback sentinel) and `possible_values` (arrayref of real
  alternates, preferred).
- `find_all_dependencies($port_dir, \@default_vars, \%opts)` — `\%opts`:
  `repo_root`, used to auto-load `possible_values` per variable.
- `extract_possible_values($text, $varname)` — Perl port of the Python
  extractor; tested for exact parity against the same real fixture text.

Uses `IPC::Open3` for subprocess calls (list-form args, no shell
interpolation), matching the security posture of the Python version's
`subprocess.run`.

Validated identically to the Python version: the real crash reproduced with
the old sentinel approach, confirmed fixed with a real alternate, against a
fake `make` modeling the real `python.mk` failure modes.

**API note:** an earlier version of this file took `$probe_value` as a bare
third positional argument. That signature is gone — third argument is now
always an options hashref. No other file in this codebase called the old
signature, so this is a clean break, not a compatibility concern.

### `default_version_variable.sql`

DDL for the `default_version_variable` table — one row per `*_DEFAULT`
variable found in `Mk/bsd.default-versions.mk`.

| Column | Notes |
|---|---|
| `id` | `bigint GENERATED ALWAYS AS IDENTITY` |
| `name` | unique, e.g. `PYTHON_DEFAULT` |
| `shape` | `CHECK`-constrained to `simple`/`conditional`/`complex` (not a Postgres `ENUM`, deliberately — easier to alter later) |
| `active` | false for variables present but currently commented out upstream |
| `last_synced_at`, `created_at`, `updated_at` | `updated_at` kept current via trigger |

Populated/kept in sync by `sync_default_version_variable.py`, not by hand.

**Not run against a real Postgres instance** (none available in the dev
sandbox) — structurally sane, but worth a real `psql -f` run before trusting
it in production.

### `port_default_version_variable.sql`

DDL for the per-port dependency results table — the payoff of the whole
feature: given a `*_DEFAULT` variable that changed, this is what lets you find
every port that needs reprocessing.

| Column | Notes |
|---|---|
| `port_id` | `REFERENCES ports(id) ON DELETE CASCADE` |
| `default_version_variable_id` | `REFERENCES default_version_variable(id) ON DELETE CASCADE` |
| `status` | `CHECK`-constrained to `depends`/`depends_error` only |
| `baseline_portversion`, `baseline_distversion` | values with no override |
| `overridden_portversion`, `overridden_distversion` | values with the probe applied; `NULL` when `status=depends_error` |
| `probe_value` | the value actually used — real alternate or fallback sentinel |
| `detail` | error detail, for `depends_error` rows |

**Design choice:** only `depends`/`depends_error` outcomes get a row.
`independent` is represented by the *absence* of a row, not a row with
`status=independent`. This means a missing row doesn't distinguish "checked
and found independent" from "never checked" — if that distinction matters to
you later, a separate last-checked marker would be needed.

Indexed on `default_version_variable_id`, since the whole point is fast
lookup: *"`PYTHON_DEFAULT` just changed — which ports depend on it?"*

**Ordering dependency:** references `default_version_variable(id)`, so must be
applied *after* `default_version_variable.sql` if your migration tooling
doesn't already enforce explicit ordering.

**Not run against a real Postgres instance**, same caveat as above.

---

## Superseded files

Kept for reference/history, not part of the active design. Safe to delete from
a deployed tree.

| File | Superseded by | Why |
|---|---|---|
| `DefaultVersionChange.pm` | `default_version_change.py` | Original Perl implementation, written before the "new code is Python" decision. Verified to produce identical results to the Python version against all four real test commits, but not the one actually wired into the pipeline. |
| `DefaultVersionCheck.pm` | `DefaultVersionsFlag.pm` + `DefaultVersionsRunner.pm` + `detect_default_version_changes.py` | Earlier all-in-one Perl integration sketch (gate + diff fetch + detect + dispatch), written before the decision to do detection in Python and split flagging from running. |
| `DefaultVersionsConfig.pm` | `sync_default_version_variable.py` (`parse_default_versions_file`) | Hand-maintained snapshot of the variable list, including `shape` classifications. **Contained real errors** — three variables' shapes were guessed wrong. Superseded by live parsing of the actual file. |
| `default_versions_config.py` | same | Python counterpart of the above, same issue, same fix. |
| `generate_default_version_variable_seed.py` | `sync_default_version_variable.py` | Generated one-time seed SQL from the (flawed) hand-maintained config. `sync_default_version_variable.py` does both the initial seed *and* ongoing sync, from live data. |
| `seed_default_version_variable.sql` | same | Generated output of the script above — inherits the same staleness. |
| `find-external-portversion.sh` | *(no direct replacement — approach abandoned)* | The very first attempt: a static grep-based scan of the whole ports tree for externally-driven `PORTVERSION`/`DISTVERSION` values. Explicitly rejected in favor of the commit-diff approach (`default_version_change.py`) for change detection and the `make -V` differential approach (`port_default_version_deps.py`) for per-port dependency checking — both more robust than regex scanning. |

---

## What's not built yet

- A batch runner to populate `port_default_version_variable` across the whole
  tree (or newly-added ports) — everything's currently per-port, on demand.
- The actual "take action" step when a `changed`/`depends` result is found
  (queueing a reprocess, notification, etc.) — deliberately left as stub hooks
  throughout (`_on_python_default_changed` in the retired
  `DefaultVersionCheck.pm`, and implicitly wherever `detect_default_version_changes.py`'s
  JSON output gets consumed) since this was explicitly out of scope for the
  conversation that produced this code.
- Support for `*_DEFAULT` variables beyond `PYTHON_DEFAULT` in the
  commit-detection path has been exercised in testing (`GO_DEFAULT`,
  `RUBY_DEFAULT`, `PERL5_DEFAULT`, etc. all appear in test cases), but
  production usage so far has focused on Python specifically, per explicit
  scope decisions made early in development.
