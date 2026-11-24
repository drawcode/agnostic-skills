---
name: config-convention
description: Where personal, device and machine values live (one home-directory config file read by homecfg.py), its format and key naming, the extra scan patterns, and how to point it somewhere else.
---

# Home config convention

One file per machine holds every personal, developer, device and machine value that scripts
need. It is never committed and never copied into a prompt. Scripts read it with `homecfg.py`.

## Resolution (first that is set wins)

1. `--file PATH` on the `homecfg.py` command line
2. `$DETERMINISTIC_SCRIPTS_CONFIG`
3. `$DETERMINISTIC_SCRIPTS_HOME/config.env`
4. Windows: `%APPDATA%\deterministic-scripts\config.env`; elsewhere
   `${XDG_CONFIG_HOME:-~/.config}/deterministic-scripts/config.env`

`homecfg.py path` prints the file in use; `homecfg.py where` also lists the patterns files.
To keep the file somewhere else (a synced private folder, say), set
`DETERMINISTIC_SCRIPTS_HOME` in the shell profile, or make the config folder a symlink.

## Format

```
# config.env   (chmod 600)
dev_name="A. Developer"
dev_email=dev@example.com
device_serial=XXXXXXXX
signing_sha1=0000000000000000000000000000000000000000
credentials_dir=~/.secrets/app      # a PATH to secrets, never the secret itself
pattern_ticket=\bPRJ-\d{4}\b        # a key starting `pattern` is a regex for the scanner
```

- `KEY=value`, `#` comments, optional `export ` prefix and quotes; a leading `~` expands.
- Keys are lowercase snake_case, named for what they hold, not for the script that reads them.
- Secrets stay in their own files or a keychain; config holds the path.
- An empty value counts as missing.

## Extra scan patterns and literals

`scan_sensitive.py` ships generic defaults. Add your own without editing a script:

| Where | What |
|---|---|
| config key `pattern_*` | a regex |
| any other config value (6+ chars) | a literal that may appear nowhere else |
| `$DETERMINISTIC_SCRIPTS_PATTERNS` | regexes, one per line (`REGEX` or `NAME :: REGEX`) |
| `$DETERMINISTIC_SCRIPTS_LITERALS` | literal values, comma separated |
| `patterns.txt` next to `config.env`, files in `$DETERMINISTIC_SCRIPTS_PATTERNS_FILE`, `patterns.local.txt` next to the scripts | one entry per line: `REGEX`, `NAME :: REGEX` or `literal: VALUE`; `#` comments |

## Behaviour when a key is missing

`homecfg.py` exits `2` with a message naming the file and the key. Scripts pass that through:
they never fall back to a hard-coded personal value. `homecfg.py check k1 k2 ...` fails early,
naming every missing key at once.
