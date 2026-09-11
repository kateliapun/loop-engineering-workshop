# pm-dev-agents

_Shared from the "Loop Engineering" talk — a real, working example of the PM/Dev loop
pattern discussed there. Clone it, copy `examples/web-app/` or `examples/telegram-bot/`
to get started, or run `skills/setup-pm-project` for a guided setup._

Two Claude Code agents — a **PM** and a **Dev** — that run a product on their own. A human
sets a goal and drops feedback; the PM discovers what the product should become, files
initiatives and tasks, and verifies what shipped; the Dev designs, builds, gets its code
reviewed, and ships. Both lean on named subagents for the work a single context does
badly (testing the live product, interviewing the real users, auditing the codebase,
architecture and code review).

Works for any repo with a deployment the agents can reach and some way to exercise the
product (a browser, a scripted Telegram client, an HTTP client). A separate staging
environment is supported and recommended, but not required — see "Release safety". Examples for a web app and a
Telegram bot are in `examples/`.

## Run

```bash
cd <agents_root>              # _core (this repo) + one folder per product as siblings
python3 _core/loop.py <slug>              # interactive: asks how many cycles
python3 _core/loop.py <slug> --once       # one cycle, no prompt — for cron
python3 _core/loop.py <slug> --render pm  # print the rendered PM prompt + subagent JSON, no claude call
```

Run in `tmux` or cron. Each cycle: PM runs (always) → Dev runs if the queue has P1/P2
tasks → pause. The cycle counter persists in `<slug>/state.db` so pacing rules
("OBSERVE sweep every 3 cycles") survive restarts.

Needs `claude` and `gh` authenticated, `pip install pyyaml requests` (and `Pillow` for
evidence GIFs). `claude --agents` is used when the installed CLI supports it; otherwise the
subagent briefs are inlined into the role prompt automatically.

## What the human does

- **Set the goal**: one open GitHub issue labelled `goal` — a metric or direction
  ("users complete a migration without support", "2x weekly active"). PM never edits it.
- **Give feedback**, any of three ways, whenever you like:
  - write in `product/FEEDBACK.md` under `## Inbox` (PM processes it next cycle and
    moves it to `## Processed` with what it did);
  - edit `product/VISION.md` or `product/ROADMAP.md` directly (PM treats a commit not
    by itself as a directive);
  - comment `human: <what you want>` on any issue.
- **Unblock**: PM/Dev file `human-blocker` only for things they truly can't do
  (credentials, infra, an irreversible product decision). Non-blocking questions land
  in `FEEDBACK.md` `## Questions` with the assumption PM is already proceeding on —
  answer them or don't.
- **Follow along** from GitHub alone: the active `initiative` issue gets a short
  shipped / next / decisions comment whenever something moved.

You don't define features and you don't check each one — the PM does, against
`product/VISION.md` and the quality bar in `config.yml`.

## How the PM decides what to build

The old loop's PM could only see Tester screenshots of screens that already existed, so
it filed polish. This one keeps a **product model** in the repo and runs in two modes:

