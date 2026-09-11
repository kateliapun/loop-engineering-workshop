#!/usr/bin/env python3
"""pm-dev-agents loop — runs each configured role (PM, Dev, ...) as a `claude -p` session
in turn, forever (bounded by max_runs), against one product repo.

Layout (AGENTS_ROOT = parent of this checkout):
  <agents_root>/_core/                this repo
  <agents_root>/<slug>/config.yml     one folder per product
  <agents_root>/<slug>/prompts/       <role>.extra.txt fill-ins for the shared templates
  <agents_root>/<slug>/state.db       run log + persisted cycle counter

See README.md for the full contract.
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

CORE = Path(__file__).parent
AGENTS_ROOT = CORE.parent  # <agents_root> — wherever _core is checked out
SUBAGENTS_DIR = CORE / "prompts" / "subagents"

sys.path.insert(0, str(CORE / "scripts"))
import interview_bot  # noqa: E402

# Single home for the house style every role's GitHub writing shares.
GITHUB_WRITING_STYLE = (
    "everything you post to GitHub — issue comments, issue bodies, PR "
    "titles/descriptions — is bullet points, straight to the point, human-readable, "
    "short. No preamble, no restating context the reader already has, no filler "
    "sentences."
)

# Template vars that may legitimately be absent from a project's config/extra files.
# They render as "" instead of failing the run. Everything else a template references
# must be defined — a missing var is a misconfiguration and stays a hard error.
OPTIONAL_VARS = {
    "pm_title_extra", "pm_filler_extra", "pm_rules", "goal_complete_extra",
    "lifecycle_notes", "queue_filters", "extra_done_fields", "regression_check",
    "context_files", "plan_notes", "style_rules", "load_context", "pm_body", "dev_body",
    "app_url", "api_base", "log_check_cmd", "deploy_cmd", "test_identity_note", "body",
    "interview_cmd",
    # Release safety (all optional — a project that sets none behaves exactly as before):
    "verify_cmd", "rollback_cmd", "preview_cmd", "preview_url", "promote_cmd",
    "promote_verify_cmd", "prod_url",
    # Derived blocks loop.py fills in from those, so prompts need no per-project edits:
    "ship_gate", "pm_promotion",
}

DEFAULT_VARS = {
    "pm_title": "PM",
    "queue_label": "pm-task",
    "reviewer_name": "PM",
    "product_dir": "product",
    "architecture_doc": "docs/ARCHITECTURE.md",
    "delivery_ratio": "70/30",
    "observe_every": "3",
    "rediscover_every": "10",
    "queue_cap": "5",
    "dev_tasks_per_session": "3",
    "interview_subjects": "%%human_name%%",
    "interview_replies": "FEEDBACK.md `## Interview`",
    "docs_branch": "main",
}

# Rendered into dev.txt.tmpl step 9 when the project has a pre-merge environment
# (`preview_cmd`). Without one, it renders empty and Dev's ship order is unchanged —
# merge, deploy, then the runner's own verify/rollback is the safety net instead.
SHIP_GATE = """**Verify before you merge.** This project has a pre-merge environment. Push the
branch, then:
```
%%preview_cmd%%
```
Exercise every AC against %%preview_url%% and run the same checks as step 7. Only when it
passes do you open and merge the PR. It fails → fix on the branch and repeat. Never merge
a change you have not seen working. Do not skip this because the diff looks small."""

# Rendered as PM's promotion step when the project has a separate production environment.
PM_PROMOTION_AUTO = """**9 · Promotion** — Production is a separate environment from the one you verify
against. Everything you accept is promoted automatically by the runner: once every issue
referenced by an unpromoted commit is closed, it runs the promotion and moves the
`promoted` tag. You never run the promotion command yourself.
Consequence to hold in mind: **closing an issue ships it to real users.** Don't close one
you're unsure of — leave it `ready-for-review` and say what you'd need. Something must
not ship yet → say so in the issue and keep it open."""

PM_PROMOTION_MANUAL = """**9 · Promotion** — Production is a separate environment and promotion is the
human's call. If `git log promoted..%%docs_branch%%` is non-empty and every issue those
commits reference is closed, file ONE `human-blocker` (or update the open one) listing
what's pending — issue numbers and one line each — plus the command:
```
%%promote_cmd%%
```
One blocker for the whole batch, never one per issue. Never run it yourself."""


GH_BIN = shutil.which("gh") or str(Path.home() / ".local/bin/gh")
CLAUDE_BIN = shutil.which("claude") or "claude"

PLACEHOLDER_RE = re.compile(r"%%([a-zA-Z0-9_]+)%%")
SECTION_RE = re.compile(r"^===\s*([a-zA-Z0-9_]+)\s*===\s*$", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


# ----------------------------------------------------------------------------- config

def load_config(slug):
    cfg_path = AGENTS_ROOT / slug / "config.yml"
    if not cfg_path.exists():
        sys.exit(f"no config.yml for project '{slug}' at {cfg_path}")
    config = yaml.safe_load(cfg_path.read_text())
    config.setdefault("slug", slug)
    return config


def parse_sections(text):
    """Split a '=== name ===' delimited trailer file into {name: body}."""
    sections = {}
    matches = list(SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[m.group(1)] = text[start:end].strip("\n")
    return sections


def substitute(text, vars_dict, *, source):
    def repl(m):
        key = m.group(1)
        if key in vars_dict:
            return str(vars_dict[key])
        if key in OPTIONAL_VARS:
            return ""
        sys.exit(f"missing template var '%%{key}%%' required by {source}")
    return PLACEHOLDER_RE.sub(repl, text)


def base_vars(config, cycle=0):
    v = dict(DEFAULT_VARS)
    v.update({
        "repo": config["github_repo"],
        "project_path": config["project_path"],
        "slug": config["slug"],
        "github_writing_style": GITHUB_WRITING_STYLE,
        "cycle": str(cycle),
        "agents_dir": str(AGENTS_ROOT / config["slug"]),
        "core_dir": str(CORE),
    })
    v.update(config.get("vars") or {})
    # A project that lists interview_ids gets the shared interview bot's send command
    # for free — no per-project setup beyond naming who to interview.
    if config.get("interview_ids") and "interview_cmd" not in v:
        v["interview_cmd"] = (
            'python3 %%core_dir%%/scripts/send_dm.py --telegram-id <id> --text "<message>"'
        )
    # Release-safety blocks are derived, not written per project: a project turns them
    # on by setting `preview_cmd` / `promote_cmd`, and the prompt text follows. Absent →
    # the placeholder renders empty (OPTIONAL_VARS) and the prompts read as they always did.
    if v.get("preview_cmd") and "ship_gate" not in v:
        v["ship_gate"] = SHIP_GATE
    if v.get("promote_cmd") and "pm_promotion" not in v:
        v["pm_promotion"] = (PM_PROMOTION_AUTO
                             if promote_policy(config) == "on_pm_accept"
                             else PM_PROMOTION_MANUAL)
    v.setdefault("preview_url", v.get("app_url", ""))

    # Let a project var reference a built-in one (e.g. flow_checklist: "%%agents_dir%%/flow_checklist.md").
    for k, val in list(v.items()):
        if isinstance(val, str) and "%%" in val:
            v[k] = substitute(val, v, source=f"config.yml vars:{k}")
    return v


def render_prompt(role_cfg, config, prompts_dir, cycle=0):
    """Render CORE/prompts/<template>.txt.tmpl + <project>/prompts/<role>.extra.txt.
    Falls back to the legacy plain prompts/<role>.txt if no shared template exists."""
    name = role_cfg["name"]
    template_name = role_cfg.get("template", name)
    template_path = CORE / "prompts" / f"{template_name}.txt.tmpl"

    if not template_path.exists():
        prompt_file = prompts_dir / f"{name}.txt"
        return prompt_file.read_text() if prompt_file.exists() else None

    vars_ = base_vars(config, cycle)
    extra_path = prompts_dir / f"{name}.extra.txt"
    sections = parse_sections(extra_path.read_text()) if extra_path.exists() else {}
    for k, v in sections.items():
        vars_[k] = substitute(v, vars_, source=f"{extra_path.name}:{k}")
    return substitute(template_path.read_text(), vars_, source=template_path.name)


# -------------------------------------------------------------------------- subagents

def load_subagent(name, vars_):
    """Read prompts/subagents/<name>.md → {"description", "prompt", "tools"?, "model"?}.
    Frontmatter is a small YAML block; the body is the subagent's system prompt."""
    path = SUBAGENTS_DIR / f"{name}.md"
    if not path.exists():
        sys.exit(f"unknown subagent '{name}' — expected {path}")
    text = path.read_text()
    meta, body = {}, text
    m = FRONTMATTER_RE.match(text)
    if m:
        meta = yaml.safe_load(m.group(1)) or {}
        body = text[m.end():]
    spec = {
        "description": substitute(str(meta.get("description", name)), vars_, source=path.name),
        "prompt": substitute(body.strip(), vars_, source=path.name),
    }
    if meta.get("tools"):
        tools = meta["tools"]
        spec["tools"] = tools if isinstance(tools, list) else [t.strip() for t in str(tools).split(",")]
    if meta.get("model"):
        spec["model"] = meta["model"]
    return spec


