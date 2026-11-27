# Install gameplay-verification

Install adds one folder and two add-on files you write. It changes nothing else. No scripts, no dependencies, any engine. The rules: [`SKILL.md`](./SKILL.md).

## Requirements

- A harness that loads skills, or one that reads an always-loaded rules file.
- Nothing else: no scripts, no dependencies, any engine.

## Install

1. Put the `gameplay-verification` folder where your harness looks for skills (paths below).
2. Write the engine add-on in `addons/` beside `SKILL.md` (template below).
3. Write the game add-on in the same folder (template below).
4. If your harness does not load add-ons beside the skill, tell the agent where they are:

   ```text
   before reporting a gameplay fix, read <skill>/SKILL.md, then <skill>/addons/<engine>.addon.md, then <skill>/addons/<game>.addon.md
   ```

The agent reads the base, then the engine add-on, then the game add-on. The later file wins on conflict.

## Check it works

Ask the agent to report a gameplay fix. It should read `SKILL.md` and both add-ons first.

## Uninstall

Delete the folder.

<details>
<summary><b>Per-harness locations</b></summary>

- Claude Code: `~/.claude/skills/gameplay-verification`, or `<repo>/.claude/skills/gameplay-verification`.
- Codex, opencode: `~/.agents/skills/gameplay-verification`, or the harness's own skills folder.
- Other harnesses: any fixed folder, with a pointer to its `SKILL.md` in your always-loaded rules (`AGENTS.md`, `GEMINI.md`, and so on).

</details>

<details>
<summary><b>Why add-ons</b></summary>

The base knows no engine and no game, so it tells the agent what to check, not how to reach it in your project. Two layers work well, each a file in `addons/` beside `SKILL.md`.

</details>

<details>
<summary><b>Engine add-on template</b></summary>

```markdown
---
name: gameplay-verification-<engine>-addon
description: How gameplay probes run in <engine>.
metadata:
  type: skill-addon
  extends: ../SKILL.md
  base_version: <date you checked the base>
---

# Gameplay verification: <engine>

Read ../SKILL.md first; this file wins on conflict.

- **Probe channel:** how to run code in the live game (a REPL, an editor eval, a debug console,
  a dev-build HTTP endpoint) and its compile or parse traps.
- **Play that keeps ticking:** entering play mode, running unfocused, stepping frames.
- **Frame hooks:** where a per-frame recorder registers, and a store that survives across calls.
- **Input injection:** keys, gamepad, touch and UI clicks without hardware.
- **Rendering traps:** what culls or skips work when no camera draws.
```

</details>

<details>
<summary><b>Game add-on template</b></summary>

```markdown
---
name: gameplay-verification-<game>-addon
description: Reaching and measuring a live round of <game>.
metadata:
  type: skill-addon
  extends: ../SKILL.md
  base_version: <date you checked the base>
---

# Gameplay verification: <game>

Read ../SKILL.md and the engine add-on first; this file wins on conflict.

- **Start a round:** the real UI route, and the scripted call chain with what it skips.
- **"Playing" flag:** the one reading that is only true mid-round.
- **Mode:** how it is set, and how to read it back.
- **Save:** where it lives, which values a round overwrites, which are live settings never restored.
- **Event bus and HUD:** how to listen for who changed what, and how to reach the controls.
```

</details>

<details>
<summary><b>Related skills</b></summary>

Pairs with a device-driving skill (for example `device-testing`) when the round has to run on a phone: one verifies on the device, this one verifies the gameplay.

</details>
