#!/usr/bin/env bash
# Daily run (task sheet section 4): Monday-Friday, finishing before 08:00 Beijing
# time so the report covers the previous US trading day. Friday also runs the
# weekly digest.
set -euo pipefail

cd "$(dirname "$0")/.."
export LIVER_INTEL_CONTACT="${LIVER_INTEL_CONTACT:?set a contact address; EDGAR requires one in the User-Agent}"

python3 -m liver_intel.cli daily "$@"

if [ "$(date +%u)" = "5" ]; then
    python3 -m liver_intel.cli weekly
fi
