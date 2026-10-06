#!/usr/bin/env python3
"""
🏆 PUTILLA AWARDS WEB APP 🏆

Flask web application to display Putilla Awards results.
Leaguemates can view the awards in their browser with password protection.

This is the main entry point for the web server. It:
1. Sets up Flask with HTTP Basic Authentication
2. Provides routes to view awards in HTML or fetch as JSON
3. Calls the awards_api.py module to calculate awards from Yahoo Fantasy data

Usage:
    python web_app.py

Then visit: http://localhost:5000
Username: YOUR_APP_USERNAME
Password: YOUR_APP_PASSWORD
"""

# Flask imports: web framework, template rendering, JSON responses
from flask import Flask, render_template, jsonify, request
from flask_httpauth import HTTPBasicAuth  # Password protection
import sys
import io
import os
import json
from contextlib import redirect_stdout
from datetime import datetime

# Import our custom awards calculation functions from awards_api.py
# These functions handle Yahoo Fantasy API calls and award logic
from awards_api import (
    ensure_oauth,           # Handles Yahoo OAuth authentication
    get_all_teams_rosters,  # Fetches all roster data from Yahoo API
    calculate_awards,       # Main function that calculates all awards
    LEAGUE_KEY             # Hard-coded league ID (466.l.10145)
)

from waiver_optimizer_api import (
    get_waiver_recommendations,
    get_waiver_team_options,
    ALL_CATEGORIES,
    DEFAULT_TEAM_KEY,
)

WAIVER_PREFS_FILE = os.path.join(os.path.dirname(__file__), "waiver_user_prefs.json")

DEFAULT_WAIVER_PREFS = {
    "selected_team_key": "",
    "projection_window": "recent_2w",
    "free_agent_pool": 120,
    "drop_candidates": 4,
    "max_results": 20,
    "punt_categories": [],
    "selected_drop_players": [],
    "selected_never_drop_players": [],
    "drop_player_text": "",
    "never_drop_text": "",
    "include_current_score": True,
    "include_news_context": True,
    "include_percent_owned_guard": True,
    "respect_undroppable": True,
    "ownership_protect_threshold": 80,
}

# Initialize Flask application
# __name__ tells Flask where to find templates and static files
app = Flask(__name__)

# Initialize HTTP Basic Auth for password protection
# This creates a login prompt in the browser
auth = HTTPBasicAuth()


def parse_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = default
    return max(minimum, min(maximum, v))


def read_waiver_prefs_store() -> dict:
    if not os.path.exists(WAIVER_PREFS_FILE):
        return {}
    try:
        with open(WAIVER_PREFS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def write_waiver_prefs_store(store: dict) -> None:
    tmp = WAIVER_PREFS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, sort_keys=True)
    os.replace(tmp, WAIVER_PREFS_FILE)


def sanitize_waiver_prefs(payload: dict) -> dict:
    if not isinstance(payload, dict):
        payload = {}

    punt = payload.get("punt_categories", [])
    if not isinstance(punt, list):
        punt = []
    punt_categories = []
    for item in punt:
        cat = str(item).strip().upper()
        if cat in ALL_CATEGORIES and cat not in punt_categories:
            punt_categories.append(cat)

    def sanitize_list(name: str) -> list[str]:
        raw = payload.get(name, [])
        if isinstance(raw, str):
            raw = [p.strip() for p in raw.split(",") if p.strip()]
        if not isinstance(raw, list):
            return []
        out = []
        for item in raw:
            text = str(item).strip()
            if text and text not in out:
                out.append(text)
        return out[:40]

    return {
        "selected_team_key": str(payload.get("selected_team_key", "") or "").strip()[:80],
        "projection_window": (
            str(payload.get("projection_window", "recent_2w") or "").strip().lower()
            if str(payload.get("projection_window", "recent_2w") or "").strip().lower() in {"recent_2w", "season"}
            else "recent_2w"
        ),
        "free_agent_pool": clamp_int(payload.get("free_agent_pool"), 120, 25, 250),
        "drop_candidates": clamp_int(payload.get("drop_candidates"), 4, 1, 8),
        "max_results": clamp_int(payload.get("max_results"), 20, 5, 50),
        "punt_categories": punt_categories,
        "selected_drop_players": sanitize_list("selected_drop_players"),
        "selected_never_drop_players": sanitize_list("selected_never_drop_players"),
        "drop_player_text": str(payload.get("drop_player_text", "") or "")[:300],
        "never_drop_text": str(payload.get("never_drop_text", "") or "")[:300],
        "include_current_score": parse_bool(payload.get("include_current_score"), True),
        "include_news_context": parse_bool(payload.get("include_news_context"), True),
        "include_percent_owned_guard": parse_bool(payload.get("include_percent_owned_guard"), True),
        "respect_undroppable": parse_bool(payload.get("respect_undroppable"), True),
        "ownership_protect_threshold": clamp_int(payload.get("ownership_protect_threshold"), 80, 0, 100),
    }


