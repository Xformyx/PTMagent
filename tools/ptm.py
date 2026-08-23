"""
PTM / PTMQuant git pull 및 스크립트 실행
"""
import asyncio
from pathlib import Path
from typing import Tuple


def _expand_path(path: str) -> Path:
    return Path(path).expanduser().resolve()


async def _run_cmd(cwd: Path, *args: str, timeout: int = 300) -> Tuple[str, str, int]:
    """명령 실행. (stdout, stderr, returncode)"""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "", "명령 시간 초과", -1
    return (
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
        proc.returncode or 0,
    )


async def run_script(project_path: str, script: str, timeout: int = 600) -> Tuple[str, bool]:
    """project_path 에서 shell 스크립트/명령 실행."""
    cwd = _expand_path(project_path)
    if not cwd.exists():
        return f"❌ 경로가 없습니다: {cwd}", False

    proc = await asyncio.create_subprocess_shell(
        script,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = out.decode("utf-8", errors="replace").strip()
        ok = proc.returncode == 0
        msg = "✅ 완료" if ok else "❌ 실패"
        return f"{msg}\n```\n{output[-3000:]}\n```", ok
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "❌ 시간 초과", False


async def git_pull_only(project_path: str) -> Tuple[str, bool]:
    """git pull origin main 실행."""
    cwd = _expand_path(project_path)
    if not cwd.exists():
        return f"❌ 경로가 없습니다: {cwd}", False

    stdout, stderr, code = await _run_cmd(cwd, "git", "pull", "origin", "main")
    output = stdout + ("\n" + stderr if stderr else "")
    ok = code == 0
    msg = "✅ git pull 완료" if ok else "❌ git pull 실패"
    body = f"{msg}\n```\n{output}\n```"
    if not ok and _needs_credential_hint(output):
        body += _GIT_CREDENTIAL_HINT
    return body, ok


_GIT_CREDENTIAL_HINT = """

💡 HTTPS private 저장소는 Docker(리눅스) 안에서 인증 입력창을 띄울 수 없습니다.

해결 방법:
1) SSH 로 변경: git remote set-url origin git@github.com:Xformyx/REPO.git
   docker-compose.yml 의 ~/.ssh 마운트 주석 해제 후 컨테이너 재생성.
2) HTTPS 유지: PAT credential 파일 마운트 필요 (설정 복잡)."""


def _needs_credential_hint(output: str) -> bool:
    o = output.lower()
    return any(n in o for n in (
        "could not read username",
        "could not read password",
        "terminal prompts disabled",
        "authentication failed",
    ))


async def git_log_recent(project_path: str, count: int = 10) -> Tuple[str, bool]:
    """최근 git log (hash, 시각, 제목) → 고정폭 텍스트."""
    cwd = _expand_path(project_path)
    if not cwd.exists():
        return f"❌ 경로가 없습니다: {cwd}", False
    if not (cwd / ".git").is_dir():
        return f"❌ git 저장소가 아닙니다: {cwd}", False

    stdout, stderr, code = await _run_cmd(
        cwd,
        "git",
        "log",
        f"-{count}",
        "--format=%h%x09%ad%x09%s",
        "--date=format-local:%Y-%m-%d %H:%M KST",
    )
    if code != 0:
        err = (stdout + "\n" + stderr).strip()
        return f"❌ git log 실패\n{err}", False

    rows: list[tuple[str, str, str]] = []
    for line in stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))

    if not rows:
        return "커밋 내역이 없습니다.", True

    lines = [
        "No. Commit   시각                  내용",
        "─── ──────── ───────────────────── ──────────",
    ]
    for i, (h, t, s) in enumerate(rows, 1):
        if i > 1:
            lines.append("")
        lines.append(f"{i:>2}. {h:<8} {t:<21} {s}")
    return "\n".join(lines), True


def format_git_log_section(title: str, body: str) -> str:
    return f"▸ {title}\n{body}"
