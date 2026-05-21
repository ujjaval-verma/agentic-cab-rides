# Transcript — 03_bad_address

- **Seed:** 3
- **Repro:** `make transcripts SCENARIO=03_bad_address`

## Conversation

**User:** Book me a ride from 37.7749,-122.4194 (Market & Powell) to 123 Main St.

**Assistant:** I'll need to resolve the dropoff first.


**Assistant:** "123 Main St" is ambiguous (low confidence, multiple matches). Which city/area do you mean? For example, 123 Main St in San Francisco, CA?

**User:** Use the Anytown, CA one — 34.0522,-118.2437.



**Assistant:** Heads up: this is a ~559 km, ~18.5 hour ride. Options:

1. UberX — $952.44
2. UberComfort — $1,400.71
3. UberXL — $1,737.16
4. Uber Black — $2,745.52

Which would you like (or want to reconsider)?

**User:** UberX please — confirm.

**Assistant:** Confirming: UberX, SF → Anytown, CA, **$952.44**, ~18.5 hr ride.




**Assistant:** Booked. Booking ID: **bk-c68e2f6a**.

## Action log

<details><summary>Click to expand</summary>

```json
{"ts": "[redacted-phone]T01:11:32.900891+00:00", "session_id": "01KS41CW2Z0K9Q1Q3W4SM5H715", "turn_id": "01KS41CW39K01P33BEVAG9EBZW", "action_id": "01KS41CY94F844E0725GP90KT5", "kind": "requested", "tool": "geocode_address", "args": {"query": "123 Main St"}, "actor": "agent"}
{"ts": "[redacted-phone]T01:11:32.902301+00:00", "session_id": "01KS41CW2Z0K9Q1Q3W4SM5H715", "action_id": "01KS41CY94F844E0725GP90KT5", "kind": "outcome", "tool": "geocode_address", "status": "ok", "result": {"formatted": "123 Main St, Springfield, IL 62701, USA", "lat": 39.7817, "lng": -89.6501, "confidence": 0.3, "alternates_count": 2}, "error": null}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS41CW2Z0K9Q1Q3W4SM5H715", "turn_id": "01KS41CW39K01P33BEVAG9EBZW", "action_id": "01KS41D45X0YB7678JF5K68XM9", "kind": "requested", "tool": "discover_options", "args": {"pickup_address": "Market & Powell, San Francisco, CA", "pickup_lat": 37.7749, "pickup_lng": -122.4194, "dropoff_address": "123 Main St, Anytown, CA", "dropoff_lat": 34.0522, "dropoff_lng": -118.2437, "party_size": 1}, "actor": "agent"}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS41CW2Z0K9Q1Q3W4SM5H715", "action_id": "01KS41D45X0YB7678JF5K68XM9", "kind": "outcome", "tool": "discover_options", "status": "ok", "result": {"options": [{"option_id": "opt-uberx-[redacted-phone]", "provider": "uber-sim", "title": "UberX \u2014 ~1118 min", "price": {"amount_cents": 95244, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "uberx", "capacity": 4}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "[redacted-phone]T12:05:00Z"}, {"option_id": "opt-comfort-[redacted-phone]", "provider": "uber-sim", "title": "UberComfort \u2014 ~1118 min", "price": {"amount_cents": 140071, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "comfort", "capacity": 4}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "[redacted-phone]T12:05:00Z"}, {"option_id": "opt-uberxl-[redacted-phone]", "provider": "uber-sim", "title": "UberXL \u2014 ~1118 min", "price": {"amount_cents": 173716, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "uberxl", "capacity": 6}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "[redacted-phone]T12:05:00Z"}, {"option_id": "opt-black-[redacted-phone]", "provider": "uber-sim", "title": "Uber Black \u2014 ~1118 min", "price": {"amount_cents": 274552, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "black", "capacity": 4}, "provider_metadata": {"distance_km": 559.121}, "expires_at": "[redacted-phone]T12:05:00Z"}]}, "error": null}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS41CW2Z0K9Q1Q3W4SM5H715", "turn_id": "01KS41CW39K01P33BEVAG9EBZW", "action_id": "01KS41DA5WD83NDQVKJHCEXJZG", "kind": "requested", "tool": "prepare_booking", "args": {"option_id": "opt-uberx-[redacted-phone]"}, "actor": "agent"}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS41CW2Z0K9Q1Q3W4SM5H715", "action_id": "01KS41DA5WD83NDQVKJHCEXJZG", "kind": "outcome", "tool": "prepare_booking", "status": "ok", "result": {"draft_id": "01KS41DA5XF60XH23E85XS197C", "fingerprint": "005fb80b2c76b04d239bad[redacted-phone]f4d58aa677cec0274f6a84f08cedb4ea82", "summary": "UberX \u2014 ~1118 min", "price_cents": 95244, "expires_at": "[redacted-phone]T12:03:00+00:00"}, "error": null}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS41CW2Z0K9Q1Q3W4SM5H715", "turn_id": "01KS41CW39K01P33BEVAG9EBZW", "action_id": "01KS41DC93217XPTR7Q9HHMS44", "kind": "requested", "tool": "confirm_booking", "args": {"draft_id": "01KS41DA5XF60XH23E85XS197C"}, "actor": "agent"}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS41CW2Z0K9Q1Q3W4SM5H715", "action_id": "01KS41DC93217XPTR7Q9HHMS44", "kind": "verified", "tool": "confirm_booking", "checks": {"draft_found": true, "fingerprint_match": true, "requote_price_drift_cents": 0, "confirmation": {"approved": true, "reason": "approved", "approved_at": "[redacted-phone]T12:00:00Z", "approver": "scripted"}}, "result": "ok"}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS41CW2Z0K9Q1Q3W4SM5H715", "action_id": "01KS41DC93217XPTR7Q9HHMS44", "kind": "executed", "tool": "confirm_booking", "provider": "uber-sim", "method": "confirm", "idempotency_key": "c68e2f6ae9dfbe050cc6cda2ca53db51ea706b4c58fc39e9a505b9d499a3f889", "request": {"draft_id": "01KS41DA5XF60XH23E85XS197C"}, "latency_ms": 0, "replayed": false}
{"ts": "[redacted-phone]T12:00:00+00:00", "session_id": "01KS41CW2Z0K9Q1Q3W4SM5H715", "action_id": "01KS41DC93217XPTR7Q9HHMS44", "kind": "outcome", "tool": "confirm_booking", "status": "confirmed", "result": {"booking_id": "bk-c68e2f6a"}, "error": null}
```

</details>

## Invariants

- [PASS] `action_log_contains` where={'kind': 'outcome', 'status': 'confirmed'}
- [PASS] `four_row_pattern` tool=confirm_booking
- [PASS] `no_double_booking`
- [PASS] `fingerprint_verified_before_execute` tool=confirm_booking
- [PASS] `gate_invoked_for_tool` tool=confirm_booking
