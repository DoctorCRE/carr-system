#!/bin/sh
# Explicit cutover helper: install.sh install|uninstall [task-id ...].
# Uses the retained product task installer.
set -eu
HEADLESS_SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
export PYTHONPATH="$HEADLESS_SOURCE${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -c 'from lib.headless_tasks import install_main; raise SystemExit(install_main())' "$@"
