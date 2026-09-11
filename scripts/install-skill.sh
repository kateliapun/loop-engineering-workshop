#!/usr/bin/env bash
set -euo pipefail

SKILL_NAME="setup-pm-project"
CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$CORE_DIR/skills/$SKILL_NAME"
DEST_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
DEST="$DEST_DIR/$SKILL_NAME"

if [ ! -d "$SRC" ]; then
  echo "error: skill source not found at $SRC" >&2
  exit 1
fi

mkdir -p "$DEST_DIR"

if [ -e "$DEST" ] && [ ! -L "$DEST" ]; then
  echo "error: $DEST already exists and is not a symlink — remove it manually first" >&2
  exit 1
fi

ln -sfn "$SRC" "$DEST"
echo "installed: $DEST -> $SRC"
echo "note: if $CORE_DIR is a temporary worktree, re-run this from your main _core checkout — the symlink above will dangle once the worktree is removed."
