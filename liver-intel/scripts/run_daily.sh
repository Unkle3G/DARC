#!/usr/bin/env bash
# The daily run: Monday to Friday, minus Chinese public holidays, at 06:30
# Beijing time -- after the US close in both daylight-saving regimes, and 90
# minutes before the 08:00 deadline (scripts/crontab.example has the
# arithmetic).
#
# Two things this script does NOT do, and the operator must know why:
#
#   * It cannot translate. The Chinese renderings come either from an
#     ANTHROPIC_API_KEY or from a Claude Code session filling the worksheet
#     (`liver-intel render`). With neither, the article still gets written --
#     with the originals standing alone and MODEL STEP DID NOT RUN at the top
#     of 运行提示. Do not publish that as if it were the full method.
#   * It does not re-run a day that already has a report. A second `daily` on
#     the same date finds nothing new (everything is in seen_items) and the
#     engine refuses to overwrite; re-render with `liver-intel render --date`.
set -euo pipefail

cd "$(dirname "$0")/.."
export LIVER_INTEL_CONTACT="${LIVER_INTEL_CONTACT:?set a contact address; EDGAR requires one in the User-Agent}"

# Weekend or public holiday -> nothing to do. The reason goes to the log so a
# silent day is always explained.
if ! python3 -m liver_intel.cli workday; then
    exit 0
fi

python3 -m liver_intel.cli daily --wechat "$@"

# Friday also drains the weekly pool. A Friday that is a holiday never reaches
# this line, so the digest simply moves to the next working Friday.
if [ "$(TZ=Asia/Shanghai date +%u)" = "5" ]; then
    python3 -m liver_intel.cli weekly
fi
