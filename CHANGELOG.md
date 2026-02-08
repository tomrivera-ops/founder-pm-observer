# Changelog

## [Unreleased]

### Added — Phase 2: Analyze
- **Analysis Agent** (`lib/analysis_agent.py`): Read-only agent that analyzes run
  history from the Context Hub, computes metrics against targets, and produces
  markdown reports with severity-classified findings
- **Analysis Config** (`lib/analysis_config.py`): Configuration loader that reads
  thresholds from the Context Hub parameter store with safe defaults
- **Agent Monitoring** (`lib/monitoring.py`): JSON-lines logging of agent executions
  with timing, outcomes, and error tracking
- **CLI `analyze` command**: Run the analysis agent from the command line with
  optional window size override and report printing
- **Integration docs** (`docs/observer_agent_integration.md`): Guide for using the
  analysis agent programmatically and via CLI
- Comprehensive test suite for analysis agent (tests/test_analysis_agent.py)

## [0.1.0] — 2026-02-06

### Added — Phase 1: Measure
- Context Hub: append-only, file-per-record JSON storage
- Run record schema with immutability enforcement (frozen dataclass)
- Validation rules for all record fields
- Metrics aggregation: duration stats, success rates, hygiene, trends
- CLI tool (`bin/observe.py`): init, record, record-fast, list, show, metrics, export
- Bridge script (`bridge/emit-to-observer.sh`): artifact-to-Observer emission
- Makefile integration snippet for founder-pm
- Parameter config system with versioned JSON files
- 33 tests covering schema, validation, Context Hub, and metrics
