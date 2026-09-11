# Test user patterns

## Telegram-bot-allowlist (primary — no web signup, access gated by user ID)

These apps have no web signup — access is gated by an `ALLOWED_USER_IDS` env var
checked against the Telegram user ID messaging the bot. There is nothing to "seed" in
a database; the test user IS the staging bot's allow-list entry.

1. Register a new bot with @BotFather for staging (never reuse the prod bot token —
   two processes can't hold the same bot's webhook). Get its token.
2. Set `STAGING_TELEGRAM_BOT_TOKEN` in the staging environment (Step 3) to that token.
3. Pick a fixed test Telegram user ID — ask the human for one they control (their own
   ID, or a throwaway account made for this), never a placeholder.
4. Set `STAGING_ALLOWED_USER_IDS` to that ID (in the host's `.env` file next to
   `docker-compose.staging.yml` — see the Bring-up section of
   `references/staging-environment.md`). If the staging container is already running,
   re-run `docker-compose -f docker-compose.staging.yml up -d` (or restart the backend
   container) so the new value actually takes effect — an env var added to `.env` after
   a container has started does not apply until that container restarts.
5. Verify: message the staging bot from that Telegram account, confirm it responds
   (proves the allow-list + staging DB path work end to end).
6. Record the test ID in `agents/<slug>/config.yml` as `vars.test_telegram_id` so
   PM/Dev agents know which identity they're allowed to act as when testing. This value
   only reaches PM/Dev agents if a prompt section actually references
   `%%test_telegram_id%%` — see `references/config-and-prompts.md`'s paragraph on
   Step 4 test-identity vars, and the `load_context` skeleton section there.

This replaces the pattern some projects start with — a `telegram_id=999999` row
flagged `is_test=1` inside the LIVE PRODUCTION database, exercised via
`?user_id=999999` against the prod URL — that pattern is not reused here; it's what
this whole design exists to replace.

## Web-auth seed script (secondary — for a future project with real signup)

1. Find the project's signup/register endpoint or CLI (e.g. a `POST /api/auth/signup`
   route, or a framework-provided create-user management command).
2. Write `scripts/seed_test_user.<ext>` (match the project's language) that calls that
   real endpoint/command against the STAGING database only, with fixed, known
   credentials — e.g. `test@<slug>.local` / a generated password — and is safe to run
   more than once (check if the account already exists before creating it).

   Example shape (Node/TypeScript — adapt to the project's actual auth stack, this is
   not a literal file to copy verbatim):

   ```ts
   import { signup, findUserByEmail } from '../src/auth';

   const EMAIL = 'test@<slug>.local';
   const PASSWORD = process.env.STAGING_TEST_USER_PASSWORD ?? 'change-me-in-config';

   async function main() {
     const existing = await findUserByEmail(EMAIL);
     if (existing) {
       console.log('test user already exists, skipping');
       return;
     }
     await signup({ email: EMAIL, password: PASSWORD });
     console.log(`created test user ${EMAIL}`);
   }

   main();
   ```

3. Run it once against staging, confirm login works through the normal auth flow (not
   a bypass).
4. Record `test_user_email` / `test_user_password` in `agents/<slug>/config.yml` vars.
   Note: `agents/<slug>/.env` (loaded by `loop.py`'s `load_env_file`/`run_role` directly
   into the agent subprocess's environment) is a better home for the password
   specifically than `config.yml`'s `vars:` block — `vars:` values get interpolated
   directly into the prompt text passed as a `-p` CLI argument, which is visible in the
   host's process list; `.env` values are not.
