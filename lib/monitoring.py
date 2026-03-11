"""
Founder-PM Observer Plane — Agent Monitoring

Lightweight monitoring for analysis agent runs.
Logs agent performance, resource usage, and outcomes to a structured log.

Design:
  - Append-only JSON-lines log (one entry per agent run)
  - No external dependencies — uses stdlib logging + file I/O
  - Safe to call from any context (never raises)
"""

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


logger = logging.getLogger("observer.monitoring")

# Retention policy: telemetry log entries older than this are eligible for purge.
# Override with OBSERVER_LOG_RETENTION_DAYS environment variable.
DEFAULT_RETENTION_DAYS = 90


@dataclass
class AgentRunLog:
    """Structured log entry for a single agent execution."""
    agent_name: str
    timestamp: str
    duration_seconds: float
    runs_analyzed: int
    findings_count: int
    success: bool
    error: Optional[str] = None
    report_filename: str = ""
    window_size: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class AgentMonitor:
    """
    Monitors and logs analysis agent executions.

    Writes structured JSON-lines to context_hub/metrics/agent_runs.jsonl.
    Each line is a self-contained JSON object describing one agent run.
    """

    def __init__(self, metrics_dir: Path):
        self.log_path = metrics_dir / "agent_runs.jsonl"
        self.metrics_dir = metrics_dir

    def log_run(self, entry: AgentRunLog) -> None:
        """Append an agent run log entry. Never raises."""
        try:
            self.metrics_dir.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")
            logger.debug("Logged agent run: %s", entry.agent_name)
        except Exception as e:
            logger.warning("Failed to log agent run: %s", e)

    def recent_runs(self, limit: int = 10) -> list[AgentRunLog]:
        """Read recent agent run logs. Returns newest first."""
        if not self.log_path.exists():
            return []

        entries = []
        try:
            with open(self.log_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        entries.append(AgentRunLog(**data))
        except Exception as e:
            logger.warning("Failed to read agent logs: %s", e)
            return []

        entries.reverse()
        return entries[:limit]

    def run_count(self) -> int:
        """Total number of logged agent runs."""
        if not self.log_path.exists():
            return 0
        try:
            with open(self.log_path, "r") as f:
                return sum(1 for line in f if line.strip())
        except Exception:
            return 0

    def success_rate(self) -> Optional[float]:
        """Success rate across all logged runs. Returns None if no runs."""
        runs = self.recent_runs(limit=10000)
        if not runs:
            return None
        successes = sum(1 for r in runs if r.success)
        return round(successes / len(runs), 4)

    @property
    def retention_days(self) -> int:
        """Retention period in days. Configurable via OBSERVER_LOG_RETENTION_DAYS."""
        env_val = os.environ.get("OBSERVER_LOG_RETENTION_DAYS")
        if env_val:
            try:
                return int(env_val)
            except ValueError:
                pass
        return DEFAULT_RETENTION_DAYS

    def purge_old_logs(self) -> int:
        """Remove log entries older than the retention period.

        Returns the number of entries purged.
        """
        if not self.log_path.exists():
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        cutoff_iso = cutoff.isoformat()

        kept = []
        purged = 0

        try:
            with open(self.log_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        ts = data.get("timestamp", "")
                        if ts and ts < cutoff_iso:
                            purged += 1
                        else:
                            kept.append(line)
                    except json.JSONDecodeError:
                        kept.append(line)

            if purged > 0:
                with open(self.log_path, "w") as f:
                    for line in kept:
                        f.write(line + "\n")
                logger.info("purged %d entries older than %d days", purged, self.retention_days)

        except Exception as e:
            logger.warning("Failed to purge old logs: %s", e)

        return purged


def create_monitor(hub_base_path: Path) -> AgentMonitor:
    """Create a monitor for the given Context Hub."""
    return AgentMonitor(hub_base_path / "metrics")
