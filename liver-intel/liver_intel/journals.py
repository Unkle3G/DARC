"""Journal roster: which titles count, and how much.

PubMed indexes everything, so "a paper was published" says nothing on its own.
The roster is the operator's own policy about which journals a liver
intelligence brief should surface, kept in ``data/journals_liver.json`` beside
the disease lines rather than in code.

Matching is against PubMed's ``fulljournalname``. The catalogue appends
qualifiers -- "Hepatology (Baltimore, Md.)", "Clinical gastroenterology and
hepatology : the official clinical practice journal of ..." -- so the name is
cut at the first bracket or colon and then matched **exactly**.

Exactly, not as a prefix. A prefix rule looks right until it puts "Nature
structural & molecular biology" in the same tier as "Nature", which is how the
first version of this graded a cryo-EM paper as a clinical headline.

The roster decides **weight, not collection**: a journal that is not on it is
still collected and still reaches the weekly digest; it just does not compete
for a slot in the daily.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import DATA_DIR

JOURNALS = DATA_DIR / "journals_liver.json"


#: Where the catalogue stops naming the journal and starts qualifying it.
_QUALIFIER = re.compile(r"\s*[(:;\[]")


def _normalise(name: str) -> str:
    """The journal's own name, lower-cased, without the catalogue's qualifier.

    'Hepatology (Baltimore, Md.)' and 'Hepatology' land on the same key;
    'Hepatology communications' does not land on either.
    """
    name = _QUALIFIER.split(name or "", 1)[0]
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9&]+", " ", name.lower())).strip()


@dataclass
class Roster:
    #: normalised journal name -> tier letter
    tiers: dict[str, str] = field(default_factory=dict)
    confirmed: bool = False

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Roster":
        path = Path(path or JOURNALS)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        tiers: dict[str, str] = {}
        for letter, block in (raw.get("tiers") or {}).items():
            for entry in block.get("journals") or []:
                for key in (entry.get("name"), entry.get("abbrev")):
                    if key:
                        tiers[_normalise(key)] = letter
        return cls(tiers=tiers, confirmed=bool(raw.get("operator_confirmed")))

    def tier(self, journal: str) -> str:
        """Tier letter, or "" when the journal is not on the roster."""
        return self.tiers.get(_normalise(journal), "")


_CACHE: Roster | None = None


def load_cached() -> Roster:
    global _CACHE
    if _CACHE is None:
        _CACHE = Roster.load()
    return _CACHE


def tier_of(journal: str) -> str:
    return load_cached().tier(journal)
