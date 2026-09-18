-- Migration for databases created before port_default_version_variable
-- had changed_vars. Fresh installs already get the column from
-- 0200-port_default_version_variable.sql; IF NOT EXISTS makes this a
-- no-op there, so the DDL files can still be applied in order.
--
-- Additive only: the baseline_/overridden_ version columns are kept, and
-- still populated. Existing rows get NULL changed_vars until
-- populate_port_default_version_deps.py re-checks them -- and note that
-- rows only exist for ports whose VERSION depended on a variable, so the
-- re-check is also what finds the ports that depend through BUILD_DEPENDS,
-- FLAVORS and the like.

ALTER TABLE port_default_version_variable
    ADD COLUMN IF NOT EXISTS changed_vars text[];

COMMENT ON TABLE port_default_version_variable IS
    'Per-port result of checking whether anything FreshPorts extracts from a port with make -V (PORTVERSION, but also BUILD_DEPENDS, FLAVORS, etc. -- see COMPARED_VARS) depends on a given *_DEFAULT variable, via differential make -V evaluation (see port_default_version_deps.py). Only "depends" and "depends_error" outcomes are stored -- an "independent" result is represented by the ABSENCE of a row for that (port_id, default_version_variable_id) pair, not by a row with status=independent. A missing row does not distinguish "checked and independent" from "never checked" -- if that distinction matters later, a separate last-checked marker would be needed.';
COMMENT ON COLUMN port_default_version_variable.status IS
    'depends: overriding the variable changed at least one compared make -V value (see changed_vars). depends_error: overriding the variable broke the build (make exited non-zero) -- still evidence of a dependency, just not evidence of the resulting value.';
COMMENT ON COLUMN port_default_version_variable.changed_vars IS
    'The compared make -V variables (e.g. {BUILD_DEPENDS}) whose values moved when the *_DEFAULT variable was overridden. A dependency need not touch the version: when PORTVERSION/DISTVERSION are not listed here, the baseline_* and overridden_* version columns are equal. NULL when status=depends_error (make failed, so nothing could be compared), and on rows written before this column existed until they are re-checked.';
