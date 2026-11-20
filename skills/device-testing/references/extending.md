# Extending: a platform, or a project add-on

The base stays agnostic to app, project, device and platform. Anything specific is one of these.

## A new platform

Simulator, emulator, the Editor, another OS: one `Backend` subclass in
`scripts/device_testing/backends/`, added to `BACKENDS` in its `__init__.py`.

- Implement `discover()` (serial -> model, cheap and quiet) and whichever primitives the platform
  can do; see `backends/base.py` for the list and contracts.
- Leave the rest. A primitive that is not implemented raises `Unsupported`, which the front reports
  as exit 3, "not supported on <platform> yet".
- Commands live in `commands.py` and are written once over the primitives, so a backend never
  re-implements `nav`, `wait` or `resume-check`, and output stays identical everywhere.

## A project add-on

A project gets its own skill that loads this one, with three parts:

1. **A wrapper command** that sets the layers and execs the base:

   ```bash
   : "${DEVICE_TESTING_CONFIG:=$HOME/.config/<project>/config.env}"   # reuse the project's config
   : "${DEVICE_TESTING_KEY_PREFIX:=<PROJECT>_}"                       # its key names
   : "${DEVICE_TESTING_KEYMAP:=APP_ANDROID_PACKAGE=<PROJECT>_APP_PACKAGE,...}"
   : "${DEVICE_TESTING_ADDONS:=$HERE/<project>_addon.py}"
   : "${DEVICE_TESTING_NAME:=<project>-device}"
   export ... PYTHONPATH="<base>/scripts"
   exec <base>/scripts/device.py "$@"
   ```

   Values a placeholder sits in (`{package}`) must not go inside `${VAR:=...}`: the first `}` ends
   the expansion. Assign them with `[ -n "${VAR:-}" ] || VAR='...'`.

2. **An add-on module** defining `register(api)`:

   ```python
   def register(api):
       api.command("dismiss-modal", cmd_dismiss_modal, "clear the app's blocking card")
       api.hook("pre-record", clear_debug_overlay)
   ```

   A command is `fn(ctx, args) -> exit code`. It uses `ctx.device` primitives (`screenshot`, `tap`,
   `key`, `focus`, `size`), `ctx.shot()`, and `ctx.run("nav", [...])`, so it runs on every platform.
   Hooks: `preflight`, `pre-record`.

3. **Profiles**: the app's screen maps, one per screen size or density.

The add-on's `SKILL.md` opens with "load `device-testing` first" and holds only the project's
method: its builds, accounts, dialogs, gates and evidence rules.
