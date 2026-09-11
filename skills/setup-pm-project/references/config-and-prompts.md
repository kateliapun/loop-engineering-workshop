# config.yml + prompts generation contract

Generates two things per project: `<agents_root>/<slug>/config.yml` and
`<agents_root>/<slug>/prompts/{pm,dev}.extra.txt`, plus three seed files
(`memory.md`, `pm_workbook.md`, `flow_checklist.md`). Start from
`_core/examples/web-app/` or `_core/examples/telegram-bot/` — copy the whole folder,
rename it to the slug, then edit every value marked `CHANGE`. Verify against
`_core/prompts/pm.txt.tmpl`, `dev.txt.tmpl` and `prompts/subagents/*.md` directly —
the lists below describe them but the files are the contract.

## config.yml

Top-level: `slug`, `project_path`, `github_repo`, `roles`; `cycle_pause`, `max_runs`
optional (defaults 300 / 20).

`vars:` — three tiers:

- **Required, no default — hard runtime error if missing:**
  - `human_name` — the actual person this loop answers to. Ask; never copy from
    another project.
  - `quality_bar` — what "good" means for THIS product, a short bulleted rubric. PM
    judges shipped work against it, Reviewer judges code against it. Ask the human
    what axes matter for this product (a bot: reply clarity, state across gaps; a data
    tool: never lose or silently change data; a game: feel, pacing). Don't default to
    the SaaS rubric.
  - `evidence_method` — how anyone exercises the live product, naming the actual
    installed tool from `verification-driver.md` (Step 3b). This sentence is handed to
    Tester and user-sim; if the tool it names isn't installed, they fall back to reading
    code and the whole loop degrades.
  - `exploration_log`, `flow_checklist` — paths, conventionally
    `"%%agents_dir%%/pm_workbook.md"` and `"%%agents_dir%%/flow_checklist.md"`. Seed
    both files (the examples have seeds; `flow_checklist.md` must list this product's
    real surfaces, all `[due]`).
- **Defaulted — set only to override:** `queue_label` (pm-task), `reviewer_name` (PM),
  `pm_title` (PM), `product_dir` (product), `architecture_doc` (docs/ARCHITECTURE.md),
  `docs_branch` (main), `delivery_ratio` (70/30), `queue_cap` (5), `observe_every` (3),
  `rediscover_every` (10), `dev_tasks_per_session` (3).
- **Referenced only by your own `.extra.txt` sections or briefs — required only if
  used:** `app_url`, `api_base`, `deploy_cmd`, `log_check_cmd`, `test_identity_note`
  (what identity Tester/user-sim act as and how — always the Step 4 test identity,
  never a real user), `test_telegram_id` / `test_user_email`, anything else you
  invent. `loop.py` substitutes `%%name%%` wherever it appears — in templates, briefs,
  sections, and inside other var values — so a var can reference `%%agents_dir%%`,
  `%%core_dir%%`, `%%project_path%%`, `%%repo%%`, `%%cycle%%`.

`roles:` — copy the example shape. `pm.tools` MUST include `Agent` (PM has no live-
product tool; Tester/user-sim are how it sees anything) and should include the browser
MCP tool name if one is installed, plus `WebSearch,WebFetch` for `market-research`.
`pm.subagents: [tester, user-sim, market-research, codebase-audit, flow, logic]`;
`dev.subagents: [architect, reviewer]`; `dev.tools` includes `Agent`. `dev.gate` keeps
Dev from running on an empty queue. Optional per role: `model`, `permission_mode`.

## prompts/pm.extra.txt sections

`=== name ===` on its own line, body until the next header. Required: `evidence_capture`
(how Tester's screenshots become an `$EVIDENCE_URL` — see the examples; may say "leave
empty"). Optional (blank is fine, header may be omitted): `context_files`, `pm_rules`,
`load_context` (a good place for the test identity and a `log_check_cmd`),
`goal_complete_extra`, `body` (project-specific extra steps).

## prompts/dev.extra.txt sections

Required: `branch_setup`, `ship_deploy` (must target STAGING via `%%deploy_cmd%%` —
this is where the prod-promotion boundary is enforced day to day). Leave `%%ship_gate%%`
in the template alone: `loop.py` fills it from `preview_cmd` and it renders empty when
the project has no pre-merge environment. Optional:
`context_files`, `lifecycle_notes`, `queue_filters`, `plan_notes` (project patterns Dev
must follow in its design note), `style_rules`, `regression_check` (the exact command
from `regression-suite.md`, or blank if Step 4b was skipped — never a made-up command),
`extra_done_fields`, `body`.

## Smoke check

```bash
python3 <agents_root>/_core/loop.py <slug> --render pm  >/dev/null && \
python3 <agents_root>/_core/loop.py <slug> --render dev >/dev/null && echo OK
```

Any unresolved required var exits non-zero with its name. Look at the rendered output
once too — it's what the agent actually reads.

## Product docs the loop expects in the repo

PM writes `product/VISION.md`, `ROADMAP.md`, `DECISIONS.md` and `research/` itself
during its first Discovery cycle — don't hand-write them, but do seed
`product/FEEDBACK.md` (Step 1c) and, if the human already has a clear picture of the
user and the job, put it in `product/VISION.md` as a starting point PM will refine
rather than invent. Dev creates `docs/ARCHITECTURE.md` on its first session if missing.
