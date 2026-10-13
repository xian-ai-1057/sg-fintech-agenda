#!/bin/bash
# macOS: run from the project folder, even if its path contains spaces.
cd -- "$(dirname -- "$0")" || exit 1
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3,10))' >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  printf '\nPython 3.10 or later was not found.\nInstall Python from https://www.python.org/downloads/ then try again.\nNo software was downloaded or installed.\n'
else
  "$PYTHON" -m team.launch
fi
printf '\nPress Return to close this window.'
read -r _
