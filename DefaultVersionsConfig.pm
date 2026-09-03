package DefaultVersionsConfig;

#
# Config data for the set of "FOO_DEFAULT" variables defined in
# Mk/bsd.default-versions.mk (freebsd/freebsd-ports, main branch).
#
# This is a SNAPSHOT, not a generated/live list -- the upstream file
# changes over time (vars get added/removed), so treat this as
# something to periodically re-sync by hand against the real file,
# not a permanent source of truth.
#
# Each entry notes whether the variable is a plain, single-line
# "VARNAME?= value" assignment (the shape detect_default_version_change
# is built to parse cleanly) or something more complex (conditional
# branches, multi-line, or computed from the installed binary) --
# those are more likely to trip the 'anomaly' path and may need
# bespoke handling if/when you watch them.
#
# active => 0 means the variable is present in the file but currently
# commented out upstream (e.g. EMACS_DEFAULT) -- not actually in
# effect right now.
#

use strict;
use warnings;

use Exporter 'import';
our @EXPORT_OK = qw(default_version_vars);

my @VARS = (
    { name => 'APACHE_DEFAULT',         shape => 'simple',      active => 1 },
    { name => 'BDB_DEFAULT',            shape => 'simple',      active => 1 },
    { name => 'COROSYNC_DEFAULT',       shape => 'simple',      active => 1 },
    { name => 'EBUR128_DEFAULT',        shape => 'conditional', active => 1 },
    { name => 'EMACS_DEFAULT',          shape => 'simple',      active => 0 },  # commented out upstream
    { name => 'FIREBIRD_DEFAULT',       shape => 'conditional', active => 1 },
    { name => 'FORTRAN_DEFAULT',        shape => 'simple',      active => 1 },
    { name => 'FPC_DEFAULT',            shape => 'conditional', active => 1 },
    { name => 'GCC_DEFAULT',            shape => 'simple',      active => 1 },
    { name => 'GHOSTSCRIPT_DEFAULT',    shape => 'simple',      active => 1 },
    { name => 'GL_DEFAULT',             shape => 'simple',      active => 1 },
    { name => 'GO_DEFAULT',             shape => 'simple',      active => 1 },
    { name => 'GUILE_DEFAULT',          shape => 'simple',      active => 1 },
    { name => 'IMAGEMAGICK_DEFAULT',    shape => 'simple',      active => 1 },
    { name => 'JAVA_DEFAULT',           shape => 'conditional', active => 1 },
    { name => 'LAZARUS_DEFAULT',        shape => 'conditional', active => 1 },
    { name => 'LIBRSVG2_DEFAULT',       shape => 'conditional', active => 1 },
    { name => 'LINUX_DEFAULT',          shape => 'conditional', active => 1 },
    { name => 'LLVM_DEFAULT',           shape => 'simple',      active => 1 },
    { name => 'LUA_DEFAULT',            shape => 'simple',      active => 1 },
    { name => 'LUAJIT_DEFAULT',         shape => 'conditional', active => 1 },
    { name => 'MONO_DEFAULT',           shape => 'simple',      active => 1 },
    { name => 'MYSQL_DEFAULT',          shape => 'simple',      active => 1 },
    { name => 'NINJA_DEFAULT',          shape => 'simple',      active => 1 },
    { name => 'NODEJS_DEFAULT',         shape => 'simple',      active => 1 },
    { name => 'OPENLDAP_DEFAULT',       shape => 'simple',      active => 1 },
    { name => 'PERL5_DEFAULT',          shape => 'complex',     active => 1 },  # conditional + binary-derived fallback
    { name => 'PGSQL_DEFAULT',          shape => 'simple',      active => 1 },
    { name => 'PHP_DEFAULT',            shape => 'simple',      active => 1 },
    { name => 'PYCRYPTOGRAPHY_DEFAULT', shape => 'conditional', active => 1 },
    { name => 'PYTHON_DEFAULT',         shape => 'simple',      active => 1 },
    { name => 'PYTHON2_DEFAULT',        shape => 'simple',      active => 1 },
    { name => 'RUBY_DEFAULT',           shape => 'simple',      active => 1 },
    { name => 'RUST_DEFAULT',           shape => 'simple',      active => 1 },
    { name => 'SAMBA_DEFAULT',          shape => 'simple',      active => 1 },
    { name => 'SSL_DEFAULT',            shape => 'complex',     active => 1 },  # multi-branch, installed-lib detection
    { name => 'SUDO_DEFAULT',           shape => 'simple',      active => 1 },  # NB: not in the upstream reserved-name guard loop
    { name => 'TCLTK_DEFAULT',          shape => 'simple',      active => 1 },
    { name => 'VARNISH_DEFAULT',        shape => 'simple',      active => 1 },
);

# default_version_vars(%opts)
#
# Returns a list of variable-name strings (e.g. 'PYTHON_DEFAULT').
#
# Options:
#   active_only => 1   - exclude vars not currently in effect (default: 1)
#   shape       => 'simple' | 'conditional' | 'complex'
#                       - restrict to one shape category (default: all)
#
# Examples:
#   my @all_simple = default_version_vars(shape => 'simple');
#   my @everything_active = default_version_vars();
sub default_version_vars {
    my (%opts) = @_;
    my $active_only = exists $opts{active_only} ? $opts{active_only} : 1;

    my @result =
        grep { !$active_only || $_->{active} }
        grep { !defined $opts{shape} || $_->{shape} eq $opts{shape} }
        @VARS;

    return map { $_->{name} } @result;
}

1;
