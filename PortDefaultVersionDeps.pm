package PortDefaultVersionDeps;

#
# Determines whether a given port's PORTVERSION/DISTVERSION depends
# on a *_DEFAULT variable (e.g. PYTHON_DEFAULT) -- by asking `make`
# itself, via differential evaluation, rather than parsing the port's
# Makefile.
#
# Technique:
#   1. Run `make -C <port_dir> -V PORTVERSION -V DISTVERSION` for a
#      baseline.
#   2. Run it again with the candidate variable overridden on the
#      command line to an obviously-fake probe value.
#   3. Compare. A difference (or a make failure caused by the probe
#      value failing the port's own validation) means the port's
#      version genuinely depends on that variable.
#
# This deliberately does not parse Makefile text at all -- `make`
# evaluates the real .include chain (Mk/bsd.default-versions.mk,
# Uses/*.mk, conditionals, etc.), so this is authoritative in a way
# regex-based scanning can't be.
#
# Note: only sanity-checked against a fake `make` stand-in in
# development (no bmake / ports tree available there) -- validate
# against a real ports tree before relying on it.
#

use strict;
use warnings;

use IPC::Open3;
use Symbol qw(gensym);
use POSIX  qw();

use Exporter 'import';
our @EXPORT_OK = qw(port_depends_on_default_var find_all_dependencies);

use constant VERSION_VARS        => qw(PORTVERSION DISTVERSION);
use constant DEFAULT_PROBE_VALUE => '999999.freshports-probe';

# port_depends_on_default_var($port_dir, $default_var, $probe_value)
#
# Returns a hashref:
#   { status => 'depends',       default_var => ..., baseline => {...}, overridden => {...}, probe_appears => 0|1 }
#   { status => 'depends_error', default_var => ..., baseline => {...}, detail => '...' }
#   { status => 'independent',   default_var => ..., baseline => {...} }
#   { status => 'error',         default_var => ..., detail => '...' }
sub port_depends_on_default_var {
    my ($port_dir, $default_var, $probe_value) = @_;
    $probe_value //= DEFAULT_PROBE_VALUE;

    my ($rc, $baseline, $err) = _run_make($port_dir);
    if ($rc != 0) {
        return {
            status      => 'error',
            default_var => $default_var,
            detail      => "baseline \`make -V\` failed (exit $rc): $err",
        };
    }

    my ($rc2, $overridden, $err2) = _run_make($port_dir, { $default_var => $probe_value });

    if ($rc2 != 0) {
        # The override itself broke the build (e.g. the port validates
        # the value and errors on anything unrecognized). Still strong
        # evidence the variable matters to this port.
        return {
            status      => 'depends_error',
            default_var => $default_var,
            baseline    => $baseline,
            detail      => "\`make -V\` with $default_var=$probe_value failed (exit $rc2): $err2",
        };
    }

    unless (_values_equal($baseline, $overridden)) {
        my $probe_appears = (grep { index($_, $probe_value) >= 0 } values %$overridden) ? 1 : 0;
        return {
            status        => 'depends',
            default_var   => $default_var,
            baseline      => $baseline,
            overridden    => $overridden,
            probe_appears => $probe_appears,
        };
    }

    return { status => 'independent', default_var => $default_var, baseline => $baseline };
}

# find_all_dependencies($port_dir, \@default_vars)
#
# Checks a port against a list of *_DEFAULT variable names and
# returns only the ones it actually depends on ('depends' or
# 'depends_error'). Runs one baseline call, then one additional call
# per candidate variable -- fine for an on-demand per-port check, not
# intended for scanning the whole tree in a tight loop.
sub find_all_dependencies {
    my ($port_dir, $default_vars) = @_;

    my @results;
    for my $var (@$default_vars) {
        my $r = port_depends_on_default_var($port_dir, $var);
        push @results, $r if $r->{status} eq 'depends' || $r->{status} eq 'depends_error';
    }
    return @results;
}

# ---------------------------------------------------------------
# Internals
# ---------------------------------------------------------------

# _run_make($port_dir, \%overrides)
#
# Runs `make -C port_dir -V PORTVERSION -V DISTVERSION [VAR=value ...]`
# via IPC::Open3, with args passed as a LIST (never a shell string) so
# port_dir/override values can't be interpreted by a shell.
#
# Returns ($rc, \%values, $stderr_text). On non-zero exit, %values may
# be empty -- callers check $rc before trusting it.
sub _run_make {
    my ($port_dir, $overrides) = @_;

    my @cmd = ('make', '-C', $port_dir, map { ('-V', $_) } VERSION_VARS);
    if ($overrides) {
        for my $k (sort keys %$overrides) {
            push @cmd, "$k=$overrides->{$k}";
        }
    }

    my ($wtr, $rdr, $errfh);
    $errfh = gensym;
    my $pid = open3($wtr, $rdr, $errfh, @cmd);
    close($wtr);

    local $/;
    my $stdout = <$rdr>  // '';
    my $stderr = <$errfh> // '';
    close($rdr);
    close($errfh);

    waitpid($pid, 0);
    my $rc = $? >> 8;

    my @lines = split /\n/, $stdout;
    my %values;
    if ($rc == 0) {
        my @varnames = VERSION_VARS;
        @values{@varnames} = @lines;
    }

    $stderr =~ s/\s+$//;
    return ($rc, \%values, $stderr);
}

sub _values_equal {
    my ($a, $b) = @_;
    return 0 unless keys(%$a) == keys(%$b);
    for my $k (keys %$a) {
        return 0 unless exists $b->{$k} && $a->{$k} eq $b->{$k};
    }
    return 1;
}

1;