def build_agents(role_cfg, config, cycle=0):
    names = role_cfg.get("subagents") or []
    if not names:
        return {}
    vars_ = base_vars(config, cycle)
    return {n: load_subagent(n, vars_) for n in names}


_agents_flag_supported = None


def claude_supports_agents_flag():
    """`--agents` (inline subagent definitions) exists in recent Claude Code CLIs. Older
    builds don't have it — detect once, fall back to inlining the briefs into the prompt."""
    global _agents_flag_supported
    if _agents_flag_supported is None:
        try:
            out = subprocess.run([CLAUDE_BIN, "--help"], capture_output=True, text=True, timeout=30)
            _agents_flag_supported = "--agents" in (out.stdout + out.stderr)
        except Exception:
            _agents_flag_supported = False
    return _agents_flag_supported


def inline_agents(prompt, agents):
    """Fallback for CLIs without --agents: append each brief so the role can dispatch a
    generic `Agent` with the brief pasted in."""
    if not agents:
        return prompt
    parts = [prompt, "\n\n---\n\n## SUBAGENT BRIEFS\n\nYour `Agent` tool has no named subagents "
             "on this machine. When a step says to dispatch one of the subagents below, "
             "dispatch a general-purpose Agent and paste the matching brief verbatim as the "
             "first part of its prompt, followed by the task-specific input.\n"]
    for name, spec in agents.items():
        parts.append(f"\n### {name}\n_{spec['description']}_\n\n{spec['prompt']}\n")
    return "".join(parts)



