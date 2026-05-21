# Transcript — 03_bad_address

- **Seed:** 3
- **Repro:** `make transcripts SCENARIO=03_bad_address`

## Conversation

**User:** Book me a ride from 37.7749,-122.4194 (Market & Powell) to 123 Main St.

**Assistant:** I need to resolve the dropoff first since "123 Main St" is ambiguous without a city.


**Assistant:** "123 Main St" is ambiguous — top match is Springfield, IL but there are other candidates. Which city did you mean?

**User:** Use the Anytown, CA one — 34.0522,-118.2437.



**Assistant:** Heads up: this is a 559 km / ~18-hour trip from SF to LA. Options:

1. **UberX** — $952.44
2. **UberComfort** — $1,400.71
3. **UberXL** — $1,736.16
4. **Uber Black** — $2,745.52

Which one — and do you actually want to proceed given the distance?

**User:** UberX please — confirm.

**Assistant:** $952.44 for UberX. Preparing draft.




**Assistant:** Confirmed. Booking ID: **bk-c68e2f6a**.

## Action log

<details><summary>Click to expand</summary>

```json
{"ts": "2026-05-19T12:00:00+00:00", "session_id": "01KS5HNDFJV1V2V2PQPVSTQQCB", "turn_id": "01KS5HNDG0ECGHNVPW0571HAG4", "action_id": "01KS5HNFV6X67ZVGYSZ2WVYWNM", "kind": "requested", "tool": "geocode_address", "args": {"query": "[redacted-address]"}, "actor": "agent"}
{"ts": "2026-05-19T12:00:00+00:00", "session_id": "01KS5HNDFJV1V2V2PQPVSTQQCB", "action_id": "01KS5HNFV6X67ZVGYSZ2WVYWNM", "kind": "outcome", "tool": "geocode_address", "status": "ok", "result": {"formatted": "123 Main St, Springfield, IL 62701, USA", "lat": 39.7817, "lng": -89.6501, "confidence": 0.3, "alternates_count": 2}, "error": null}
{"ts": "2026-05-19T12:00:00+00:00", "session_id": "01KS5HNDFJV1V2V2PQPVSTQQCB", "turn_id": "01KS5HNDG0ECGHNVPW0571HAG4", "action_id": "01KS5HNNG4YY5JJE9J8GWZV9M8", "kind": "requested", "tool": "discover_options", "args": {"pickup_address": "[redacted-address]", "pickup_lat": 37.7749, "pickup_lng": -122.4194, "dropoff_address": "[redacted-address]", "dropoff_lat": 34.0522, "dropoff_lng": -118.2437, "party_size": 1}, "actor": "agent"}
{"ts": "2026-05-19T12:00:00+00:00", "session_id": "01KS5HNDFJV1V2V2PQPVSTQQCB", "action_id": "01KS5HNNG4YY5JJE9J8GWZV9M8", "kind": "outcome", "tool": "discover_options", "status": "ok", "result": {"options": [{"option_id": "opt-uberx-649562111997", "provider": "uber-sim", "title": "UberX \u2014 ~1118 min", "price": {"amount_cents": 95244, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "uberx", "capacity": 4}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "2026-05-19T12:05:00Z"}, {"option_id": "opt-comfort-144071367498", "provider": "uber-sim", "title": "UberComfort \u2014 ~1118 min", "price": {"amount_cents": 140071, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "comfort", "capacity": 4}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "2026-05-19T12:05:00Z"}, {"option_id": "opt-uberxl-522284859645", "provider": "uber-sim", "title": "UberXL \u2014 ~1118 min", "price": {"amount_cents": 173716, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "uberxl", "capacity": 6}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "2026-05-19T12:05:00Z"}, {"option_id": "opt-black-638342608038", "provider": "uber-sim", "title": "Uber Black \u2014 ~1118 min", "price": {"amount_cents": 274552, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "black", "capacity": 4}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "2026-05-19T12:05:00Z"}]}, "error": null}
{"ts": "2026-05-19T12:00:00+00:00", "session_id": "01KS5HNDFJV1V2V2PQPVSTQQCB", "turn_id": "01KS5HNDG0ECGHNVPW0571HAG4", "action_id": "01KS5HNVB1Q6GNY0D68VRMKYN3", "kind": "requested", "tool": "prepare_booking", "args": {"option_id": "opt-uberx-649562111997"}, "actor": "agent"}
{"ts": "2026-05-19T12:00:00+00:00", "session_id": "01KS5HNDFJV1V2V2PQPVSTQQCB", "action_id": "01KS5HNVB1Q6GNY0D68VRMKYN3", "kind": "outcome", "tool": "prepare_booking", "status": "ok", "result": {"draft_id": "01KS5HNVB2DG0PBRE0J9SW4JQ6", "fingerprint": "005fb80b2c76b04d239bad29925854f4d58aa677cec0274f6a84f08cedb4ea82", "summary": "UberX \u2014 ~1118 min", "price_cents": 95244, "expires_at": "2026-05-19T12:03:00+00:00"}, "error": null}
{"ts": "2026-05-19T12:00:00+00:00", "session_id": "01KS5HNDFJV1V2V2PQPVSTQQCB", "turn_id": "01KS5HNDG0ECGHNVPW0571HAG4", "action_id": "01KS5HNXCW21XX9YXQMWPY1VAE", "kind": "requested", "tool": "confirm_booking", "args": {"draft_id": "01KS5HNVB2DG0PBRE0J9SW4JQ6"}, "actor": "agent"}
{"ts": "2026-05-19T12:00:00+00:00", "session_id": "01KS5HNDFJV1V2V2PQPVSTQQCB", "action_id": "01KS5HNXCW21XX9YXQMWPY1VAE", "kind": "verified", "tool": "confirm_booking", "checks": {"draft_found": true, "fingerprint_match": true, "requote_price_drift_cents": 0, "confirmation": {"approved": true, "reason": "approved", "approved_at": "2026-05-19T12:00:00Z", "approver": "scripted"}}, "result": "ok"}
{"ts": "2026-05-19T12:00:00+00:00", "session_id": "01KS5HNDFJV1V2V2PQPVSTQQCB", "action_id": "01KS5HNXCW21XX9YXQMWPY1VAE", "kind": "executed", "tool": "confirm_booking", "provider": "uber-sim", "method": "confirm", "idempotency_key": "c68e2f6ae9dfbe050cc6cda2ca53db51ea706b4c58fc39e9a505b9d499a3f889", "request": {"draft_id": "01KS5HNVB2DG0PBRE0J9SW4JQ6"}, "latency_ms": 0, "replayed": false}
{"ts": "2026-05-19T12:00:00+00:00", "session_id": "01KS5HNDFJV1V2V2PQPVSTQQCB", "action_id": "01KS5HNXCW21XX9YXQMWPY1VAE", "kind": "outcome", "tool": "confirm_booking", "status": "confirmed", "result": {"booking_id": "bk-c68e2f6a"}, "error": null}
```

</details>

## Invariants

- [PASS] `action_log_contains` where={'kind': 'outcome', 'status': 'confirmed'}
- [PASS] `four_row_pattern` tool=confirm_booking
- [PASS] `no_double_booking`
- [PASS] `fingerprint_verified_before_execute` tool=confirm_booking
- [PASS] `gate_invoked_for_tool` tool=confirm_booking
