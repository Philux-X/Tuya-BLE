# YR05/H13 Development Handoff

This document captures the current known-good YR05/H13 local BLE lock control work so a fresh session can resume safely. Treat `yr05-known-good-local-control` at commit `bd142a0777ad1720b618c6ff1b53bff48bbe58bf` as the recovery baseline.

## Confirmed Observations

### Hardware And Product Identity

- Device name: YR05
- Model: H13
- Tuya category: `jtmspro`
- Product ID: `hhxgpozj`

### Relevant Datapoints

- DP8: battery
- DP12: fingerprint event
- DP13: PIN/password event
- DP19: BLE unlock event
- DP46: lock command
- DP47: physical cylinder state
- DP71: authenticated BLE unlock
- DP101: Passage Mode

### Physical Behavior

- Locked means the physical cylinder is secured.
- Unlocked means the physical cylinder is released.
- Passage Mode keeps the lock normally open, but it is not the source of truth for lock state.
- DP47 is authoritative for physical lock state. Do not infer physical state from DP101.
- On Home Assistant restart, DP47 may not arrive immediately, so the entity can start as Unknown. That should remain Unknown until physical state evidence arrives.

### DP71 Authenticated Unlock

- The DP71 request is built from private runtime `ble_unlock_check` data.
- The central and peripheral IDs are reversed from the reference data.
- A fresh timestamp is generated for each outbound request.
- The exact outbound transaction is retained in memory.
- The response must match the outbound IDs, random value, operation, timestamp, and method.
- Result `0x00` means the lock accepted authentication.
- Authentication acceptance alone is not physical success. DP47 remains the final confirmation that the lock actually unlocked.
- Do not weaken the exact request/response validation to improve apparent reliability.

### Current Working Home Assistant Features

- Local lock using DP46.
- Authenticated local unlock using DP71.
- Battery reporting using DP8.
- Passage Mode switch using DP101.
- Physical lock-state updates using DP47.
- YR05-only idle BLE disconnect and reconnect-on-command lifecycle.

### Confirmed Hardware Observations: Idle Disconnect Lifecycle

- Startup connects and authenticates normally.
- Startup schedules the 30-second idle release after the initial successful update.
- Intentional idle disconnect works.
- The Home Assistant lock entity remains available while intentionally disconnected.
- The phone app can connect while Home Assistant is idle.
- Home Assistant Lock reconnects on demand and succeeds through DP46 with DP47 reporting `False`.
- A pending idle timer is cancelled when another Home Assistant command arrives.
- Home Assistant Unlock succeeds through authenticated DP71 with result `0x00` and DP47 reporting `True`.
- A new idle timer is scheduled after command completion.
- Intentional disconnect occurs cleanly about 30 seconds later.
- DP19 value `1` was observed after the successful BLE unlock.

### Current Transport State

- Existing A201/FD50 support is present.
- FD50/Raykube support existed before the YR05 baseline and should not be conflated with YR05 changes.
- Preserve FD50/Raykube behavior unless intentionally working on that transport path.

## Security And Privacy

- `devices.json` is runtime-only and private.
- Never commit `devices.json`.
- Never commit `local_key`, actual `ble_unlock_check` values, device UUIDs, addresses, IDs, or other private runtime credentials.
- Avoid adding new logs that include full sensitive DP71 transaction payloads.
- The preserved `yr05-known-good-local-control` baseline tag contains a debug log of the full DP71 payload. Current development hardens DP71 logging; keep treating DP71 transaction material as sensitive.

## Main Unresolved Engineering Problem

The original problem was that Home Assistant held the BLE connection continuously. While HA owned the connection, the phone app could not connect to the lock, and there was possible battery impact. The current YR05-only idle-disconnect experiment has been hardware-tested for basic lock/unlock and phone coexistence, but longer-term battery behavior, missed-event behavior, and reconnect robustness still need observation.

## Open Questions And Hypotheses

These items are not yet confirmed and should be tested with logs or controlled experiments.

- Advertisement and wake behavior after the lock has slept.
- Whether pending datapoints are retained while HA is disconnected.
- Whether physical events are recovered after reconnect.
- How the official Tuya gateway manages connection ownership and idle disconnect.
- Appropriate idle disconnect timeout for reliable commands, phone coexistence, and battery life.
- Whether startup Unknown state can be improved without inventing state or misusing DP101.

## Development Roadmap

1. Preserve the known-good baseline.
2. Add durable documentation.
3. Harden sensitive logging.
4. Inspect BLE lifecycle architecture.
5. Design the smallest safe connect-on-demand change.
6. Test phone coexistence, reconnects, failures, and battery behavior.
7. Clean up, refactor, add tests/docs, and assess an upstream pull request.

## Safety Rules For Future Work

- Be conservative with commands that can physically actuate the lock.
- Prefer observation and logs over assumptions.
- Use small reversible Git milestones.
- Do not force-push or modify `master`/`upstream` without explicit instruction.
