# Founder-PM Observer Plane

**Advisory Optimization System — Phase 1: Measure**

The Observer Plane is a parallel advisory system that watches Founder-PM executions, records outcome metrics, and (in later phases) produces optimization recommendations.

It does not execute builds, modify prompts, or alter runtime behavior.

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
founder-pm-observer/             # Sibling to founder-pm/ — never inside it
├── bin/
│   ├── observe.py               # CLI tool (primary interface)
│   └── seed_data.py             # Sample data generator
├── bridge/
│   ├── emit-to-observer.sh      # Post-hook: artifact → Observer
│   └── MAKEFILE_SNIPPET.mk      # Append to founder-pm Makefile
├── lib/
│   ├── __init__.py
│   ├── schema.py                # Run record contract + validation
│   ├── context_hub.py           # Storage layer (append-only)
│   └── metrics.py               # Aggregation + trends
├── tests/
│   └── test_phase1.py           # 33 tests
├── context_hub/
│   ├── runs/                    # Immutable run records (JSON)
│   ├── metrics/                 # Aggregated snapshots
│   ├── analysis/                # Markdown reports (Phase 2)
│   ├── proposals/               # Parameter change proposals (Phase 3)
│   └── parameters/              # Versioned configs
└── docs/
```

---

## Quick Start

```bash
# 1. Initialize (idempotent)
python bin/observe.py init

# 2. Seed sample data (optional)
python bin/seed_data.py

# 3. View runs
python bin/observe.py list

# 4. View metrics
python bin/observe.py metrics

# 5. Show a specific run
python bin/observe.py show <run-id>

# 6. Record a new run (interactive)
python bin/observe.py record

# 7. Record a new run (fast, CLI args)
python bin/observe.py record-fast \
  --type PRD \
  --ref my-feature-prd.md \
  --model claude-4.6 \
  --steps ingest,build,audit,ship \
  --duration 25 \
  --tests-passed 30 \
  --diff 200

# 8. Export all runs as JSON
python bin/observe.py export
```

---

## Integration with Founder-PM

The bridge script reads founder-pm artifacts and emits Observer run records. **Zero changes to founder-pm source code.**

### Setup (two steps)

1. Place `founder-pm-observer/` as a sibling to `founder-pm/`:
   ```
   your-workspace/
   ├── founder-pm/           # EXISTING — UNTOUCHED
   └── founder-pm-observer/  # NEW
   ```

2. Append `bridge/MAKEFILE_SNIPPET.mk` to your `founder-pm/Makefile`:
   ```makefile
   # --- Observer Plane Integration (optional, non-blocking) ---
   OBSERVER_BRIDGE := ../founder-pm-observer/bridge/emit-to-observer.sh

   .PHONY: observe
   observe:
       @if [ -x "$(OBSERVER_BRIDGE)" ]; then $(OBSERVER_BRIDGE); \
       else echo "Observer Plane not installed. Skipping."; fi
   ```

### Usage

```bash
# After any build — emit the latest artifact
make observe

# Or emit a specific artifact
../founder-pm-observer/bridge/emit-to-observer.sh artifacts/20260207-153000-abc123.json

# Optional: chain after your ship target
# ship: build test lint _ship observe
```

### Field Mapping (founder-pm -> Observer)

| Founder-PM Artifact | Observer Run Record | Notes |
|---|---|---|
| `.target` | `input_type` | `pse`/`build` -> PRD, `bugfix` -> BUGFIX |
| `.description` | `input_ref` | Direct pass-through |
| `.status` | `build_success` | `complete` -> true, else false |
| `.steps_completed[]` | `pipeline_steps_executed` | `build_success` -> `build`, `commit_success` -> `ship` |
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
  make observe
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

## Running Tests

```bash
pip install pytest
cd founder-pm-observer
pytest tests/ -v
```

33 tests covering: schema immutability, serialization roundtrips, validation rules, Context Hub CRUD, append-only enforcement, metrics aggregation, and trend computation.

---

## Phase Roadmap

| Phase | Status | Scope |
|-------|--------|-------|
| **1 — Measure** | Complete | Context Hub, schema, CLI, metrics, bridge |
| **2 — Analyze** | Next | Read-only analysis agent, markdown reports |
| **3 — Suggest** | Planned | Parameter proposals, approval gate |
| **4 — Automate** | Future | Confidence-gated auto-apply, rollback |

---

## Compatibility Guarantee

Founder-PM behaves identically whether the Observer Plane is enabled, disabled, or removed entirely. This is enforced by design, not convention.
