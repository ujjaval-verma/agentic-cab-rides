# Transcript — 04_surge_on_confirm

- **Seed:** 7
- **Repro:** `make transcripts SCENARIO=04_surge_on_confirm`

## Conversation

**User:** Book me a ride from 37.7749,-122.4194 (Market & Powell) to 37.7849,-122.4094 (Union Square).



**Assistant:** Options:
- UberX — $4.20, ~2 min
- UberComfort — $6.25, ~2 min
- UberXL — $8.01, ~2 min
- Uber Black — $12.36, ~2 min

Which one?

**User:** UberX please — go ahead and confirm.

**Assistant:** UberX is $4.20, ~2 min ETA. Preparing now.




**Assistant:** Price changed: UberX is now **$12.60** (up from $4.20). Want me to confirm at the new price?

## Action log

<details><summary>Click to expand</summary>

```json
{"ts": "[redacted-phone]T10:00:00+00:00", "session_id": "01KS5A77FAQ1PBJTQF29WQ5NE6", "turn_id": "01KS5A77FN3FN49CEES2VPYTBS", "action_id": "01KS5A7APB1QSJXW4P21ZQQ3QV", "kind": "requested", "tool": "discover_options", "args": {"pickup_address": "[redacted-address]", "pickup_lat": 37.7749, "pickup_lng": -122.4194, "dropoff_address": "[redacted-address]", "dropoff_lat": 37.7849, "dropoff_lng": -122.4094, "party_size": 1}, "actor": "agent"}
{"ts": "[redacted-phone]T10:00:00+00:00", "session_id": "01KS5A77FAQ1PBJTQF29WQ5NE6", "action_id": "01KS5A7APB1QSJXW4P21ZQQ3QV", "kind": "outcome", "tool": "discover_options", "status": "ok", "result": {"options": [{"option_id": "opt-uberx-[redacted-phone]", "provider": "uber-sim", "title": "UberX \u2014 ~2 min", "price": {"amount_cents": 420, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "uberx", "capacity": 4}, "provider_metadata": {"distance_km": 1.417}, "expires_at": "[redacted-phone]T10:05:00Z"}, {"option_id": "opt-comfort-[redacted-phone]", "provider": "uber-sim", "title": "UberComfort \u2014 ~2 min", "price": {"amount_cents": 625, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "comfort", "capacity": 4}, "provider_metadata": {"distance_km": 1.417}, "expires_at": "[redacted-phone]T10:05:00Z"}, {"option_id": "opt-uberxl-[redacted-phone]", "provider": "uber-sim", "title": "UberXL \u2014 ~2 min", "price": {"amount_cents": 801, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "uberxl", "capacity": 6}, "provider_metadata": {"distance_km": 1.417}, "expires_at": "[redacted-phone]T10:05:00Z"}, {"option_id": "opt-black-[redacted-phone]", "provider": "uber-sim", "title": "Uber Black \u2014 ~2 min", "price": {"amount_cents": 1236, "currency": "USD"}, "eta_seconds": 180, "when": null, "details": {"kind": "ride", "vehicle_class": "black", "capacity": 4}, "provider_metadata": {"distance_km": 1.417}, "expires_at": "[redacted-phone]T10:05:00Z"}]}, "error": null}
{"ts": "[redacted-phone]T10:00:00+00:00", "session_id": "01KS5A77FAQ1PBJTQF29WQ5NE6", "turn_id": "01KS5A77FN3FN49CEES2VPYTBS", "action_id": "01KS5A7FCMM7225Q5G2R3BCHYT", "kind": "requested", "tool": "prepare_booking", "args": {"option_id": "opt-uberx-[redacted-phone]"}, "actor": "agent"}
{"ts": "[redacted-phone]T10:00:00+00:00", "session_id": "01KS5A77FAQ1PBJTQF29WQ5NE6", "action_id": "01KS5A7FCMM7225Q5G2R3BCHYT", "kind": "outcome", "tool": "prepare_booking", "status": "ok", "result": {"draft_id": "01KS5A7FCN1SY3EZJ2RFHQV5ZP", "fingerprint": "5e7df5c8936c64bddaf1beb5f147c2c086b5c40c1165d3389f4f68aef948b5bd", "summary": "UberX \u2014 ~2 min", "price_cents": 420, "expires_at": "[redacted-phone]T10:03:00+00:00"}, "error": null}
{"ts": "[redacted-phone]T10:00:00+00:00", "session_id": "01KS5A77FAQ1PBJTQF29WQ5NE6", "turn_id": "01KS5A77FN3FN49CEES2VPYTBS", "action_id": "01KS5A7JEC54N13VYNFC2ZEER8", "kind": "requested", "tool": "confirm_booking", "args": {"draft_id": "01KS5A7FCN1SY3EZJ2RFHQV5ZP"}, "actor": "agent"}
{"ts": "[redacted-phone]T10:00:00+00:00", "session_id": "01KS5A77FAQ1PBJTQF29WQ5NE6", "action_id": "01KS5A7JEC54N13VYNFC2ZEER8", "kind": "verified", "tool": "confirm_booking", "checks": {"draft_found": true, "fingerprint_match": true, "requote_price_drift_cents": 840, "new_draft_id": "01KS5A7JEDY8F8TK8V0ZNV7SW5"}, "result": "rejected", "reason": "price_changed"}
{"ts": "[redacted-phone]T10:00:00+00:00", "session_id": "01KS5A77FAQ1PBJTQF29WQ5NE6", "action_id": "01KS5A7JEC54N13VYNFC2ZEER8", "kind": "outcome", "tool": "confirm_booking", "status": "price_changed", "result": {"new_draft_id": "01KS5A7JEDY8F8TK8V0ZNV7SW5", "new_price_cents": 1260}, "error": null}
```

</details>

## Invariants

- [PASS] `price_changed_rejected` tool=confirm_booking
- [PASS] `no_executed_after_price_changed` tool=confirm_booking
- [PASS] `no_double_booking`
