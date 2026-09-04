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
our @EXPORT_OK = qw(port_depends_on_default_var find_all_dependencies extract_possible_values);

use constant VERSION_VARS        => qw(PORTVERSION DISTVERSION);
use constant DEFAULT_PROBE_VALUE => '999999.freshports-probe';

# port_depends_on_default_var($port_dir, $default_var, \%opts)
#
# \%opts (all optional):
#   probe_value     - fallback fake value, used only if possible_values
#                      isn't given or has no usable alternate. Some
#                      *_DEFAULT variables (notably PYTHON_DEFAULT) are
#                      used to select a SIBLING PORT DIRECTORY, not
#                      just compared against -- for those, ANY made-up
#                      value fails regardless of formatting, because
#                      the referenced port directory doesn't exist.
#                      Default: DEFAULT_PROBE_VALUE.
#   possible_values - arrayref of the variable's real legitimate
#                      values (e.g. from extract_possible_values()
#                      against Mk/bsd.default-versions.mk). When
#                      given, the probe uses a REAL alternate value
#                      instead of a fake one -- the reliable path,
#                      avoiding the "Cannot open .../Makefile.version"
#                      class of failure entirely.
#
# Returns a hashref:
#   { status => 'depends',       default_var => ..., baseline => {...}, overridden => {...}, probe_appears => 0|1, probe_value => ... }
#   { status => 'depends_error', default_var => ..., baseline => {...}, detail => '...', probe_value => ... }
#   { status => 'independent',   default_var => ..., baseline => {...} }
#   { status => 'error',         default_var => ..., detail => '...' }
sub port_depends_on_default_var {
    my ($port_dir, $default_var, $opts) = @_;
    $opts //= {};
    my $fallback_probe   = $opts->{probe_value} // DEFAULT_PROBE_VALUE;
    my $possible_values  = $opts->{possible_values};

    my @extra = $possible_values ? ($default_var) : ();
    my ($rc, $baseline_full, $err) = _run_make($port_dir, undef, \@extra);
    if ($rc != 0) {
        return {
            status      => 'error',
            default_var => $default_var,
            detail      => "baseline \`make -V\` failed (exit $rc): $err",
        };
    }
    my %baseline = map { $_ => $baseline_full->{$_} } VERSION_VARS;

    my $chosen_probe;
    my $used_real_alternate = 0;
    if ($possible_values && @$possible_values) {
        my $current_value = $baseline_full->{$default_var};
        my @alternates = grep { !defined($current_value) || $_ ne $current_value } @$possible_values;
        if (@alternates) {
            $chosen_probe = $alternates[0];
            $used_real_alternate = 1;
        }
    }
    $chosen_probe //= $fallback_probe;

    my ($rc2, $overridden, $err2) = _run_make($port_dir, { $default_var => $chosen_probe });

    if ($rc2 != 0) {
        # The override itself broke the build (e.g. the port validates
        # the value and errors on anything unrecognized). Still strong
        # evidence the variable matters to this port.
        my $note = $used_real_alternate
            ? ' (used a real alternate value from possible_values, not a fake sentinel -- unexpected that this still failed)'
            : '';
        return {
            status      => 'depends_error',
            default_var => $default_var,
            baseline    => \%baseline,
            probe_value => $chosen_probe,
            detail      => "\`make -V\` with $default_var=$chosen_probe failed (exit $rc2): $err2$note",
        };
    }

    unless (_values_equal(\%baseline, $overridden)) {
        my $probe_appears = (grep { index($_, $chosen_probe) >= 0 } values %$overridden) ? 1 : 0;
        return {
            status        => 'depends',
            default_var   => $default_var,
            baseline      => \%baseline,
            overridden    => $overridden,
            probe_appears => $probe_appears,
            probe_value   => $chosen_probe,
        };
    }

    return { status => 'independent', default_var => $default_var, baseline => \%baseline };
}

