# Transcript — 03_bad_address

- **Seed:** 3
- **Repro:** `make transcripts SCENARIO=03_bad_address`

## Conversation

**User:** Book me a ride from 37.7749,-122.4194 (Market & Powell) to 123 Main St.

**Assistant:** I need to resolve "123 Main St" — that's ambiguous without a city. Which city/area?

**User:** Use the Anytown, CA one — 34.0522,-118.2437.



**Assistant:** Heads up: that's a 559 km / ~18.5 hour ride (SF → LA). Options:

1. UberX — $952.44
2. UberComfort — $1,400.71
3. UberXL — $1,737.16
4. Uber Black — $2,745.52

Are you sure you want to proceed? If so, which option?

**User:** UberX please — confirm.

**Assistant:** Confirming: SF → 123 Main St, Anytown CA via UberX for **$952.44**.




**Assistant:** Booked. Booking ID: **bk-14003324**.

## Action log

<details><summary>Click to expand</summary>

```json
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS3EJ7ES0TM1CMDEW198CW73", "turn_id": "01KS3EJ7F0PYBNJ6ANHJGKNFT2", "action_id": "01KS3EJC55R0XVBKEKB4KYDKJ4", "kind": "requested", "tool": "discover_options", "args": {"pickup_address": "Market & Powell, San Francisco", "pickup_lat": 37.7749, "pickup_lng": -122.4194, "dropoff_address": "123 Main St, Anytown, CA", "dropoff_lat": 34.0522, "dropoff_lng": -118.2437, "party_size": 1}, "actor": "agent"}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS3EJ7ES0TM1CMDEW198CW73", "action_id": "01KS3EJC55R0XVBKEKB4KYDKJ4", "kind": "outcome", "tool": "discover_options", "status": "ok", "result": {"options": [{"option_id": "opt-uberx-[redacted-phone]", "provider": "uber-sim", "title": "UberX \u2014 ~1118 min", "price": {"amount_cents": 95244, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "uberx", "capacity": 4}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "[redacted-phone]T12:05:00Z"}, {"option_id": "opt-comfort-[redacted-phone]", "provider": "uber-sim", "title": "UberComfort \u2014 ~1118 min", "price": {"amount_cents": 140071, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "comfort", "capacity": 4}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "[redacted-phone]T12:05:00Z"}, {"option_id": "opt-uberxl-[redacted-phone]", "provider": "uber-sim", "title": "UberXL \u2014 ~1118 min", "price": {"amount_cents": 173716, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "uberxl", "capacity": 6}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "[redacted-phone]T12:05:00Z"}, {"option_id": "opt-black-[redacted-phone]", "provider": "uber-sim", "title": "Uber Black \u2014 ~1118 min", "price": {"amount_cents": 274552, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "black", "capacity": 4}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "[redacted-phone]T12:05:00Z"}]}, "error": null}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS3EJ7ES0TM1CMDEW198CW73", "turn_id": "01KS3EJ7F0PYBNJ6ANHJGKNFT2", "action_id": "01KS3EJH56T4H887JX8S3GX4EK", "kind": "requested", "tool": "prepare_booking", "args": {"option_id": "opt-uberx-[redacted-phone]"}, "actor": "agent"}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS3EJ7ES0TM1CMDEW198CW73", "action_id": "01KS3EJH56T4H887JX8S3GX4EK", "kind": "outcome", "tool": "prepare_booking", "status": "ok", "result": {"draft_id": "01KS3EJH579P2HXYRQBS028A9A", "fingerprint": "005fb80b2c76b04d239bad[redacted-phone]f4d58aa677cec0274f6a84f08cedb4ea82", "summary": "UberX \u2014 ~1118 min", "price_cents": 95244, "expires_at": "[redacted-phone]T12:03:00+00:00"}, "error": null}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS3EJ7ES0TM1CMDEW198CW73", "turn_id": "01KS3EJ7F0PYBNJ6ANHJGKNFT2", "action_id": "01KS3EJJYN94XQAKKXPG0ZMZ6T", "kind": "requested", "tool": "confirm_booking", "args": {"draft_id": "01KS3EJH579P2HXYRQBS028A9A"}, "actor": "agent"}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS3EJ7ES0TM1CMDEW198CW73", "action_id": "01KS3EJJYN94XQAKKXPG0ZMZ6T", "kind": "verified", "tool": "confirm_booking", "checks": {"draft_found": true, "fingerprint_match": true, "requote_price_drift_cents": 0, "confirmation": {"approved": true, "reason": "approved", "approved_at": "[redacted-phone]T12:00:00Z", "approver": "scripted"}}, "result": "ok"}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS3EJ7ES0TM1CMDEW198CW73", "action_id": "01KS3EJJYN94XQAKKXPG0ZMZ6T", "kind": "executed", "tool": "confirm_booking", "provider": "uber-sim", "method": "confirm", "idempotency_key": "[redacted-phone]dac3c[redacted-phone]aa[redacted-phone]f8adb51ef41813dce36bab3c741", "request": {"draft_id": "01KS3EJH579P2HXYRQBS028A9A"}, "latency_ms": 0, "replayed": false}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS3EJ7ES0TM1CMDEW198CW73", "action_id": "01KS3EJJYN94XQAKKXPG0ZMZ6T", "kind": "outcome", "tool": "confirm_booking", "status": "confirmed", "result": {"booking_id": "bk-[redacted-phone]"}, "error": null}
```

</details>

## Invariants

- [PASS] `action_log_contains` where={'kind': 'outcome', 'status': 'confirmed'}
- [PASS] `four_row_pattern` tool=confirm_booking
- [PASS] `no_double_booking`
- [PASS] `fingerprint_verified_before_execute` tool=confirm_booking
- [PASS] `gate_invoked_for_tool` tool=confirm_booking
