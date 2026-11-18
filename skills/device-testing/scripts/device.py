#!/usr/bin/env python3
# device: the one entry point of the device-testing skill. `device help` (or --help) prints every
# command; `device help --markdown` regenerates references/commands.md. Output is plain text that
# harnesses grep, or JSON where a command says so (`devices`); there is no global --json switch.
# Exit codes: 0 ok, 1 failed, 2 a wait or check came back negative, 3 not supported on this platform.
"""device: drive an attached Android phone or iPhone with one command set. See `device help`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from device_testing.cli import main  # noqa: E402

sys.exit(main())
