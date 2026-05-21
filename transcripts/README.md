# Scenario Transcripts

The 6 scenario transcripts in this directory — 5 from the brief, plus `06_cross_session_dedup` covering intent-bound idempotency — are rendered from `scenarios/*.yaml` by `scripts/render_transcript.py`.

| File | Scenario | Demonstrates |
|---|---|---|
| `01_happy_path.md` | Single ride, defaults, end-to-end | Tracer bullet works; 4-row action log; fingerprint check; idempotency claim |
| `02_price_comparison.md` | Three vehicle classes surfaced | Agent reasons over multiple options; picks per user preference |
| `03_bad_address.md` | Nominatim returns low confidence | Graceful clarification loop; geocode failure is structurally distinguishable |
| `04_surge_on_confirm.md` | Sim surges between prepare and confirm | Re-quote pivot; `PriceChanged` returned; user gets a second confirmation chance — no silent overcharge |
| `05_cancel_with_fee.md` | Mid-ride cancellation with fee | Gated cancellation; fee surfaced through `ConfirmationChannel`; state machine handles the exit |
| `06_cross_session_dedup.md` | Same intent across two sessions | Intent-bound `idempotency_key` survives `draft_id` regen + process restart; second confirm short-circuits to the original booking; explicit-yes nonce path for a deliberate second booking |

## Regenerate

```bash
make transcripts                       # all 6
python -m agentic_cab run-scenario 03  # one
```

Determinism: `temperature=0`, pinned model ID, `FrozenClock`, seeded RNG. ~5% wording drift accepted between regenerations; the **invariants** asserted in each scenario YAML are what's tested. The committed `.md` files are the last-known-good rendering.

Each rendered transcript includes: scenario header (name/seed/repro command), conversation, action-log highlights (the four-row pattern for gated actions), and invariants pass/fail block.
