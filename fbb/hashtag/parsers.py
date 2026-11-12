"""Parsers for Hashtag Basketball premium pages (HTML -> plain dicts).

Every parser raises HashtagPageError naming the anchor it could not find, so a
markup change on the site fails loudly instead of returning empty data.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag

from ..categories import ALL_CATEGORIES, name_key, normalize_category, normalize_team
from ..util import safe_float, safe_int
from .session import HashtagPageError

DAY_LABELS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
_PCT_RE = re.compile(r"([\d.]+)\s*\(([\d.]+)\s*/\s*([\d.]+)\)")
_POS_TEAM_RE = re.compile(r"\(([A-Z,]+)-([A-Z]+)\)")
_CAP_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_RANGE_RE = re.compile(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(\d{1,2}\s+\w+\s+\d{4})\s*-\s*\w+\s+(\d{1,2}\s+\w+\s+\d{4})")
_RECORD_RE = re.compile(r"\((\d+)-(\d+)\)")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _text(el: Optional[Tag]) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _header_map(row: Tag) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, cell in enumerate(row.find_all(["th", "td"])):
        label = _text(cell).upper()
        if not label:
            continue
        cat = normalize_category(label)
        out[cat if cat in ALL_CATEGORIES else label] = i
    return out


def _stat_cells(cells: list[Tag], headers: dict[str, int]) -> tuple[dict[str, float], dict[str, str]]:
    """The 9 category values (+ FGM/FGA/FTM/FTA when the cell shows made/att) and the tier class per cell."""
    stats: dict[str, float] = {}
    tiers: dict[str, str] = {}
    for cat in ALL_CATEGORIES:
        idx = headers.get(cat)
        if idx is None or idx >= len(cells):
            continue
        cell = cells[idx]
        text = _text(cell)
        m = _PCT_RE.search(text)
        if m:
            stats[cat] = safe_float(m.group(1))
            made, att = ("FGM", "FGA") if cat == "FG%" else ("FTM", "FTA")
            stats[made], stats[att] = safe_float(m.group(2)), safe_float(m.group(3))
        else:
            stats[cat] = safe_float(text.split()[0]) if text else 0.0
        cls = " ".join(cell.get("class", []))
        tiers[cat] = cls.strip()
    return stats, tiers


def _player_cell(cell: Tag) -> dict[str, Any]:
    """Name, positions, NBA team, injury flag, rookie flag, note and Hashtag id from a player cell."""
    full = cell.find(lambda t: t.name in ("span", "a") and "d-sm-inline" in (t.get("class") or []))
    name_node = full if full is not None else cell
    small = name_node.find("span", class_="small") if full is not None else None
    name = _text(name_node)
    if small is not None:
        name = name.replace(_text(small), "")
    name = re.sub(r"\s+", " ", name).strip()
    pos_team = cell.find("span", class_="small")
    positions, team = [], ""
    m = _POS_TEAM_RE.search(_text(pos_team) if pos_team else _text(cell))
    if m:
        positions = m.group(1).split(",")
        team = normalize_team(m.group(2))
    injury = None
    for span in cell.find_all("span", class_=re.compile(r"icon-(injured|in-doubt)")):
        title = (span.get("data-bs-original-title") or span.get("aria-label") or span.get("title") or "").lower()
        if "doubt" in title or "in-doubt" in " ".join(span.get("class", [])):
            injury = injury or "doubt"
        elif "out" in title:
            injury = "out"
        elif "injur" in title:
            injury = injury or "injured"
    note_span = cell.find("span", attrs={"aria-label": True}, class_="text-dark")
    note = note_span["aria-label"].strip() if note_span else ""
    rookie = any("fa-registered" in (i.get("class") or []) for i in cell.find_all("i"))
    hid = cell.find("input", attrs={"name": re.compile(r"HFpid$")})
    gp_m = re.search(r"GP:\s*(\d+)", _text(cell))
    return {
        "name": name, "name_key": name_key(name), "positions": positions, "nba_team": team, "injury": injury,
        "note": note, "rookie": rookie, "hashtag_id": hid["value"] if hid else "", "gp": safe_int(gp_m.group(1)) if gp_m else None,
        "rostered": any("fa-user" in (i.get("class") or []) for i in cell.find_all("i")),
    }


def _pct_from_text(text: str) -> float:
    text = text.strip().rstrip("%")
    return safe_float(text) / (100.0 if safe_float(text) > 1.0 else 1.0)


# ---------------------------------------------------------------------------
def parse_planner(html: str) -> dict[str, Any]:
    soup = _soup(html)
    stats_table = soup.find("table", id="statsTable")
    if stats_table is None:
        raise HashtagPageError("planner: table#statsTable not found")
    before: dict[str, dict[str, float]] = {}
    after: dict[str, dict[str, float]] = {}
    prob: dict[str, float] = {}
    record = ""
    section = ""
    headers: dict[str, int] = {}
    team_order: list[str] = []
    for row in stats_table.find_all("tr"):
        cells = row.find_all(["th", "td"])
        first = _text(cells[0]).upper()
        if first in ("BEFORE", "AFTER"):
            section = first
            headers = _header_map(row)
            continue
        if first.startswith("PROBABILITY"):
            m = _RECORD_RE.search(first)
            record = f"{m.group(1)}-{m.group(2)}" if m else ""
            for cat in ALL_CATEGORIES:
                idx = headers.get(cat)
                if idx is not None and idx < len(cells):
                    prob[cat] = _pct_from_text(_text(cells[idx]))
            continue
        team = _text(cells[0])
        stats, _ = _stat_cells(cells, headers)
        (before if section == "BEFORE" else after)[team] = stats
        if section == "BEFORE" and team not in team_order:
            team_order.append(team)
    if len(team_order) < 2:
        raise HashtagPageError("planner: expected two teams in the BEFORE block")

    selects = {s.get("name", "").split("$")[-1]: s for s in soup.find_all("select")}

    def selected(suffix: str) -> tuple[str, str]:
        sel = selects.get(suffix)
        if sel is None:
            return "", ""
        opt = next((o for o in sel.find_all("option") if o.has_attr("selected")), None) or sel.find("option")
        return (opt.get("value", ""), _text(opt)) if opt else ("", "")

    my_team_value, my_team = selected("DDTEAM1")
    opp_value, opp_team = selected("DDTEAM2")
    week = safe_int(selected("DDWEEK")[0])
    duration = selected("DDDURATION")[0]
    spots = safe_int(selected("DDSPOTS")[0], 10)
    teams = [(o.get("value", ""), _text(o)) for o in selects["DDTEAM2"].find_all("option")] if "DDTEAM2" in selects else []
    days_played = [i for i in range(1, 8) if (soup.find("input", attrs={"name": re.compile(rf"\$CBM{i}$")}) or {}).has_attr("checked")]

    roster_table = None
    for t in soup.find_all("table"):
        if t.find("input", attrs={"name": re.compile(r"HFpid$")}):
            roster_table = t
    if roster_table is None:
        raise HashtagPageError("planner: roster table (with HFpid inputs) not found")
    rows = roster_table.find_all("tr")
    headers = _header_map(rows[0])
    day_cols = [(label, idx) for label, idx in headers.items() if label in DAY_LABELS]
    day_cols.sort(key=lambda li: li[1])
    caps: dict[str, dict[str, int]] = {}
    if len(rows) > 1 and "planner-cap-row" in (rows[1].get("class") or []):
        cap_cells = rows[1].find_all(["th", "td"])
        for label, idx in day_cols:
            m = _CAP_RE.search(_text(cap_cells[idx])) if idx < len(cap_cells) else None
            if m:
                caps[label] = {"used": int(m.group(1)), "spots": int(m.group(2))}
    roster: list[dict[str, Any]] = []
    for row in rows:
        cells = row.find_all("td")
        if not cells or not row.find("input", attrs={"name": re.compile(r"HFpid$")}):
            continue
        player = _player_cell(cells[0])
        player["mpg"] = safe_float(_text(cells[headers["MPG"]])) if "MPG" in headers else 0.0
        player["stats"], player["tiers"] = _stat_cells(cells, headers)
        games = []
        for label, idx in day_cols:
            cell = cells[idx] if idx < len(cells) else None
            opp = _text(cell.find("span")) if cell else ""
            cb = cell.find("input", type="checkbox") if cell else None
            if opp:
                games.append({"day": label, "opponent": opp, "counted": bool(cb is not None and cb.has_attr("checked")),
                              "checkbox": cb.get("name") if cb else ""})
        player["games"] = games
        player["games_left"] = sum(1 for g in games if g["counted"])
        player["games_scheduled"] = len(games)
        roster.append(player)

    return {
        "week": week, "duration": duration, "spots": spots, "days_played": days_played,
        "my_team": my_team, "my_team_value": my_team_value, "opp_team": opp_team, "opp_team_value": opp_value, "teams": teams,
        "team_order": team_order, "before": before, "after": after, "prob": prob, "record": record,
        "days": [{"label": label, **caps.get(label, {})} for label, _ in day_cols],
        "roster": roster,
        "recommendations": parse_recommendations(html, soup),
    }


def parse_recommendations(html: str, soup: Optional[BeautifulSoup] = None) -> list[dict[str, Any]]:
    """Rows of the planner's 'Add to my team' panel (empty list when the panel is closed)."""
    soup = soup or _soup(html)
    table = soup.find("table", id="recTable")
    if table is None:
        return []
    rows = table.find_all("tr")
    headers = _header_map(rows[0])
    out: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        player = _player_cell(cells[0])
        hfa = cells[0].find("input", attrs={"name": re.compile(r"HFA$")})
        flags = [c.strip() == "1" for c in (hfa["value"].split(",") if hfa else [])]
        this_week = [DAY_LABELS[i] for i, f in enumerate(flags[:7]) if f]
        next_week = [DAY_LABELS[i] for i, f in enumerate(flags[7:14]) if f]
        games_cell = cells[headers["GAMES"]] if "GAMES" in headers else None
        fit_days = [s.get_text(strip=True).upper() for s in games_cell.find_all("span", class_="rec-day--fit")] if games_cell else []
        shown_days = [s.get_text(strip=True).upper() for s in games_cell.find_all("span", class_="rec-day")] if games_cell else []
        add_link = row.find("a", href=re.compile(r"__doPostBack"))
        target = ""
        if add_link:
            m = re.search(r"__doPostBack\('([^']+)'", add_link["href"])
            target = m.group(1) if m else ""
        player["mpg"] = safe_float(_text(cells[headers["MPG"]])) if "MPG" in headers else 0.0
        player["stats"], player["tiers"] = _stat_cells(cells, headers)
        player.update({"games_this_week": this_week or shown_days, "fit_days": fit_days, "games_next_week": next_week,
                       "hfa": flags, "add_target": target})
        out.append(player)
    return out


