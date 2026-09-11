"""Render every example project's prompts and subagent briefs — fails on any missing
template var, so a change to a .tmpl/brief/example that breaks the contract is caught
without a claude call. Run: python3 -m pytest tests/ -q  (or python3 tests/test_render.py)"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE))
import loop  # noqa: E402

EXAMPLES = sorted(p.parent for p in (CORE / "examples").glob("*/config.yml"))
REQUIRED_PM_STEPS = ["## RULES", "4 · Discovery", "5 · Delivery", "Ambition guard"]
REQUIRED_DEV_STEPS = ["4 · Design note", "8 · Code review", "Reuse before you write"]


def _render_all(example_dir):
    """Point loop.AGENTS_ROOT at a temp root containing the example as a slug."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        slug = example_dir.name
        shutil.copytree(example_dir, root / slug)
        old_root = loop.AGENTS_ROOT
        loop.AGENTS_ROOT = root
        try:
            config = loop.load_config(slug)
            prompts_dir = root / slug / "prompts"
            out = {}
            for role_cfg in config["roles"]:
                prompt = loop.render_prompt(role_cfg, config, prompts_dir, cycle=7)
                agents = loop.build_agents(role_cfg, config, cycle=7)
                out[role_cfg["name"]] = (prompt, agents)
            return out
        finally:
            loop.AGENTS_ROOT = old_root


def _check(example_dir):
    rendered = _render_all(example_dir)
    assert set(rendered) == {"pm", "dev"}, f"{example_dir.name}: roles {set(rendered)}"
    for role, (prompt, agents) in rendered.items():
        assert prompt and "%%" not in prompt, f"{example_dir.name}/{role}: unresolved placeholder"
        assert "cycle 7" in prompt.lower() or "cycle 7" in prompt, f"{role}: cycle var not injected"
        for name, spec in agents.items():
            assert spec["description"] and spec["prompt"], f"{role}/{name}: empty brief"
            assert "%%" not in spec["prompt"], f"{role}/{name}: unresolved placeholder in brief"
        json.dumps(agents)  # must be serialisable for --agents
    pm_prompt, pm_agents = rendered["pm"]
    dev_prompt, dev_agents = rendered["dev"]
    for s in REQUIRED_PM_STEPS:
        assert s in pm_prompt, f"pm prompt lost section {s!r}"
    for s in REQUIRED_DEV_STEPS:
        assert s in dev_prompt, f"dev prompt lost section {s!r}"
    assert {"tester", "codebase-audit"} <= set(pm_agents), "pm must have tester + codebase-audit"
    assert {"architect", "reviewer"} <= set(dev_agents), "dev must have architect + reviewer"
    assert "user-sim" not in pm_agents and "market-research" not in pm_agents, \
        "user-sim/market-research should be removed, not just unused"
    # Every subagent the PM template names must exist as a brief.
    for name in ["tester", "codebase-audit", "flow", "logic"]:
        assert f"`{name}`" in pm_prompt, f"pm prompt no longer references {name}"
    # Fallback path renders too.
    inlined = loop.inline_agents(pm_prompt, pm_agents)
    assert "## SUBAGENT BRIEFS" in inlined and "### codebase-audit" in inlined


def test_examples_render():
    assert EXAMPLES, "no examples found"
    for ex in EXAMPLES:
        _check(ex)


def test_every_brief_parses():
    vars_ = loop.base_vars({"github_repo": "o/r", "project_path": "/p", "slug": "s",
                            "vars": {"evidence_method": "x", "quality_bar": "y",
                                     "human_name": "Test"}}, cycle=1)
    for path in sorted(loop.SUBAGENTS_DIR.glob("*.md")):
        spec = loop.load_subagent(path.stem, vars_)
        assert spec["description"] != path.stem, f"{path.name}: missing description frontmatter"
        assert len(spec["prompt"]) > 200, f"{path.name}: brief suspiciously short"


def test_missing_required_var_is_hard_error():
    import pytest
    with pytest.raises(SystemExit):
        loop.substitute("%%quality_bar%%", {}, source="t")
    assert loop.substitute("a%%pm_rules%%b", {}, source="t") == "ab"  # optional → ""


if __name__ == "__main__":
    for ex in EXAMPLES:
        _check(ex)
        print(f"OK {ex.name}")
    test_every_brief_parses()
    print("OK briefs")
