# Founder-PM Observer Plane

**Advisory Optimization System — Phase 3: Suggest**

Faraday Capital Systems Holdings LLC

The Observer Plane is a parallel advisory system that watches Founder-PM executions, records outcome metrics, analyzes trends, and generates parameter change proposals for human approval. It does not execute builds, modify prompts, or alter runtime behavior.

```
197 tests  |  12 lib modules  |  4 phases  |  zero external dependencies
```

---

## Architecture

```
Founder-PM (Execution Plane)     Observer Plane (Advisory Only)
├── ingest PRD                   ├── record metrics
├── build                        ├── analyze history
├── audit                        ├── generate recommendations
├── debug                        └── wait for approval
└── ship
       ↓
  Immutable Run Artifact ──────→ Context Hub (read-only access)
       (via bridge)
```

**Dependency direction is one-way only.** Founder-PM never reads Observer outputs. The Observer Plane can be disabled or removed with zero impact on execution.

---

## Directory Structure

```
founder-pm-observer/                # Sibling to founder-pm/ — never inside it
├── bin/
│   ├── observe.py                  # Main CLI (primary interface)
│   ├── observe-verdict.py          # Verdict generator for recursive runner
│   ├── observe-record-v1.py        # Record command v1
│   ├── phase4_readiness.py         # Phase 4 graduation checker
│   └── seed_data.py                # Sample data generator
├── bridge/
│   ├── emit-to-observer.sh         # Post-hook: artifact → Observer
│   ├── emit-to-observer-v1.sh      # Bridge v1 (used by founder-pm)
│   └── MAKEFILE_SNIPPET.mk         # Append to founder-pm Makefile
├── lib/
│   ├── __init__.py
│   ├── schema.py                   # Run record contract (frozen dataclass)
│   ├── context_hub.py              # Append-only storage layer
│   ├── metrics.py                  # Aggregation & trend computation
│   ├── metrics_persistence.py      # Metrics snapshot writer
│   ├── analysis_agent.py           # Phase 2: deterministic analysis agent
│   ├── analysis_config.py          # Agent configuration loader
│   ├── monitoring.py               # Agent performance monitoring
│   ├── proposal_engine.py          # Phase 3: rule-based proposal engine
│   ├── proposal_schema.py          # Proposal contract & versioning
│   ├── verdict_engine.py           # Verdict generation from sidecar data
│   └── repo_filter.py              # Multi-repo filtering
├── tests/                          # 197 tests across 11 files
│   ├── test_phase1.py              # Schema, hub, metrics (33 tests)
│   ├── test_analysis_agent.py      # Analysis agent (36 tests)
│   ├── test_proposal_engine.py     # Proposals (28 tests)
│   ├── test_approval_gate.py       # Approval flow (26 tests)
│   ├── test_verdict_engine.py      # Verdict generation
│   ├── test_monitoring.py          # Agent monitoring
│   ├── test_context_hub.py         # Storage layer
│   ├── test_metrics_persistence.py # Metrics snapshots
│   ├── test_repo_filter.py         # Repo filtering
│   ├── test_schema_extensions.py   # Schema backward compat
│   └── test_phase4_readiness.py    # Graduation criteria
├── context_hub/                    # Persistent data store
│   ├── runs/                       # Immutable run records (JSON)
│   ├── metrics/                    # Aggregated snapshots
│   ├── analysis/                   # Markdown reports
│   ├── proposals/                  # Parameter change proposals
│   ├── parameters/                 # Versioned configs (v0.1.0 → v0.5.0)
│   └── verdicts/                   # Verdict files from sidecar data
└── docs/
    ├── OBSERVER-INTEGRATION.md     # Integration guide
    ├── PHASE3-PROTOCOL.md          # Phase 3 operating protocol
    └── observer_agent_integration.md  # Analysis agent guide
```

---

## Quick Start