def parse_waiver_wire(html: str) -> list[dict[str, Any]]:
    soup = _soup(html)
    table = next((t for t in soup.find_all("table") if "TOTAL" in _text(t.find("tr")).upper()), None)
    if table is None:
        raise HashtagPageError("waiver wire: ranking table (header with TOTAL) not found")
    rows = table.find_all("tr")
    headers = _header_map(rows[0])
    out: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < len(headers) - 1:
            continue
        player = _player_cell(cells[headers["PLAYER"]])
        if "POS" in headers:
            player["positions"] = _text(cells[headers["POS"]]).split(",")
        if "TEAM" in headers:
            player["nba_team"] = normalize_team(_text(cells[headers["TEAM"]]))
        player["rank"] = safe_int(_text(cells[headers["R#"]])) if "R#" in headers else 0
        player["gp"] = safe_int(_text(cells[headers["GP"]])) if "GP" in headers else player.get("gp")
        player["mpg"] = safe_float(_text(cells[headers["MPG"]])) if "MPG" in headers else 0.0
        player["stats"], player["tiers"] = _stat_cells(cells, headers)
        player["total_z"] = safe_float(_text(cells[headers["TOTAL"]])) if "TOTAL" in headers else 0.0
        out.append(player)
    return out


def _parse_day_header(label: str, start: date) -> Optional[date]:
    """Grid headers are Mon..Sun for the week, then '23rd', '24th' for the days after."""
    up = label.upper()[:3]
    if up in DAY_LABELS:
        return start.fromordinal(start.toordinal() + DAY_LABELS.index(up))
    m = re.match(r"(\d{1,2})(st|nd|rd|th)", label.strip().lower())
    if m:
        day = int(m.group(1))
        probe = start.fromordinal(start.toordinal() + 7)
        for _ in range(14):
            if probe.day == day:
                return probe
            probe = probe.fromordinal(probe.toordinal() + 1)
    return None


