# Observer Plane — Phase 3 Operating Protocol

You are currently in **Phase 3 (Suggest)** of the Observer Plane. Phase 4 (Automate) is NOT yet authorized. Follow this protocol after every build cycle.

## Post-Build Checklist (Every Run)

After every successful `founder-pm` build:

1. **Emit the run:**
   ```bash
   make observe
   ```

2. **Generate analysis:**
   ```bash
   cd ../founder-pm-observer && python bin/observe.py analyze --print
   ```

3. **Generate proposal (if findings exist):**
   ```bash
   python bin/observe.py propose
   ```

4. **If a proposal was generated, notify the user:**
   Print the proposal summary and ask:
   > "Observer Plane generated a parameter proposal. Review and approve/reject?"

   Then wait for explicit instruction before running `observe approve` or `observe reject`.

5. **NEVER run `observe approve` without explicit human authorization.**

## Weekly: Phase 4 Readiness Check

Once per week (or when the user asks about Phase 4 status), run:

```bash
cd ../founder-pm-observer && python bin/phase4_readiness.py
```

Report the results to the user. If all criteria are met, inform the user:

> "Phase 4 readiness criteria are fully met. When you're ready, I can generate the Phase 4 PRD with auto-apply thresholds derived from your actual approval history."

## Phase 4 Graduation Criteria (Do Not Modify)

Phase 4 build is blocked until ALL of these are met:

- [ ] >=20 total runs recorded (>=15 real, non-seed)
- [ ] >=10 proposals generated
- [ ] >=8 proposals resolved (approved or rejected)
- [ ] >=5 proposals approved
- [ ] >=3 low-risk proposals approved
- [ ] Low-risk approval rate >=80%
- [ ] Build success rate >=90%
- [ ] Manual intervention rate <=15%
- [ ] Duration and reliability trends not degrading
- [ ] >=5 analysis reports generated
- [ ] Zero pending (unresolved) proposals
- [ ] >=14 days since first proposal

These criteria exist because Phase 4 grants the Observer auto-apply authority. The thresholds for auto-apply must be calibrated from real approval patterns, not guesses.

## What You Must NOT Do During Phase 3

- Do not build Phase 4 code
- Do not set `observer.auto_apply_enabled` to `true` in any parameter config
- Do not auto-approve proposals without human confirmation
- Do not skip the post-build emit step