```bash
# 1. Initialize (idempotent)
python3 bin/observe.py init

# 2. Seed sample data (optional)
python3 bin/seed_data.py

# 3. View runs
python3 bin/observe.py list

# 4. View metrics
python3 bin/observe.py metrics

# 5. Show a specific run
python3 bin/observe.py show <run-id>

# 6. Record a new run (interactive)
python3 bin/observe.py record

# 7. Record a new run (fast, CLI args)
python3 bin/observe.py record-fast \
  --type PRD \
  --ref my-feature-prd.md \
  --model claude-4.6 \
  --steps ingest,build,audit,ship \
  --duration 25 \
  --tests-passed 30 \
  --diff 200

# 8. Run analysis agent (Phase 2)
python3 bin/observe.py analyze
python3 bin/observe.py analyze --print          # Print report to stdout
python3 bin/observe.py analyze --window 20      # Custom window size

# 9. Generate a parameter change proposal (Phase 3)
python3 bin/observe.py propose
python3 bin/observe.py propose --window 20      # Custom window size

# 10. Approve or reject a proposal (Phase 3)
python3 bin/observe.py approve <proposal-id>
python3 bin/observe.py approve <proposal-id> --by tom
python3 bin/observe.py reject <proposal-id> --reason "Not appropriate now"

# 11. List all proposals
python3 bin/observe.py proposals

# 12. Export all runs as JSON
python3 bin/observe.py export
```

---

## Integration with Founder-PM

The bridge script reads founder-pm artifacts and emits Observer run records. **Zero changes to founder-pm source code required.**

### Setup

Place `founder-pm-observer/` as a sibling to `founder-pm/`, or set the `OBSERVER_PATH` environment variable.

Founder-PM resolves the Observer location via a 3-tier fallback:
1. `OBSERVER_PATH` env var (explicit override)
2. Sibling directory named `founder-pm-observer`
3. `~/projects/founder-pm-observer` (canonical default)

### Bridge Protocol

The bridge script (`emit-to-observer-v1.sh`) is invoked by Founder-PM's `lib/observer_bridge.py` after each run:

```
Founder-PM Run Complete
       ↓
observer_bridge.py invokes emit-to-observer-v1.sh
       ↓
Bridge reads artifact JSON → maps fields → calls observe record-fast
       ↓
Observer writes immutable run record to context_hub/runs/
```

### Field Mapping (founder-pm artifact -> Observer run record)

| Founder-PM Artifact | Observer Run Record | Notes |
|---|---|---|
| `.target` | `input_type` | `pse`/`build` -> PRD, `bugfix` -> BUGFIX |
| `.description` | `input_ref` | Direct pass-through |
| `.status` | `build_success` | `complete` -> true, else false |
| `.steps_completed[]` | `pipeline_steps_executed` | Mapped step names |
| `.created_at` -> log mtime | `duration_minutes` | Computed from timestamps |
| `.validation.results.pytest.passed` | `tests_passed` | Direct |
| `.validation.results.pytest.failed` | `tests_failed` | Direct |
| `.validation.results.ruff.issues` | `lint_errors` | Direct |
| `.auto` + `human_review` step | `manual_intervention` | Auto + no human review -> false |
| `.code_review.*` | notes summary | `code_review:0crit/1maj/2min` |
| `.cursor_audit.*` | notes summary | `arch_audit:0p0/1p1/2p2` |
| `.id` | dedup fingerprint | Prevents duplicate emissions |

### Bridge Safety Guarantees

- **Always exits 0** — Observer failures never block founder-pm
- **Idempotent** — safe to run multiple times on the same artifact
- **Graceful degradation** — silently skips if Observer Plane is not installed
- **Requires:** `jq`, `python3` (no other dependencies)

### Environment Overrides

For fields not in the artifact, you can pass overrides:

```bash
LLM_MODEL_OVERRIDE=claude-4.6 \
DIFF_LINES_OVERRIDE=350 \
  ../founder-pm-observer/bridge/emit-to-observer-v1.sh
```

---

## Run Metadata Contract

The sole coupling point between Founder-PM and the Observer Plane:

