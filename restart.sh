#!/bin/zsh
# 기존 PTM Agent(main.py) 종료 후 venv로 백그라운드 재기동.
# launchd(com.openclaw.home)로 떠 있는 경우도 먼저 내린다.

set -euo pipefail

AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$AGENT_DIR/venv/bin/python"
MAIN="$AGENT_DIR/main.py"
LOG_DIR="$AGENT_DIR/logs"
LOG_FILE="$LOG_DIR/agent.log"
LAUNCH_LABEL="gui/$(id -u)/com.openclaw.home"
MATCH="$AGENT_DIR/main.py"

_load_user_env() {
  [[ -f "$HOME/.zshenv" ]] && source "$HOME/.zshenv"
  [[ -f "$HOME/.zprofile" ]] && source "$HOME/.zprofile"
}

_launchd_running() {
  launchctl print "$LAUNCH_LABEL" 2>/dev/null | grep -q 'state = running'
}

_stop_agent() {
  if _launchd_running; then
    echo "Stopping launchd job $LAUNCH_LABEL ..."
    launchctl kill SIGTERM "$LAUNCH_LABEL" 2>/dev/null || true
    for _ in {1..10}; do
      _launchd_running || break
      sleep 0.5
    done
    if _launchd_running; then
      launchctl kill SIGKILL "$LAUNCH_LABEL" 2>/dev/null || true
      sleep 1
    fi
  fi

  local pids=("${(@f)$(pgrep -f "$MATCH" 2>/dev/null || true)}")
  if (( ${#pids} )); then
    echo "Stopping main.py PIDs: ${pids[*]}"
    kill -TERM ${pids[@]} 2>/dev/null || true
    sleep 1
    pids=("${(@f)$(pgrep -f "$MATCH" 2>/dev/null || true)}")
    if (( ${#pids} )); then
      kill -KILL ${pids[@]} 2>/dev/null || true
      sleep 0.5
    fi
  fi

  if pgrep -f "$MATCH" >/dev/null 2>&1; then
    echo "❌ main.py 프로세스가 아직 남아 있습니다. pgrep -fl main.py 로 확인하세요."
    exit 1
  fi
}

_start_agent() {
  if [[ ! -x "$PYTHON" ]]; then
    echo "❌ venv 없음: $PYTHON — python3 -m venv venv && pip install -r requirements.txt"
    exit 1
  fi
  if [[ ! -f "$AGENT_DIR/config.yaml" ]]; then
    echo "❌ config.yaml 없음. config.example.yaml 을 복사해 설정하세요."
    exit 1
  fi

  mkdir -p "$LOG_DIR"
  cd "$AGENT_DIR"
  nohup "$PYTHON" -u "$MAIN" >>"$LOG_FILE" 2>&1 &
  local pid=$!
  sleep 1

  if ! kill -0 "$pid" 2>/dev/null; then
    echo "❌ 기동 실패. 로그: tail -n 30 $LOG_FILE"
    exit 1
  fi
  if ! pgrep -f "$MATCH" >/dev/null 2>&1; then
    echo "❌ 프로세스가 바로 종료됨. 로그: tail -n 30 $LOG_FILE"
    exit 1
  fi

  echo "✅ PTM Agent 백그라운드 기동 (PID $pid)"
  echo "   로그: tail -f $LOG_FILE"
}

_load_user_env
_stop_agent
_start_agent
