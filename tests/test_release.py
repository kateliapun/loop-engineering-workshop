"""Release-safety gates: the verify/rollback net and the staging→prod promotion queue.

These are the checks the runner enforces after a role exits, so unlike the prompt
rules they can be tested without a claude call. The git-backed cases use a real
throwaway repo with a real `origin` — the logic is all about what git reports, and a
mock would only assert that the mock was called.
Run: python3 tests/test_release.py  (or python3 -m pytest tests/ -q)
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CORE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE))
import loop  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


def make_repo():
    """A work repo with a real bare `origin`, one commit on main."""
    tmp = Path(tempfile.mkdtemp())
    origin, work = tmp / "origin.git", tmp / "work"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   capture_output=True, check=True)
    subprocess.run(["git", "clone", str(origin), str(work)], capture_output=True, check=True)
    git(work, "config", "user.email", "t@example.com")
    git(work, "config", "user.name", "T")
    (work / "app.txt").write_text("v1\n")
    git(work, "add", "-A")
    git(work, "commit", "-m", "feat: initial")
    git(work, "push", "-u", "origin", "main")
    return work


def add_commit(repo, text, subject):
    (repo / "app.txt").write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", subject)
    git(repo, "push", "origin", "main")
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def base_config(**vars_):
    vars_.setdefault("human_name", "Kate")
    return {"slug": "t", "github_repo": "you/x", "project_path": "/tmp/x", "vars": vars_}


class Log:
    def __init__(self):
        self.lines = []

    def __call__(self, role, msg):
        self.lines.append(msg)

    def saw(self, needle):
        return any(needle in line for line in self.lines)


# ------------------------------------------------------------------ config → prompts

class TestDerivedBlocks(unittest.TestCase):
    def test_policy_defaults_to_manual(self):
        self.assertEqual(loop.promote_policy({}), "manual")
        self.assertEqual(loop.promote_policy({"promote_policy": "on_pm_accept"}), "on_pm_accept")

    def test_no_release_keys_means_no_gate_text(self):
        """The whole point of the design: a project that configures nothing renders
        exactly the prompts it rendered before these features existed."""
        v = loop.base_vars(base_config())
        self.assertNotIn("ship_gate", v)
        self.assertNotIn("pm_promotion", v)

    def test_preview_cmd_turns_on_the_pre_merge_gate(self):
        v = loop.base_vars(base_config(preview_cmd="deploy-preview", preview_url="https://p"))
        self.assertIn("Verify before you merge", v["ship_gate"])
        self.assertIn("deploy-preview", v["ship_gate"])
        self.assertIn("https://p", v["ship_gate"])

    def test_preview_url_falls_back_to_app_url(self):
        v = loop.base_vars(base_config(preview_cmd="x", app_url="https://staging"))
        self.assertIn("https://staging", v["ship_gate"])

    def test_promotion_block_follows_the_policy(self):
        auto = loop.base_vars(dict(base_config(promote_cmd="ship-prod"),
                                   promote_policy="on_pm_accept"))
        self.assertIn("promoted automatically", auto["pm_promotion"])
        self.assertIn("closing an issue ships it to real users", auto["pm_promotion"])

        manual = loop.base_vars(base_config(promote_cmd="ship-prod"))
        self.assertIn("human's call", manual["pm_promotion"])
        self.assertIn("ship-prod", manual["pm_promotion"])
        self.assertNotIn("automatically", manual["pm_promotion"])


# --------------------------------------------------------------- verify and rollback

class TestVerify(unittest.TestCase):
    def setUp(self):
        self.log = Log()

    def test_no_verify_cmd_is_a_no_op(self):
        with patch.object(loop, "run_shell") as run:
            self.assertTrue(loop.verify_deploy({}, "/tmp", {}, "main", "abc", self.log))
            run.assert_not_called()

    def test_passing_verify_does_not_roll_back(self):
        with patch.object(loop, "run_shell", return_value=0) as run, \
             patch.object(loop, "rollback") as rb:
            self.assertTrue(loop.verify_deploy({"verify_cmd": "true"}, "/tmp", {},
                                               "main", "abc", self.log))
            rb.assert_not_called()
            self.assertEqual(run.call_count, 1)
        self.assertTrue(self.log.saw("verify passed"))

    def test_failing_verify_rolls_back(self):
        with patch.object(loop, "run_shell", return_value=1), \
             patch.object(loop, "rollback", return_value=True) as rb:
            self.assertFalse(loop.verify_deploy({"verify_cmd": "false"}, "/tmp", {},
                                                "main", "abc", self.log))
            rb.assert_called_once()
        self.assertTrue(self.log.saw("verify FAILED"))

    def test_rollback_cmd_overrides_the_default(self):
        calls = []
        with patch.object(loop, "run_shell", side_effect=lambda c, *a, **k: calls.append(c) or 0):
            loop.rollback({"rollback_cmd": "restore-from-snapshot"}, "/tmp", {},
                          "main", "abc", self.log)
        self.assertEqual(calls, ["restore-from-snapshot"])


class TestDefaultRollback(unittest.TestCase):
    """The default rollback must never rewrite history — the branch is shared."""

    def setUp(self):
        self.repo = make_repo()
        self.log = Log()
        self.last_good = git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def test_reverts_and_redeploys_what_the_cycle_landed(self):
        add_commit(self.repo, "v2-broken\n", "feat: something broken (Ref #7)")
        calls = []
        with patch.object(loop, "run_shell",
                          side_effect=lambda c, *a, **k: calls.append(c) or 0):
            loop.rollback({"deploy_cmd": "redeploy-me"}, self.repo, {},
                          "main", self.last_good, self.log)
        self.assertEqual(len(calls), 2, calls)
        self.assertIn("git revert", calls[0])
        self.assertNotIn("reset --hard", calls[0])
        self.assertNotIn("push --force", calls[0])
        self.assertEqual(calls[1], "redeploy-me")

    def test_nothing_landed_means_redeploy_only(self):
        with patch.object(loop, "run_shell", return_value=0) as run:
            loop.rollback({"deploy_cmd": "redeploy-me"}, self.repo, {},
                          "main", self.last_good, self.log)
            self.assertEqual([c.args[0] for c in run.call_args_list], ["redeploy-me"])
        self.assertTrue(self.log.saw("nothing landed"))

    def test_a_failed_revert_never_redeploys_a_half_reverted_tree(self):
        add_commit(self.repo, "v2\n", "feat: two")
        with patch.object(loop, "run_shell",
                          side_effect=lambda c, *a, **k: 0 if "reset --hard" in c else 1):
            ok = loop.rollback({"deploy_cmd": "redeploy-me"}, self.repo, {},
                               "main", self.last_good, self.log)
        self.assertFalse(ok)
        self.assertTrue(self.log.saw("ROLLBACK FAILED"))


# ------------------------------------------------------------------------- promotion

class TestPromotionQueue(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.log = Log()

    def test_first_run_plants_the_tag_and_promotes_nothing(self):
        """Production is whatever is live now — not the entire history."""
        self.assertEqual(loop.pending_promotion(self.repo, "main", self.log), [])
        self.assertTrue(self.log.saw("planting it at"))
        tagged = git(self.repo, "rev-parse", loop.PROMOTED_TAG).stdout.strip()
        self.assertEqual(tagged, git(self.repo, "rev-parse", "HEAD").stdout.strip())

    def test_queue_is_the_commits_since_the_tag(self):
        loop.pending_promotion(self.repo, "main", self.log)          # plant
        add_commit(self.repo, "v2\n", "feat: two (Ref #7)")
        add_commit(self.repo, "v3\n", "fix: three (Ref #8)")
        pending = loop.pending_promotion(self.repo, "main", self.log)
        self.assertEqual([s for _, s in pending],
                         ["fix: three (Ref #8)", "feat: two (Ref #7)"])  # newest first

    def test_blockers_are_the_issues_pm_has_not_closed(self):
        commits = [("a1", "feat: two (Ref #7)"), ("a2", "fix: three (Ref #8)")]
        states = {"7": "CLOSED", "8": "OPEN"}

        def fake_gh(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout=states[cmd[3]], stderr="")

        with patch.object(loop.subprocess, "run", side_effect=fake_gh):
            self.assertEqual(loop.promotion_blockers(commits, {"github_repo": "y/x"}, self.log),
                             ["8"])
            states["8"] = "CLOSED"
            self.assertEqual(loop.promotion_blockers(commits, {"github_repo": "y/x"}, self.log),
                             [])

    def test_an_unreadable_issue_counts_as_open(self):
        """Fail closed: if gh can't answer, nothing ships."""
        def broken(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="gh: not authenticated")

        with patch.object(loop.subprocess, "run", side_effect=broken):
            self.assertEqual(loop.promotion_blockers([("a", "x (Ref #9)")],
                                                     {"github_repo": "y/x"}, self.log), ["9"])
        self.assertTrue(self.log.saw("treating as open"))

    def test_commits_referencing_no_issue_never_block(self):
        with patch.object(loop.subprocess, "run") as run:
            self.assertEqual(loop.promotion_blockers([("a", "pm(cycle 4): roadmap")],
                                                     {"github_repo": "y/x"}, self.log), [])
            run.assert_not_called()


