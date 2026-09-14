"""Runtime configuration for the liver intelligence engine.

Everything network-facing is parameterised here.  No feed URL is ever
hard-coded in source: candidate endpoints live in ``data/feeds/registry.json``
and are only used once verification has marked them ``verified`` (see
``liver_intel.verify``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PKG_ROOT.parent
DATA_DIR = Path(os.environ.get("LIVER_INTEL_DATA", PROJECT_ROOT / "data"))
OUT_DIR = Path(os.environ.get("LIVER_INTEL_OUT", PROJECT_ROOT / "out"))
DB_PATH = Path(os.environ.get("LIVER_INTEL_DB", DATA_DIR / "state.sqlite3"))

FEED_REGISTRY = DATA_DIR / "feeds" / "registry.json"
DOMAIN_MAP = DATA_DIR / "domain_map_liver_v3.md"
CONFERENCES = DATA_DIR / "conferences.json"

# Contact address is mandated by the SEC EDGAR access policy (T2) and is good
# manners everywhere else.  Override with LIVER_INTEL_CONTACT.
CONTACT = os.environ.get("LIVER_INTEL_CONTACT", "unkle.hsu@gmail.com")
USER_AGENT = os.environ.get(
    "LIVER_INTEL_UA", f"liver-intel/1.0 ({CONTACT})"
)

# Report timezone: the daily run must finish before 08:00 Beijing time and
# covers the previous US trading day (task sheet section 4).
REPORT_TZ = os.environ.get("LIVER_INTEL_TZ", "Asia/Shanghai")

#: Per-host request budget, requests per second.  EDGAR publishes a hard 10/s
#: ceiling; everything else is throttled far below any plausible limit because
#: this engine only needs a few hundred requests a day.
RATE_LIMITS: dict[str, float] = {
    "www.sec.gov": 8.0,
    "data.sec.gov": 8.0,
    "clinicaltrials.gov": 3.0,
    "api.fda.gov": 2.0,
    "eutils.ncbi.nlm.nih.gov": 2.0,
    "__default__": 1.0,
}

#: Chinese regulator list pages are scraped at most once a day and are never
#: retried aggressively: on a block we downgrade to a manual prompt (section T4).
CN_LIST_MAX_ATTEMPTS = 2

# --- Branding and reader-facing labels ------------------------------------
#: Product name. Provisional per the operator's instruction.
BRAND = os.environ.get("LIVER_INTEL_BRAND", "HepaDaily")

#: P0/P1/P2 are internal triage grades and are never shown to readers. The
#: reader-facing article uses these section names instead.
SECTION_NAMES: dict[str, str] = {
    "P0": "今日头条",
    "P1": "前沿速览",
    "P2": "最新动态",
}

#: Hosts the engine talks to that are not themselves registry entries (they are
#: built from a documented API shape at request time). Used by `preflight`.
EXTRA_HOSTS: tuple[str, ...] = (
    "www.accessdata.fda.gov",     # Drugs@FDA application pages (T4)
    "pubmed.ncbi.nlm.nih.gov",    # article landing pages (T4)
)

# --- Selection rules (section 0 of the task sheet) -------------------------
#: P0 is uncapped.  P0+P1+P2 together are capped at this number, which in
#: practice means P1/P2 fill the slots P0 leaves behind.  When P0 alone exceeds
#: the cap every P0 still ships and P1/P2 get nothing.
DAILY_CAP = int(os.environ.get("LIVER_INTEL_DAILY_CAP", "10"))

#: Main lines L1-L8, auxiliary APASL lines L9-L16.
MAIN_LINES = tuple(f"L{i}" for i in range(1, 9))
AUX_LINES = tuple(f"L{i}" for i in range(9, 17))
AUX_WEIGHT = 0.5
#: L13 (AI) is the documented exception: auxiliary, but full weight.
AUX_WEIGHT_EXCEPTIONS = {"L13": 1.0}


@dataclass(frozen=True)
class Settings:
    data_dir: Path = DATA_DIR
    out_dir: Path = OUT_DIR
    db_path: Path = DB_PATH
    user_agent: str = USER_AGENT
    contact: str = CONTACT
    daily_cap: int = DAILY_CAP
    report_tz: str = REPORT_TZ
    rate_limits: dict[str, float] = field(default_factory=lambda: dict(RATE_LIMITS))
    #: When true the collector may use registry entries that have not passed
    #: verification.  Off by default -- section 3 of the task sheet exists
    #: because the previous version shipped unverified URLs.
    allow_unverified: bool = False

    def ensure_dirs(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


def line_weight(line: str) -> float:
    """Weight of a single line tag."""
    if line in AUX_WEIGHT_EXCEPTIONS:
        return AUX_WEIGHT_EXCEPTIONS[line]
    return AUX_WEIGHT if line in AUX_LINES else 1.0
