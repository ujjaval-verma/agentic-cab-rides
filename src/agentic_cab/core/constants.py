"""Single source for model + sampling settings. See spec §11 + CLAUDE.md inv. 9."""

MODEL_ID = "claude-opus-4-7"
# claude-opus-4-7 is an extended-thinking model; it does not accept a 'temperature'
# parameter (the API rejects it with 400).  Determinism is achieved via FrozenClock
# and seeded RNG in scenario mode (CLAUDE.md inv. 9).  AGENT_TEMPERATURE is kept as
# documentation of intent but is NOT forwarded to the API.
AGENT_TEMPERATURE = 0.0
AGENT_MAX_TOKENS = 4096
