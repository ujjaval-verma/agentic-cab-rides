"""Single source for model + sampling settings. See spec §11 + CLAUDE.md inv. 9."""

MODEL_ID = "claude-opus-4-7"
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
