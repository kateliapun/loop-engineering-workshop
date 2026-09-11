# Regression suite

Purpose: a small, fast, deterministic, committed suite that Dev's own TEST step
(`dev.txt.tmpl` step 6) runs on every task, and that Tester can lean on for the parts
of REVIEW/OBSERVE that don't need fresh judgment every cycle. Neither replaces
Tester — Tester still judges the specific acceptance criteria and the quality bar,
which a fixed suite can't do — but "did anything I didn't touch just break" shouldn't
cost a full LLM-driven sweep every single cycle when a script can answer it in
seconds.

## Where it lives

- Reuses the **driver** from `references/verification-driver.md` (browser tool /
  Telegram test client / HTTP client / CLI invocation) — don't stand up a second,
  different way of exercising the product.
- Reuses the **Step 4 test identity** as a session/auth helper: log in / establish the
  test identity ONCE (saved session state, or a minted token), reuse it across specs.
  Never re-pay the login cost per spec.
- Runs against **STAGING only** — same as everything else in this loop, never prod.
- Lives in the project repo (`e2e/` or `tests/regression/`, match the project's own
  convention if one already exists), committed — not gitignored output.

## Scope — small and real, not exhaustive

1. Pick a small number of critical flows (3-8 to start) — the ones that would be
   genuinely bad to silently break. Don't try to cover everything at setup time.
2. Explore each flow live first with the driver (don't guess selectors/message
   formats), then commit it as a spec.
3. Each new shipped feature adds its own spec going forward — Dev adds one as part of
   the task that introduced the behavior (mention this in `dev.extra.txt`'s
   `plan_notes` or `style_rules` section, Step 5). The suite compounds; it does not
   start complete.

## Practices

- **Real flow, not a bypass or a mock.** For a bot: send an actual message through the
  driver from `verification-driver.md`, read the actual reply. For a web app: click
  through the real UI, don't call an internal function directly.
- **Layered assertions where they exist**: not just "the reply/page changed" — did the
  underlying state actually change too (a DB row, a status field), and is the
  user-visible outcome the right one. Not every flow has three layers; use as many as
  actually exist.
- **Fresh data per run** where the flow creates something (avoid collisions across
  reruns); reuse the one seeded test identity for anything that just reads/acts as a
  user.
- **Evidence**: a screenshot/log capture per run, gitignored output (not the specs
  themselves) — the same evidence convention Tester already uses, so a failure is
  reviewable without re-running it live.

## When a spec fails

Same triage as any test: classify before "fixing" — a real regression (fix the
product), a stale spec (the flow intentionally changed — update the spec to match),
or flakiness/env (staging down, timing, rate limit — fix robustness, don't touch the
assertion). Never weaken or delete an assertion just to get green; only loosen one when
the intended contract actually changed, confirmed from the diff, not assumed.

## Wiring it in (Step 5)

`dev.txt.tmpl`'s TEST step references `%%regression_check%%`, a `dev.extra.txt`
section (like `style_rules`/`lifecycle_notes`, it's allowed to be blank — `loop.py`
only requires the section header to exist, not a non-empty body):

- Suite scaffolded → write the exact command that runs it (e.g. `npm run test:e2e --
  -- --project=staging`, or the script path for a bot-flow suite) plus one line
  telling Dev to add a spec for whatever the current task just shipped, when the flow
  is worth covering going forward.
- Step 4b explicitly skipped → leave the section body blank. Never invent a fake
  command — a `regression_check` section that silently does nothing is honest; one
  that references a command that doesn't exist fails Dev's TEST step for real.
