"""Single source for model + sampling settings. See spec §11 + CLAUDE.md inv. 9."""

MODEL_ID = "claude-opus-4-7"

# Price per million tokens, in microcents (1 cent = 1_000_000 microcents).
# Snapshot verified against the claude-api skill's cached model reference
# (cache date 2026-06-24), checked 2026-07-29. NOT fetched at runtime. Stale
# prices are a correctness bug — re-verify when MODEL_ID changes.
# claude-opus-4-7: $5.00 / $25.00 per MTok input/output; cache read is 0.1x input,
# cache write is 1.25x input (default 5-minute TTL).
MODEL_PRICES_MICROCENTS_PER_MTOK: dict[str, dict[str, int]] = {
    "claude-opus-4-7": {
        "input": 500_000_000,
        "output": 2_500_000_000,
        "cache_read": 50_000_000,
        "cache_write": 625_000_000,
    },
}
# claude-opus-4-7 is an extended-thinking model; it does not accept a 'temperature'
# parameter (the API rejects it with 400).  Determinism is achieved via FrozenClock
# and seeded RNG in scenario mode (CLAUDE.md inv. 9).  AGENT_TEMPERATURE is kept as
# documentation of intent but is NOT forwarded to the API.
AGENT_TEMPERATURE = 0.0
AGENT_MAX_TOKENS = 4096

# Re-quote drift threshold (cents). If the absolute price difference between
# the draft's option_snapshot price and the re-quoted price exceeds this value,
# confirm_booking rejects the draft and returns price_changed. See spec §7 + T28.
RE_QUOTE_DRIFT_THRESHOLD_CENTS = 100