def get_user_waiver_prefs(username: str) -> dict:
    store = read_waiver_prefs_store()
    saved = store.get(username, {}) if isinstance(store, dict) else {}
    prefs = dict(DEFAULT_WAIVER_PREFS)
    if isinstance(saved, dict):
        prefs.update(sanitize_waiver_prefs(saved))
    return prefs


def set_user_waiver_prefs(username: str, payload: dict) -> dict:
    store = read_waiver_prefs_store()
    if not isinstance(store, dict):
        store = {}

    current = get_user_waiver_prefs(username)
    updated = dict(current)
    updated.update(sanitize_waiver_prefs(payload))
    store[username] = updated
    write_waiver_prefs_store(store)
    return updated


# ============================================================================
# AUTHENTICATION
# ============================================================================

@auth.verify_password
def verify_password(username, password):
    """
    Password verification function for HTTP Basic Auth.
    
    This gets called automatically when someone visits any @auth.login_required route.
    Browser shows a login popup, and this function checks the credentials.
    
    Args:
        username: Username entered by user
        password: Password entered by user
    
    Returns:
        username if credentials are correct, None otherwise
        (returning None triggers another login prompt)
    """
    # Credentials are configured through environment variables
    if username == os.environ.get("APP_USERNAME") and password == os.environ.get("APP_PASSWORD"):
        return username  # Credentials match, allow access
    return None  # Wrong credentials, show login prompt again


# ============================================================================
# AWARDS DATA FUNCTIONS
# ============================================================================

def get_awards_data():
    """
    Orchestrates the entire awards calculation process.
    
    This function:
    1. Authenticates with Yahoo Fantasy API using OAuth
    2. Fetches all roster data (players owned by each team)
    3. Calculates all awards (team and player awards)
    4. Adds metadata like timestamp
    5. Returns everything in a structured dictionary
    
    Returns:
        dict: {
            'success': True/False,
            'data': {
                'team_awards': {...},    # Team-based awards
                'player_awards': {...},   # Player-based awards
                'generated_at': 'Jan 6, 2026 at 3:45 PM',
                'league_key': '466.l.10145'
            },
            'error': 'error message if failed',
            'traceback': 'full error details for debugging'
        }
    """
    try:
        # Step 1: Authenticate with Yahoo Fantasy API
        # This reads oauth2.json which contains refresh tokens
        oauth = ensure_oauth(os.environ.get('YAHOO_OAUTH_FILE', 'oauth2.json'))
        
        # Step 2: Fetch all roster data from Yahoo
        # Returns dict like: {'Team Name': [player1_dict, player2_dict, ...], ...}
        rosters_by_team = get_all_teams_rosters(oauth)
        
        # Step 3: Calculate all awards based on roster data
        # This does the heavy lifting: filters putillas, calculates values, ranks them
        results = calculate_awards(rosters_by_team)
        
        # Step 4: Add metadata for display
        results['generated_at'] = datetime.now().strftime('%B %d, %Y at %I:%M %p')
        results['league_key'] = LEAGUE_KEY
        
        # Return success response with all data
        return {
            'success': True,
            'data': results
        }
    
    except Exception as e:
        # If anything goes wrong, capture full error details for debugging
        import traceback
        return {
            'success': False,
            'error': str(e),           # Simple error message
            'traceback': traceback.format_exc()  # Full stack trace
        }


# ============================================================================
# WEB ROUTES
# ============================================================================

@app.route('/health')
def health():
    """Lightweight readiness endpoint for the hosting platform."""
    return jsonify({"status": "ok"})