def _short(obj, n=160):
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def stream_claude(cmd, cwd, env, role, cycle_no, logs_dir, log):
    """Run `claude -p` with stream-json output: raw events go to
    <slug>/logs/cycle-<n>-<role>.jsonl, a one-line digest of each tool call / assistant
    message goes to the console, so a 40-minute PM cycle is observable while it runs."""
    logs_dir.mkdir(exist_ok=True)
    raw_path = logs_dir / f"cycle-{cycle_no}-{role}.jsonl"
    # `-p` is followed by the prompt as the last arg; insert flags before it.
    cmd = cmd[:-1] + ["--output-format", "stream-json", "--verbose"] + cmd[-1:]
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    with raw_path.open("a") as raw:
        for line in proc.stdout:
            raw.write(line)
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                print(f"           {role:<8} {line.rstrip()[:200]}", flush=True)
                continue
            t = ev.get("type")
            if t == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        inp = block.get("input", {})
                        detail = inp.get("command") or inp.get("description") or inp.get("prompt") \
                            or inp.get("file_path") or inp.get("pattern") or inp
                        log(role, f"→ {block.get('name')}: {_short(detail)}")
                    elif block.get("type") == "text" and block.get("text", "").strip():
                        log(role, f"· {_short(block['text'], 300)}")
            elif t == "result":
                cost = ev.get("total_cost_usd")
                turns = ev.get("num_turns")
                log(role, f"result: {ev.get('subtype')} · {turns} turns"
                          + (f" · ${cost:.2f}" if isinstance(cost, (int, float)) else ""))
    proc.wait()
    return proc.returncode

# -------------------------------------------------------------- release safety

