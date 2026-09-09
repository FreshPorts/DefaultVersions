CREATE TABLE default_version_variable (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            text        NOT NULL,
    shape           text        NOT NULL DEFAULT 'simple',
    active          boolean     NOT NULL DEFAULT true,
    last_synced_at  timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT default_version_variable_name_uniq UNIQUE (name),
    CONSTRAINT default_version_variable_shape_check
        CHECK (shape IN ('simple', 'conditional', 'complex'))
);

COMMENT ON TABLE  default_version_variable IS
    'One row per *_DEFAULT variable defined in Mk/bsd.default-versions.mk (e.g. PYTHON_DEFAULT). Snapshot data, periodically re-synced against the upstream file.';
COMMENT ON COLUMN default_version_variable.name IS
    'The variable name as it appears in Mk/bsd.default-versions.mk, e.g. PYTHON_DEFAULT.';
COMMENT ON COLUMN default_version_variable.shape IS
    'simple: a plain single-line VARNAME?= value assignment. conditional: value depends on a branch (.if/.elif) but is still a straightforward assignment. complex: multi-branch and/or derived from the installed binary (e.g. PERL5_DEFAULT, SSL_DEFAULT) -- more likely to need bespoke handling in diff-based detection.';
COMMENT ON COLUMN default_version_variable.active IS
    'False if the variable is present in the file but currently commented out upstream (e.g. EMACS_DEFAULT) -- not actually in effect right now.';
COMMENT ON COLUMN default_version_variable.last_synced_at IS
    'When this row was last reconciled against the real Mk/bsd.default-versions.mk file. The variable list drifts over time (additions/removals), so this is not a live/generated table.';

-- Keep updated_at current on any row change.
CREATE OR REPLACE FUNCTION default_version_variable_set_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_default_version_variable_updated_at
    BEFORE UPDATE ON default_version_variable
    FOR EACH ROW
    EXECUTE FUNCTION default_version_variable_set_updated_at();
