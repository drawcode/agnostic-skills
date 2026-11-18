---
name: gameplay-verification
description: Use before reporting any gameplay change as fixed, when reproducing a bug report from a device or a player, and whenever a probe is about to touch a save or a shared editor. Compiling is not verifying -- drive a live round into the failing state and show the measurement changed. Covers reaching a real round (and what a scripted shortcut skips), probing state rather than screenshots, measuring on screen before believing a diagnosis, sampling rates and sub-second effects from inside the frame, hitting a race window through an existing callback, keeping a combat target alive and reachable, protecting the player's save (and never restoring a live setting another session edits), working in an editor other sessions share, and reporting what was and was not exercised. Engine-agnostic; engine and game specifics live in add-ons.
allowed-tools:
  - Bash
  - Read
---

# Gameplay verification

**"It compiles and the logic is right" is not verification.** A gameplay fix is verified when a
live round has been driven into the failing state and the measurement changed. Pin a reproduction
first (a probe that shows the bug), then fix, then run the same probe and show it moved.

Engine transport (how to run code in the live game, enter play, read the console) and game
specifics (how to start a round, where the save lives) come from add-ons; see
[Add-ons](#add-ons).

## 1. Reach a real round

- **Confirm with the reading that means "playing".** Many flags are also true at the menu or
  while loading. Find the one that is only true mid-round and read it, every time.
- **A slow boot is not a broken build.** A cold cache or a content sync can hold the loader for a
  long time. Poll for progress (scene, panel count, frame count) and retry before concluding.
- **A scripted shortcut skips what the real flow sets up.** Starting a round from code instead
  of the menu commonly leaves out: the player-controlled actor, the game mode (without which the
  round may never end), input routing (touch sticks or pads that only reach a fully built
  player), and the cameras (the world may not draw). List what the real flow does that the
  shortcut does not, check each one, and name the gaps in the report.
- **A round that cannot end proves nothing about ending.** Any claim about game over, results,
  timers or score submission needs a round whose mode is set, read back before the claim.
- **Rounds expire while you think.** Re-read the "playing" flag in the same call as any sample
  you mean to trust; a reading taken after the round ended is the menu.

## 2. Protect the save before probing

- A half-booted session holds **defaults**, not the player's save, and anything that saves then
  writes those defaults over the real file. Before any probe that may write, compare one or two
  saved values in memory against the file on disk; defaults where the disk differs means the save
  has not loaded, so write nothing.
- **Take a fresh backup per session** of the whole save folder. A reused backup is stale by
  definition.
- **A backup is safe for a save the round overwrites** (level, counters, statistics) and
  **dangerous for a live setting another session may be editing** (language, accessibility,
  anything a parallel session is repairing). A byte-exact restore reverts whatever anyone changed
  since you took it. Exclude live settings from every restore; to probe another value of one,
  set it through the game's API and set it back.
- **Diff before restoring and copy back only what this session changed.** A difference your
  probe cannot explain is probably someone else's: leave it and say so.
- To tell whether your restore changed a live setting, compare three things: the value in your
  backup, the value live now, and the live file's modified time against when you restored.
- Before blaming the game for "reverting" a value, **check your own restores first**.

## 3. Probe state, call the code

- **Call the method under test directly.** Hit tests, setup methods and change handlers can be
  invoked from a probe; you do not need to synthesize input to test the logic inside them.
- **Return one string per probe** and build state across calls: create in one call, inspect in
  the next.
- **Prove a value came from disk** by making the load path replace the object, then reading it.
  A static or cached object that survives a play restart proves nothing about loading.
- **Is a flag latched across rounds?** Set it, run the transition, read it back.
- **What is under a point?** List every hit along the ray, sorted by distance, never only the
  nearest; the nearest hides everything behind it.
- **Injected input needs an A/B.** Inject the raw control, read the effect in a later call, then
  disable input and inject again: the same injection must now do nothing. Always run a control
  case; a "pass" where the control also passes means the probe measured something else.
- **Who changed it?** Subscribe to the game's event bus from a probe and log each event with the
  frame number, then line the frames up against the state you poll.

## 4. Measure before believing a diagnosis

- **For anything positional, measure on screen.** Authored sizes and centres say nothing about
  where a thing lands after anchors, parent scales and the aspect ratio. A root cause reasoned
  from asset files is a hypothesis until one probe confirms it.
- **Check the thing is on**: active in the hierarchy and enabled, not just present. An object
  that exists in the asset may be inactive at runtime.
- **Rate, not presence.** "The gate passes" says nothing about how often. Count changes of the
  component's own timer from inside the frame loop, deduplicated per frame, and divide by frames
  or time. Sample at two framerates before calling a rate framerate-independent.
- **Watch the instance under test.** When every component carries its own timer or state under
  the same key, a global singleton with that key is a trap: it never moves, and that reads as "never fires".
- **Sub-second effects cannot be polled.** A flash, pulse or fade shorter than a round trip
  needs a recorder inside the frame that keeps max/first/last; prove the recorder ticks (a
  counter) before trusting a zero.
- **Measure allocations with the engine's profiler counters**, plant a known allocation once to
  prove the counter counts, and compare A against B under the same polling, since the probe
  allocates too.
- **A race inside a coroutine:** find a callback the coroutine already raises at the right moment
  and inject through a one-shot handler (guard it and unsubscribe it, or it re-arms every pass).
  Then read a state field that records which request was serviced. No production code added for
  testability, and the test repeats instead of depending on timing.
- **A lifecycle claim needs a state field, not a log.** Logging is often off during boot. A
  `lastXReason` / `lastXFrame` pair on the class makes "this never happened during boot" checkable in one probe.

## 5. Combat and actors

A scripted player stands still and dies; a scripted enemy may regenerate faster than a round trip
lands hits, and pooled actors get reused between calls.

- Re-resolve the target every call and confirm its identity, not just non-null.
- Pin the player (health or the damage path) so the round does not end mid-measurement.
- Apply the hits in one call, not one per call; disable regeneration if needed.
- Moving an actor: set it through whatever owns its position (a navigation agent pulls a bare
  transform write back within a frame).
- Forcing a speed band: re-arm the controller's speed from successive calls rather than disabling
  the controller, which stops driving the character and can end the round.
- **Disclose every one of those in the report.** "Dies in 4 hits" measured with regeneration off
  and the player pinned is a statement about the damage numbers, not the game as played.

## 6. State is not a screenshot

A scripted round may not enable the gameplay cameras. Then "actors move", "the animation
advances", "the effect is off" are **state readings**: real evidence about state and zero evidence
about what is on screen.

- Say "verified by state, not seen" in the report.
- Anything visual (layout, effects, fades, culling, render order) needs the real UI route or a
  capture. An empty frame on a route with no camera is expected, not a negative result.
- Rendering-dependent state lies off camera: animation weights and times can read zero for a
  culled, healthy actor. Ask "is it playing", not "is its weight above zero".

## 7. A shared editor or live tree

When other sessions build or commit from the same editor or working tree:

1. **Ask first.** Name the repos, files and time; wait for each peer to agree to hold builds and
   commits until you say "restored".
2. Check the editor is idle (not playing, compiling or building) and each repo is on the commit
   you expect with nothing of yours uncommitted.
3. **Fresh save backup and a baseline test run before anything changes.** A remembered test
   count is not a baseline.
4. Copy your change in, compile, prove the new symbol is really in the build, then test.
5. **Restore by path, never by switching branches**, then prove the symbol is gone and the
   compile is clean. Send "restored".

## 8. Leave it as you found it, and report

- Destroy anything the probe created, remove injected devices, unsubscribe listeners.
- Restore the save per §2, restore every value you wrote, and confirm each restore.
- Stop play mode and confirm it stopped.
- **Report what was and was not exercised, in those words**: the route (real UI or scripted), the
  mode, what was pinned or disabled, and "verified by state, not seen" where that applies.

## Add-ons

This base knows no engine and no game. Read it first, then any add-ons, in order; **an add-on
wins on conflict**:

- an **engine add-on**: how to run code in the live game, compile traps of the probe channel,
  frame hooks, input injection, render-a-probe-camera, the engine's animation and UI traps;
- a **game add-on**: the call chain that starts a round, the "playing" flag, how the mode is set,
  where the save lives and which keys are live settings, the game's event bus and HUD.

An add-on records only the delta and cites this file for the rest.
