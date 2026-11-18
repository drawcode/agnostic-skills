# Gameplay verification: install

Verify a gameplay fix by driving a live round into the failing state and measuring the change,
instead of reasoning that it compiles and looks right. The rules: [`SKILL.md`](./SKILL.md). No
scripts, no dependencies, any engine.

## Install

1. Put the `gameplay-verification` folder where your harness looks for skills.
   - Claude Code: `~/.claude/skills/gameplay-verification`, or `<repo>/.claude/skills/gameplay-verification`.
   - Codex, opencode: `~/.agents/skills/gameplay-verification`, or the harness's own skills folder.
   - Other harnesses: any fixed folder, with a pointer to its `SKILL.md` in your always-loaded
     rules (`AGENTS.md`, `GEMINI.md`, and so on).
2. Write your add-ons. The base knows no engine and no game, so it tells the agent what to check,
   not how to reach it in your project. Two layers work well, each a file in `addons/` beside
   `SKILL.md`; the agent reads the base, then the engine add-on, then the game add-on, and the
   later file wins on conflict.

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

3. Tell the agent where the add-ons are if your harness does not load them beside the skill:
   "before reporting a gameplay fix, read `<skill>/SKILL.md`, then `<skill>/addons/<engine>.addon.md`,
   then `<skill>/addons/<game>.addon.md`".

Pairs with a device-driving skill (for example `device-testing`) when the round has to run on a
phone: one verifies on the device, this one verifies the gameplay. To remove it, delete the folder.
