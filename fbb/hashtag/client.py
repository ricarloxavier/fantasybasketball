"""Typed access to Hashtag Basketball league tools built on HashtagSession."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from ..cache import TTLCache
from ..config import Settings
from . import parsers
from .session import BASE_URL, FormState, HashtagPageError, HashtagSession, extract_form_state

log = logging.getLogger(__name__)

PLANNER = "/import-v4/plan-cat-yahoo.aspx"
WAIVER = "/import-v4/waiver-cat-yahoo.aspx"
GRID = "/import-v4/grid-cat-yahoo.aspx"

# DDDURATION codes on every premium page.
PROJECTIONS = {"ROS": "0", "BLEND7": "77", "BLEND14": "1414", "BLEND30": "3030",
               "STREAM7": "777", "STREAM14": "141414", "STREAM30": "303030", "SEASON": "1"}


class HashtagClient:
    def __init__(self, session: HashtagSession, league_id: str):
        self.session = session
        self.league_id = league_id

    @classmethod
    def from_settings(cls, settings: Settings, cache: Optional[TTLCache] = None) -> "HashtagClient":
        return cls(HashtagSession.from_settings(settings, cache), settings.hashtag_league_id)

    # -- helpers -----------------------------------------------------------------
    def _url(self, path: str, **params: str) -> tuple[str, dict[str, str]]:
        q = {"lid": self.league_id, **params}
        return path, q

    def _page(self, path: str, params: dict[str, str], use_cache: bool = True) -> tuple[str, FormState, str]:
        html = self.session.get(path, params, use_cache=use_cache)
        url = BASE_URL + path + "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return html, extract_form_state(html, url), url

    def _apply_selects(self, html: str, state: FormState, url: str, wanted: dict[str, str], use_cache: bool) -> tuple[str, FormState]:
        """Change selects one at a time (each is an AutoPostBack), re-reading the form after each."""
        for suffix, value in wanted.items():
            if value is None or value == "":
                continue
            name = state.name_for(suffix)
            if state.fields.get(name) == str(value):
                continue
            if str(value) not in {v for v, _ in state.selects.get(name, [])}:
                raise HashtagPageError(f"{suffix}: value {value!r} is not an option ({[v for v, _ in state.selects.get(name, [])][:12]}...)")
            target = state.postback_targets.get(name, name)
            html = self.session.postback(url, state, {name: str(value)}, event_target=target, use_cache=use_cache)
            state = extract_form_state(html, url)
        return html, state

    # -- planner -----------------------------------------------------------------
    def planner_raw(self, week: Optional[int] = None, opponent: Optional[str] = None, projection: str = "ROS",
                    show: str = "my", use_cache: bool = True) -> tuple[str, FormState, str]:
        path, params = self._url(PLANNER)
        html, state, url = self._page(path, params, use_cache)
        wanted = {"DDDURATION": PROJECTIONS.get(projection.upper(), projection)}
        if week is not None:
            wanted["DDWEEK"] = str(week)
        if opponent:
            wanted["DDTEAM2"] = str(opponent)
        html, state = self._apply_selects(html, state, url, wanted, use_cache)
        if show == "opp":
            name = state.name_for("RadioButtonList1")
            if state.fields.get(name) != "2":
                html = self.session.postback(url, state, {name: "2"}, event_target=state.postback_targets.get(name, name), use_cache=use_cache)
                state = extract_form_state(html, url)
        return html, state, url

    def planner(self, **kw) -> dict[str, Any]:
        html, state, _ = self.planner_raw(**kw)
        data = parsers.parse_planner(html)
        data["_state"] = state
        return data

    def open_recommendations(self, html: str, state: FormState, url: str, use_cache: bool = True) -> tuple[str, FormState]:
        """Expand the 'Add to my team' panel if it is collapsed."""
        if 'id="recTable"' in html:
            return html, state
        target = state.name_for("BTNADD")
        html = self.session.postback(url, state, {}, event_target=target, use_cache=use_cache)
        return html, extract_form_state(html, url)

    def planner_with_recommendations(self, **kw) -> dict[str, Any]:
        html, state, url = self.planner_raw(**kw)
        html, state = self.open_recommendations(html, state, url, kw.get("use_cache", True))
        data = parsers.parse_planner(html)
        data["_state"] = state
        data["_url"] = url
        data["_html"] = html
        return data

    def planner_scenario(self, drop_hashtag_ids: list[str] = (), add_target: str = "", use_cache: bool = True, **kw) -> dict[str, Any]:
        """Planner AFTER/PROBABILITY with `drop` players' remaining games unchecked and one recommended player added."""
        data = self.planner_with_recommendations(use_cache=use_cache, **kw)
        html, state, url = data["_html"], data["_state"], data["_url"]
        if drop_hashtag_ids:
            overrides: dict[str, Optional[str]] = {}
            for player in data["roster"]:
                if player["hashtag_id"] in set(drop_hashtag_ids):
                    for g in player["games"]:
                        if g["checkbox"]:
                            overrides[g["checkbox"]] = None
            if not overrides:
                raise HashtagPageError(f"No remaining-game checkboxes found for ids {list(drop_hashtag_ids)}")
            html = self.session.postback(url, state, overrides, submit=state.name_for("Button5"), use_cache=use_cache)
            state = extract_form_state(html, url)
        if add_target:
            if 'id="recTable"' not in html:
                html, state = self.open_recommendations(html, state, url, use_cache)
            html = self.session.postback(url, state, {}, event_target=add_target, use_cache=use_cache)
            state = extract_form_state(html, url)
        out = parsers.parse_planner(html)
        out["_state"] = state
        return out

    # -- waiver wire & grid --------------------------------------------------------
    def waiver_wire(self, projection: str = "ROS", show: int = 200, rank: str = "AVG", type_: str = "H2H",
                    team: str = "", pos: str = "All", use_cache: bool = True) -> list[dict[str, Any]]:
        path, params = self._url(WAIVER)
        html, state, url = self._page(path, params, use_cache)
        wanted = {"DDDURATION": PROJECTIONS.get(projection.upper(), projection), "DDSHOW": str(show), "DDRANK": rank, "DDTYPE": type_, "DDPOS": pos}
        if team:
            wanted["DDTEAM"] = str(team)
        html, _ = self._apply_selects(html, state, url, wanted, use_cache)
        return parsers.parse_waiver_wire(html)

    def schedule_grid(self, week: int, projection: str = "ROS", show: int = 200, team: str = "", use_cache: bool = True) -> dict[str, Any]:
        path, params = self._url(GRID, w=str(week))
        html, state, url = self._page(path, params, use_cache)
        wanted = {"DDDURATION": PROJECTIONS.get(projection.upper(), projection), "DDSHOW": str(show)}
        if team:
            wanted["DDTEAM"] = str(team)
        html, _ = self._apply_selects(html, state, url, wanted, use_cache)
        out = parsers.parse_schedule_grid(html)
        out["week"] = week
        return out