@app.route('/')
@auth.login_required  # Decorator: requires login before accessing this route
def index():
    """
    Main homepage route - displays the awards page.
    
    When someone visits http://localhost:5000/
    1. Browser prompts for username/password (@auth.login_required)
    2. verify_password() checks credentials
    3. If correct, render the HTML template from templates/awards.html
    
    The template contains JavaScript that fetches award data from /api/awards
    """
    return render_template('awards.html')


@app.route('/api/awards')
@auth.login_required  # Also password-protected
def api_awards():
    """
    API endpoint that returns awards data as JSON.
    
    The frontend JavaScript calls this endpoint to fetch award data.
    URL: http://localhost:5000/api/awards
    
    Returns:
        JSON response with structure:
        {
            "success": true,
            "data": {
                "team_awards": {...},
                "player_awards": {...},
                "generated_at": "Jan 6, 2026 at 3:45 PM"
            }
        }
    """
    return jsonify(get_awards_data())


@app.route('/api/awards/refresh')
@auth.login_required
def api_refresh():
    """
    Force refresh awards data (recalculate from Yahoo API).
    
    If awards look stale, leaguemates can hit this endpoint to force
    a fresh calculation. Useful after roster moves or drops.
    
    URL: http://localhost:5000/api/awards/refresh
    
    Returns same JSON structure as /api/awards
    """
    return jsonify(get_awards_data())


@app.route('/waiver')
@auth.login_required
def waiver_page():
    """Waiver optimization dashboard."""
    return render_template('waiver.html')


@app.route('/api/waiver/analyze')
@auth.login_required
def api_waiver_analyze():
    """
    Analyze current matchup waiver moves.

    Query params:
      - team_key (optional Yahoo team key, e.g. "466.l.10145.t.12")
      - projection_window ("recent_2w" or "season")
      - free_agent_pool (default 120, min 25, max 250)
      - drop_candidates (default 4, min 1, max 8)
      - max_results (default 20, min 5, max 50)
      - punt (comma-separated categories, e.g. "TO,FG%")
      - drop_player (comma-separated player names or player_keys for manual drop mode)
      - never_drop (comma-separated names or player_keys)
      - include_current_score (true/false)
      - include_news_context (true/false)
      - include_percent_owned_guard (true/false)
      - respect_undroppable (true/false)
      - ownership_protect_threshold (0-100)
    """
    user = auth.current_user() or "default"
    prefs = get_user_waiver_prefs(user)
    selected_team_key = str(
        request.args.get("team_key", prefs.get("selected_team_key", "") or "")
    ).strip()
    projection_window = str(
        request.args.get("projection_window", prefs.get("projection_window", "recent_2w") or "recent_2w")
    ).strip().lower()
    if projection_window not in {"recent_2w", "season"}:
        projection_window = "recent_2w"

    free_agent_pool = clamp_int(
        request.args.get("free_agent_pool", prefs["free_agent_pool"]),
        prefs["free_agent_pool"],
        25,
        250,
    )
    drop_candidates = clamp_int(
        request.args.get("drop_candidates", prefs["drop_candidates"]),
        prefs["drop_candidates"],
        1,
        8,
    )
    max_results = clamp_int(
        request.args.get("max_results", prefs["max_results"]),
        prefs["max_results"],
        5,
        50,
    )

    punt_raw = request.args.get("punt")
    punt_categories = []
    if punt_raw is None:
        punt_categories = list(prefs.get("punt_categories", []))
    else:
        for token in punt_raw.split(","):
            cat = token.strip().upper()
            if cat in ALL_CATEGORIES and cat not in punt_categories:
                punt_categories.append(cat)

    drop_player_raw = request.args.get("drop_player")
    forced_drop_players = []
    if drop_player_raw is None:
        forced_drop_players = list(prefs.get("selected_drop_players", []))
        typed = str(prefs.get("drop_player_text", "") or "").strip()
        if typed:
            forced_drop_players.extend([t.strip() for t in typed.split(",") if t.strip()])
    else:
        forced_drop_players = [t.strip() for t in drop_player_raw.split(",") if t.strip()]

    never_drop_raw = request.args.get("never_drop")
    never_drop_players = []
    if never_drop_raw is None:
        never_drop_players = list(prefs.get("selected_never_drop_players", []))
        typed = str(prefs.get("never_drop_text", "") or "").strip()
        if typed:
            never_drop_players.extend([t.strip() for t in typed.split(",") if t.strip()])
    else:
        never_drop_players = [t.strip() for t in never_drop_raw.split(",") if t.strip()]

    include_current_score = parse_bool(
        request.args.get("include_current_score", prefs.get("include_current_score", True)),
        prefs.get("include_current_score", True),
    )
    include_news_context = parse_bool(
        request.args.get("include_news_context", prefs.get("include_news_context", True)),
        prefs.get("include_news_context", True),
    )
    include_percent_owned_guard = parse_bool(
        request.args.get("include_percent_owned_guard", prefs.get("include_percent_owned_guard", True)),
        prefs.get("include_percent_owned_guard", True),
    )
    respect_undroppable = parse_bool(
        request.args.get("respect_undroppable", prefs.get("respect_undroppable", True)),
        prefs.get("respect_undroppable", True),
    )
    ownership_protect_threshold = clamp_int(
        request.args.get("ownership_protect_threshold", prefs.get("ownership_protect_threshold", 80)),
        prefs.get("ownership_protect_threshold", 80),
        0,
        100,
    )

    try:
        return jsonify(
            get_waiver_recommendations(
                team_key=selected_team_key or DEFAULT_TEAM_KEY,
                projection_window=projection_window,
                free_agent_pool=free_agent_pool,
                drop_candidates=drop_candidates,
                max_results=max_results,
                punt_categories=punt_categories,
                forced_drop_players=forced_drop_players,
                never_drop_players=never_drop_players,
                include_current_score=include_current_score,
                include_news_context=include_news_context,
                include_percent_owned_guard=include_percent_owned_guard,
                respect_undroppable=respect_undroppable,
                ownership_protect_threshold=ownership_protect_threshold,
            )
        )
    except Exception as e:
        import traceback
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
        })


