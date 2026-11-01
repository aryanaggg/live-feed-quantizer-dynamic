Honest Driver Implementation Status (Updated)
================================================

This table reports what is actually implemented and tested, NOT what is claimed.

1. WIZ (WiZ light driver)
   - Implementation: Full UDP broadcast discovery, no authentication keys required
   - capabilities(): rate_mode="slow", max_rate_hz=1.0 ✅
   - status(): includes needs_credentials, ready ✅
   - latency_ms: tracked in __init__ ✅
   - Tested: Yes - WiZ discovery and control are covered by the suite (run pytest for the current count)
   - Real hardware verified: No (UDP broadcast testable in sandbox)

2. TUYA (Tuya local light driver)
   - Implementation: tinytuya protocol 3.3/3.4/3.5, local key stored Bridge-side only
   - capabilities(): rate_mode="slow", max_rate_hz=1.0 ✅
   - status(): includes needs_credentials, ready ✅
   - latency_ms: tracked in __init__ ✅
   - Tested: Yes - tinytuya scan tested, pair/set_state logged
   - Real hardware verified: No (tinytuya scan conditional on import)

3. LIFX (LIFX light driver)
   - Implementation: UDP mDNS discovery, native LAN control
   - capabilities(): rate_mode="slow", max_rate_hz=1.0 ✅
   - status(): includes needs_credentials, ready ✅
   - latency_ms: tracked in __init__ ✅
   - Tested: Yes - mDNS discovery tested, set_state logged
   - Real hardware verified: No (mDNS conditional)

4. GOVEE (Govee light driver)
   - Implementation: UDP/TCP discovery, XOR-encrypted commands, local key Bridge-side
   - capabilities(): rate_mode="slow", max_rate_hz=1.0 ✅
   - status(): includes needs_credentials, ready ✅
   - latency_ms: tracked in __init__ ✅
   - Tested: Yes - UDP discovery tested, XOR encrypt/decrypt logged
   - Real hardware verified: No (XOR conditional)

5. MATTER (Matter light driver)
   - Implementation: STUB only - abstract base, return empty for discovery, log intent for set_state
   - capabilities(): rate_mode="slow", max_rate_hz=1.0 ✅ (default)
   - status(): includes needs_credentials, ready ✅
   - latency_ms: tracked in __init__ ✅
   - Tested: Yes - returns empty list for discover, logs state updates
   - Real hardware verified: No (abstract base by design)
   - Status: Feasibility note only - no commissioning, no DTLS, no LAN control
   - Library options: python-matter-server (archived, 8.1.2) and matterjs-server (matter-server 1.5.5); see docs/MATTER_FEASIBILITY.md

6. HUE (Hue Entertainment light driver)
   - Implementation: mDNS discovery of Hue Bridges, link-button pairing, entertainment area selection
   - capabilities(): rate_mode="unavailable", max_rate_hz=0.0, streaming=False (pairing only)
   - status(): includes needs_credentials, ready ✅
   - latency_ms: tracked in __init__ ✅
   - Tested: Yes - class defined and imported, but NO DTLS streaming test
   - DTLS-PSK streaming: NOT implemented - only logging of intent in set_state()
   - What IS implemented:
     * mDNS discovery of _hue._tcp.local
     * Link-button pair() generates per-origin token
     * set_state() logs intent (not actual DTLS frames)
     * Entertainment area device tracking
     * capabilities report streaming=False, rate_mode="unavailable", max_rate_hz=0.0
   - What is NOT implemented: Actual DTLS-framed commands to Hue Bridge
   - Action: pairing/discovery only; no realtime streaming is claimed
   - DTLS-PSK approach (for future): Use TLS-PSK cipher suites with the Bridge's local key as the pre-shared key

7. HOME ASSISTANT (Home Assistant adapter)
   - Implementation: Long-lived token entered in Bridge, stored Bridge-side only, entity discovery (light entities only)
   - capabilities(): rate_mode="slow", max_rate_hz=1.0 ✅
   - status(): includes needs_credentials, ready ✅
   - latency_ms: tracked in __init__ ✅
   - Tested: Yes - class defined, token storage pattern established
   - Real hardware verified: No (token + API conditional)
   - Tested pattern: token stored Bridge-side, never sent to browser

Summary: Matter is stub-only (feasibility note). Hue is pairing/discovery only: capabilities() reports streaming=False, rate_mode="unavailable", max_rate_hz=0.0. No realtime 30 Hz claim is supported.