# Everything below is enforced by the runner, not by prompt text. A role can forget an
# instruction; it cannot forget a check that runs after it exits. Two independent
# mechanisms, each off unless the project configures it:
#
#   verify_cmd   — an independent health check run after Dev's cycle. Non-zero exit
#                  rolls the deployed branch back and redeploys. This is the safety net
#                  for a project with ONE environment (no staging): a bad change is
#                  undone inside the same cycle instead of sitting live until a human
#                  notices. It is not the agent grading its own work.
#   promote_cmd  — a second, production environment. `deploy_cmd` then targets staging,
#                  and production only moves when everything referenced by the
#                  unpromoted commits has been closed by PM. The `promoted` git tag
#                  records where production is; `git log promoted..<branch>` is the
#                  queue, readable by a human with no extra state to keep in sync.

PROMOTED_TAG = "promoted"
# `Ref #12`, `Part of #12`, `Fixes #12`, or a bare `#12` in a commit subject.
ISSUE_REF_RE = re.compile(r"#(\d+)")


def promote_policy(config):
    """`manual` (default — agents never touch production) or `on_pm_accept`."""
    return (config.get("promote_policy") or "manual").strip()


def _git(project, *args):
    return subprocess.run(["git", "-C", str(project), *args],
                          capture_output=True, text=True)


def run_shell(cmd, cwd, env, label, log, timeout=1800):
    """Run one configured shell command. Returns its exit code (124 on timeout)."""
    log("system", f"{label}: {_short(cmd)}")
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, shell=True,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log("system", f"{label} TIMED OUT after {timeout}s")
        return 124
    if r.returncode != 0:
        log("system", f"{label} failed (exit {r.returncode}): "
                      f"{_short((r.stdout + r.stderr).strip(), 300)}")
    return r.returncode


def remote_head(project, branch):
    """The sha the deploy branch points at on the remote — the thing that actually got
    deployed, whether the deploy ran here or over ssh from somewhere else."""
    _git(project, "fetch", "--quiet", "origin", branch)
    sha = _git(project, "rev-parse", f"origin/{branch}").stdout.strip()
    return sha or None


def rollback(vars_, project, env, branch, last_good, log):
    """Undo whatever landed on `branch` this cycle and redeploy the previous state.

    `git revert`, never `reset --hard`: the branch is shared with GitHub, so rollback
    has to be a new commit rather than rewritten history. A project whose rollback
    needs anything else (a database restore, a blue/green switch) sets `rollback_cmd`
    and that runs instead."""
    if vars_.get("rollback_cmd"):
        return run_shell(vars_["rollback_cmd"], project, env, "rollback", log) == 0

    head = remote_head(project, branch)
    if not last_good or not head or head == last_good:
        log("system", "rollback: nothing landed on "
                      f"{branch} this cycle — redeploying current state only")
    else:
        log("system", f"rollback: reverting {last_good[:8]}..{head[:8]} on {branch}")
        cmd = (f"git checkout {branch} && git pull --ff-only && "
               f"git revert --no-edit --no-commit {last_good}..{head} && "
               f"git commit -m 'revert: automatic rollback of a failed verify "
               f"({last_good[:8]}..{head[:8]})' && git push")
        if run_shell(cmd, project, env, "rollback", log) != 0:
            # Leave the clone usable even if the revert conflicted, and stop — a
            # half-reverted tree must never be deployed.
            run_shell("git revert --abort 2>/dev/null; git reset --hard HEAD",
                      project, env, "rollback-cleanup", log)
            log("system", "ROLLBACK FAILED — deployment left as-is, needs a human")
            return False

    if vars_.get("deploy_cmd"):
        return run_shell(vars_["deploy_cmd"], project, env, "redeploy", log) == 0
    return True


def verify_deploy(vars_, project, env, branch, last_good, log):
    """Run the project's independent health check; roll back if it fails.
    Returns True when the deployment is good (or when no check is configured)."""
    if not vars_.get("verify_cmd"):
        return True
    if run_shell(vars_["verify_cmd"], project, env, "verify", log) == 0:
        log("system", "verify passed")
        return True
    log("system", "verify FAILED — rolling back")
    rolled = rollback(vars_, project, env, branch, last_good, log)
    log("system", "rolled back and redeployed" if rolled
                  else "rollback did not complete — needs a human")
    return False


