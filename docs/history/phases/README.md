# Phase files

This directory holds the per-phase artefacts written by the scrum
agent loop.

Naming: `PHASE_NN_<slug>.md`, where `NN` is the two-digit phase
number from [../v1-plan.md](../v1-plan.md) and `<slug>` is the
kebab-case form of the phase title (e.g. `PHASE_00_verify-mbm-baseline.md`).

Structure:

```markdown
# PH00 — Verify mbm integration baseline

## Plan
(scrum-planner writes here)

## Implementation
(scrum-developer writes here, with file:line references for each change)

## Tests
(scrum-sqe writes here)

## Verification
(scrum-tester writes Pass/Fail here)

## Blockers
(only if the phase cannot complete as planned)
```

A phase file is the single source of truth for that phase's progress.
Commit messages reference the phase id, not the file path.
