"""
Metrics Persistence — snapshot writer for aggregated metrics.

Writes metrics snapshots to context_hub/metrics/ for historical tracking.
Called externally after analysis, not inside the analysis agent.
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def persist_snapshot(metrics_summary, context_hub_path: str = "context_hub") -> str:
    """Write a metrics snapshot to the metrics directory.

    Args:
        metrics_summary: MetricsSummary dataclass or dict with metrics data
        context_hub_path: Path to the context hub directory

    Returns:
        Path to the written snapshot file as string
    """
    metrics_dir = Path(context_hub_path) / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"snapshot-{timestamp}.json"
    path = metrics_dir / filename

    # Handle both dataclass and dict inputs
    if hasattr(metrics_summary, "__dataclass_fields__"):
        from dataclasses import asdict
        data = asdict(metrics_summary)
    elif isinstance(metrics_summary, dict):
        data = metrics_summary
    else:
        data = {"raw": str(metrics_summary)}

    data["snapshot_timestamp"] = datetime.now(timezone.utc).isoformat()

    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    return str(path)
