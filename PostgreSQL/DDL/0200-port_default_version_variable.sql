CREATE TABLE port_default_version_variable (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    port_id                  integer     NOT NULL REFERENCES ports(id) ON DELETE CASCADE,
    default_version_variable_id
                             bigint      NOT NULL REFERENCES default_version_variable(id) ON DELETE CASCADE,
    status                   text        NOT NULL,
    baseline_portversion     text,
    baseline_distversion     text,
    overridden_portversion   text,
    overridden_distversion   text,
    probe_value              text,
    detail                   text,
    checked_at               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT port_default_version_variable_uniq
        UNIQUE (port_id, default_version_variable_id),
    CONSTRAINT port_default_version_variable_status_check
        CHECK (status IN ('depends', 'depends_error'))
);

-- The whole point of this table: given a *_DEFAULT variable that just
-- changed, quickly find every port that depends on it.
CREATE INDEX idx_port_default_version_variable_variable_id
    ON port_default_version_variable (default_version_variable_id);

COMMENT ON TABLE port_default_version_variable IS
    'Per-port result of checking whether a port''s PORTVERSION/DISTVERSION depends on a given *_DEFAULT variable, via differential make -V evaluation (see port_default_version_deps.py). Only "depends" and "depends_error" outcomes are stored -- an "independent" result is represented by the ABSENCE of a row for that (port_id, default_version_variable_id) pair, not by a row with status=independent. A missing row does not distinguish "checked and independent" from "never checked" -- if that distinction matters later, a separate last-checked marker would be needed.';
COMMENT ON COLUMN port_default_version_variable.status IS
    'depends: the probe value flowed through into PORTVERSION/DISTVERSION. depends_error: overriding the variable broke the build (make exited non-zero) -- still evidence of a dependency, just not evidence of the resulting value.';
COMMENT ON COLUMN port_default_version_variable.probe_value IS
    'The value used to override the variable for this check -- a real alternate from Mk/bsd.default-versions.mk''s "Possible values" comment when available, otherwise the fallback sentinel.';
COMMENT ON COLUMN port_default_version_variable.baseline_portversion IS
    'PORTVERSION as make -V reports it with no override.';
COMMENT ON COLUMN port_default_version_variable.overridden_portversion IS
    'PORTVERSION as make -V reports it with the variable overridden to probe_value. NULL when status=depends_error, since the overridden make invocation failed before producing output.';
