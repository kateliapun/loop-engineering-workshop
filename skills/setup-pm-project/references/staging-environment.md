# Staging environment patterns

## docker-compose (primary — the common case for a bot or small backend)

Generate `docker-compose.staging.yml` next to the existing `docker-compose.yml`, never
editing the original. Every name must differ from its prod counterpart so both can run
on the same host without colliding:

- Container names: append `_staging` (e.g. `acmebot_backend` → `acmebot_staging_backend`).
- DB name: append `_staging` (e.g. `acmebot` → `acmebot_staging`).
- Volumes: new named volumes, never reuse a prod volume.
- Network: a new isolated network, even if prod and staging end up on the same VPS as
  other projects' prod stacks.
- Host ports: prod port + 1 (e.g. `3000` → `3001`), or ask if that collides with
  something else already running on the host.
- Any bot/webhook token env var (e.g. `TELEGRAM_BOT_TOKEN`): staging needs its OWN
  token from a separate bot registration (e.g. via @BotFather) — reusing the prod
  token means staging and prod fight over the same webhook.
- SQLite (no `db` image in `docker-compose.yml`, `DATABASE_URL` points at a file, e.g.
  `sqlite:///./data/app.db`): there is no second DB container to add. Instead, give
  the staging service a new named volume distinct from prod's data volume (e.g.
  `<slug>_staging_data`, never the prod data volume) and set `DATABASE_URL` on the
  staging service to an explicitly different file path inside it (e.g.
  `sqlite:///./data/app_staging.db`), never the prod DB file. Never mount or reuse
  the prod data volume for staging — that defeats the whole point of a staging
  environment.

### Worked example — acmebot

Prod `docker-compose.yml` has `db` (postgres:16-alpine, db `acmebot`, container
`acmebot_db`) and `backend` (container `acmebot_backend`, port `3000:3000`,
network `acmebot_net`). The staging equivalent:

```yaml
name: acmebot_staging

services:
  db:
    image: postgres:16-alpine
    container_name: acmebot_staging_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: acmebot_staging
      POSTGRES_USER: acmebot
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - acmebot_staging_pgdata:/var/lib/postgresql/data
      - ../db:/docker-entrypoint-initdb.d:ro
    networks:
      - acmebot_staging_net

  backend:
    build: .
    container_name: acmebot_staging_backend
    restart: unless-stopped
    depends_on:
      - db
    environment:
      TELEGRAM_BOT_TOKEN: ${STAGING_TELEGRAM_BOT_TOKEN}
      SOME_THIRD_PARTY_API_KEY: ${SOME_THIRD_PARTY_API_KEY}
      ALLOWED_USER_IDS: ${STAGING_ALLOWED_USER_IDS}
      DATABASE_URL: postgresql://acmebot:${DB_PASSWORD}@db:5432/acmebot_staging
      PORT: 3000
      WEBHOOK_SECRET: ${WEBHOOK_SECRET}
      WEBHOOK_URL: ${STAGING_WEBHOOK_URL}
      ADMIN_TOKEN: ${ADMIN_TOKEN}
    ports:
      - "3001:3000"
    volumes:
      - acmebot_staging_uploads:/app/uploads
    networks:
      - acmebot_staging_net

volumes:
  acmebot_staging_pgdata:
  acmebot_staging_uploads:

networks:
  acmebot_staging_net:
    name: acmebot_staging_net
```

Note: this example reuses the prod values for `SOME_THIRD_PARTY_API_KEY`,
`WEBHOOK_SECRET`, `ADMIN_TOKEN`, and `DB_PASSWORD` — only the bot token gets its own
staging value. This is a deliberate pragmatic choice: provisioning separate
third-party credentials per project isn't in scope for this skill. Whoever runs the
wizard should be aware that staging traffic will consume prod API quota and act
against prod-scoped third-party accounts for these specific services.

### Bring-up (CONFIRM first)

1. Write the file, `git add`/commit it to the project repo (staging config is code,
   belongs in version control).
2. SSH to the host, `cd` to the project directory, `git pull`.
3. Check for a reverse proxy (Caddy/nginx) routing the prod subdomain — if one exists,
   add a matching `staging-<slug>...` block pointing at the new staging port, following
   the same pattern as the existing prod block. If none exists (prod is reached by IP
   or a nip.io host:port URL directly), use the same convention for staging
   (`staging-<slug>.<ip>.nip.io:<staging-port>` or equivalent).
