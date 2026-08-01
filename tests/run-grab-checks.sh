#!/bin/bash
#
# Run both grab-project checks over a set of projects, sharing one
# environment setup. Used by .github/workflows/grab-pipeline-compat.yml.
#
#   tests/run-grab-checks.sh prepare projects.json
#   tests/run-grab-checks.sh check   projects.json
#
# where projects.json is [{"repo": "owner/name", "ref": ""}, ...].
#
# The two phases exist so that the checks can run with the network
# physically removed. 'prepare' needs the network -- it clones the projects
# and runs their warrior-install.sh, which apt/pip installs. 'check' needs
# none, and the workflow runs it in a container started with
# --network none, so "nothing external is contacted" is enforced by the
# kernel rather than by patching sockets in the harness. That matters:
# several pipelines shell out to wget, and no amount of in-process
# patching can stop a subprocess.
#
# Every project is attempted even if an earlier one fails; the exit status
# is non-zero if any project failed. A project whose pipeline needs input
# the harness does not have is reported SKIP and does not fail the run.

set -u

# No braces in these messages: a '}' would end the expansion early.
phase="${1:?usage: run-grab-checks.sh prepare|check PROJECTS_JSON}"
projects_json="${2:?usage: run-grab-checks.sh prepare|check PROJECTS_JSON}"
seesaw_dir="${SEESAW_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
workdir="${WORKDIR:-/var/tmp/grab-projects}"

tests_dir="$seesaw_dir/tests"
failed=()
skipped=()
passed=()

# Deliberately no wget-at is placed in the project directories. Pipelines
# search ./wget-at first, then /home/warrior/data/wget-at{,-nss,-gnutls,
# -openssl}, and the variant they name is not interchangeable: only the
# NSS build carries --impersonate (TLS impersonation), so youtube-grab
# asking for wget-at-nss means it needs that build specifically. The
# warrior clones projects without a binary in them, so leaving the project
# directory bare is what makes find_executable() pick what it would pick
# in production.
if [ ! -e /home/warrior/data/wget-at ]; then
    echo "No wget-at in /home/warrior/data -- is this the warrior image?" >&2
    exit 1
fi

project_dir_for() {
    echo "$workdir/$(echo "$1" | tr '/' '_')"
}

read_projects() {
    python3 -c '
import json, sys
with open(sys.argv[1]) as f:
    for entry in json.load(f):
        print(entry["repo"], entry.get("ref", ""))
' "$projects_json"
}

if [ "$phase" = prepare ]; then
    mkdir -p "$workdir"
    while read -r repo ref; do
        echo "::group::fetch $repo"
        project_dir="$(project_dir_for "$repo")"
        rm -rf "$project_dir"

        clone_args=(--quiet --depth 1 --recurse-submodules)
        if [ -n "$ref" ]; then
            clone_args+=(--branch "$ref")
        fi

        if ! git clone "${clone_args[@]}" "https://github.com/$repo" \
            "$project_dir"
        then
            echo "clone failed"
            echo "clone failed" > "$project_dir.error"
            echo "::endgroup::"
            continue
        fi

        # No grab repo ships a requirements.txt today, but honour one if it
        # appears. warrior-install.sh is what the warrior itself runs.
        if [ -f "$project_dir/requirements.txt" ]; then
            python3 -m pip install --no-cache-dir \
                -r "$project_dir/requirements.txt"
        fi

        if [ -f "$project_dir/warrior-install.sh" ]; then
            chmod +x "$project_dir/warrior-install.sh"
            if ! (cd "$project_dir" && ./warrior-install.sh); then
                echo "warrior-install.sh failed"
                echo "warrior-install.sh failed" > "$project_dir.error"
            fi
        fi
        echo "::endgroup::"
    done < <(read_projects)
    exit 0
fi

if [ "$phase" != check ]; then
    echo "unknown phase: $phase" >&2
    exit 2
fi

while read -r repo ref; do
    echo "::group::$repo"
    project_dir="$(project_dir_for "$repo")"

    if [ -f "$project_dir.error" ]; then
        echo "prepare phase failed: $(cat "$project_dir.error")"
        failed+=("$repo ($(cat "$project_dir.error"))")
        echo "::endgroup::"
        continue
    fi

    if [ ! -d "$project_dir" ]; then
        echo "not fetched during the prepare phase"
        failed+=("$repo (not fetched)")
        echo "::endgroup::"
        continue
    fi

    if ! python3 "$tests_dir/check_grab_pipeline.py" "$project_dir"; then
        failed+=("$repo (pipeline.py failed to load)")
        echo "::endgroup::"
        continue
    fi

    runtime_output="$(python3 "$tests_dir/check_grab_runtime.py" \
        "$project_dir" --repo "$repo" 2>&1)"
    runtime_status=$?
    echo "$runtime_output"

    if [ "$runtime_status" -ne 0 ]; then
        failed+=("$repo (pipeline failed before download)")
    elif echo "$runtime_output" | grep -q '^SKIP: '; then
        skipped+=("$repo")
    else
        passed+=("$repo")
    fi
    echo "::endgroup::"
done < <(read_projects)

echo
echo "================================================================"
for repo in "${passed[@]:-}";  do [ -n "$repo" ] && echo "PASS  $repo"; done
for repo in "${skipped[@]:-}"; do [ -n "$repo" ] && echo "SKIP  $repo"; done
for repo in "${failed[@]:-}";  do [ -n "$repo" ] && echo "FAIL  $repo"; done
echo "----------------------------------------------------------------"
echo "${#passed[@]} passed, ${#skipped[@]} skipped, ${#failed[@]} failed"
echo "================================================================"

[ "${#failed[@]}" -eq 0 ]