def parse_schedule_grid(html: str) -> dict[str, Any]:
    soup = _soup(html)
    m = _RANGE_RE.search(soup.get_text(" ", strip=True))
    if not m:
        raise HashtagPageError("schedule grid: date range text not found")
    start = datetime.strptime(m.group(2), "%d %B %Y").date()
    end = datetime.strptime(m.group(3), "%d %B %Y").date()
    table = next((t for t in soup.find_all("table") if "MPG" in _text(t.find("tr")).upper() and "MON" in _text(t.find("tr")).upper()), None)
    if table is None:
        raise HashtagPageError("schedule grid: player table not found")
    rows = table.find_all("tr")
    header_cells = rows[0].find_all(["th", "td"])
    headers = _header_map(rows[0])
    day_cols: list[tuple[date, int]] = []
    for i, cell in enumerate(header_cells):
        d = _parse_day_header(_text(cell), start)
        if d is not None:
            day_cols.append((d, i))
    players: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < len(header_cells) - 2:
            continue
        player = _player_cell(cells[headers["PLAYER"]])
        player["rank"] = safe_int(_text(cells[headers["R#"]])) if "R#" in headers else 0
        player["mpg"] = safe_float(_text(cells[headers["MPG"]])) if "MPG" in headers else 0.0
        player["stats"], player["tiers"] = _stat_cells(cells, headers)
        player["total_z"] = safe_float(_text(cells[headers["TOTAL"]])) if "TOTAL" in headers else 0.0
        games: dict[str, dict[str, Any]] = {}
        for d, idx in day_cols:
            cell = cells[idx] if idx < len(cells) else None
            if cell is None:
                continue
            spans = cell.find_all("span", recursive=False)
            opp = _text(spans[0]) if spans else ""
            if not opp:
                continue
            badge = cell.find("span", class_="badge")
            games[d.isoformat()] = {"opponent": opp, "def_rank": safe_int(_text(badge)) if badge else None}
        player["games"] = games
        player["games_in_week"] = sum(1 for k in games if start.isoformat() <= k <= end.isoformat())
        players.append(player)
    return {"start": start, "end": end, "players": players}
