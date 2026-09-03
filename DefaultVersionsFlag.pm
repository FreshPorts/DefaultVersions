package DefaultVersionsFlag;

#
# The "detection" half on the Perl side: given a commit your pipeline
# is already processing (with its changed-file list already known),
# decide whether it touched Mk/bsd.default-versions.mk and, if so,
# set a flag for the daemon to pick up.
#
# This deliberately does NOT fetch a diff or run any detection logic
# itself -- that all now lives in the Python side
# (detect_default_version_changes.py), launched later by the daemon.
# This module's only job is: "did this commit touch the file? if so,
# make sure the daemon knows to look at it."
#

use strict;
use warnings;

use Exporter 'import';
our @EXPORT_OK = qw(flag_if_default_versions_touched);

use constant TARGET_FILE => 'Mk/bsd.default-versions.mk';

# flag_if_default_versions_touched($commit)
#
# $commit - whatever object/struct/row your pipeline already uses to
#           represent a single commit, with its changed-file list
#           available.
#
# Returns 1 if the flag was set, 0 if the commit didn't touch the
# target file (nothing to do).
sub flag_if_default_versions_touched {
    my ($commit) = @_;

    # TODO: replace with your real accessor, same as before.
    my @changed_files = _changed_files_for($commit);

    return 0 unless grep { $_ eq TARGET_FILE } @changed_files;

    _set_flag($commit);
    return 1;
}

# ---------------------------------------------------------------
# Stubs -- replace with your real implementations.
# ---------------------------------------------------------------

sub _changed_files_for {
    my ($commit) = @_;
    die "TODO: wire up your changed-file-list accessor";
}

sub _set_flag {
    my ($commit) = @_;
    # TODO: however your pipeline actually flags a commit for the
    # daemon -- a DB column/row, a queue entry, a marker file, etc.
    # Whatever it is, it just needs to carry the commit's identifier
    # (SHA) through to the daemon side.
    die "TODO: wire up your flag-setting mechanism";
}

1;