def pending_promotion(project, branch, log):
    """[(sha, subject)] on `branch` that production hasn't got yet.

    First run has no `promoted` tag: rather than treating the whole history as
    pending, the tag is planted at the current remote head and nothing is promoted —
    production is assumed to be whatever is live right now."""
    _git(project, "fetch", "--quiet", "--tags", "origin", branch)
    head = _git(project, "rev-parse", f"origin/{branch}").stdout.strip()
    if not head:
        return []
    if not _git(project, "rev-parse", "--verify", "--quiet", f"{PROMOTED_TAG}^{{commit}}").stdout.strip():
        log("system", f"no `{PROMOTED_TAG}` tag yet — planting it at {head[:8]}, "
                      "nothing to promote this cycle")
        set_promoted(project, head, log)
        return []
    out = _git(project, "log", "--format=%H\x1f%s", f"{PROMOTED_TAG}..{head}").stdout
    commits = []
    for line in out.splitlines():
        sha, _, subject = line.partition("\x1f")
        if sha.strip():
            commits.append((sha.strip(), subject.strip()))
    return commits


def promotion_blockers(commits, config, log):
    """Issue numbers referenced by unpromoted commits that PM has NOT closed yet.

    This is the whole gate: PM closing an issue is what marks the work accepted, so
    "every referenced issue is closed" means "PM accepted everything that would ship".
    A commit referencing no issue (PM's own product-doc commits) never blocks."""
    refs = sorted({n for _, subject in commits for n in ISSUE_REF_RE.findall(subject)}, key=int)
    open_refs = []
    for n in refs:
        r = subprocess.run([GH_BIN, "issue", "view", n, "--repo", config["github_repo"],
                            "--json", "state", "--jq", ".state"],
                           capture_output=True, text=True)
        state = r.stdout.strip().upper()
        if r.returncode != 0:
            log("system", f"promotion: could not read issue #{n} "
                          f"({_short(r.stderr.strip(), 120)}) — treating as open")
            open_refs.append(n)
        elif state != "CLOSED":
            open_refs.append(n)
    return open_refs


def set_promoted(project, sha, log):
    _git(project, "tag", "-f", PROMOTED_TAG, sha)
    r = _git(project, "push", "--force", "origin", f"refs/tags/{PROMOTED_TAG}")
    if r.returncode != 0:
        log("system", f"could not push the `{PROMOTED_TAG}` tag: {_short(r.stderr.strip(), 160)}")


def promote(vars_, config, project, env, branch, log):
    """Ship what PM has accepted from staging to production, then move the tag.

    The tag moves only after both the promotion and its verification succeed — so a
    failed promotion leaves the queue intact and simply retries next cycle rather than
    silently marking work as live."""
    commits = pending_promotion(project, branch, log)
    if not commits:
        return
    blockers = promotion_blockers(commits, config, log)
    if blockers:
        log("system", f"promotion held: {len(commits)} commit(s) pending, "
                      f"issues still open: {', '.join('#' + n for n in blockers)}")
        return

    head = commits[0][0]  # git log is newest-first
    log("system", f"promoting {len(commits)} commit(s) to production (→ {head[:8]})")
    if run_shell(vars_["promote_cmd"], project, env, "promote", log) != 0:
        log("system", "PROMOTION FAILED — tag not moved, will retry next cycle")
        return
    if vars_.get("promote_verify_cmd") and \
            run_shell(vars_["promote_verify_cmd"], project, env, "promote-verify", log) != 0:
        log("system", "PRODUCTION VERIFY FAILED after promotion — tag not moved, "
                      "production needs a human now")
        return
    set_promoted(project, head, log)
    log("system", f"promoted — `{PROMOTED_TAG}` now at {head[:8]}")


# ------------------------------------------------------------------------------ misc

def load_env_file(path):
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def available_projects():
    return sorted(p.parent.name for p in AGENTS_ROOT.glob("*/config.yml"))


