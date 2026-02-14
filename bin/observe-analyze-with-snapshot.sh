#!/usr/bin/env bash
set -euo pipefail

# Run analysis and persist a metrics snapshot afterward.
# Usage: bin/observe-analyze-with-snapshot.sh [--window N] [--print]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Run standard analysis
python3 "${PROJECT_ROOT}/bin/observe.py" analyze "$@"

# Persist metrics snapshot
python3 -c "
import sys
sys.path.insert(0, '${PROJECT_ROOT}')
from lib.metrics_persistence import persist_snapshot
from lib.metrics import compute_metrics
from lib.context_hub import ContextHub
import os

hub_path = os.environ.get('OBSERVER_HUB_PATH', '${PROJECT_ROOT}/context_hub')
hub = ContextHub(hub_path)
runs = hub.list_runs()
if runs:
    from lib.metrics import compute_metrics
    summary = compute_metrics(runs)
    path = persist_snapshot(summary, hub_path)
    print(f'Metrics snapshot: {path}')
else:
    print('No runs to snapshot')
"

exit 0