**Discovery** (no vision yet, no active initiative, an initiative just finished, a
rethink requested, or a light refresh every N cycles):
- `codebase-audit` — what the product actually does today (read-only).
- **Interview the real users directly** — no simulated personas. PM messages them
  through the shared interview bot (`interview_ids` in `config.yml`; see "Real user
  interviews" below), or writes to `FEEDBACK.md ## Interview` if none is configured,
  with 2-4 concrete questions grounded in VISION. Replies are delivered automatically —
  `loop.py` fetches and appends them before PM's cycle starts, no polling on PM's part.
  Research only, never a retention nudge; a decision ask goes through `human-blocker`.
- PM synthesizes: rewrites `VISION.md`, drafts 3-6 candidate **initiatives** (each with
  a Before/After user story, mechanism to the goal, size), applies the **ambition guard**
  (if After differs from Before only in adjectives, it's polish, not an initiative),
  ranks, picks one `## Active`, files it as an `initiative` issue with a user-observable
  `## Done when` list, logs the decision.

**Delivery** (an initiative is active): PM slices it into vertical, user-visible tasks
(`pm-task` + `P1/P2` + `feature`, body `Part of #<initiative>`), using `flow` (interaction
alternatives) and `logic` (correct architectural shape) where the slice warrants it.
When all slices are closed, `tester` checks the initiative's Done-when list before PM
closes it — then back to Discovery.

**Maintenance** rides along inside a fixed share of capacity (`delivery_ratio`, default
70/30): a Tester OBSERVE sweep every `observe_every` cycles with rotating lenses, P1 bugs
jump the queue, P3/polish accumulate in `ROADMAP.md ## Polish` and get bundled into one
task when Dev has slack. No `idea` label — GitHub issues without commitment rot.

## How the Dev keeps quality up

Per task: orient (CLAUDE.md, `docs/ARCHITECTURE.md`, VISION, the initiative) → **reuse
search** before writing anything → **design note** as an issue comment (approach, files,
data, reuse, rejected alternatives, tests, risks) → `architect` subagent critiques it
(mandatory for M/L or anything touching data model / shared modules / new dependency) →
implement test-first → verify like a user → `reviewer` subagent reviews the diff
(mandatory, BLOCKING findings must be fixed, two rounds max) → PR with `Ref #n`, merge,
deploy staging → update `ARCHITECTURE.md` if a module/entity/pattern was added → Done
comment with tests and review verdict. Too big for a session → ship the first working
slice and file the remainder as its own task. Dev never closes issues.

## Release safety

Two gates, both enforced by `loop.py` after a role exits rather than by prompt text — a
role can forget an instruction, it cannot forget a check that runs after it. Every key
is optional: set none and the loop behaves exactly as it did before they existed.

| Key (under `vars:`) | What it buys |
|---|---|
| `verify_cmd` | Independent health check after each cycle. Non-zero exit → the cycle's commits are reverted and the previous state redeployed. |
| `rollback_cmd` | Overrides the default rollback, for when an undo needs more than a revert (a database restore, a blue/green switch). |
| `preview_cmd` + `preview_url` | A pre-merge environment. Dev must deploy the branch and verify it **before** the PR is merged. |
| `promote_cmd` + `promote_verify_cmd` + `prod_url` | Production as a separate environment from `app_url`. |
| `promote_policy` (top level) | `manual` (default) or `on_pm_accept`. |

**Pick a shape.** Both are legitimate; the choice is about what an undo costs versus
what a second environment costs.

*One environment* (`examples/telegram-bot`) — the agents own a single deployment and
there is no promotion. Dev merges, deploys, and `verify_cmd` is the net: a bad change is
reverted inside the same cycle instead of sitting live until someone notices. Main is
not protected before the merge; the deployment is. This is the right shape for a small
product where a second stack costs more than it is worth — **but only with
`verify_cmd` set.** No second environment and no undo is the one combination to avoid.

*Two environments* (`examples/web-app`) — `deploy_cmd` targets staging, `promote_cmd`
targets production. Add `preview_cmd` and main is protected too, because Dev verifies
the branch before merging rather than after.

### Promotion

`promote_cmd` makes production a separate environment, and a git tag named `promoted`
records where it is. `git log promoted..main` is therefore the pending-promotion queue —
no new state to keep in sync, readable by hand at any time. On the first cycle the tag is
planted at the current head and nothing is promoted; production is assumed to be
whatever is already live.

Under `promote_policy: on_pm_accept`, the runner promotes once **every issue referenced
by an unpromoted commit is closed**. PM closing an issue is what marks work accepted, so
that one rule is the whole gate — and PM's prompt says so explicitly, because it means
closing an issue ships it to real users. A commit referencing no issue (PM's own product
-doc commits) never blocks. The tag moves only after both `promote_cmd` and
`promote_verify_cmd` succeed, so a failed promotion leaves the queue intact and retries
next cycle rather than silently marking work as live. An issue `gh` can't read counts as
open — it fails closed.

Under `manual` (the default) the runner never touches production; PM instead files one
`human-blocker` listing what's pending with the command to run, and the Telegram notice
for blockers fires as usual.

### What rollback does

`git revert`, never `reset --hard` — the branch is shared with GitHub, so an undo has to
be a new commit rather than rewritten history. The window is the whole cycle, so a
rollback also reverts PM's product-doc commits from that cycle: deliberate, so the
deployed tree returns to one known-good state instead of a half-reverted mix. If the
revert conflicts, nothing is redeployed and the loop says so — a half-reverted tree must
never ship.

## Layout

```
<agents_root>/
  _core/                       this repo
    loop.py                    runner: renders prompts, builds --agents JSON, gates roles
    prompts/pm.txt.tmpl        PM cycle (shared across projects)
    prompts/dev.txt.tmpl       Dev pipeline (shared)
    prompts/subagents/*.md     tester, codebase-audit, flow, logic, architect, reviewer
    scripts/interview_bot.py   the shared interview channel every project reuses (see below)
    scripts/send_dm.py         sends one message through it
    scripts/publish_evidence.py
    examples/web-app/          copy → <agents_root>/<slug>/ and edit
    examples/telegram-bot/
    skills/setup-pm-project/   /setup-pm-project onboarding wizard (labels, staging, driver, config)
    tests/test_render.py       renders every example; run `python3 tests/test_render.py`
    tests/test_release.py      the verify/rollback and promotion gates
    .env                       shared secrets — INTERVIEWER_BOT_TOKEN goes here, once
  <slug>/
    config.yml                 project_path, github_repo, interview_ids, vars, roles
    prompts/{pm,dev}.extra.txt project fill-ins (=== section === blocks → %%section%% in the .tmpl)
    memory.md · pm_workbook.md · flow_checklist.md · state.db · .env
  .interview/                  shared inbox + Telegram offset — every project reads it

<project repo>/
  product/VISION.md · ROADMAP.md · DECISIONS.md · FEEDBACK.md · research/   PM-owned
  docs/ARCHITECTURE.md                                                      Dev-owned
  CLAUDE.md                                                                 constraints
```

### Real user interviews

One Telegram bot, shared by every project, replaces the old `user-sim` subagent — PM
talks to the actual people instead of playing a persona.

1. Create a bot via [@BotFather](https://t.me/BotFather) once — this is *not* any
   product's own bot. Put its token in `_core/.env` as `INTERVIEWER_BOT_TOKEN`.
2. Each real person sends this bot any message once (Telegram requires first contact
   before a bot can message someone) — that also gives you their `chat_id`.
3. In a project's `config.yml`, list them: `interview_ids: {Kate: "111...", Alex: "222..."}`.
   That's the whole setup — `interview_cmd`/`interview_replies` are derived automatically.

Every cycle, before running any role, `loop.py` calls `interview_bot.sync()` (fetches
new Telegram messages into a shared, lock-protected inbox — safe with multiple projects
running at once) then `interview_bot.deliver()` (appends any new reply from this
project's `interview_ids` into its `FEEDBACK.md ## Interview`). PM never polls
Telegram itself; it just reads `FEEDBACK.md` like any other file, same as it always has.
No `interview_ids` configured → PM writes questions to `FEEDBACK.md ## Interview` and a
human relays them instead — same section, no code path change for PM.

### config.yml

See `examples/*/config.yml` — every knob is commented there. Required `vars`:
`human_name`, `quality_bar`, `evidence_method`, `exploration_log`, `flow_checklist`.
Also required, as `=== section ===` blocks in the project's `prompts/*.extra.txt`
(the examples have all three — copy one rather than starting from scratch):
`evidence_capture` in `pm.extra.txt`, `branch_setup` and `ship_deploy` in
`dev.extra.txt`. A missing one is a hard error at render time, not a silent default.
Everything else has a default (`queue_label: pm-task`, `product_dir: product`,
`architecture_doc: docs/ARCHITECTURE.md`, `delivery_ratio: 70/30`, `queue_cap: 5`,
`observe_every: 3`, `rediscover_every: 10`, `dev_tasks_per_session: 3`,
`docs_branch: main`, `interview_subjects: human_name`). A var may reference a built-in
(`%%agents_dir%%`, `%%core_dir%%`, `%%project_path%%`, `%%repo%%`, `%%cycle%%`).

Optional: `verify_cmd`, `preview_cmd`, `promote_cmd` and friends turn on the release
gates — see "Release safety" above. `interview_ids` (top-level, not under `vars:`) turns on real user interviews —
see "Real user interviews" above. `interview_cmd`/`interview_replies` are derived from
it automatically; set them yourself only to bypass the shared bot.

Per role: `tools` (PM needs `Agent`; add your browser MCP tool and `WebSearch,WebFetch`
if wanted), `subagents` (names from `prompts/subagents/`), optional `model`,
`permission_mode`, `gate`, `post_delay`.

### Labels

`goal` · `initiative` · `pm-task` · `P1` `P2` `P3` · `feature` `bug` `tech-debt` ·
`in-progress` · `ready-for-review` · `needs-clarification` · `human-blocker`.
`skills/setup-pm-project` creates them idempotently.

## New project

`scripts/install-skill.sh` installs `/setup-pm-project` into Claude Code; it walks
through labels, a staging environment, a verification driver (browser / Telegram test
client / HTTP), a test identity, a regression suite, `CLAUDE.md`, `product/FEEDBACK.md`
and `config.yml`. Or copy `examples/<shape>/` to `<agents_root>/<slug>/` and edit the
values marked `CHANGE`. Then `python3 _core/loop.py <slug> --render pm` to check it
renders, and `--once` for a supervised first cycle. The first cycle is always Discovery:
expect a `product/VISION.md`, a `ROADMAP.md` and one `initiative` issue, no code.

## Stopping and steering

- `Ctrl+C` stops the loop; the cycle counter is kept.
- `human: <text>` on an issue redirects the agent on that issue.
- Delete or edit `ROADMAP.md ## Active` to change direction; PM picks it up next cycle.
- `sqlite3 <slug>/state.db "insert or replace into state values('paused','true')"` pauses
  between cycles.
