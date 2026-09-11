---
name: setup-pm-project
description: Onboard a project (new or existing) into the pm-dev-agents PM/Dev loop — creates canonical GitHub issue labels, provisions an isolated staging environment (test DB + seeded test user + test deployment), and generates agents/<slug>/config.yml + prompts/*.extra.txt. Use when asked to set up, onboard, or wire a project into pm-dev-agents, or to add staging/test-user infra to an existing one.
---

# Setup PM Project

Wizard that onboards a project into the pm-dev-agents loop (`_core/loop.py`). Safe to
re-run against a project that's already partially set up — every step checks current
state first and reports "already set up" instead of redoing work.

`<agents_root>` below means wherever `_core` (this repo) is checked out, with each
project's `agents/<slug>/` folder as its sibling (never nested inside `_core`) — e.g.
`/home/you/agents` on a typical VPS setup. Resolve it from where `_core` actually lives
on this machine; don't assume that literal path.

Ask for the project slug if not given. Then work through the steps below in order,
stopping to confirm before anything in the CONFIRM list.

CONFIRM before running (never run these without an explicit yes from the human driving
this session):
- Any `gh repo create`.
- Any `gh label create` batch (show the exact commands first).
- Any SSH command that changes state on a remote host (bringing up docker containers,
  editing a Caddyfile, restarting services).
- Any action that touches an existing database or volume (not creating a new one).
- Overwriting an existing `agents/<slug>/config.yml` or `prompts/*.extra.txt` (a
  retrofit run can silently replace a live prod `deploy_cmd` with no way to recover the
  old value — there is no git history for `<agents_root>`).

If a step's prerequisite is missing (no `gh` auth, no SSH access, no docker on the
target host) — stop at that step, report exactly what's missing, do not skip ahead.

Installing a new local/global tool (a browser driver, a bot test client's
dependencies) also goes through CONFIRM — same bar as the list above, it's changing
state on whatever machine this session runs on.

---

## Step 1 — Discover

1. Resolve `project_path`:
   - If `<agents_root>/<slug>/config.yml` exists, read `project_path` from it.
   - Otherwise ask the human for the local path to the project.

2. Detect the stack by reading files at `project_path` (do not guess — read them):
   - `docker-compose.yml` present → containerized. Parse it for:
     - DB engine: a `postgres`/`postgresql` image → Postgres; a `DATABASE_URL` env var
       containing `sqlite` → SQLite.
     - Existing service names, ports, and container/volume/network names — needed to
       build non-colliding staging equivalents in Step 3.
   - `vercel.json` or `next.config.*` present → Vercel-hosted.
   - Neither found → look for a generic process-based stack instead of stopping:
     `package.json`/`pyproject.toml`/`go.mod`/`Cargo.toml`/`Makefile`/`Procfile`, a
     `dev`/`start`/`serve` script, and the port(s) it binds (grep `.env`/config for
     `PORT`, `listen(`). See `references/staging-environment.md`'s "Generic process"
     pattern.
   - None of the three found → STOP. Ask the human directly how this project is
     deployed. Do not assume a topology.

3. Detect the identity/auth model — this determines the Step 4 (test user) pattern:
   - An `ALLOWED_USER_IDS` (or similar allow-list) env var in `docker-compose.yml` /
     `.env.example` → Telegram-bot-gated, single allow-listed identity per deploy. No
     web signup exists; the "test user" is a staging bot + a test ID on its allow-list.
   - A `/signup`, `/register`, or similar auth route in the backend source → traditional
     web-auth app. The "test user" is a seeded fixture account created through that
     route.
   - Neither found → STOP. Ask the human how this project identifies users.

4. Detect the GitHub repo:
   - `git remote get-url origin` in `project_path`, or ask.
   - `gh repo view <owner>/<repo>` to confirm it exists. If it doesn't, CONFIRM then
     `gh repo create <owner>/<repo> --private --source=<project_path>`.

5. Report what was found before moving on: stack, DB engine, identity model, repo. This
   is the discovery report — show it to the human, then continue.

---

## Step 1b — Legibility check

Dev's prompt (`dev.txt.tmpl`) reads `%%project_path%%/CLAUDE.md` first, always, and
follows its constraints exactly — but that file is only useful if it actually has
content. A project with months of prior cycles has a real one; a brand-new onboarding
does not, and Dev will otherwise improvise conventions cycle to cycle instead of
following a stable one.

1. Check whether `CLAUDE.md` (or `AGENTS.md`) exists at `project_path` and has more
   than a stub (rough bar: covers stack, run/test commands, and at least one actual
   constraint — not just a title).
2. Already substantive → leave it alone, report "CLAUDE.md already set up," move on.
3. Missing or thin → generate a **map, not a manual**: a short (~100 line) file with
   a one-line overview, the project tree, a short "golden rules" list (hard
   invariants — ask the human for any they already hold in their head but never wrote
   down), and a table of where to look for what (routes, models, tests, config). Pull
   the factual parts (stack, structure, run commands) from Step 1's discovery instead
   of guessing. Depth belongs in the project's own `docs/`, if it has one — this file
   stays a table of contents.
4. Show the generated file to the human before writing it — this is a judgment call
   about what counts as a golden rule, not a mechanical fill-in.

---

## Step 1c — Product docs

PM keeps its product model in the repo (`product/VISION.md`, `ROADMAP.md`,
`DECISIONS.md`, `FEEDBACK.md`, `research/`) so the human can read and edit it. PM
writes VISION/ROADMAP itself in its first Discovery cycle; this step only seeds the
human's side:

1. Create `product/FEEDBACK.md` with three headers: `## Inbox` (human writes here),
   `## Questions` (PM writes here, each with its working assumption), `## Processed`.
2. Ask the human two questions and, if they have answers, write them into a starter
   `product/VISION.md` for PM to refine: *who is the user and what job do they hire this
   product for?* and *what would "the product meets its purpose" look like, concretely?*
   If they don't know yet, skip — PM's user-sim/market-research will propose one.
3. Commit both on the default branch (docs only). CONFIRM before committing to a repo
   the human hasn't said you may commit to.

---

## Step 2 — GitHub labels

Idempotent: for each label below, check `gh label list --repo <repo> --json name` first
and only create the ones missing.

| name | color | description |
|---|---|---|
| `<queue_label>` (ask; default `pm-task`) | `1d76db` | Dev's queue — filed by PM |
| `P1` | `b60205` | Unblocks everything after it / core job broken — dev picks first |
| `P2` | `fbca04` | Normal priority |
| `P3` | `c5def5` | Low — dev does not pick these up; PM bundles polish instead |
| `feature` | `a2eeef` | Task is a slice of an initiative |
| `bug` | `d73a4a` | Task fixes confirmed broken behavior |
| `tech-debt` | `fef2c0` | Task pays down structure, no user-visible change |
| `initiative` | `0052cc` | PM's epic — Before/After story + Done-when list; tasks say `Part of #n` |
| `ready-for-review` | `0e8a16` | Shipped to staging, awaiting PM verification |
| `in-progress` | `5319e7` | Claimed by dev agent |
| `human-blocker` | `e11d21` | Needs the human — credentials/access/an irreversible decision |
| `needs-clarification` | `d4c5f9` | Dev needs more detail before starting |
| `goal` | `006b75` | Standing objective — only the human edits/closes this |

No `idea` label: uncommitted candidates live in `product/ROADMAP.md` (`## Later`,
`## Polish`), which PM prunes, instead of rotting as open issues.

CONFIRM, then for each missing label:
```
gh label create "<name>" --repo <repo> --color <color> --description "<description>"
```

---

## Step 3 — Staging environment

See `references/staging-environment.md` for the docker-compose, Vercel, and generic
process patterns. Follow the one matching the stack detected in Step 1. CONFIRM before
the SSH/bring-up sub-step in any of the three.

## Step 3b — Verification driver

PM never touches the live product itself — every live check goes through a dispatched
Tester subagent (see `pm.txt.tmpl`), and Tester needs a concrete tool to actually
exercise the product with, not a description of one. `evidence_method` (Step 5) is
just the sentence PM reads; this step is what makes that sentence true.

See `references/verification-driver.md`. Follow the pattern matching the app shape
detected in Step 1 (web UI / bot / API / CLI) — confirm the driver is installed and
can actually reach the staging environment from Step 3 before moving on. Falling back
to "read the source code" as the only verification method is a last resort, not a
default — it's static analysis, not behavioral proof, and it's what this step exists
to avoid for the common cases (a bot with no web UI, in particular).

## Step 4 — Test user + test DB

See `references/test-user-and-seed.md` for the Telegram-bot-allowlist pattern and the
traditional web-auth seed-script pattern. Follow the one matching the identity model
detected in Step 1.

## Step 4b — Regression suite

Dev's own TEST step (`dev.txt.tmpl` step 6) is a per-task spot-check, and Tester's live
sweeps are LLM-driven and re-verify from scratch every cycle — neither is a fast,
deterministic, compounding gate against regressions. See
`references/regression-suite.md` for a small committed suite (one spec per shipped
flow, reusing the Step 4 test identity as a session helper) that both of those lean on
instead of re-earning the same confidence by hand every cycle.

Skip this step only if the human explicitly doesn't want one yet — note that in the
Step 6 report so it's a visible decision, not a silent gap.

## Step 5 — config.yml + prompts

See `references/config-and-prompts.md`. Start from `_core/examples/web-app/` or
`_core/examples/telegram-bot/` (copy the folder to `<agents_root>/<slug>/`), then edit
`config.yml`, `prompts/{pm,dev}.extra.txt`, and seed `flow_checklist.md` with this
product's real surfaces.

If `agents/<slug>/config.yml` or any `prompts/*.extra.txt` already exists, this is a
retrofit run — show the human a diff of exactly what would change (including the
current `deploy_cmd`, if any) and require a typed, explicit confirmation (not a bare
"yes/ok") before overwriting. There is no git history for `<agents_root>`, so an
overwritten value cannot be recovered.

**Smoke check** — after writing `config.yml` and `prompts/*.extra.txt`, confirm they
actually render before moving on, so a missing var (e.g. a `%%var%%` referenced in an
`.extra.txt` section but never added to `vars:`) fails now instead of surfacing later
as a silent misconfiguration:

```bash
python3 <agents_root>/_core/loop.py <slug> --render pm  >/dev/null && \
python3 <agents_root>/_core/loop.py <slug> --render dev >/dev/null && echo OK
```

A non-zero exit names the unresolved var. Read the rendered PM prompt once — it is
exactly what the agent will see.

## Step 6 — Report

Summarize in one message: what was created, what already existed and was left alone,
and what still needs the human (DNS, secrets/API keys, deciding this project's
prod-promotion policy — see "Prod-promotion boundary" below). Include the CLAUDE.md
verdict (already substantive / generated fresh — Step 1b), whether a starter
`product/VISION.md` was seeded (Step 1c), which driver was provisioned
(Step 3b), and whether a regression suite was scaffolded or explicitly deferred
(Step 4b). If Step 5 overwrote an existing `config.yml`, explicitly echo back the
previous `deploy_cmd` value that was replaced — it's now the human's manual
prod-promotion command going forward and this may be the only record of it.

---

## Prod-promotion boundary

`deploy_cmd` generated in Step 5 always targets STAGING, never prod. This skill never
generates a `promote_cmd`, and the default `promote_policy: manual` means no agent
touches production: PM files one `human-blocker` listing what's pending and the human
runs the promotion.

Automatic promotion exists but is opt-in and never set up here. A human who wants it
adds `promote_cmd` plus `promote_policy: on_pm_accept` by hand, having understood the
consequence — PM closing an issue then ships it to real users. Say this in the Step 6
report, and leave it to them. See README "Release safety".

If the project has no separate staging at all, do not silently fall back to pointing
`deploy_cmd` at production: say so, and require `verify_cmd` (the runner's revert-and
-redeploy net) before the loop is left running unattended.