```json
{
  "run_id": "2026-02-07-abc123",
  "source": "founder-pm",
  "input_type": "PRD",
  "input_ref": "Build payment gateway service from PRD",
  "timestamp": "2026-02-07T15:30:00+00:00",
  "duration_minutes": 31.0,
  "llm_model": "claude-4.6",
  "pipeline_steps_executed": ["ingest", "audit", "build", "code_review", "validation", "cursor_audit", "ship"],
  "build_success": true,
  "tests_passed": 47,
  "tests_failed": 2,
  "lint_errors": 3,
  "type_errors": 0,
  "diff_size_lines": 0,
  "files_created": 0,
  "files_modified": 0,
  "manual_intervention": false,
  "manual_intervention_reason": "",
  "notes": "Build payment gateway | status:complete | code_review:0crit/1maj/2min | arch_audit:0p0/1p1/2p2"
}
```

**Contract rules:** Immutable once written. Observer has read-only access. No reverse dependency.

---

## Analysis Agent (Phase 2)

The analysis agent is a read-only, deterministic agent that examines run history, computes metrics, and produces markdown reports. No LLM calls — same input always produces the same output.

### How It Works

1. Loads the most recent runs from the Context Hub (window size configurable)
2. Splits runs into current and previous windows for trend comparison
3. Computes aggregated metrics (success rate, cycle time, hygiene)
4. Compares metrics against targets from the parameter config
5. Generates findings classified by severity: critical, warning, info
6. Writes a markdown report to `context_hub/analysis/`
7. Logs performance to `context_hub/metrics/agent_runs.jsonl`

### Configuration

The agent reads targets from `context_hub/parameters/`. Key settings:

| Parameter | Default | Source |
|-----------|---------|--------|
| `analysis_window_size` | 10 | `observer.analysis_window_size` |
| `trend_threshold` | 0.1 | `observer.trend_threshold` |
| `target_build_success_rate` | 0.9 | `targets.build_success_rate` |
| `target_median_cycle_time` | 30m | `targets.median_cycle_time_minutes` |
| `target_manual_intervention_rate` | 0.1 | `targets.manual_intervention_rate` |
| `target_max_lint_errors` | 5 | `targets.max_lint_errors_per_run` |
| `target_max_type_errors` | 0 | `targets.max_type_errors_per_run` |

---

## Proposal Engine (Phase 3)

The proposal engine converts analysis findings into parameter change proposals via deterministic rules. All proposals require explicit human approval.

### Rules

| Finding | Rule | Parameter Change |
|---------|------|-----------------|
| Cycle time exceeds target | Relax target by 10% | `targets.median_cycle_time_minutes` |
| Build success rate below target | Lower target by 5% (floor: 50%) | `targets.build_success_rate` |
| Lint errors exceed target | Raise tolerance by 2 | `targets.max_lint_errors_per_run` |
| Type errors exceed target | Raise tolerance by 1 | `targets.max_type_errors_per_run` |
| Manual intervention exceeds target | Relax target by 5% (cap: 100%) | `targets.manual_intervention_rate` |
| Critical degrading trend | Expand analysis window by 5 | `observer.analysis_window_size` |

### Version Bumping

- **Low impact** (1-2 changes, no critical findings): patch bump (v0.1.0 -> v0.1.1)
- **Medium impact** (>2 changes): minor bump (v0.1.0 -> v0.2.0)
- **High impact** (any critical finding): minor bump (v0.1.0 -> v0.2.0)

### Constraints

- **One pending proposal at a time** — approve or reject before creating another
- **No LLM calls** — all rules are deterministic and auditable
- **No auto-apply** — every change requires explicit human approval (Phase 4)
- **Read-only** with respect to run records (never modifies runs)

### Example Workflow

```bash
# 1. Generate proposal from current analysis
python3 bin/observe.py propose

# Output:
# Proposal generated: prop-20260209-003327-d67d89
#   Impact:  low
#   Version: v0.1.0 -> v0.1.1
#   Changes: 2
#     targets.median_cycle_time_minutes: 30 -> 33.0
#     targets.manual_intervention_rate: 0.1 -> 0.15

# 2. Review and approve
python3 bin/observe.py approve prop-20260209-003327-d67d89

# 3. Or reject with reason
python3 bin/observe.py reject prop-20260209-003327-d67d89 --reason "Wait for more data"
```

---

## Verdict Engine

The verdict engine generates pass/fail/warn verdicts from Founder-PM sidecar telemetry, consumed by the recursive runner for retry decisions.

