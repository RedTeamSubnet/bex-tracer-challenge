#!/usr/bin/env bash
# Install the config templates into the local dev volume that compose.yml mounts
# at /etc/rest-exc-challenge. Run after changing anything in templates/configs/.
#
# The two directories are otherwise kept in sync by hand, which is how they drift.
set -euo pipefail

_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-"$0"}")" >/dev/null 2>&1 && pwd -P)"
_PROJECT_DIR="$(cd "${_SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"

_SRC="${_PROJECT_DIR}/templates/configs/challenge"
_DST="${_PROJECT_DIR}/volumes/configs/rest-exc-challenge"

mkdir -p "${_DST}"
cp -v "${_SRC}"/*.yml "${_DST}/"

echo
echo "Installed $(ls -1 "${_SRC}"/*.yml | wc -l | tr -d ' ') config file(s) into ${_DST}"
echo "compose.yml mounts this at /etc/rest-exc-challenge"
