"""fbb: fantasy basketball tooling for the Chantasy Yahoo league.

Package layout:
- config      settings (from .env) and named model parameters
- categories  category keys, Yahoo stat ids, NBA team codes, name helpers
- util        small numeric/date helpers
- cache       TTL cache (memory + disk)
- yahoo       Yahoo Fantasy API client and response parsing
- schedule    NBA schedule (ESPN scoreboard) with caching
- projection  per-game stats, blends and remaining-week projections
- matchup     category win probabilities and matchup win probability
- optimizer   waiver add/drop recommendations
- awards      Putilla awards engine
- hashtag     Hashtag Basketball premium data and roster decisions
- cli         `fbb` command line entry point
"""

__version__ = "1.0.0"