### Checks

| Check | Severity | Retry Eligible |
|-------|----------|----------------|
| `build_success` | blocking | yes |
| `tests_passing` | blocking | yes |
| `lint_clean` | advisory | no |
| `type_clean` | advisory | no |
| `arch_p0_clear` | blocking | yes |
| `code_review_clear` | advisory | no |
| `secrets_clean` | blocking | no |

### Verdict Invocation

```bash
python3 bin/observe-verdict.py \
  --artifact-id <id> \
  --sidecar-path founder-pm/artifacts/<id>.run.v1.json
```

### Degraded Mode

When the Observer is unavailable, Founder-PM falls back to `lib/verdict_fallback.py` — a lightweight verdict that only checks `build_success` and `tests_passing`, always with `degraded=True`.

---

## Phase 4 Readiness

Check graduation criteria for Phase 4 (confidence-gated auto-apply):

```bash
python3 bin/phase4_readiness.py          # Human-readable report
python3 bin/phase4_readiness.py --json   # Machine-readable output
```

**Graduation Criteria (12 checks):**
1. 20+ total runs recorded (15+ real, non-seed)
2. 10+ proposals generated
3. 8+ proposals resolved (approved or rejected)
4. 5+ proposals approved
5. 3+ low-risk proposals approved
6. Low-risk approval rate >= 80%
7. Build success rate >= 90%
8. Manual intervention rate <= 15%
9. Duration and reliability trends not degrading
10. 5+ analysis reports generated
11. Zero pending (unresolved) proposals
12. 14+ days since first proposal

---

## Design Principles

- **One-way dependency** — Founder-PM -> Observer, never reverse
- **Immutability** — run records are frozen after write (`@dataclass(frozen=True)`)
- **Append-only** — no updates, only additions; `RecordExistsError` on overwrite
- **Fail-open** — Observer failures never block founder-pm
- **Deterministic** — no LLM calls in analysis or proposals (repeatable results)
- **Observable** — all agent runs logged with timing and outcomes
- **Versioned parameters** — every config change creates a new version
- **Human-in-loop** — Phase 3 requires explicit approval (no auto-apply until Phase 4)
- **Zero external dependencies** — Python stdlib only (no pip packages required)

---

## Dependencies

**Runtime:** Python standard library only. No external packages.

**System (for bridge):** `bash`, `jq`, `python3`

**Development:** `pytest` (for tests only)

---

## Running Tests

```bash
pip install pytest
cd founder-pm-observer
pytest tests/ -v
```

197 tests across 11 files covering: schema immutability, serialization roundtrips, validation rules, Context Hub CRUD, append-only enforcement, metrics aggregation, trend computation, analysis agent execution, finding generation, report format, agent monitoring, proposal schema, rule matching, version bumping, impact computation, approval gate, rejection flow, one-pending enforcement, verdict generation, and Phase 4 readiness.

---

## Phase Roadmap

| Phase | Status | Scope |
|-------|--------|-------|
| **1 — Measure** | Complete | Context Hub, schema, CLI, metrics, bridge |
| **2 — Analyze** | Complete | Read-only analysis agent, markdown reports, monitoring |
| **3 — Suggest** | Complete | Parameter proposals, approval gate, rule engine |
| **4 — Automate** | Future | Confidence-gated auto-apply, rollback |

---

## Three-System Architecture

The Observer is one of three interconnected systems in the Faraday Capital development workflow:

```
              Founder-PM (Execution)
              ├── 10-step pipeline
              ├── portfolio queue
              └── recursive runner
                    │
         ┌──────────┼──────────┐
         ▼                     ▼
    Observer (Advisory)   Adversary (Security)
    ├── metrics            ├── intruder pass
    ├── analysis           ├── governed pass
    ├── proposals          └── signal-only hook
    └── verdicts               in founder-pm
```

- [Founder-PM](https://github.com/faraday-build/founder-pm) — orchestration engine
- [Adversary](https://github.com/faraday-build/adversary) — adversarial security analysis

---

## Compatibility Guarantee

Founder-PM behaves identically whether the Observer Plane is enabled, disabled, or removed entirely. This is enforced by design, not convention.
