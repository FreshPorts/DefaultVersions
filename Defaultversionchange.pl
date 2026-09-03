package DefaultVersionChange;

#
# Detects a change to a specific "FOO_DEFAULT?=" style variable
# assignment (e.g. PYTHON_DEFAULT) by inspecting a unified diff
# (patch text) for a commit -- not by evaluating make or diffing
# whole-file content.
#
# Intended use: your commit pipeline already knows a given commit
# touches Mk/bsd.default-versions.mk. Hand this module the diff text
# for that commit (or just the diff for that one file, if you've
# already isolated it) and ask whether a specific variable's value
# changed.
#

use strict;
use warnings;

use Exporter 'import';
our @EXPORT_OK = qw(detect_default_version_change);

# detect_default_version_change($diff_text, $varname, $target_file)
#
# $diff_text   - the patch/diff text for the commit (may contain
#                multiple files' hunks; we isolate $target_file)
# $varname     - e.g. 'PYTHON_DEFAULT'
# $target_file - path as it appears in the diff headers, e.g.
#                'Mk/bsd.default-versions.mk'. Optional -- if
#                omitted, the whole $diff_text is scanned as-is
#                (use this if you've already pre-scoped the diff to
#                just that file).
#
# Returns a hashref:
#   { status => 'changed',   old => '3.11', new => '3.12' }
#   { status => 'unchanged' }                                  # var line not touched
#   { status => 'anomaly',   detail => '...', raw => '...' }   # removed w/o added, or vice versa, or >1 pair
#
sub detect_default_version_change {
    my ($diff_text, $varname, $target_file) = @_;

    die "varname required" unless defined $varname && length $varname;

    my $scoped = defined $target_file
        ? _isolate_file_diff($diff_text, $target_file)
        : $diff_text;

    return { status => 'unchanged' } unless defined $scoped && length $scoped;

    my @removed;
    my @added;

    for my $line (split /\n/, $scoped) {
        # Only look at actual added/removed lines, not diff metadata
        # ('---', '+++') or context lines.
        if ($line =~ /^-(?!--)\s*\Q$varname\E\??=\s*(\S+)/) {
            push @removed, _strip_comment($1);
        }
        elsif ($line =~ /^\+(?!\+\+)\s*\Q$varname\E\??=\s*(\S+)/) {
            push @added, _strip_comment($1);
        }
    }

    if (!@removed && !@added) {
        return { status => 'unchanged' };
    }

    if (@removed == 1 && @added == 1) {
        if ($removed[0] eq $added[0]) {
            # Line was touched (e.g. whitespace, comment) but the
            # actual value didn't change.
            return { status => 'unchanged' };
        }
        return { status => 'changed', old => $removed[0], new => $added[0] };
    }

    # Anything else -- 0/1 mismatch, or multiple assignment lines in
    # one commit -- don't guess, surface it for a human to check.
    return {
        status => 'anomaly',
        detail => sprintf(
            'expected exactly one removed and one added %s line; got %d removed, %d added',
            $varname, scalar(@removed), scalar(@added)
        ),
        raw => $scoped,
    };
}

# Strip a trailing make comment ("# ...") and surrounding whitespace
# from a captured value, e.g. "3.12" from "3.12   # supported until 2028".
# Note: the capture regex above already stops at \s+ before a
# comment in the common case, but this guards against values that
# were captured with an attached comment due to no space, or tabs.
sub _strip_comment {
    my ($val) = @_;
    $val =~ s/\s*#.*$//;
    $val =~ s/\s+$//;
    return $val;
}

# Cut a multi-file diff down to just the hunks for $target_file.
# Handles standard git diff headers:
#   diff --git a/Mk/bsd.default-versions.mk b/Mk/bsd.default-versions.mk
#   ...
#   @@ -a,b +c,d @@
#   <hunk lines>
#   diff --git a/<next file> ...   <- stop here
sub _isolate_file_diff {
    my ($diff_text, $target_file) = @_;

    my $quoted = quotemeta($target_file);
    my @lines  = split /\n/, $diff_text;
    my @out;
    my $in_target = 0;

    for my $line (@lines) {
        if ($line =~ /^diff --git a\/$quoted b\/$quoted\b/) {
            $in_target = 1;
            next;
        }
        elsif ($line =~ /^diff --git /) {
            $in_target = 0;
            next;
        }

        push @out, $line if $in_target;
    }

    return join("\n", @out);
}

1;
