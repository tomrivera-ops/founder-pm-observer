#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════
# emit-to-observer-v1.sh
#
# Enhanced bridge: reads BOTH artifact JSON AND sidecar (.run.v1.json).
# If sidecar exists, extracts v2.1 fields (step_timings, fail_category, etc.).
# If sidecar absent, falls back to existing extraction behavior.
#
# Calls observe-record-v1.py (new CLI) instead of observe record-fast.
#
# Usage:
#   ./emit-to-observer-v1.sh <artifact_id>
#   ./emit-to-observer-v1.sh path/to/artifact.json
# ═══════════════════════════════════════════════════════════════

# ── Path Resolution ────────────────────────────────────────────
BRIDGE_DIR="$(cd "$(dirname "$0")" && pwd)"
OBSERVER_DIR="$(cd "${BRIDGE_DIR}/.." && pwd)"
RECORD_CLI="${OBSERVER_DIR}/bin/observe-record-v1.py"
CONTEXT_HUB="${OBSERVER_DIR}/context_hub"

FOUNDER_PM_DIR="${FOUNDER_PM_DIR:-$(cd "${OBSERVER_DIR}/../founder-pm" 2>/dev/null && pwd || echo "")}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-${FOUNDER_PM_DIR}/artifacts}"
LOGS_DIR="${LOGS_DIR:-${FOUNDER_PM_DIR}/logs}"

# ── Preflight Checks ──────────────────────────────────────────

if ! command -v jq &>/dev/null; then
    echo "ERROR: jq is required. Install with: apt install jq / brew install jq"
    exit 0  # Don't block
fi

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is required."
    exit 0
fi

if [[ ! -f "${RECORD_CLI}" ]]; then
    echo "Observer record CLI not found at ${RECORD_CLI}. Skipping emit."
    exit 0
fi

# ── Resolve Artifact ──────────────────────────────────────────

ARTIFACT_ID="${1:-}"
ARTIFACT=""

# If argument looks like a path to a file
if [[ -n "${ARTIFACT_ID}" && -f "${ARTIFACT_ID}" ]]; then
    ARTIFACT="${ARTIFACT_ID}"
    ARTIFACT_ID=$(jq -r '.id // empty' < "${ARTIFACT}" 2>/dev/null || basename "${ARTIFACT}" .json)
elif [[ -n "${ARTIFACT_ID}" ]]; then
    # Treat as artifact ID — find the file
    ARTIFACT="${ARTIFACTS_DIR}/${ARTIFACT_ID}.json"
fi

if [[ -z "${ARTIFACT}" || ! -f "${ARTIFACT}" ]]; then
    echo "No artifact found for: ${ARTIFACT_ID}"
    exit 0
fi

echo "Emitting to Observer Plane (v1 bridge)"
echo "  Artifact: ${ARTIFACT}"

# ── Deduplication ──────────────────────────────────────────────

ARTIFACT_FINGERPRINT="[source-artifact:${ARTIFACT_ID}]"
if grep -rqF "${ARTIFACT_FINGERPRINT}" "${CONTEXT_HUB}/runs/" 2>/dev/null; then
    echo "  Already emitted: ${ARTIFACT_ID}"
    exit 0
fi

# ── Helper Functions ──────────────────────────────────────────

extract() {
    local result
    result=$(jq -r "$1" < "${ARTIFACT}" 2>/dev/null)
    if [[ "${result}" == "null" || -z "${result}" ]]; then
        echo ""
    else
        echo "${result}"
    fi
}

extract_int() {
    local result
    result=$(jq -r "$1" < "${ARTIFACT}" 2>/dev/null)
    if [[ "${result}" == "null" || -z "${result}" ]]; then
        echo "${2:-0}"
    else
        echo "${result}"
    fi
}

# ── Extract Standard Fields ───────────────────────────────────

TARGET=$(extract '.target // ""')
case "${TARGET}" in
    pse|build)   INPUT_TYPE="PRD" ;;
    bugfix|fix)  INPUT_TYPE="BUGFIX" ;;
    refactor)    INPUT_TYPE="REFACTOR" ;;
    hotfix)      INPUT_TYPE="HOTFIX" ;;
    feature)     INPUT_TYPE="FEATURE" ;;
    *)           INPUT_TYPE="PRD" ;;
