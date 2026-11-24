# Writing agnostic skills

How the skills in this repo are built, so you can write your own the same way: one folder that
works in any harness, on any OS, for anyone, with everything personal kept out of it. It pairs
with the [`deterministic-scripts`](skills/deterministic-scripts/) skill, which does the scripting
and scanning half.

## 1. What makes a skill agnostic

A skill is a folder: `SKILL.md` (what the agent reads), `INSTALL.md` (what a person follows),
and `scripts/` (stdlib `python3`). It is agnostic when nothing in it depends on who runs it:

| Never in a skill | Put it here instead |
|---|---|
| Your name, email, user name, host name, home path | Nowhere: scripts discover what they need at run time |
| Device serials, IPs, team ids, bundle ids, project names | The skill's config file, or an add-on (section 3) |
| Tokens, passwords, API keys, cookies, signing details | A home config file only scripts read (section 5) |
| One project's screens, folders, build steps | An add-on |
| Harness-only syntax as the only way in | Plain markdown plus scripts any harness can call |

Write scripts in stdlib `python3` so they run the same on macOS, Linux and Windows: `pathlib`,
`subprocess.run([...])` with a list (never `shell=True`), `tempfile` for temp paths, and
`encoding="utf-8"` on every open. Aim at the oldest python3 a machine ships (3.9 on macOS). A shell
script is the exception, used only when nothing else can do the job, and says why in a header line.

## 2. Keep configs out: the lookup order

Every setting a skill needs is looked up in this order, and the first hit wins:

1. An environment variable named after the skill (`VIDEO_TRIM_FPS=30`).
2. The skill's own file, `~/.config/<skill>/config.env` (`%APPDATA%\<skill>\config.env` on
   Windows), one `KEY=value` per line.
3. A built-in default that works for most people.

So no one has to create a config file to start, and the values that do differ per person never
sit in the skill. The skill folder stays read-only: state (logs, caches, captures) goes in the
per-user state folder (`${XDG_STATE_HOME:-~/.local/state}/<skill>`, `%LOCALAPPDATA%` on Windows),
never in your repos and never in the skill.

Ship a `config.example.env` with every key commented out and explained, so a person copies it
instead of guessing.

## 3. Overriding a skill: base plus add-ons

Do not fork a skill to tune it for one project. Keep the **base** generic and write an **add-on**
that records only what differs:

```yaml
---
name: video-trim-myproject-addon
description: What this changes about the base, for this project, in one line
metadata:
  type: skill-addon
  extends: ../video-trim/SKILL.md
  base_version: 2026-11-23    # the base you checked the delta against
---

# video-trim: myproject

- Recordings come from the in-game recorder at 60 fps; pass --fps 60.
- The notification banner sits in the top 90 px on this device; scrub with --region top:90.
```

- **Reading order:** the base first, then the add-on. **The add-on wins on a conflict.**
- **Never restate the base.** Cite it. A copy rots when the base changes.
- **Reuse the base's scripts** and pass the project as an argument or a config key. A script only
  one project needs lives in that project.
- **When the base changes,** re-read every add-on whose `base_version` is older before trusting it.
- Add-ons are private by default. They hold exactly the things the base keeps out, so they never
  ship with the base.

The harness pointer names both files, in order: "for video work, read
`skills/video-trim/SKILL.md`, then `addons/myproject.addon.md`".

## 4. Your own contexts: one portable home, harness files point at it

Keep everything you want agents to know (skills, add-ons, rules, feedback, memory, project
notes) in one folder that you own, and let each harness's own file point into it. That folder
can be:

- a **local folder** (`~/contexts`), for one machine, or
- a **git repo** you clone on every machine, or mount into each project as a **submodule** at
  `contexts/`, so every repo and every machine reads the same copy.

A layout that works:

```text
contexts/
  skills/                 base skills (yours, or vendored from this repo)
    video-trim/
  <area>/skills/addons/   your add-ons, next to the area they tune
  rules/                  standing rules: how you want work done
  feedback/               corrections and confirmed approaches, one note per file
  memory/                 facts agents should keep across sessions
  scripts/                deterministic scripts (section 6)
```

The standard harness files then hold **pointers only**, never copies:

| File | Read by | What it holds |
|---|---|---|
| `AGENTS.md` | Codex, opencode, Cursor and most others | "Read `contexts/rules/core.md`. Skills are in `contexts/skills/`." |
| `CLAUDE.md` | Claude Code | `@contexts/rules/core.md` imports, plus the same pointers |
| `GEMINI.md` | Gemini, Antigravity | The same pointers |
| `MEMORY.md` | Claude Code auto-memory | One line per memory, each naming its file under `contexts/memory/` or `contexts/feedback/` |

