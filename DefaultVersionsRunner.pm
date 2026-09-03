package DefaultVersionsRunner;

#
# The daemon-side half: given a commit SHA that was flagged by
# DefaultVersionsFlag, launch the Python detection script as a
# subprocess, wait for it, and read back its JSON result.
#
# This does NOT re-implement any detection logic -- that all lives
# in Python now (detect_default_version_changes.py). This module's
# job is purely process orchestration: build the command line, run
# it, parse what it wrote, and hand the parsed result to a dispatch
# stub.
#

use strict;
use warnings;

use JSON::PP qw(decode_json);
use File::Temp qw(tempdir);

use Exporter 'import';
our @EXPORT_OK = qw(run_default_version_check);

# Path to the Python script and the ports tree checkout. Package
# variables (not constants) so callers/tests can override them, same
# reasoning as $DefaultVersionCheck::REPO_DIR earlier.
our $PYTHON_BIN    = 'python3';
our $SCRIPT_PATH   = '/usr/local/freshports/bin/detect_default_version_changes.py';
our $REPO_DIR      = '/usr/ports';
our $OUTPUT_DIR    = '/var/freshports/work';   # TODO: point at your real work dir

# run_default_version_check($commit_sha)
#
# Runs the Python script for the given commit, waits for it to
# finish, and returns the parsed JSON result as a hashref -- the same
# structure detect_default_version_changes.py writes, e.g.:
#
#   {
#     commit       => '135741412e9fe53270e9078d14f71066a2aecaa6',
#     target_file  => 'Mk/bsd.default-versions.mk',
#     checked_vars => [...],
#     results      => [ { var => 'PYTHON_DEFAULT', status => 'changed', old => '3.11', new => '3.12' }, ... ],
#     changed      => ['PYTHON_DEFAULT'],
#     anomalies    => [],
#   }
#
# On a script-level failure (non-zero exit), dies with a message
# built from the exit code and, if the output file was still written
# with an "error" field, that error text too. Callers should wrap
# this in eval/Try::Tiny as appropriate for your pipeline's error
# handling conventions.
sub run_default_version_check {
    my ($commit_sha) = @_;

    die "commit_sha required" unless defined $commit_sha && length $commit_sha;

    my $output_path = "$OUTPUT_DIR/${commit_sha}.json";

    # Args passed as a LIST to system(), never a shell string --
    # same reasoning as the git subprocess call earlier: commit_sha
    # can't be interpreted by a shell this way.
    my @cmd = (
        $PYTHON_BIN, $SCRIPT_PATH,
        '--commit', $commit_sha,
        '--repo',   $REPO_DIR,
        '--output', $output_path,
    );

    my $exit_status = system(@cmd);
    my $rc = $exit_status == -1 ? -1 : ($exit_status >> 8);

    my $payload = _read_result_file($output_path);

    if ($rc != 0) {
        my $detail = (ref $payload eq 'HASH' && $payload->{error})
            ? $payload->{error}
            : '(no error detail available -- output file missing or unreadable)';
        die "detect_default_version_changes.py failed for commit $commit_sha "
          . "(exit $rc): $detail";
    }

    return $payload;
}

# _read_result_file($path)
#
# Reads and JSON-decodes the result file. Returns undef if the file
# doesn't exist or can't be parsed -- callers check for that
# explicitly rather than getting a die from deep inside JSON::PP.
sub _read_result_file {
    my ($path) = @_;

    return undef unless -e $path;

    open(my $fh, '<', $path) or return undef;
    local $/;
    my $raw = <$fh>;
    close($fh);

    my $data = eval { decode_json($raw) };
    return $data;   # undef if decode failed
}

1;
