"""Tests for lib/monitoring.py — retention policy (OBS-006)."""

import json
import sys
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.monitoring import AgentMonitor, AgentRunLog, DEFAULT_RETENTION_DAYS


def _make_entry(agent_name="analysis_agent", timestamp=None, **kwargs):
    """Build an AgentRunLog with a specific timestamp."""
    defaults = {
        "agent_name": agent_name,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "duration_seconds": 1.0,
        "runs_analyzed": 5,
        "findings_count": 0,
        "success": True,
    }
    defaults.update(kwargs)
    return AgentRunLog(**defaults)


class TestRetentionPolicy:
    def test_default_retention_is_90_days(self, tmp_path, monkeypatch):
        """Default retention period is 90 days."""
        monkeypatch.delenv("OBSERVER_LOG_RETENTION_DAYS", raising=False)
        monitor = AgentMonitor(tmp_path)
        assert monitor.retention_days == 90
        assert DEFAULT_RETENTION_DAYS == 90

    def test_env_var_overrides_retention(self, tmp_path, monkeypatch):
        """OBSERVER_LOG_RETENTION_DAYS env var overrides default."""
        monkeypatch.setenv("OBSERVER_LOG_RETENTION_DAYS", "30")
        monitor = AgentMonitor(tmp_path)
        assert monitor.retention_days == 30

    def test_purge_removes_old_entries(self, tmp_path, monkeypatch):
        """purge_old_logs() removes entries older than retention window."""
        monkeypatch.delenv("OBSERVER_LOG_RETENTION_DAYS", raising=False)
        monitor = AgentMonitor(tmp_path)

        # Write entries: 2 old (120 days ago), 1 recent (today)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        recent_ts = datetime.now(timezone.utc).isoformat()

        monitor.log_run(_make_entry(timestamp=old_ts))
        monitor.log_run(_make_entry(timestamp=old_ts))
        monitor.log_run(_make_entry(timestamp=recent_ts))

        assert monitor.run_count() == 3

        purged = monitor.purge_old_logs()
        assert purged == 2
        assert monitor.run_count() == 1
