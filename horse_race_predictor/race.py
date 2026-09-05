"""
Race identification and entry model for Horse Race Predictor.

A race is uniquely identified by (track_code, race_number, race_date) using
Equibase track codes. A small alias map accepts common full track names so the
CLI can be invoked as "--track Saratoga" as well as "--track SAR".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import date, datetime


# Equibase track codes for major US thoroughbred tracks, plus common aliases
# mapping full/casual names -> code. Extend freely; lookup is case-insensitive.
TRACK_ALIASES = {
    # New York
    "aqueduct": "AQU", "belmont": "BEL", "belmont park": "BEL",
    "saratoga": "SAR", "saratoga springs": "SAR",
    # California
    "santa anita": "SA", "santa anita park": "SA",
    "del mar": "DMR", "golden gate": "GG", "golden gate fields": "GG",
    "los alamitos": "LRL",
    # Kentucky
    "churchill": "CD", "churchill downs": "CD",
    "keeneland": "KEE", "ellis park": "ELP", "turfway": "TP", "turfway park": "TP",
    # Florida
    "gulfstream": "GP", "gulfstream park": "GP",
    "tampa bay": "TAM", "tampa bay downs": "TAM", "tampa": "TAM",
    "calder": "CDM", "hialeah": "HIL",
    # Other major
    "oaklawn": "OP", "oaklawn park": "OP",
    "pimlico": "PIM", "laurel": "LRL", "laurel park": "LRL",
    "monmouth": "MTH", "monmouth park": "MTH",
    "parx": "PRX", "parx racing": "PRX",
    "colonial": "COL", "colonial downs": "COL",
    "fair grounds": "FG", "fairgrounds": "FG",
    "arlington": "AP", "arlington park": "AP",
    "woodbine": "WO",  # Canadian but commonly grouped with US cards
    "remington": "RP", "remington park": "RP",
    "horseshoe indianapolis": "IND", "indiana grand": "IND",
    "mahoning valley": "MVG", "finger lakes": "FL", "finger lakes gaming": "FL",
    " mountaineer": "MNR", "mountaineer": "MNR",
    "presque isle": "PID", "thistledown": "TDP", "belterra": "BELP",
    "delta downs": "DTA", "evangeline": "EVD", "lone star": "LSA",
    "sam houston": "HOU", "sam houston race park": "HOU",
    "william hill": "TAM",
    "santa rosa": "SAR",  # careful: SAR is also Saratoga - alias kept but code wins
}


def canonicalize_track(track):
    """Normalize a track code or name to an uppercase Equibase track code.

    Accepts either a 1-3 letter code ("SAR", "sa") or a full name ("Saratoga",
    "saratoga park"). Returns the uppercase code, or the uppercased input if no
    alias matches (so unknown codes pass through for the entries source to reject).
    """
    if not track:
        return track
    key = track.strip().lower()
    if len(key) <= 3 and key.isalpha():
        return key.upper()
    return TRACK_ALIASES.get(key, track.strip().upper())


def canonicalize_date(d):
    """Accept a date string 'YYYY-MM-DD' or a datetime/date; default to today.

    Returns a 'YYYY-MM-DD' string. Raises ValueError on unparseable input.
    """
    if d is None:
        return date.today().isoformat()
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    s = str(d).strip()
    # Accept a few common formats
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unparseable date: {d!r} (expected YYYY-MM-DD)")


def normalize_horse_name(name):
    """Fuzzy-normalize a horse name for matching picks to entries.

    Lowercases, strips diacritics, collapses whitespace, and removes common
    country/breeding suffixes (e.g. "(IRE)", "(GB)") and trailing punctuation.
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().strip()
    # Drop parenthetical country/breeding suffixes
    s = re.sub(r"\([^)]*\)", "", s)
    # Drop trailing jr/sr/ii/iii and stray punctuation
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+(jr|sr|ii|iii|iv)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


@dataclass
class Entry:
    """One horse in a race."""
    program_number: str
    horse_name: str
    jockey: str = ""
    trainer: str = ""
    morning_line_odds: float | None = None
    post_position: int | None = None
    scratched: bool = False
    status: str = "in"  # "in" | "mto" | "ae" | "scratched"

    def to_dict(self):
        return asdict(self)


def filter_active(entries, include_mto=False, include_ae=False, extra_scratched=None):
    """Split entries into (active, excluded) based on scratch/MTO/AE status.

    - Scratched horses are always excluded (plus any program numbers in
      `extra_scratched`, for manual late-scratch overrides).
    - MTO and AE are excluded unless the corresponding include flag is True
      (MTO only run if the race moves to dirt; AE only draw in on a scratch).

    Returns:
        (active_list, excluded_list) where excluded_list items are
        (entry, reason) tuples with reason in {"scratched", "mto", "ae"}.
    """
    extra = set(extra_scratched or [])
    active = []
    excluded = []
    for e in entries:
        st = e.get("status", "in") if isinstance(e, dict) else e.status
        prog = (e.get("program_number") if isinstance(e, dict) else e.program_number)
        if st == "scratched" or prog in extra:
            excluded.append((e, "scratched"))
            continue
        if st == "mto" and not include_mto:
            excluded.append((e, "mto"))
            continue
        if st == "ae" and not include_ae:
            excluded.append((e, "ae"))
            continue
        active.append(e)
    return active, excluded


@dataclass
class Race:
    """A race identity + (optionally) its entries and race-card metadata."""
    track_code: str
    race_number: int
    race_date: str  # YYYY-MM-DD
    post_time: str = ""
    distance: str = ""
    surface: str = ""
    race_type: str = ""
    entries: list = field(default_factory=list)

    @classmethod
    def from_inputs(cls, track, race_number, race_date=None):
        return cls(
            track_code=canonicalize_track(track),
            race_number=int(race_number),
            race_date=canonicalize_date(race_date),
        )

    @property
    def key(self):
        return f"{self.track_code}#{self.race_number}@{self.race_date}"

    def __str__(self):
        return f"Race {self.race_number} at {self.track_code} on {self.race_date}"