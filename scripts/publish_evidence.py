#!/usr/bin/env python3
"""Stitch PNG frames into a GIF and upload it via GitHub's user-attachments endpoint
so it renders inline in markdown — the same mechanism the web UI uses when you drag
an image into an issue/PR comment box.

Usage:
  publish_evidence.py --repo <owner/repo> --name <asset-basename> <frame1.png> [frame2.png ...]

Prints the attachment URL on stdout (nothing else) on success.
Embed it in an issue/comment body with: ![test evidence](<url>)

Two prior approaches were dead ends, confirmed live:
- GitHub Release assets always force Content-Disposition: attachment — click-to-download
  only, never inline, no way around it.
- raw.githubusercontent.com requires a session-signed, short-lived token that GitHub only
  issues via its own "view raw" UI — a bare link 404s for everyone, including the owner,
  when pasted into a private repo's issue.

uploads.github.com/user-attachments/assets is undocumented but is the actual endpoint the
web UI's drag-and-drop uses. The resulting github.com/user-attachments/assets/<uuid> URL is
viewed via normal GitHub web session auth (cookies), same as any other private-repo page —
not a signed/expiring token — so it renders inline for anyone with repo access, same as a
manually-dragged image. Confirmed working live in a real browser against a private repo.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

FRAME_DURATION_MS = 900


def gh(args):
    return subprocess.run(["gh"] + args, capture_output=True, text=True)


def make_gif(frames, out_path):
    images = [Image.open(f).convert("RGB") for f in frames]
    if not images:
        sys.exit("no frames given")
    images[0].save(
        out_path, save_all=True, append_images=images[1:],
        duration=FRAME_DURATION_MS, loop=0,
    )


def repo_id(repo):
    r = gh(["api", f"repos/{repo}", "--jq", ".id"])
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit(f"could not resolve repo id for {repo}: {r.stderr}")
    return r.stdout.strip()


def auth_token():
    r = gh(["auth", "token"])
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit(f"could not get gh auth token: {r.stderr}")
    return r.stdout.strip()


def upload(repo, gif_path, asset_name):
    rid = repo_id(repo)
    token = auth_token()
    r = subprocess.run(
        [
            "curl", "-s",
            f"https://uploads.github.com/user-attachments/assets"
            f"?name={asset_name}&content_type=image/gif&repository_id={rid}",
            "-X", "POST",
            "-H", f"Authorization: Bearer {token}",
            "-H", "Accept: application/json",
            "--data-binary", f"@{gif_path}",
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit(f"upload failed: {r.stderr}")
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        sys.exit(f"unexpected upload response: {r.stdout}")
    if "url" not in data:
        sys.exit(f"upload response missing url: {r.stdout}")
    return data["url"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--name", required=True, help="asset basename, no extension")
    ap.add_argument("frames", nargs="+")
    args = ap.parse_args()

    asset_name = f"{args.name}.gif"
    with tempfile.TemporaryDirectory() as tmpdir:
        gif_path = Path(tmpdir) / asset_name
        make_gif(args.frames, gif_path)
        url = upload(args.repo, gif_path, asset_name)

    print(url)


if __name__ == "__main__":
    main()