4. Add the new `STAGING_*` keys the compose file references (e.g.
   `STAGING_TELEGRAM_BOT_TOKEN`, `STAGING_ALLOWED_USER_IDS`, `STAGING_WEBHOOK_URL`) to
   the host's `.env` file next to the compose file — `docker-compose` silently
   substitutes an empty string for any var missing from `.env`, so skipping this step
   produces a container that reports healthy with an empty bot token and an empty
   allow-list. `STAGING_WEBHOOK_URL` is the staging URL just set up in the reverse-proxy
   sub-step above. `STAGING_ALLOWED_USER_IDS` is set in Step 4
   (`references/test-user-and-seed.md`) — if Step 4 hasn't run yet, bring the container
   up anyway but expect the allow-list check to fail until Step 4 sets that var and the
   container is restarted (see that reference's Telegram section for the restart step).
5. `docker-compose -f docker-compose.staging.yml up -d --build`.
6. Verify: `curl` the staging URL's `/health` (or equivalent) endpoint, confirm 200.

## Vercel (secondary — no current project uses this; not yet live-validated)

1. Confirm the project already deploys to Vercel (`vercel.json`/`.vercel/project.json`
   present, or `vercel project ls` shows it).
2. Staging = Vercel Preview Deployments off a `staging` branch — no new hosting to
   provision, Vercel does this per-push automatically. Confirm preview deployments are
   enabled for the project (`vercel project inspect <name>`).
3. Test DB = a separate database branch/instance from prod, never a shared one:
   - Neon Postgres: `neon branches create --name staging --parent main` (or via the
     Vercel Storage integration's branching UI), then set the resulting connection
     string as the `DATABASE_URL` environment variable on the Preview environment only
     (`vercel env add DATABASE_URL preview`).
   - Any other managed DB: use its equivalent "branch/clone for a non-prod environment"
     feature. If it doesn't have one, STOP and ask the human — do not point staging at
     the prod database.
4. `app_url`/`api_base` in Step 5 becomes the Preview Deployment URL for the `staging`
   branch (`vercel ls`, or the URL Vercel prints on push).

## Generic process (tertiary — no docker-compose, no Vercel)

For a project that's just a process with a start command — no container, no managed
hosting — investigate before writing anything, the same discipline as the other two
patterns:

1. **Package manager & start command** — `package.json` (`dev`/`start`/`serve`
   script), `pyproject.toml` (`uvicorn`/`gunicorn`/a poetry script), `go.mod` (`go run`
   / a built binary), `Cargo.toml` (`cargo run`), a `Makefile` target, or a `Procfile`
   line. Note the exact command — don't invent one.
2. **Port** — grep `.env`/`.env.example`/framework config for `PORT`/`listen(`. Pick a
   distinct staging port (prod port + 1, or ask if that collides).
3. **Infra deps** — does it need Postgres/Redis/etc.? Same rule as docker-compose:
   staging gets its own DB name/instance and its own volume, never a shared one with
   prod. If nothing manages this yet (no compose file at all), a single
   `docker run` per dependency, named distinctly (`<slug>-staging-db`), is enough —
   don't build a whole compose file for one container.
4. **Env file** — copy `.env.example` (never `.env` itself, which may hold prod
   secrets) to a staging env file (e.g. `.env.staging`), pointing every `*_TOKEN`/
   `DATABASE_URL`/`*_ID` at the staging equivalents from Steps 2-3 and Step 4's test
   identity.
5. **Where it runs** — two options, pick based on what the human actually has:
   - **Remote host, no container orchestration** — run the staging process under
     whatever the host already uses for long-lived processes (`systemd` a unit file,
     `pm2`, or a detached `tmux` session as a last resort) so it survives a disconnect
     and restarts on host reboot. Never share the prod process's working directory or
     env file.
   - **No remote host at all (local-only)** — generate a one-command launcher instead:
     a single `scripts/dev-local.sh` (`up`/`down`/`status`/`logs`) that starts the
     process (and any `docker run` infra deps from Step 3) locally, reading
     `.env.staging`. `app_url`/`api_base` in Step 5 then points at `localhost:<port>`,
     and this only works while the launcher is actually running on this machine —
     say so plainly in the Step 6 report, since Dev/Tester/PM all assume the
     environment they're pointed at is reachable when a cycle runs.
6. Verify: hit the staging port's `/health` (or equivalent) endpoint, or the process's
   own smoke check, confirm it responds.
