"""Yahoo Fantasy Sports API client.

One place for OAuth handling, HTTP with timeouts, pagination (Yahoo returns at
most 25 players per request), league/team resolution and response parsing.
Raises exceptions instead of exiting so the web app and CLI decide what to do.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional

from yahoo_oauth import OAuth2

from ..config import DEFAULT_PARAMS, ModelParams, Settings
from ..util import safe_int, walk_values
from . import parse

log = logging.getLogger(__name__)

BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"
PAGE_SIZE = 25
ROSTER_INACTIVE_SLOTS = {"IL", "IL+", "IR", "NA"}


class YahooAuthError(RuntimeError):
    """OAuth file missing or the app is not authorised for Fantasy Sports."""


class YahooApiError(RuntimeError):
    def __init__(self, status: int, message: str, url: str = ""):
        super().__init__(f"Yahoo API {status}: {message}")
        self.status = status
        self.message = message
        self.url = url


def load_oauth(oauth_file: Path, timeout: float = 25.0) -> OAuth2:
    """Load tokens from `oauth_file`, refreshing the access token when expired."""
    path = Path(oauth_file)
    if not path.exists():
        raise YahooAuthError(
            f"Yahoo OAuth file not found at {path}. Create it with consumer_key/consumer_secret "
            "and run `fbb auth` once to link your Yahoo account."
        )
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise YahooAuthError(f"Could not read {path}: {exc}") from exc
    if not data.get("consumer_key") or not data.get("consumer_secret"):
        raise YahooAuthError(f"{path} must contain consumer_key and consumer_secret.")
    if not data.get("access_token") or not data.get("refresh_token"):
        raise YahooAuthError(f"{path} has no tokens yet. Run `fbb auth` to link your Yahoo account.")
    oauth = OAuth2(None, None, from_file=str(path))
    if not oauth.token_is_valid():
        oauth.refresh_access_token()
    oauth.session.timeout = timeout  # informational; every call passes timeout explicitly
    return oauth


class YahooClient:
    """Thin, explicit wrapper over the Yahoo Fantasy REST API for one league."""

    def __init__(self, oauth: OAuth2, league_id: str, team_key: str = "",
                 params: ModelParams = DEFAULT_PARAMS, timeout: float = 25.0):
        self.oauth = oauth
        self.timeout = timeout
        self.params = params
        self._league_key: Optional[str] = league_id if ".l." in league_id else None
        self._league_id = league_id.split(".l.")[-1]
        self._team_key_pref = team_key
        self._cache: dict[str, Any] = {}

    @classmethod
    def from_settings(cls, settings: Settings, params: ModelParams = DEFAULT_PARAMS) -> "YahooClient":
        return cls(load_oauth(settings.yahoo_oauth_file), settings.league_id, settings.team_key, params=params)

    # -- HTTP ------------------------------------------------------------------
    def get(self, path: str) -> dict[str, Any]:
        url = f"{BASE_URL}/{path}"
        sep = "&" if "?" in url else "?"
        resp = self.oauth.session.get(f"{url}{sep}format=json", timeout=self.timeout)
        if resp.status_code != 200:
            message = ""
            try:
                message = resp.json().get("error", {}).get("description", "")
            except ValueError:
                message = resp.text[:200]
            if resp.status_code in (401, 403):
                raise YahooAuthError(
                    f"Yahoo refused the request ({resp.status_code}: {message or 'no detail'}). "
                    "Check that the Yahoo app has Fantasy Sports read permission and run `fbb auth` to re-link."
                )
            raise YahooApiError(resp.status_code, message or resp.reason, url)
        try:
            return resp.json()
        except ValueError as exc:
            raise YahooApiError(resp.status_code, f"non-JSON response: {exc}", url) from exc

    # -- league / team resolution ---------------------------------------------
    @property
    def league_key(self) -> str:
        if self._league_key:
            return self._league_key
        data = self.get("users;use_login=1/games;game_keys=nba/leagues")
        found: list[str] = []
        for league_node in walk_values(data, "league"):
            meta = league_node[0] if isinstance(league_node, list) and league_node else league_node
            if isinstance(meta, dict) and str(meta.get("league_id", "")) == self._league_id:
                found.append(meta.get("league_key", ""))
        found = [k for k in found if k]
        if not found:
            raise YahooApiError(404, f"League id {self._league_id} not found among the logged-in user's NBA leagues.")
        # Prefer the newest game (highest game id prefix) if the league id repeats across seasons.
        self._league_key = sorted(found, key=lambda k: safe_int(k.split(".l.")[0]))[-1]
        return self._league_key

    def league_meta(self) -> dict[str, Any]:
        if "league_meta" not in self._cache:
            self._cache["league_meta"] = parse.parse_league_meta(self.get(f"league/{self.league_key}"))
        return self._cache["league_meta"]

    def league_settings(self) -> dict[str, Any]:
        if "league_settings" not in self._cache:
            data = self.get(f"league/{self.league_key}/settings")
            section = data.get("fantasy_content", {}).get("league", [])
            settings = section[1].get("settings", [{}])[0] if len(section) > 1 else {}
            self._cache["league_settings"] = settings
        return self._cache["league_settings"]

    def teams(self) -> dict[str, dict[str, Any]]:
        if "teams" not in self._cache:
            self._cache["teams"] = parse.parse_teams_response(self.get(f"league/{self.league_key}/teams"))
        return self._cache["teams"]

    @property
    def team_key(self) -> str:
        """The user's team: the configured key if it belongs to this league, else the one Yahoo marks as theirs."""
        teams = self.teams()
        if self._team_key_pref:
            if self._team_key_pref in teams:
                return self._team_key_pref
            raise YahooApiError(404, f"Configured FANTASY_TEAM_KEY {self._team_key_pref} is not in league {self.league_key}.")
        mine = [k for k, v in teams.items() if v.get("is_mine")]
        if len(mine) != 1:
            raise YahooApiError(404, f"Could not identify your team in league {self.league_key}; set FANTASY_TEAM_KEY.")
        return mine[0]

    def current_week(self) -> int:
        return int(self.league_meta()["current_week"])

    def matchup(self, team_key: str, week: int) -> dict[str, Any]:
        """Opponent and week dates for `team_key` in `week`."""
        data = self.get(f"team/{team_key}/matchups;weeks={week}")
        for m in walk_values(data, "matchup"):
            if not isinstance(m, dict):
                continue
            teams_block = m.get("0", {}).get("teams", {})
            keys = []
            for k, wrapped in teams_block.items():
                if k == "count" or not isinstance(wrapped, dict):
                    continue
                for item in wrapped.get("team", [[]])[0]:
                    if isinstance(item, dict) and "team_key" in item:
                        keys.append(item["team_key"])
            opp = [k for k in keys if k != team_key]
            return {
                "week": safe_int(m.get("week"), week),
                "week_start": m.get("week_start", ""),
                "week_end": m.get("week_end", ""),
                "status": m.get("status", ""),
                "opponent_team_key": opp[0] if opp else "",
            }
        raise YahooApiError(404, f"No matchup found for {team_key} in week {week}.")

    # -- rosters and stats ----------------------------------------------------
    def roster(self, team_key: str, day: Optional[date] = None, week: Optional[int] = None) -> list[dict[str, Any]]:
        if day is not None:
            path = f"team/{team_key}/roster;date={day.isoformat()}/players"
        elif week is not None:
            path = f"team/{team_key}/roster;week={week}/players"
        else:
            path = f"team/{team_key}/roster/players"
        return parse.parse_team_roster_response(self.get(path))

    @staticmethod
    def active_roster(players: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [p for p in players if p.get("selected_position") not in ROSTER_INACTIVE_SLOTS]

    def team_week_stats(self, team_key: str, week: int) -> dict[str, float]:
        return parse.extract_category_stats(self.get(f"team/{team_key}/stats;type=week;week={week}"))

    def _players_with_stats(self, endpoint: str, stats_type: Optional[str]) -> list[dict[str, Any]]:
        suffix = f"/stats;type={stats_type}" if stats_type else ""
        return parse.parse_players_response(self.get(f"league/{self.league_key}/{endpoint}{suffix}"))

    def player_stats(self, player_keys: list[str], stats_type: str) -> dict[str, dict[str, Any]]:
        """Stats of type `stats_type` (season, average_season, lastweek, lastmonth...) keyed by player_key."""
        out: dict[str, dict[str, Any]] = {}
        keys = list(dict.fromkeys(player_keys))
        for i in range(0, len(keys), PAGE_SIZE):
            chunk = keys[i:i + PAGE_SIZE]
            for row in self._players_with_stats(f"players;player_keys={','.join(chunk)}", stats_type):
                out[row["player_key"]] = row
        return out

    def free_agents(self, pool_size: int, stats_type: Optional[str] = None, status: str = "FA",
                    sort: str = "OR") -> list[dict[str, Any]]:
        """Top `pool_size` available players by Yahoo rank, 25 per request."""
        out: dict[str, dict[str, Any]] = {}
        limit = max(PAGE_SIZE, min(pool_size, self.params.max_free_agent_pool))
        for start in range(0, limit, PAGE_SIZE):
            count = min(PAGE_SIZE, limit - start)
            rows = self._players_with_stats(f"players;status={status};sort={sort};start={start};count={count}", stats_type)
            for row in rows:
                out[row["player_key"]] = row
            if len(rows) < count:
                break
        return list(out.values())

    def percent_owned(self, player_keys: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        keys = list(dict.fromkeys(player_keys))
        for i in range(0, len(keys), PAGE_SIZE):
            chunk = keys[i:i + PAGE_SIZE]
            data = self.get(f"league/{self.league_key}/players;player_keys={','.join(chunk)}/percent_owned")
            for row in parse.parse_players_response(data):
                if row.get("percent_owned") is not None:
                    out[row["player_key"]] = float(row["percent_owned"])
        return out

    def player_details(self, player_keys: list[str]) -> dict[str, dict[str, Any]]:
        """Status, status_full and injury note per player (Yahoo's metadata without stats)."""
        out: dict[str, dict[str, Any]] = {}
        keys = list(dict.fromkeys(player_keys))
        for i in range(0, len(keys), PAGE_SIZE):
            chunk = keys[i:i + PAGE_SIZE]
            for row in self._players_with_stats(f"players;player_keys={','.join(chunk)}", None):
                out[row["player_key"]] = row
        return out

    def transactions(self, types: str = "add,drop", page_size: int = PAGE_SIZE, max_pages: int = 400) -> list[dict[str, Any]]:
        """All transactions of the given types, newest first, paged until Yahoo returns a short page."""
        out: list[dict[str, Any]] = []
        start = 0
        for _ in range(max_pages):
            data = self.get(f"league/{self.league_key}/transactions;types={types};start={start};count={page_size}")
            section = data.get("fantasy_content", {}).get("league", [])
            txs = section[1].get("transactions", {}) if len(section) > 1 else {}
            page = [w.get("transaction") for k, w in txs.items() if k != "count" and isinstance(w, dict)]
            page = [t for t in page if t]
            out.extend(page)
            if len(page) < page_size:
                break
            start += page_size
        return out

    def player_key_prefix(self) -> str:
        return self.league_key.split(".l.")[0] + ".p."