esac

INPUT_REF=$(extract '.description // ""')
LLM_MODEL="${LLM_MODEL_OVERRIDE:-}"

# Duration
CREATED_AT=$(extract '.created_at // ""')
DURATION=""
if [[ -n "${CREATED_AT}" ]]; then
    MAIN_LOG="${LOGS_DIR}/${ARTIFACT_ID}.log"
    END_EPOCH=""
    if [[ -f "${MAIN_LOG}" ]]; then
        END_EPOCH=$(stat -c %Y "${MAIN_LOG}" 2>/dev/null || stat -f %m "${MAIN_LOG}" 2>/dev/null || echo "")
    else
        END_EPOCH=$(stat -c %Y "${ARTIFACT}" 2>/dev/null || stat -f %m "${ARTIFACT}" 2>/dev/null || echo "")
    fi
    if [[ -n "${END_EPOCH}" ]]; then
        START_EPOCH=$(date -d "${CREATED_AT}" +%s 2>/dev/null || echo "")
        if [[ -n "${START_EPOCH}" && ${START_EPOCH} -gt 0 ]]; then
            ELAPSED=$(( END_EPOCH - START_EPOCH ))
            if [[ ${ELAPSED} -gt 0 ]]; then
                DURATION=$(echo "scale=1; ${ELAPSED} / 60" | bc 2>/dev/null || echo "")
            fi
        fi
    fi
fi

# Build success
STATUS=$(extract '.status // "unknown"')
case "${STATUS}" in
    complete|committed|shipped) BUILD_SUCCESS="" ;;  # no --failed flag
    *)                          BUILD_SUCCESS="--failed" ;;
esac

