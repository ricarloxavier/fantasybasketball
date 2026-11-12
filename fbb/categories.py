"""Category keys, Yahoo stat ids, NBA team codes and name normalisation.

Internal category keys are the ones the optimizer always used:
FG%, FT%, 3PTM, PTS, REB, AST, ST, BLK, TO. Everything user-facing goes
through `normalize_category` so "3PM", "STL" or "TOV" all map to them.
"""

from __future__ import annotations

import re
import unicodedata

ALL_CATEGORIES = ["FG%", "FT%", "3PTM", "PTS", "REB", "AST", "ST", "BLK", "TO"]
COUNTING_CATEGORIES = ["3PTM", "PTS", "REB", "AST", "ST", "BLK", "TO"]
PERCENT_CATEGORIES = ["FG%", "FT%"]
REVERSE_CATEGORIES = {"TO"}
VOLUME_KEYS = ["FGM", "FGA", "FTM", "FTA"]

# Yahoo stat ids for the 9 categories plus the made/attempted composites.
STAT_MAP = {
    "5": "FG%",
    "8": "FT%",
    "10": "3PTM",
    "12": "PTS",
    "15": "REB",
    "16": "AST",
    "17": "ST",
    "18": "BLK",
    "19": "TO",
}
# Composite "made/attempted" stats Yahoo returns as "5/10" strings.
COMPOSITE_STAT_MAP = {
    "9004003": ("FGM", "FGA"),
    "9007006": ("FTM", "FTA"),
}
# Individual volume stats, when a league exposes them.
VOLUME_STAT_MAP = {
    "3": "FGA",
    "4": "FGM",
    "6": "FTA",
    "7": "FTM",
}

_CATEGORY_ALIASES = {
    "3PM": "3PTM",
    "3PT": "3PTM",
    "3PTM": "3PTM",
    "3P": "3PTM",
    "STL": "ST",
    "ST": "ST",
    "FG": "FG%",
    "FG%": "FG%",
    "FGPCT": "FG%",
    "FT": "FT%",
    "FT%": "FT%",
    "FTPCT": "FT%",
    "PTS": "PTS",
    "REB": "REB",
    "TRB": "REB",
    "AST": "AST",
    "BLK": "BLK",
    "TO": "TO",
    "TOV": "TO",
}


def normalize_category(cat: str) -> str:
    cleaned = (cat or "").strip().upper().replace(" ", "")
    return _CATEGORY_ALIASES.get(cleaned, cleaned)


def parse_punt_categories(values) -> list[str]:
    """Normalise a list/comma string of category names, keeping only known ones."""
    if isinstance(values, str):
        values = values.split(",")
    out: list[str] = []
    for v in values or []:
        key = normalize_category(str(v))
        if key in ALL_CATEGORIES and key not in out:
            out.append(key)
    return out


NBA_TEAM_CODES = {
    "ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW",
    "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK",
    "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WSH",
}

# Maps every spelling seen on Yahoo, ESPN and Hashtag Basketball to one code.
TEAM_ABBR_ALIASES = {
    "GS": "GSW", "GSW": "GSW",
    "PHO": "PHX", "PHX": "PHX",
    "NO": "NOP", "NOP": "NOP", "NOR": "NOP",
    "NY": "NYK", "NYK": "NYK",
    "SA": "SAS", "SAS": "SAS",
    "BRK": "BKN", "BKN": "BKN", "BRO": "BKN",
    "CHO": "CHA", "CHA": "CHA",
    "WAS": "WSH", "WSH": "WSH",
    "UTA": "UTA", "UTAH": "UTA",
}


def normalize_team(team_abbr: str) -> str:
    team = (team_abbr or "").strip().upper()
    return TEAM_ABBR_ALIASES.get(team, team)


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name_token(name: str) -> str:
    """Lower-case alphanumerics only: "De'Andre Hunter" -> "deandrehunter"."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").strip().lower())


def name_key(name: str) -> str:
    """Stricter key for cross-site matching: strips accents and generational suffixes."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    parts = [p for p in re.split(r"[\s.,'’\-]+", text.lower()) if p]
    while len(parts) > 1 and parts[-1] in _SUFFIXES:
        parts.pop()
    return re.sub(r"[^a-z0-9]+", "", "".join(parts))
