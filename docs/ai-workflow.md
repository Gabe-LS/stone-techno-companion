# AI Agent Workflow — Fable + Sonnet 5 Orchestration

How this project's larger features are built with a multi-model Claude Code setup. Written after the E2EE work (v1, multi-device v2, DM notification fixes, CLAUDE.md audit) which was delivered end to end with this process.

## Roles

| Model | Invocation | Role |
|---|---|---|
| Fable (main session) | interactive Claude Code | Orchestrator and sole reviewer: writes specs and agent prompts, arbitrates findings, runs ALL tests and verification, fixes small issues directly, commits. Never delegates judgment. |
| Sonnet 5 | `claude -p --model claude-sonnet-5` | The workhorse: implementation runs, read-only investigations, test authoring. Executes a written spec; does not make design decisions. |
| Opus 4.6 | `claude -p --model claude-opus-4-6` | Adversarial spec review — a different model family reviewing the orchestrator's design before implementation. |

## Mechanics

- **Model pinning**: `claude -p --model <exact-id>`. The installed build's agent-tier mapping can lag new releases; the CLI passes IDs through to the API. Verify which model actually served via `--output-format json` -> `modelUsage` — never via the model's self-report (it guesses).
- **Prompts as files**: each run's prompt is written to a file and piped via stdin (`claude -p ... < prompt.md`). Prompts state: authoritative spec, exact scope, allowed files, hard rules, and a required final-report format.
- **Scoped tools**: `--allowedTools "Read" "Glob" "Grep"` for investigation/review runs; add `"Edit" "Write"` for implementation. Bash is never granted — and is broken anyway in nested Claude Code sessions (EPERM on session-env mkdir), so prompts tell agents explicitly: you cannot run anything; the reviewer executes tests. Agents must not claim verification they didn't do.
- **Streaming**: `--output-format stream-json --verbose`, run in background; the orchestrator tails the event log for progress and reads only the final result into context.

## Patterns

1. **Spec -> adversarial review -> implement -> verify** (used for multi-device E2EE, one commit per stage):
   the orchestrator writes the spec; Opus reviews it adversarially; the orchestrator arbitrates every finding (accept/reject with evidence) and folds accepted ones into the spec; Sonnet implements the amended spec verbatim; Sonnet builds the verification; the orchestrator executes it and iterates on failures.
2. **Diagnose -> falsify -> re-diagnose -> fix** (used for the DM notification bug):
   a read-only Sonnet diagnosis is treated as a hypothesis, not truth. The orchestrator validates with a runtime repro BEFORE any fix run — round one blamed the wrong subsystem and a five-minute harness repro falsified it, redirecting round two to the real cause. The fix run receives the confirmed diagnosis plus the repro as its regression oracle.
3. **Blind audit -> reconcile -> edit** (used for the CLAUDE.md refresh):
   the audit agent gets no hint of the orchestrator's own findings, making it an independent check; the two lists are reconciled (each catches what the other missed); the edit agent receives only pre-verified findings and may not re-derive facts.

## Review gate (every run, no exceptions)

- Read the agent's final report; check claimed deviations from spec — agents pushing back on the spec are sometimes right (grep-verified) and sometimes must be overruled.
- `git diff` review line by line for crypto/security surfaces; targeted spot-checks elsewhere.
- Full pytest suite + `python tests/e2ee_browser_check.py` (browser verification) run by the orchestrator, twice when flakiness is plausible.
- One commit per stage with a message that records what changed and why, including bugs found by the process itself.

## Known traps

- Nested `claude -p` cannot Bash (harness EPERM) — plan for reviewer-run verification from the start.
- Agents' self-reported model name is unreliable; billing metadata is ground truth.
- A subagent's plausible, well-cited diagnosis can still be wrong — falsification before fixing is cheaper than a wrong fix.
- Fresh agents re-read the codebase every run (no shared context): raw token usage is HIGHER than single-session work, but quota cost is lower (Sonnet weight) and the orchestrator's context stays lean, which preserves review quality across a long session. Self-contained spec docs (docs/e2ee-*.md) are what make the context transfer cheap.
- Parallel human/agent commits can silently regress features (a sidebar rework dropped the Phase 4 lock icon); the browser verification script is the regression net — extend it with every user-reported bug.