class TestPromote(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.log = Log()
        loop.pending_promotion(self.repo, "main", self.log)  # plant the tag
        self.head = add_commit(self.repo, "v2\n", "feat: two (Ref #7)")
        self.vars = {"promote_cmd": "ship-prod", "promote_verify_cmd": "check-prod"}

    def tag(self):
        return git(self.repo, "rev-parse", loop.PROMOTED_TAG).stdout.strip()

    def test_open_issues_hold_the_promotion(self):
        with patch.object(loop, "promotion_blockers", return_value=["7"]), \
             patch.object(loop, "run_shell") as run:
            loop.promote(self.vars, {"github_repo": "y/x"}, self.repo, {}, "main", self.log)
            run.assert_not_called()
        self.assertNotEqual(self.tag(), self.head)
        self.assertTrue(self.log.saw("promotion held"))

    def test_clean_queue_promotes_and_moves_the_tag(self):
        with patch.object(loop, "promotion_blockers", return_value=[]), \
             patch.object(loop, "run_shell", return_value=0) as run:
            loop.promote(self.vars, {"github_repo": "y/x"}, self.repo, {}, "main", self.log)
            self.assertEqual([c.args[0] for c in run.call_args_list],
                             ["ship-prod", "check-prod"])
        self.assertEqual(self.tag(), self.head)

    def test_a_failed_promotion_leaves_the_queue_intact(self):
        with patch.object(loop, "promotion_blockers", return_value=[]), \
             patch.object(loop, "run_shell", return_value=1):
            loop.promote(self.vars, {"github_repo": "y/x"}, self.repo, {}, "main", self.log)
        self.assertNotEqual(self.tag(), self.head)
        self.assertTrue(self.log.saw("PROMOTION FAILED"))

    def test_a_failed_production_check_does_not_mark_work_as_live(self):
        with patch.object(loop, "promotion_blockers", return_value=[]), \
             patch.object(loop, "run_shell",
                          side_effect=lambda c, *a, **k: 1 if c == "check-prod" else 0):
            loop.promote(self.vars, {"github_repo": "y/x"}, self.repo, {}, "main", self.log)
        self.assertNotEqual(self.tag(), self.head)
        self.assertTrue(self.log.saw("PRODUCTION VERIFY FAILED"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
