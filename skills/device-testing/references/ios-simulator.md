# iOS simulator plugin harness

`scripts/ios-sim-harness.py` builds a throwaway UIKit app around one or more `.mm` plugin files and
drives it on a simulator. No Unity, no `.xcodeproj`, no signing. It knows no app or project.

## When it is the right tool

- The code under test imports only `Foundation` and `UIKit` and has an `extern "C"` entry point or a
  `+load`. Minutes per cycle against hours for a Unity export.
- You need dark/light flips, the software keyboard, rotation or real touches (`idb`).
- Not for the full app (vendor binaries, AR, camera, perf): use a real iPhone via `scripts/device.py`.

## Commands

Global options before the command: `--name N` (scratch dir, default `harness`), `--out DIR`
(default `<temp dir>/ios-sim-harness-out/<name>`), `--udid U`, `--label L` (subfolder per arm or run).

| Command | Does |
|---|---|
| `build --main m.mm [--plugin f.mm]... --bundle-id ID` | `clang++ -DDEBUG=1`, hand-made `.app`, ad-hoc signed; prints the path. `--framework F`, `--define X`, `--plist`, `--min-ios`, `--no-arc` |
| `build ... --ab --plugin-b g.mm --bundle-id-b ID2` | also builds `<name>B.app` with another id and plugin set |
| `boot [--device "iPhone 17"]` | boots or reuses a simulator, hardware keyboard off, prints the udid |
| `install <app>` | uninstall then install |
| `launch <bundle> [--env K=V]...` | env sent as `SIMCTL_CHILD_K`, console to `<label>/logs/console.log` |
| `appearance dark\|light`, `terminate <bundle>` | |
| `shot <name>` | `<label>/screenshots/<name>.png` |
| `record start [name]` / `record stop` | `<label>/video/<name>.mov` |
| `idb tap X Y [--duration S]`, `swipe`, `rotate`, `describe`, `text`, `key`, `button`, `raw ...` | starts `idb_companion` and connects as needed |
| `run <app> --bundle-id ID [--env K=V]... [--secs N]` | boot, light shot, install, record, launch, dark and light shots, stop |
| `shutdown` | shuts the simulator and companion down |

Arm-specific steps (gate arms, which buttons to tap) belong in the caller: pass the arm as
`--env`, then script `idb tap` and `shot` calls around `launch`.

## Traps the script already handles

- Links with `clang++`, and always passes `-DDEBUG=1`.
- Turns the hardware keyboard off, or the software keyboard never shows.
- `simctl io` cannot write to a removable volume (TCC 513): captures are staged under `$TMPDIR` and
  copied across, then the staged copy is deleted.
- `idb` installed with `pip3 install --user` lands in `~/Library/Python/<version>/bin`, which the script puts on `PATH` itself.

## Traps it does not

- Missing frameworks fail at link time (`SFSafariViewController` undefined): add `--framework`.
- Two versions of one plugin cannot share a binary; use `--ab` and read fresh coordinates per app
  (`idb describe`), since label heights differ.
- A tap exits 0 even when it hits the wrong thing. Verify with a screenshot.
- Assert the precondition (a dark write really happened) or a no-op A/B reads as a match.

## Worked example

```bash
H=<skill dir>/scripts/ios-sim-harness.py
OUT=${TMPDIR:-/tmp}/ios-sim-harness-out/kbd           # the default --out for --name kbd
SRC=<dir with main.mm and Plugin.mm>
$H --name kbd build --main $SRC/main.mm --plugin $SRC/Plugin.mm --framework SafariServices \
   --bundle-id com.example.kbd --app-name Harness --ab --bundle-id-b com.example.kbd.b
$H --name kbd --label arm-base run "$OUT"/build/Harness.app \
   --bundle-id com.example.kbd --env HARNESS_ARM=base --env HARNESS_AUTOPLAY=1
$H --name kbd --label arm-base idb describe        # find button frames
$H --name kbd --label arm-base idb tap 201 361
$H --name kbd --label arm-base shot after-tap
$H --name kbd shutdown
```