Skills are linked, not copied: `.claude/skills/video-trim -> ../../contexts/skills/video-trim`
(a relative symlink), or the harness's skills folder pointed at `contexts/skills/`. Change a skill
once and every harness and machine sees it.

Rules for the pointers:

- Relative paths only (`contexts/skills/x`, `../rules/y.md`), so a fresh clone works anywhere.
- A machine path (where one clone sits) may appear only in a machine-local file such as
  `~/.claude/...`, never in contexts or a repo.
- Moving a file updates every pointer in the same change.
- Keep the always-loaded index small (one line per entry). Detail lives in the files it points to,
  which the agent reads when the topic comes up.

Because the content sits in your own folder, it is not tied to any one harness: switch harness,
add a pointer file, and the same skills and rules load.

**Mixing this repo with your own:** install a base skill from here into `contexts/skills/` (or as
a submodule), put your add-ons beside your areas, and the harness reads base then add-on. You
can update the base from this repo without touching your add-ons.

## 5. Keep auth, configs, PII and secrets away from the agent

An agent reads whatever is in its context and may echo it into a commit, a log or a chat. So
sensitive values should never reach the context in the first place:

1. **Values live in a home config file** outside every repo (`~/.config/<tool>/config.env`,
   `chmod 600`), never in a skill, a prompt, a script, a rules file or an `.env` inside a repo.
2. **Scripts read the file and use the value; they never print it.** An agent that needs a value
   runs the script (`deploy.py --target staging`) instead of reading the file. Output names the key
   (`using API token from config`), never the value. `--json` output follows the same rule.
3. **Prose uses placeholders:** write `<config:api_token>` or `~/...`, never the value or a full home path.
4. **Tell the harness not to read secrets.** Most harnesses have deny rules: in Claude Code,
   `permissions.deny` entries such as `Read(~/.ssh/**)`, `Read(~/.config/<tool>/**)`, `Read(**/.env)`;
   in others, the ignore file the harness supports. Scripts still read them; the agent does not.
5. **Scan before every commit.** A pre-commit hook runs the sensitive-value scan from
   `deterministic-scripts`: home paths, emails, private keys, token shapes (`ghp_`, `sk-`, `AKIA`,
   `xox`, `Bearer`), `password=` style assignments, plus the literal values from your own config, so
   your real name or device id is caught even though no pattern names it. A hit prints the file,
   line and kind, never the value. Do not bypass the hook for a real hit.
6. **Public repos commit under a noreply address.** Set the clone's `user.email` to your
   `<id>+<login>@users.noreply.github.com` address, turn on the host's "keep my email private"
   setting (web-UI merges use your primary email otherwise), and add a pre-push check that refuses
   any other address.
7. **Recordings and screenshots count.** Scrub notification banners and other overlays out of a
   recording before it is analyzed or shared (`video-trim` does this).

## 6. Deterministic scripts do the repeatable work

Anything an agent would do the same way twice (a count, an inventory, a parse, a build recipe,
a check, a value lookup) belongs in a script, not in model tokens. Install
[`deterministic-scripts`](skills/deterministic-scripts/) for the full method. In short:

- **The script contract:** `--help` that explains itself, `--json` for agents, and exit codes
  `0` ok, `1` the check failed, `2` bad input.
- **No personal values in scripts.** They come from arguments, paths relative to the script, or
  the home config reader. That is what lets the same script ship publicly and run privately.
- **The scan, scrub and hook scripts** in that skill are what sections 5.5 and 5.6 run.

## 7. Moving a skill from private to public

If you keep your skills in a private contexts repo and publish some of them:

1. Fix the base first: move every workspace detail into an add-on.
2. Export by manifest: a list of the exact files that ship (add-ons are never listed), copied by
   a script, never by hand.
3. Leak-scan the exported folder and then the whole public repo: home paths, workspace names,
   your own config values, secrets.
4. Clean-room test: copy the skill into an empty HOME, run it with no environment and no
   config, and run its installer's report, install, re-check and uninstall.
5. Decide what stays private even when it scans clean. Workflows about your clients or your
   business can be generic and still not belong in public: keep an internal list the exporter
   refuses, and ask before shipping anything on that topic.

## Checklist for a new skill

- [ ] `SKILL.md` frontmatter: `name`, and a `description` that says when to use it.
- [ ] Scripts in stdlib `python3` (3.9+), each with `--help`, `--json` and exit codes 0/1/2.
- [ ] Settings: env, then `~/.config/<skill>/config.env`, then defaults; `config.example.env` shipped.
- [ ] State outside the skill and outside your repos.
- [ ] No names, emails, paths, devices, ids or secrets; scan passes.
- [ ] Project specifics in an add-on that names its base and `base_version`.
- [ ] `INSTALL.md`: where to put the folder per harness, the tools it needs, and how to uninstall.
- [ ] Hooks fail silent (print nothing, exit 0 on an error) so they never break the harness.
