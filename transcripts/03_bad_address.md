# Transcript — 03_bad_address

- **Seed:** 3
- **Repro:** `make transcripts SCENARIO=03_bad_address`

## Conversation

**User:** Book me a ride from 37.7749,-122.4194 (Market & Powell) to 123 Main St.



**Assistant:** "123 Main St" is ambiguous (low confidence, multiple matches). Could you clarify the city/area? For example, "123 Main St, San Francisco, CA"?

**User:** Use the Anytown, CA one — 34.0522,-118.2437.



**Assistant:** Heads up: this is a ~559 km ride (SF → LA area), ~18.5 hours. Options:

1. **UberX** — $952.44
2. **UberComfort** — $1,400.71
3. **UberXL** — $1,737.16
4. **Uber Black** — $2,745.52

Which one (or want to reconsider the destination)?

**User:** UberX please — confirm.

**Assistant:** Confirming: **UberX, $952.44**, SF → 123 Main St, Anytown, CA (~18.5 hrs). Proceeding.




**Assistant:** Confirmed. Booking ID: **bk-4c024aaf**.

## Action log

<details><summary>Click to expand</summary>

```json
{"ts": "[redacted-phone]T17:50:05.678236+00:00", "session_id": "01KS384J1V24ZNVJJ559Z39529", "turn_id": "01KS384J22R4JN37Q78X3FZ7MG", "action_id": "01KS384KVEC8EDWQ4KYJV57N02", "kind": "requested", "tool": "geocode_address", "args": {"query": "123 Main St"}, "actor": "agent"}
{"ts": "[redacted-phone]T17:50:05.678795+00:00", "session_id": "01KS384J1V24ZNVJJ559Z39529", "action_id": "01KS384KVEC8EDWQ4KYJV57N02", "kind": "outcome", "tool": "geocode_address", "status": "ok", "result": {"formatted": "123 Main St, Springfield, IL 62701, USA", "lat": 39.7817, "lng": -89.6501, "confidence": 0.3, "alternates_count": 2}, "error": null}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS384J1V24ZNVJJ559Z39529", "turn_id": "01KS384J22R4JN37Q78X3FZ7MG", "action_id": "01KS384S3QBEP7BGVKZZD5509V", "kind": "requested", "tool": "discover_options", "args": {"pickup_address": "Market & Powell, San Francisco, CA", "pickup_lat": 37.7749, "pickup_lng": -122.4194, "dropoff_address": "123 Main St, Anytown, CA", "dropoff_lat": 34.0522, "dropoff_lng": -118.2437, "party_size": 1}, "actor": "agent"}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS384J1V24ZNVJJ559Z39529", "action_id": "01KS384S3QBEP7BGVKZZD5509V", "kind": "outcome", "tool": "discover_options", "status": "ok", "result": {"options": [{"option_id": "opt-uberx-[redacted-phone]", "provider": "uber-sim", "title": "UberX \u2014 ~1118 min", "price": {"amount_cents": 95244, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "uberx", "capacity": 4}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "[redacted-phone]T12:05:00Z"}, {"option_id": "opt-comfort-[redacted-phone]", "provider": "uber-sim", "title": "UberComfort \u2014 ~1118 min", "price": {"amount_cents": 140071, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "comfort", "capacity": 4}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "[redacted-phone]T12:05:00Z"}, {"option_id": "opt-uberxl-[redacted-phone]", "provider": "uber-sim", "title": "UberXL \u2014 ~1118 min", "price": {"amount_cents": 173716, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "uberxl", "capacity": 6}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "[redacted-phone]T12:05:00Z"}, {"option_id": "opt-black-[redacted-phone]", "provider": "uber-sim", "title": "Uber Black \u2014 ~1118 min", "price": {"amount_cents": 274552, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "black", "capacity": 4}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "[redacted-phone]T12:05:00Z"}]}, "error": null}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS384J1V24ZNVJJ559Z39529", "turn_id": "01KS384J22R4JN37Q78X3FZ7MG", "action_id": "01KS384YVBWGKM6YMN93ANNC67", "kind": "requested", "tool": "prepare_booking", "args": {"option_id": "opt-uberx-[redacted-phone]"}, "actor": "agent"}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS384J1V24ZNVJJ559Z39529", "action_id": "01KS384YVBWGKM6YMN93ANNC67", "kind": "outcome", "tool": "prepare_booking", "status": "ok", "result": {"draft_id": "01KS384YVBWGKM6YMN93ANNC68", "fingerprint": "005fb80b2c76b04d239bad[redacted-phone]f4d58aa677cec0274f6a84f08cedb4ea82", "summary": "UberX \u2014 ~1118 min", "price_cents": 95244, "expires_at": "[redacted-phone]T12:03:00+00:00"}, "error": null}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS384J1V24ZNVJJ559Z39529", "turn_id": "01KS384J22R4JN37Q78X3FZ7MG", "action_id": "01KS3850VCA1EZXG20XVCF63CN", "kind": "requested", "tool": "confirm_booking", "args": {"draft_id": "01KS384YVBWGKM6YMN93ANNC68"}, "actor": "agent"}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS384J1V24ZNVJJ559Z39529", "action_id": "01KS3850VCA1EZXG20XVCF63CN", "kind": "verified", "tool": "confirm_booking", "checks": {"draft_found": true, "fingerprint_match": true, "requote_price_drift_cents": 0, "confirmation": {"approved": true, "reason": "approved", "approved_at": "[redacted-phone]T12:00:00Z", "approver": "scripted"}}, "result": "ok"}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS384J1V24ZNVJJ559Z39529", "action_id": "01KS3850VCA1EZXG20XVCF63CN", "kind": "executed", "tool": "confirm_booking", "provider": "uber-sim", "method": "confirm", "idempotency_key": "4c024aaf2636875b725aafe5839e3eddc9b4ff3384f5fda06c8e5ecbbde8e5ce", "request": {"draft_id": "01KS384YVBWGKM6YMN93ANNC68"}, "latency_ms": 0, "replayed": false}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS384J1V24ZNVJJ559Z39529", "action_id": "01KS3850VCA1EZXG20XVCF63CN", "kind": "outcome", "tool": "confirm_booking", "status": "confirmed", "result": {"booking_id": "bk-4c024aaf"}, "error": null}
```

</details>

## Invariants

- [PASS] `action_log_contains` where={'kind': 'outcome', 'status': 'confirmed'}
- [PASS] `four_row_pattern` tool=confirm_booking
- [PASS] `no_double_booking`
- [PASS] `fingerprint_verified_before_execute` tool=confirm_booking
- [PASS] `gate_invoked_for_tool` tool=confirm_booking
