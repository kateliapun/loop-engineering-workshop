# Verification driver patterns

Tester (`pm.txt.tmpl`) is dispatched for every live check because PM holds none of
`evidence_method`'s tools itself — so whatever `evidence_method` says has to be an
actual, callable tool by the time this step is done, not just a sentence in the
prompt. Inventory before installing anything — reuse what already works:

1. Check `agents/<slug>/config.yml` (if this is a retrofit) for an existing
   `evidence_method` — does the tool it names still exist and run?
2. Check for a browser driver already available in this environment (e.g. a `browse`
   binary skill, an installed `playwright` with the `chrome` channel) before installing
   a second one.
3. Only install what's actually missing.

## Web UI

1. Confirm a browser automation tool is installed and callable — a `browse`-style
   binary skill if this environment already has one, otherwise Playwright
   (`npx --yes @playwright/cli --version`; install it + the `chrome` channel if
   missing — CONFIRM first, this installs global tooling).
2. Confirm it can actually reach the Step 3 staging `app_url` — one real navigate +
   screenshot, not just a version check.
3. `evidence_method` (Step 5): name the concrete tool and its invocation, e.g.
   `"as a real user — use <the browse binary path> against %%app_url%%"`.

## Telegram bot (or any bot with no web UI)

A bot has no page to screenshot, so "read the source code" is the instinctive
fallback — but that's static analysis, not proof the running staging bot actually
behaves that way. Give Tester a real test client instead:

1. Confirm the Step 4 test identity (`test_telegram_id`) can actually message the
   staging bot — this should already be true from `test-user-and-seed.md`'s own
   verification sub-step; if it isn't, fix that first.
2. Provision a scripted client that can act as that identity programmatically — not
   a human manually opening Telegram. Two options, in order of preference:
   - A Telegram **user** client (e.g. `telethon`/`gramjs`) logged in once as the test
     account (its own session file, stored outside the repo, never committed) — can
     send a real message to the staging bot and read back the real reply, the same
     path a real user takes.
   - If standing up a user-client session isn't practical yet, the staging bot's own
     webhook/long-poll endpoint hit directly with a synthetic Telegram Update payload
     (`curl`/a small script) — weaker (bypasses Telegram's own transport) but still
     exercises real bot logic against the real staging DB, unlike reading source.
3. Wrap it as one callable script (e.g. `scripts/tg_test_client.py send "<text>"` /
   `... read`) so Tester's dispatch is a tool call, not free-form messaging.
4. `evidence_method` (Step 5): name the script, e.g. `"as %%test_telegram_id%% — use
   scripts/tg_test_client.py against the staging bot to send messages and read
   replies. Fall back to reading source only for behavior that has no observable
   message-level effect (e.g. an internal scheduled job)."`

## API / service (no UI at all)

1. Confirm an HTTP client is available (`curl`, or the project's own typed client) and
   can reach the staging `api_base` with the Step 4 test credentials.
2. `evidence_method`: name the concrete calls, e.g. `"curl %%api_base%% with the
   %%test_user_email%% session token; assert on response body and status."`

## CLI

1. Confirm the built binary/entry point runs against staging config (a `--env
   staging` flag, or `STAGING=1` env var — whatever the project already has; add one
   if it doesn't).
2. `evidence_method`: the exact invocation and what in stdout/exit-code counts as
   pass/fail.

## Verify before moving on

Whatever pattern above applies, do one real end-to-end check now — not just "the tool
is installed" — before writing `evidence_method` into Step 5: actually exercise the
staging environment through the driver and confirm you get back a real, current
response. A driver that's installed but has never actually reached staging is an
untested assumption, not a working setup.
