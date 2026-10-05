#!/bin/bash
# Double-click me. Refreshes the Job Board: finds new jobs on every source, tags only what is new,
# publishes it, and checks a few old jobs are still open. The first time, I also set myself up.
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
  echo "First-time setup (one minute)..."
  python3 -m venv .venv || { echo "Python 3 is needed: https://www.python.org/downloads/"; read -n 1 -s -r -p "Press any key to close"; exit 1; }
fi
if ! .venv/bin/python -c "import jobspy" 2>/dev/null; then
  echo "Installing the job-fetching tools (first time only)..."
  .venv/bin/pip install -q -r requirements-ingest.txt || { echo "Setup failed. Check your internet connection."; read -n 1 -s -r -p "Press any key to close"; exit 1; }
fi

echo "Refreshing jobs. You can close this window to stop; nothing is lost."
echo
.venv/bin/python scripts/ingest_jobs.py refresh
echo
read -n 1 -s -r -p "Done. Press any key to close"