@app.route('/api/waiver/teams')
@auth.login_required
def api_waiver_teams():
    """List available league teams for the waiver analyzer team selector."""
    user = auth.current_user() or "default"
    prefs = get_user_waiver_prefs(user)
    selected_team_key = str(
        request.args.get("team_key", prefs.get("selected_team_key", "") or "")
    ).strip()

    try:
        return jsonify(
            get_waiver_team_options(
                preferred_team_key=selected_team_key or DEFAULT_TEAM_KEY
            )
        )
    except Exception as e:
        import traceback
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
        })


@app.route('/api/waiver/preferences', methods=['GET', 'POST'])
@auth.login_required
def api_waiver_preferences():
    """Load or save persistent waiver preferences for the authenticated user."""
    user = auth.current_user() or "default"

    if request.method == 'GET':
        return jsonify({
            "success": True,
            "data": get_user_waiver_prefs(user),
        })

    payload = request.get_json(silent=True) or {}
    updated = set_user_waiver_prefs(user, payload)
    return jsonify({
        "success": True,
        "data": updated,
    })


# ============================================================================
# SERVER STARTUP
# ============================================================================

if __name__ == '__main__':
    """
    This block only runs when you execute: python web_app.py
    It won't run if this file is imported as a module.
    """
    import os
    
    # Get port from environment variable or default to 5000
    # Cloud platforms (Railway, Render, GCP Cloud Run) set PORT env var
    # Locally, it defaults to 5000
    port = int(os.environ.get('PORT', 5000))
    
    # Debug mode enables:
    # - Auto-reload on code changes
    # - Detailed error pages
    # - Debug toolbar
    # Only enable in local development, NOT in production
    debug = os.environ.get('FLASK_ENV') != 'production'
    
    # Print startup banner in local development mode
    if debug:
        print("\n" + "=" * 80)
        print("🏆 PUTILLA AWARDS WEB SERVER 🏆")
        print("=" * 80)
        print("\n📡 Starting server...")
        print("   Local URL: http://localhost:5000")
        print("   Network URL: http://0.0.0.0:5000")
        print("\n🌐 Share with leaguemates:")
        print("   1. Run this on your computer")
        print("   2. Share your local IP address (e.g., http://192.168.1.x:5000)")
        print("   OR deploy to free hosting (Render, Railway, PythonAnywhere)")
        print("\n⌨️  Press Ctrl+C to stop the server\n")
        print("=" * 80 + "\n")
    
    # Start the Flask development server
    # host='0.0.0.0' means accept connections from any IP (not just localhost)
    # This allows other devices on your network to access the site
    app.run(host='0.0.0.0', port=port, debug=debug)
