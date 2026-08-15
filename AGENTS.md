# Codex Operating Notes

This repository contains a preserved known-good YR05/H13 local BLE lock control baseline. Follow these rules when working here.

- Preserve known-good YR05 behavior unless the user explicitly asks to change it.
- Baseline recovery point: annotated tag `yr05-known-good-local-control` at commit `bd142a0777ad1720b618c6ff1b53bff48bbe58bf`.
- DP46 is the YR05 lock command.
- DP47 is the physical lock-state authority.
- Never infer lock state from DP101 Passage Mode.
- DP71 authenticated unlock must preserve exact request/response transaction validation.
- DP71 result `0x00` indicates authentication acceptance, but physical success still requires DP47 confirmation.
- Do not weaken DP71 validation just to improve apparent reliability.
- Preserve existing FD50/Raykube support unless intentionally working on it.
- Never commit `devices.json`.
- Never commit `local_key`, `ble_unlock_check` values, device UUIDs, addresses, IDs, or other private runtime credentials.
- Avoid logging full sensitive DP71 transaction payloads in new code.
- Be conservative with commands that can physically actuate the lock.
- Prefer observation/log evidence over assumptions.
- Use small reversible Git milestones.
- Do not force-push or modify `master`/`upstream` without explicit instruction.
