package DefaultVersionCheck;

#
# Integration point for DefaultVersionChange.pm into the commit
# processing pipeline.
#
# Design:
#   1. Cheap gate: only look further if this commit's already-known
#      changed-file list includes Mk/bsd.default-versions.mk. This
#      runs on every commit, so it needs to be free -- no diff fetch,
#      no subprocess, just a list membership check.
#   2. Only for commits that pass the gate: fetch the diff for that
#      one file on demand (not the whole commit's diff -- just this
#      file, to keep the fetch small).
#   3. Hand the diff to DefaultVersionChange::detect_default_version_change
#      for PYTHON_DEFAULT specifically.
#   4. Dispatch on the result. The 'changed' and 'anomaly' branches
#      are stubs -- deliberately left for you to wire up, since what
#      happens next (queue a reprocess, log, notify, etc.) isn't
#      part of this feature's scope.
#
# You'll need to replace the two TODOs below with your actual
# per-commit APIs:
#   - the changed-file-list accessor
#   - the diff-fetch-for-one-file call
#

use strict;
use warnings;

use DefaultVersionChange qw(detect_default_version_change);

use Exporter 'import';
our @EXPORT_OK = qw(check_python_default_change);

use constant TARGET_FILE => 'Mk/bsd.default-versions.mk';
use constant TARGET_VAR  => 'PYTHON_DEFAULT';

# check_python_default_change($commit)
#
# $commit - whatever object/struct/row your pipeline already uses
#           to represent a single commit being processed. Only
#           requirement: it can tell you (a) the changed-file list,
#           and (b) fetch the diff for one path, on demand.
#
# Returns nothing meaningful yet -- it dispatches to the stub
# handlers below. Call this once per commit, after you have the
# changed-file list but before (or instead of) any full-diff fetch,
# so the common case (commit doesn't touch this file) costs nothing
# extra.
sub check_python_default_change {
    my ($commit) = @_;

    # --- Step 1: cheap gate -------------------------------------
    # TODO: replace with your real accessor, e.g.:
    #   my @changed = $commit->changed_files;
    # or
    #   my @changed = @{ $commit->{changed_files} };
    my @changed_files = _changed_files_for($commit);

    return unless grep { $_ eq TARGET_FILE } @changed_files;

    # --- Step 2: on-demand diff fetch, only for the rare commit --
    # that actually touched the file. Fetch just this file's diff,
    # not the whole commit -- smaller payload, and
    # detect_default_version_change doesn't need the rest anyway.
    # TODO: replace with your real diff-fetch call, e.g.:
    #   my $diff = $commit->diff_for_path(TARGET_FILE);
    # or a git subprocess call equivalent to what you've been
    # running by hand:
    #   git show <sha> -- Mk/bsd.default-versions.mk
    my $diff_text = _diff_for_path($commit, TARGET_FILE);

    unless (defined $diff_text && length $diff_text) {
        # Changed-file list said it was touched but we couldn't get
        # a diff for it -- that's worth knowing about, not silently
        # skipping.
        _log_anomaly($commit, "expected a diff for " . TARGET_FILE . " but got none");
        return;
    }

    # --- Step 3: detect -------------------------------------------
    my $result = detect_default_version_change($diff_text, TARGET_VAR, TARGET_FILE);

    # --- Step 4: dispatch -------------------------------------------
    if ($result->{status} eq 'changed') {
        _on_python_default_changed($commit, $result->{old}, $result->{new});
    }
    elsif ($result->{status} eq 'anomaly') {
        _log_anomaly($commit, $result->{detail}, $result->{raw});
    }
    # 'unchanged' -> nothing to do.

    return $result;
}

# ---------------------------------------------------------------
# Stubs -- replace with your real implementations.
# ---------------------------------------------------------------

sub _changed_files_for {
    my ($commit) = @_;
    die "TODO: wire up your changed-file-list accessor";
}

sub _diff_for_path {
    my ($commit, $path) = @_;
    die "TODO: wire up your per-file diff fetch";
}

sub _on_python_default_changed {
    my ($commit, $old, $new) = @_;
    # TODO: this is the "take action" step that's out of scope for
    # now -- queue a reprocess of lang/python (and any other ports
    # you've identified as depending on PYTHON_DEFAULT), log it,
    # notify, whatever fits your workflow.
    warn sprintf(
        "PYTHON_DEFAULT changed %s -> %s in commit %s\n",
        $old, $new, _commit_id($commit),
    );
}

sub _log_anomaly {
    my ($commit, $detail, $raw) = @_;
    # TODO: route this to wherever you want anomalies surfaced for
    # manual review (log file, ticket, admin email, etc.)
    warn sprintf(
        "DefaultVersionChange anomaly on commit %s: %s\n",
        _commit_id($commit), $detail,
    );
}

sub _commit_id {
    my ($commit) = @_;
    # TODO: however you get a displayable identifier (sha, etc.)
    return ref($commit) ? ($commit->{id} // $commit->{sha} // 'unknown') : $commit;
}

1;