# Pipeline steps
STEPS_RAW=$(jq -r '
    .steps_completed // []
    | map(
        if . == "build_success" then "build"
        elif . == "commit_success" then "ship"
        elif . == "pre_build_safety" or . == "pre_commit_safety" then empty
        elif . == "human_review" then empty
        else .
        end
    )
    | unique
    | join(",")
' < "${ARTIFACT}" 2>/dev/null || echo "")

# Test counts
TESTS_PASSED=$(extract_int '.validation.results.pytest.passed' 0)
TESTS_FAILED=$(extract_int '.validation.results.pytest.failed' 0)
LINT_ERRORS=$(extract_int '.validation.results.ruff.issues' 0)
TYPE_ERRORS=$(extract_int '.validation.results.mypy.errors // .validation.results.pyright.errors // 0' 0)

# Manual intervention
AUTO=$(extract '.auto // false')
HAS_HUMAN_REVIEW=$(jq '.steps_completed // [] | any(. == "human_review")' < "${ARTIFACT}" 2>/dev/null || echo "false")
MANUAL_FLAGS=""
if [[ "${AUTO}" != "true" || "${HAS_HUMAN_REVIEW}" == "true" ]]; then
    MANUAL_FLAGS="--manual"
    if [[ "${AUTO}" != "true" ]]; then
        MANUAL_FLAGS="${MANUAL_FLAGS} --manual-reason Non-auto_mode"
    fi
fi

DIFF_LINES="${DIFF_LINES_OVERRIDE:-0}"
FILES_CREATED="${FILES_CREATED_OVERRIDE:-0}"
FILES_MODIFIED="${FILES_MODIFIED_OVERRIDE:-0}"

# Notes
NOTES_PARTS=()
[[ -n "${INPUT_REF}" ]] && NOTES_PARTS+=("${INPUT_REF}")
NOTES_PARTS+=("status:${STATUS}")
NOTES_PARTS+=("${ARTIFACT_FINGERPRINT}")
NOTES=$(IFS=' | '; echo "${NOTES_PARTS[*]}")

# ── Extract v2.1 Fields from Sidecar ─────────────────────────

SIDECAR_FILE="${ARTIFACTS_DIR}/${ARTIFACT_ID}.run.v1.json"
V21_FLAGS=""

if [[ -f "${SIDECAR_FILE}" ]]; then
    echo "  Sidecar found: ${SIDECAR_FILE}"

    # Error taxonomy
    FAIL_CAT=$(jq -r '.error_taxonomy.fail_category // ""' < "${SIDECAR_FILE}" 2>/dev/null)
    FAIL_STG=$(jq -r '.error_taxonomy.fail_stage // ""' < "${SIDECAR_FILE}" 2>/dev/null)
    HASH=$(jq -r '.input_content_hash // ""' < "${SIDECAR_FILE}" 2>/dev/null)

    # Step timings as JSON
    TIMINGS=$(jq -c '.step_timings // {}' < "${SIDECAR_FILE}" 2>/dev/null)

    [[ -n "${FAIL_CAT}" && "${FAIL_CAT}" != "null" ]] && V21_FLAGS="${V21_FLAGS} --fail-category ${FAIL_CAT}"
    [[ -n "${FAIL_STG}" && "${FAIL_STG}" != "null" ]] && V21_FLAGS="${V21_FLAGS} --fail-stage ${FAIL_STG}"
    [[ -n "${HASH}" && "${HASH}" != "null" ]] && V21_FLAGS="${V21_FLAGS} --input-content-hash ${HASH}"
    [[ -n "${TIMINGS}" && "${TIMINGS}" != "null" && "${TIMINGS}" != "{}" ]] && V21_FLAGS="${V21_FLAGS} --step-timings ${TIMINGS}"
else
    echo "  No sidecar found (using standard fields only)"
fi

# ── Repo Identity ─────────────────────────────────────────────

# REPO_ID can be set by the batch runner or caller.
# If not set, auto-detect from artifact's target_repo field + git remote.
if [[ -z "${REPO_ID:-}" ]]; then
    TARGET_REPO=$(extract '.target_repo // ""')
    if [[ -n "${TARGET_REPO}" && -d "${TARGET_REPO}" ]]; then
        REPO_ID=$(git -C "${TARGET_REPO}" remote get-url origin 2>/dev/null \
            | sed -E 's#^(https?://[^/]+/|git@[^:]+:)##; s/\.git$//' || echo "")
    fi
fi
REPO_ID="${REPO_ID:-}"

# ── Build & Execute CLI Command ───────────────────────────────

CMD=(python3 "${RECORD_CLI}")
CMD+=(--type "${INPUT_TYPE}")
[[ -n "${INPUT_REF}" ]]     && CMD+=(--ref "${INPUT_REF}")
[[ -n "${LLM_MODEL}" ]]     && CMD+=(--model "${LLM_MODEL}")
[[ -n "${STEPS_RAW}" ]]     && CMD+=(--steps "${STEPS_RAW}")
[[ -n "${DURATION}" ]]      && CMD+=(--duration "${DURATION}")
CMD+=(--tests-passed "${TESTS_PASSED}")
CMD+=(--tests-failed "${TESTS_FAILED}")
CMD+=(--lint-errors "${LINT_ERRORS}")
CMD+=(--type-errors "${TYPE_ERRORS}")
CMD+=(--diff "${DIFF_LINES}")
CMD+=(--files-created "${FILES_CREATED}")
CMD+=(--files-modified "${FILES_MODIFIED}")
CMD+=(--notes "${NOTES}")

# Build success flag
[[ -n "${BUILD_SUCCESS}" ]] && CMD+=(${BUILD_SUCCESS})

# Manual flags
[[ -n "${MANUAL_FLAGS}" ]] && CMD+=(${MANUAL_FLAGS})

# v2.1 flags (if sidecar was found)
[[ -n "${V21_FLAGS}" ]] && CMD+=(${V21_FLAGS})

# Repo identity
[[ -n "${REPO_ID}" ]] && CMD+=(--repo-id "${REPO_ID}")

echo "  Source ID: ${ARTIFACT_ID}"
echo "  Status:    ${STATUS}"
echo "  Steps:     ${STEPS_RAW}"
echo "  Tests:     ${TESTS_PASSED} passed, ${TESTS_FAILED} failed"
echo ""

export OBSERVER_HUB_PATH="${CONTEXT_HUB}"
"${CMD[@]}"

EXIT_CODE=$?
if [[ ${EXIT_CODE} -eq 0 ]]; then
    echo "  Run emitted to Observer Plane (v1)"
else
    echo "  Failed to emit (exit code: ${EXIT_CODE})"
fi

exit 0  # Always exit 0