# find_all_dependencies($port_dir, \@default_vars, \%opts)
#
# \%opts:
#   repo_root - if given, Mk/bsd.default-versions.mk is read once and
#               used to look up real possible_values for each
#               variable, so probing uses real alternates rather than
#               the fake sentinel wherever possible. Strongly
#               recommended -- omit only if you don't have a repo
#               checkout available.
#
# Checks a port against a list of *_DEFAULT variable names and
# returns only the ones it actually depends on ('depends' or
# 'depends_error'). Runs one baseline call, then one additional call
# per candidate variable -- fine for an on-demand per-port check, not
# intended for scanning the whole tree in a tight loop.
sub find_all_dependencies {
    my ($port_dir, $default_vars, $opts) = @_;
    $opts //= {};

    my %possible_values_by_var;
    if ($opts->{repo_root}) {
        my $mk_text = _read_target_file($opts->{repo_root});
        for my $var (@$default_vars) {
            my @vals = extract_possible_values($mk_text, $var);
            $possible_values_by_var{$var} = \@vals if @vals;
        }
    }

    my @results;
    for my $var (@$default_vars) {
        my $r = port_depends_on_default_var($port_dir, $var, {
            possible_values => $possible_values_by_var{$var},
        });
        push @results, $r if $r->{status} eq 'depends' || $r->{status} eq 'depends_error';
    }
    return @results;
}

# extract_possible_values($text, $varname)
#
# Scan the file for a "# Possible values: a, b, c" (or "Possible
# value:", or "Possible_values:") style comment immediately preceding
# an assignment line for $varname, and return the listed tokens.
#
# Best-effort text scan, not a full parser -- if no matching comment
# is found (or the format doesn't match, e.g. SSL_DEFAULT's comment is
# separated from its assignment by several nested .if lines), returns
# an empty list and callers fall back to sentinel probing.
sub extract_possible_values {
    my ($text, $varname) = @_;

    my @lines = split /\n/, $text;
    my $assignment_re = qr/^([A-Za-z_][A-Za-z0-9_]*_DEFAULT)\s*(?:\?=|:=|\+=|!=|=)/;

    for (my $i = 0; $i < @lines; $i++) {
        my $stripped = $lines[$i];
        $stripped =~ s/^\s+|\s+$//g;
        next unless $stripped =~ /^#/;

        my $content = $stripped;
        $content =~ s/^#\s*//;
        next unless $content =~ /possible[_ ]values?:\s*(.+)/i;
        my $values_text = $1;

        # Look ahead a few lines for the matching assignment.
        for (my $j = $i + 1; $j < @lines && $j < $i + 6; $j++) {
            my $la = $lines[$j];
            $la =~ s/^\s+|\s+$//g;
            next if $la eq '';
            if ($la =~ $assignment_re && $1 eq $varname) {
                $values_text =~ s/\s*\(.*$//;   # strip trailing parenthetical note
                my @tokens = grep { length } split /[,\s]+/, $values_text;
                return @tokens;
            }
            last unless $la =~ /^#/;   # non-comment, non-matching line -- not ours
        }
    }

    return ();
}

sub _read_target_file {
    my ($repo_root) = @_;
    open(my $fh, '<', "$repo_root/Mk/bsd.default-versions.mk")
        or die "cannot open $repo_root/Mk/bsd.default-versions.mk: $!";
    local $/;
    my $text = <$fh>;
    close($fh);
    return $text;
}

# ---------------------------------------------------------------
# Internals
# ---------------------------------------------------------------

# _run_make($port_dir, \%overrides, \@extra_vars)
#
# Runs `make -C port_dir -V PORTVERSION -V DISTVERSION [-V extra ...] [VAR=value ...]`
# via IPC::Open3, with args passed as a LIST (never a shell string) so
# port_dir/override values can't be interpreted by a shell.
#
# extra_vars lets callers also query the *_DEFAULT variable's own
# current value in the same invocation (used to pick a real
# alternate probe value -- see port_depends_on_default_var).
#
# Returns ($rc, \%values, $stderr_text). On non-zero exit, %values may
# be empty -- callers check $rc before trusting it.
sub _run_make {
    my ($port_dir, $overrides, $extra_vars) = @_;

    my @query_vars = (VERSION_VARS, @{ $extra_vars // [] });
    my @cmd = ('make', '-C', $port_dir, map { ('-V', $_) } @query_vars);
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
        @values{@query_vars} = @lines;
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
