"""
PTM Platform 배포 락 & 브로드캐스트 관리자

- 동시 배포 충돌 방지 (파일 기반 락)
- 배포 시작/완료/실패 전체 사용자 알림
"""
import asyncio
import json
import time
from pathlib import Path
from typing import Optional, Tuple


LOCK_FILE = Path("/tmp/ptm_deploy.lock")
LOCK_TIMEOUT_SEC = 600  # 10분 후 자동 만료


# ── 락 관리 ───────────────────────────────────────────────────────────────────

def acquire_lock(user_id: int, user_name: str) -> Tuple[bool, str]:
    """배포 락 획득 시도. (성공 여부, 실패 메시지) 반환."""
    if LOCK_FILE.exists():
        try:
            data = json.loads(LOCK_FILE.read_text())
            elapsed = time.time() - data.get("ts", 0)
            if elapsed < LOCK_TIMEOUT_SEC:
                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                owner = data.get("name", f"ID {data.get('uid', '?')}")
                return False, (
                    f"🔒 *{owner}*이(가) 배포 중입니다 ({mins}분 {secs}초 전 시작)\n"
                    f"완료되면 자동으로 알림이 옵니다. 잠시 기다려 주세요.\n"
                    f"강제 해제: /ptmplatform_unlock"
                )
        except Exception:
            pass  # 손상된 락 파일이면 덮어씌움

    LOCK_FILE.write_text(json.dumps({
        "uid": user_id,
        "name": user_name,
        "ts": time.time(),
    }))
    return True, ""


def release_lock() -> None:
    LOCK_FILE.unlink(missing_ok=True)


def get_lock_status() -> Optional[dict]:
    """현재 락 정보. 없거나 만료됐으면 None."""
    if not LOCK_FILE.exists():
        return None
    try:
        data = json.loads(LOCK_FILE.read_text())
        elapsed = time.time() - data.get("ts", 0)
        if elapsed >= LOCK_TIMEOUT_SEC:
            LOCK_FILE.unlink(missing_ok=True)
            return None
        data["elapsed"] = elapsed
        return data
    except Exception:
        return None


# ── 유틸 ──────────────────────────────────────────────────────────────────────

async def _run(cwd: str, cmd: str, timeout: int = 900) -> Tuple[str, bool]:
    """쉘 명령 실행 → (출력, 성공 여부)."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = out.decode("utf-8", errors="replace").strip()
        return output, proc.returncode == 0
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "⏱️ 시간 초과", False


async def _exec(cwd: str, *args: str, timeout: int = 15) -> Tuple[str, bool]:
    """shell 없이 직접 실행 → (출력, 성공 여부). launchd 환경 호환."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = out.decode("utf-8", errors="replace").strip()
        return output, proc.returncode == 0
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "⏱️ 시간 초과", False


# ── 스마트 배포 ───────────────────────────────────────────────────────────────

async def smart_deploy(
    project_path: str,
    compose_project_dir: str = "",
) -> Tuple[str, bool]:
    """
    git pull → dev-deploy.sh (스마트 변경 감지) 실행.
    Returns (요약 메시지, 성공 여부).
    """
    cwd = str(Path(project_path).expanduser().resolve())
    if not Path(cwd).exists():
        return f"❌ 경로 없음: {cwd}", False

    env_prefix = f'COMPOSE_PROJECT_DIR="{compose_project_dir}" ' if compose_project_dir else ""

    # 1) git pull
    pull_out, pull_ok = await _run(cwd, "git pull origin main")
    if not pull_ok:
        return f"❌ git pull 실패\n```\n{pull_out[-2000:]}\n```", False

    # pull 결과 요약 (Already up to date / N files changed)
    pull_summary = pull_out.splitlines()[-1] if pull_out else "(출력 없음)"

    # 2) dev-deploy.sh
    deploy_cmd = f'{env_prefix}./scripts/dev-deploy.sh'.strip()
    deploy_out, deploy_ok = await _run(cwd, deploy_cmd, timeout=900)

    # 배포 결과에서 핵심만 추출
    summary_lines = []
    for line in deploy_out.splitlines():
        if any(kw in line for kw in ("Changed:", "Building:", "Restarting:", "Build:", "Done.", "변경 없음", "Warning:")):
            summary_lines.append(line.strip())
    deploy_summary = "\n".join(summary_lines) if summary_lines else deploy_out[-500:]

    status = "✅ 배포 완료" if deploy_ok else "❌ 배포 실패"
    msg = (
        f"{status}\n"
        f"📥 Pull: {pull_summary}\n"
        f"🔨 Deploy:\n{deploy_summary}"
    )
    return msg, deploy_ok


def _run_sync(args: list, cwd: str, timeout: int = 10) -> str:
    """동기 subprocess.run — asyncio 이벤트 루프와 무관하게 실행."""
    import os
    import subprocess
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    sock = Path.home() / ".docker" / "run" / "docker.sock"
    if sock.exists():
        env.setdefault("DOCKER_HOST", f"unix://{sock}")
    try:
        r = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return (r.stdout or r.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return "⏱️ 시간 초과"
    except Exception as e:
        return f"❌ {e}"


def _recent_commits_from_files(cwd: str) -> str:
    """subprocess 없이 .git 에서 최근 커밋 요약을 읽는다."""
    git_dir = Path(cwd) / ".git"
    if not git_dir.exists():
        return "❌ .git 없음"
    try:
        head = (git_dir / "HEAD").read_text().strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            sha = (git_dir / ref).read_text().strip()[:12]
            return f"{sha}  ({ref})"
        return head[:12]
    except Exception as e:
        return f"❌ git 파일 읽기 실패: {e}"


async def get_platform_status(project_path: str) -> dict:
    """락 + 최근 커밋 + 컨테이너 상태."""
    cwd = str(Path(project_path).expanduser().resolve())
    if not Path(cwd).exists():
        return {"error": f"❌ 경로 없음: {cwd}"}

    loop = asyncio.get_event_loop()
    git_out = await loop.run_in_executor(
        None,
        _run_sync,
        ["/usr/bin/git", "--no-pager", "log", "--oneline", "-5"],
        cwd,
        5,
    )
    if git_out.startswith("⏱️") or git_out.startswith("❌"):
        git_out = _recent_commits_from_files(cwd)

    docker_out = await loop.run_in_executor(
        None,
        _run_sync,
        [
            "/usr/local/bin/docker",
            "compose",
            "ps",
            "--format",
            "table {{.Name}}\t{{.Status}}",
        ],
        cwd,
        5,
    )

    lock = get_lock_status()
    if lock:
        elapsed = int(lock.get("elapsed", 0))
        mins, secs = divmod(elapsed, 60)
        lock_info = f"🔒 배포 중: {lock.get('name', '?')} ({mins}분 {secs}초 전)"
    else:
        lock_info = "🔓 유휴 (배포 없음)"

    return {
        "lock": lock_info,
        "commits": git_out[:800],
        "containers": docker_out[:1200],
    }
