#!/bin/sh
# Explicit cutover helper: install.sh install|uninstall [task-id ...].
# Checks native registration state before installing headless jobs.
set -eu
HEADLESS_SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
export PYTHONPATH="$HEADLESS_SOURCE${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -c 'from lib.scheduler_hygiene import install_main; raise SystemExit(install_main())' "$@"