# ------------------------------------------------------------------------------ main

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("project", nargs="?", default=None,
                        help="project slug (folder name under <agents_root>/) — asked interactively if omitted")
    parser.add_argument("--max-runs", type=int, default=None, help="cycles to run (also disables the interactive prompt)")
    parser.add_argument("--once", action="store_true", help="run exactly one cycle, no pause, no prompt (cron-friendly)")
    parser.add_argument("--role", default=None, help="run only this role each cycle (e.g. pm or dev)")
    parser.add_argument("--render", metavar="ROLE", default=None,
                        help="print the rendered prompt + subagent JSON for ROLE and exit — no claude call")
    args = parser.parse_args()

    slug = args.project
    if not slug:
        options = available_projects()
        if options:
            print(f"Available projects: {', '.join(options)}")
        try:
            slug = input("Which project? ").strip()
        except EOFError:
            slug = ""
        if not slug:
            sys.exit("no project given")

    os.environ.update(load_env_file(CORE / ".env"))  # shared secrets, e.g. INTERVIEWER_BOT_TOKEN

    config = load_config(slug)
    project_agents_dir = AGENTS_ROOT / slug
    prompts_dir = Path(config.get("prompts_dir", project_agents_dir / "prompts"))
    PROJECT = config["project_path"]
    # On case-insensitive filesystems (macOS default) <agents_root>/inksight and a repo
    # clone at <agents_root>/InkSight are the same directory — the agent's files would
    # land inside the product repo. Refuse to run in that state.
    if Path(PROJECT).resolve() == project_agents_dir.resolve():
        sys.exit(f"project_path {PROJECT} resolves to the agent folder {project_agents_dir} — "
                 "move the repo clone elsewhere (e.g. <agents_root>/repos/<Name>)")
    PAUSE = 0 if args.once else config.get("cycle_pause", 300)
    MAX = 1 if args.once else (args.max_runs or config.get("max_runs", 20))
    roles = config["roles"]
    if args.role:
        roles = [r for r in roles if r["name"] == args.role]
        if not roles:
            sys.exit(f"no role '{args.role}' in {slug}/config.yml")

    db = sqlite3.connect(project_agents_dir / "state.db")
    db.execute("CREATE TABLE IF NOT EXISTS logs (role TEXT, msg TEXT, ts TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
    db.commit()

    def get_state(key, default=None):
        row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(key, value):
        db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, str(value)))
        db.commit()

    def log(role, msg):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {role:<8} {msg}", flush=True)
        db.execute("INSERT INTO logs VALUES (?,?,?)", (role, msg, datetime.now().isoformat()))
        db.commit()

    def paused():
        return get_state("paused") == "true"

    # Cycle counter persists across restarts so prompts can pace "every N cycles" work.
    cycle = int(get_state("cycle_count", "0"))

    if args.render:
        role_cfg = next((r for r in config["roles"] if r["name"] == args.render), None)
        if not role_cfg:
            sys.exit(f"no role '{args.render}' in {slug}/config.yml")
        prompt = render_prompt(role_cfg, config, prompts_dir, cycle + 1)
        agents = build_agents(role_cfg, config, cycle + 1)
        print(prompt)
        if agents:
            print("\n\n===== --agents JSON =====")
            print(json.dumps(agents, indent=2, ensure_ascii=False))
        return

    def wait_for_reset():
        cache = Path.home() / ".claude/cache/rate-limits-cache.json"
        try:
            limits = json.loads(cache.read_text()).get("rate_limits", {})
            resets = [v["resets_at"] for v in limits.values()
                      if v.get("used_percentage", 0) >= 95 and "resets_at" in v]
            if resets:
                wait = max(0, min(resets) - time.time()) + 90
                log("system", f"rate limit — sleeping {int(wait // 60)}m until reset")
                time.sleep(wait)
                return
        except Exception:
            pass
        log("system", "rate limit — sleeping 30m (fallback)")
        time.sleep(1800)

    def run_role(role_cfg, cycle_no):
        name = role_cfg["name"]
        tools = role_cfg.get("tools", "Bash,Read,Write,Edit,Glob,Grep")
        prompt = render_prompt(role_cfg, config, prompts_dir, cycle_no)
        if prompt is None:
            log(name, f"no prompt template or file found for role '{name}' — skipping")
            return False
        agents = build_agents(role_cfg, config, cycle_no)

        cmd = [CLAUDE_BIN, "-p", "--allowedTools", tools]
        if agents:
            if claude_supports_agents_flag():
                cmd += ["--agents", json.dumps(agents, ensure_ascii=False)]
            else:
                prompt = inline_agents(prompt, agents)
        if role_cfg.get("model"):
            cmd += ["--model", role_cfg["model"]]
        if role_cfg.get("permission_mode"):
            cmd += ["--permission-mode", role_cfg["permission_mode"]]
        cmd.append(prompt)

        log(name, f"started (cycle {cycle_no}, subagents: {', '.join(agents) or 'none'})")
        env = os.environ.copy()
        env["AGENT_SESSION"] = "1"
        env["PM_DEV_CYCLE"] = str(cycle_no)
        # A role that dispatches a subagent can easily run 10+ minutes; `claude -p`'s
        # default 600s background-task ceiling would silently truncate it. Wait forever —
        # cycle pacing/max_runs already bound the loop at a higher level.
        env["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] = "0"
        env.update(load_env_file(project_agents_dir / ".env"))
        returncode = stream_claude(cmd, PROJECT, env, name, cycle_no, project_agents_dir / "logs", log)
        log(name, f"exited with code {returncode}" if returncode != 0 else "finished")
        return returncode != 0

    def gh_json(args_list):
        r = subprocess.run([GH_BIN] + args_list, capture_output=True, text=True, cwd=PROJECT)
        return json.loads(r.stdout or "[]")

    def gate_open(gate):
        priorities = gate.get("priorities") or [None]
        for pr in priorities:
            call = ["issue", "list", "--repo", config["github_repo"],
                    "--label", gate["label"], "--state", "open", "--json", "number"]
            if pr:
                call += ["--label", pr]
            if gh_json(call):
                return True
        return False

    interactive = sys.stdin.isatty() and not args.once and args.max_runs is None
    if interactive:
        try:
            cli_max = input(f"How many loops to run for '{slug}'? [{MAX}]: ").strip()
            if cli_max:
                MAX = int(cli_max)
        except (ValueError, EOFError):
            pass

    log("system", f"Starting '{slug}' — max {MAX} runs (resuming at cycle {cycle + 1})")
    runs = 0
    while runs < MAX:
        while paused():
            time.sleep(10)

        runs += 1
        cycle += 1
        set_state("cycle_count", cycle)
        log("system", f"=== run {runs}/{MAX} · cycle {cycle} ===")

        # Release safety is measured per cycle: remember where the deploy branch was
        # before any role touched it, so a failed verify has something to revert to.
        # The window is the whole cycle, so a rollback undoes PM's product-doc commits
        # along with Dev's code — deliberate: the deployed tree returns to one known
        # -good state rather than a half-reverted mix.
        release = base_vars(config, cycle)
        release_branch = release["docs_branch"]
        release_env = os.environ.copy()
        release_env.update(load_env_file(project_agents_dir / ".env"))
        last_good = (remote_head(PROJECT, release_branch)
                     if (release.get("verify_cmd") or release.get("promote_cmd")) else None)

        try:
            fetched = interview_bot.sync(AGENTS_ROOT)
            delivered = interview_bot.deliver(AGENTS_ROOT, config, db)
            if fetched or delivered:
                log("system", f"interview bot: {fetched} fetched, {delivered} appended to FEEDBACK.md")
        except Exception as e:
            log("system", f"interview bot sync failed (non-fatal): {e}")

        cycle_failed = False
        for role_cfg in roles:
            if not role_cfg.get("always_run", True):
                gate = role_cfg.get("gate")
                if gate and not gate_open(gate):
                    log(role_cfg["name"], "no open tasks — skipping")
                    continue
            rate_limited = run_role(role_cfg, cycle)
            if rate_limited:
                wait_for_reset()
                cycle_failed = True
                break
            time.sleep(role_cfg.get("post_delay", 0))

        if cycle_failed:
            runs -= 1
            cycle -= 1
            set_state("cycle_count", cycle)
            continue

        # The two runner-enforced gates. Both no-op unless the project configures them.
        deployment_ok = verify_deploy(release, PROJECT, release_env,
                                      release_branch, last_good, log)
        if deployment_ok and release.get("promote_cmd") \
                and promote_policy(config) == "on_pm_accept":
            promote(release, config, PROJECT, release_env, release_branch, log)

        if runs < MAX and PAUSE:
            log("system", f"sleeping {PAUSE}s")
            time.sleep(PAUSE)

    log("system", f"Done — {MAX} runs reached")


if __name__ == "__main__":
    main()
