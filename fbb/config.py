"""Settings loaded from the environment (and .env) plus named model parameters.

Nothing else in the package reads os.environ directly. Every numeric knob the
models use lives in `ModelParams` with a name and a comment, so the behaviour
is visible in one place and testable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env(env: Mapping[str, str], key: str, default: str = "") -> str:
    return (env.get(key) or default).strip()


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Build with `Settings.from_env()`."""

    yahoo_oauth_file: Path = REPO_ROOT / "oauth2.json"
    yahoo_oauth_json: str = ""          # contents of oauth2.json; seeds the file when it is missing (cloud sessions)
    league_id: str = "17275"            # numeric Yahoo league id or a full key like 466.l.10145
    team_key: str = ""                  # empty = the team Yahoo says belongs to the login
    timezone: str = "America/New_York"
    cache_dir: Path = REPO_ROOT / ".cache"
    web_auth_user: str = ""
    web_auth_password_hash: str = ""
    hashtag_email: str = ""
    hashtag_password: str = ""
    hashtag_league_id: str = "17275"
    hashtag_cookie: str = ""            # optional pasted cookie header, fallback for login
    flask_debug: bool = False
    host: str = "127.0.0.1"
    port: int = 5000

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, dotenv_path: Optional[Path] = None) -> "Settings":
        if env is None:
            load_dotenv(dotenv_path or REPO_ROOT / ".env", override=False)
            env = os.environ
        root = REPO_ROOT

        def path_of(value: str, default: Path) -> Path:
            if not value:
                return default
            p = Path(value).expanduser()
            return p if p.is_absolute() else root / p

        return cls(
            yahoo_oauth_file=path_of(_env(env, "YAHOO_OAUTH_FILE"), root / "oauth2.json"),
            yahoo_oauth_json=_env(env, "YAHOO_OAUTH_JSON"),
            league_id=_env(env, "FANTASY_LEAGUE_ID") or _env(env, "FANTASY_LEAGUE_KEY") or "17275",
            team_key=_env(env, "FANTASY_TEAM_KEY"),
            timezone=_env(env, "TIMEZONE", "America/New_York"),
            cache_dir=path_of(_env(env, "FBB_CACHE_DIR"), root / ".cache"),
            web_auth_user=_env(env, "WEB_AUTH_USER"),
            web_auth_password_hash=_env(env, "WEB_AUTH_PASSWORD_HASH"),
            hashtag_email=_env(env, "HASHTAG_EMAIL"),
            hashtag_password=_env(env, "HASHTAG_PASSWORD"),
            hashtag_league_id=_env(env, "HASHTAG_LEAGUE_ID", "17275"),
            hashtag_cookie=_env(env, "HASHTAG_COOKIE"),
            flask_debug=_env(env, "FLASK_DEBUG", "0") == "1",
            host=_env(env, "HOST", "127.0.0.1"),
            port=int(_env(env, "PORT", "5000") or 5000),
        )


@dataclass(frozen=True)
class ModelParams:
    """Every tunable number in the projection and matchup models.

    Defaults reproduce the behaviour of the original waiver optimizer so the
    golden tests hold; deliberate model changes are flagged in comments.
    """

    # --- availability: multiplier on games remaining by Yahoo status ---
    status_multiplier: Mapping[str, float] = field(default_factory=lambda: {
        "": 1.0, "DTD": 0.85, "GTD": 0.75,
        "NA": 0.0, "O": 0.0, "INJ": 0.0, "IR": 0.0, "IL": 0.0, "IL+": 0.0,
    })
    news_probable_floor: float = 0.90
    news_questionable_floor: float = 0.75
    news_day_to_day_floor: float = 0.85
    out_with_return_hint: float = 0.25   # "out" but Yahoo lists a return date

    # --- recent-form blend (lastweek vs lastmonth totals, per game) ---
    recent_week_weight: float = 0.65
    recent_month_weight: float = 0.35
    default_fg_pct: float = 0.45
    default_ft_pct: float = 0.75
    assumed_games_played_for_totals: float = 50.0   # only when Yahoo gives season totals, not averages

    # --- shot-volume estimate when FGA/FTA are not in the data ---
    fga_per_point: float = 0.55
    fga_pct_floor: float = 0.25
    fta_per_point: float = 0.18
    fta_pct_floor: float = 0.40

    # --- schedule fallbacks when ESPN is unreachable ---
    fallback_games_per_day_lookback: float = 0.5
    fallback_games_per_day_remaining: float = 3.0 / 7.0

    # --- category win probability ---
    pct_edge_scale: float = 0.010        # 1 percentage point of FG%/FT% ~ one sigmoid unit
    counting_scale_ratio: float = 0.14   # scale = ratio * max(|my|,|opp|,1) + floor
    counting_scale_floor: float = 0.40
    prob_clamp_lo: float = 0.02
    prob_clamp_hi: float = 0.98
    # Model change vs original: shrink the edge when little of the week remains.
    # 0 reproduces the original (no scaling). 1 = full scaling by remaining share.
    remaining_games_scaling: float = 0.0

    # --- drop value score (lower = easier to drop) ---
    drop_weights: Mapping[str, float] = field(default_factory=lambda: {
        "PTS": 0.60, "REB": 0.90, "AST": 1.10, "ST": 1.50, "BLK": 1.50, "3PTM": 0.80, "TO": -0.90,
    })
    drop_fg_baseline: float = 0.46
    drop_fg_weight: float = 0.60
    drop_ft_baseline: float = 0.75
    drop_ft_weight: float = 0.40
    drop_penalty_out: float = 6.0
    drop_penalty_dtd: float = 2.0
    ownership_guard_base: float = 4.0
    ownership_guard_per_point: float = 0.45
    ownership_guard_near_band: int = 15
    ownership_guard_near_bonus: float = 1.8
    undroppable_bonus: float = 100.0

    # --- optimizer defaults ---
    default_free_agent_pool: int = 120
    default_drop_candidates: int = 4
    default_max_results: int = 20
    default_ownership_protect_threshold: int = 80
    max_free_agent_pool: int = 250
    yahoo_page_size: int = 25            # Yahoo caps player collections at 25 per request


DEFAULT_PARAMS = ModelParams()
