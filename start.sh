#!/bin/zsh
# launchd는 최소 환경으로 실행되므로 사용자 zsh 환경을 source해서 보완
# (docker, git, brew 등 경로 포함)

# zshenv → zprofile 순으로 sourcing (zshrc는 interactive-only)
[[ -f "$HOME/.zshenv" ]]   && source "$HOME/.zshenv"
[[ -f "$HOME/.zprofile" ]] && source "$HOME/.zprofile"

AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$AGENT_DIR/venv/bin/python" -u "$AGENT_DIR/main.py"
