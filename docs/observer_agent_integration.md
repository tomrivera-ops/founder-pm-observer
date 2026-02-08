# Observer Analysis Agent — Integration Guide

The Analysis Agent is a Phase 2 component of the Observer Plane. It reads historical run data, computes metrics, and produces markdown reports with findings.

## Hard Rules

- The agent has **read-only** access to run records — it never modifies them
- The agent **never** writes back to Founder-PM
- The agent **never** makes LLM calls — all analysis is deterministic
- Reports are advisory only — no auto-apply without explicit approval
- Agent failures are logged but **never** block execution

## Running the Agent

### Via CLI

```bash
# Run analysis with default settings (from parameter config)
python bin/observe.py analyze

# Print report to stdout
python bin/observe.py analyze --print

# Override window size
python bin/observe.py analyze --window 20
```

### Programmatic Usage

```python
from lib.context_hub import ContextHub
from lib.analysis_agent import AnalysisAgent
from lib.analysis_config import AnalysisConfig

hub = ContextHub("./context_hub")
params = hub.latest_parameters()
config = AnalysisConfig.from_parameters(params)

agent = AnalysisAgent(hub, config)
result = agent.run()

if result.success:
    print(f"Report: {result.report_filename}")
    print(f"Findings: {result.findings_count}")
else:
    print(f"Error: {result.error}")
```

## Data Flow

```
Context Hub (runs/)
    ↓  read-only
Analysis Agent
    ↓  write
context_hub/analysis/   ← markdown report
context_hub/metrics/    ← agent_runs.jsonl (monitoring log)
```

## Configuration

The agent loads configuration from the Context Hub parameter store (`context_hub/parameters/`). Relevant sections:

```json
{
  "observer": {
    "analysis_window_size": 10,
    "trend_threshold": 0.1
  },
  "targets": {
    "median_cycle_time_minutes": 30,
    "build_success_rate": 0.9,
    "manual_intervention_rate": 0.1,
    "max_lint_errors_per_run": 5,
    "max_type_errors_per_run": 0
  }
}
```

If no parameter config is found, safe defaults are used.

## Finding Severities

| Severity | Meaning | Example |
|----------|---------|---------|
| **critical** | Metric significantly below target | Build success rate 60% vs 90% target |
| **warning** | Metric outside target or degrading trend | Cycle time 45m vs 30m target |
| **info** | Positive observation | All builds succeeded |

## Monitoring

Every agent execution is logged to `context_hub/metrics/agent_runs.jsonl` as a JSON-lines file. Each entry records:

- Agent name, timestamp, duration
- Runs analyzed, findings count
- Success/failure status and error details
- Report filename, window size

This can be used to track agent reliability and performance over time.

## Architecture Context

```
your-workspace/
├── founder-pm/                  # Execution Plane (UNTOUCHED)
│   ├── artifacts/
│   └── Makefile
└── founder-pm-observer/         # Observer Plane
    ├── lib/
    │   ├── analysis_agent.py    # Analysis Agent (this component)
    │   ├── analysis_config.py   # Configuration loader
    │   └── monitoring.py        # Agent monitoring
    └── context_hub/
        ├── runs/                # Input: immutable run records
        ├── analysis/            # Output: markdown reports
        └── metrics/             # Output: agent run logs
```
