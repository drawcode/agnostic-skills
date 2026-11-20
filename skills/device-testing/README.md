# device-testing

See [SKILL.md](./SKILL.md). Subagent profile: [agents/device-runner.md](./agents/device-runner.md).

## Index

| Script | What it does |
|---|---|
| `scripts/device.py` | The `device` CLI: one command set for an attached Android phone or iPhone |
| `scripts/ios-sim-harness.py` | iOS simulator harness |
| `scripts/device_tap_nudge.py` | PostToolUse (Bash) hook: nudges toward a plan or the device-runner after N single device inputs in a row |
| `scripts/device_testing/ios_recorder.py` | iOS screen recording helper used by `device.py record` |
