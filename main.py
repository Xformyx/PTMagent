"""
OpenClaw-Home 메인 진입점
- Telegram 봇 (가족 멤버만 사용)
- 주기적 작업: 입시 문서 체크
- Ollama gemma3:27b
"""
import sys
from pathlib import Path
from urllib.parse import urlparse

# 프로젝트 루트를 path에 추가
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import asyncio
import fcntl
import logging
import os
from html import escape

import httpx
import yaml
from telegram import Update, BotCommand
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from engine import AIAgent
from tools import browser, translator, admission

# 설정 로드
CONFIG_PATH = ROOT / "config.yaml"
if not CONFIG_PATH.exists():
    print(f"❌ config.yaml이 없습니다. config.example.yaml을 복사해 설정하세요.")
    sys.exit(1)

with open(CONFIG_PATH) as f:
    CONFIG = yaml.safe_load(f)


def _configure_logging():
    """터미널·Docker 로그에서 텔레그램 수신 내역 확인용."""
    tg = CONFIG.get("telegram") or {}
    level_name = (
        tg.get("log_level")
        or os.environ.get("LOG_LEVEL")
        or "INFO"
    ).upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=level,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.WARNING)


TOKEN = CONFIG["telegram"]["token"]
ALLOWED_IDS = set(int(x) for x in CONFIG["telegram"]["allowed_user_ids"])
OLLAMA_MODEL = CONFIG["ollama"].get("model", "gemma3:27b")

agent = AIAgent(model=OLLAMA_MODEL)
scheduler = AsyncIOScheduler()


def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_IDS


INSTANCE_PID_FILE = Path("/tmp/ptm_agent.pid")
_INSTANCE_LOCK_FD: int | None = None


def _acquire_single_instance_lock() -> None:
    """같은 토큰으로 두 프로세스가 뜨면 getUpdates Conflict. flock + PID 파일로 하나만 허용."""
    global _INSTANCE_LOCK_FD
    fd = os.open(str(INSTANCE_PID_FILE), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        other = ""
        try:
            other = os.read(fd, 64).decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        os.close(fd)
        extra = f" (PID {other})" if other else ""
        print(f"❌ 이미 봇이 실행 중입니다{extra}. 하나만 실행하세요.")
        sys.exit(1)
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    # fd 를 열어 둔 채로 유지해야 flock 이 유지된다. 파일은 지우지 않는다
    # (unlink 하면 다른 프로세스가 새 inode 에 락을 잡을 수 있음).
    _INSTANCE_LOCK_FD = fd


async def _telegram_reply_long_pre(message, text: str, chunk: int = 3200) -> None:
    """docker 로그 등 특수문자 많은 긴 텍스트를 Markdown 없이 전송 (HTML pre)."""
    if not text:
        await message.reply_text("(출력 없음)")
        return
    for i in range(0, len(text), chunk):
        part = text[i : i + chunk]
        await message.reply_text(f"<pre>{escape(part)}</pre>", parse_mode="HTML")


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⚠️ 이 봇은 등록된 가족만 사용할 수 있습니다.")
        return
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    _text_routes = {
        "ptmplatform_deploy": cmd_ptmplatform_deploy,
        "ptmplatform_status": cmd_ptmplatform_status,
        "ptmplatform_unlock": cmd_ptmplatform_unlock,
        "ptmquant_deploy": cmd_ptmquant_deploy,
    }
    handler = _text_routes.get(text.lower())
    if handler:
        await handler(update, context)
        return
    try:
        reply = await agent.route_and_respond(user_id, text)
        await update.message.reply_text(reply[:4000])
    except Exception as e:
        await update.message.reply_text(f"오류: {str(e)[:500]}")


HELP_TEXT = """👋 PTM Agent

<b>ptm-platform</b>
• /ptmplatform_deploy — pull + 스마트 빌드, 양쪽 결과 알림
• /ptmplatform_status — git·컨테이너·락 상태 확인
• /ptmplatform_unlock — 배포 락 강제 해제
• /ptmplatform_unlock quant — PTMQuant 락 해제

<b>PTMQuant</b>
• /ptmquant_deploy — pull + build

<b>범용</b>
• /latest — ptm-platform · PTMQuant 최근 커밋 10개
• /shell &lt;명령어&gt; — Mac Studio 쉘 명령 (최대 15분)
  예) /shell ./scripts/dev-deploy.sh --all
  예) /shell docker compose restart gateway"""


def _telegram_bot_commands() -> list[BotCommand]:
    """입력창 / 메뉴에 노출되는 명령 목록."""
    return [
        BotCommand("help", "도움말"),
        BotCommand("ptmplatform_deploy", "pull + 스마트 빌드"),
        BotCommand("ptmplatform_status", "상태 확인 (git/docker/락)"),
        BotCommand("ptmplatform_unlock", "배포 락 강제 해제"),
        BotCommand("ptmquant_deploy", "PTMQuant pull + build"),
        BotCommand("latest", "최근 커밋 히스토리"),
        BotCommand("shell", "Mac Studio 쉘 명령"),
    ]


async def cmd_shell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """임의의 쉘 명령 실행: /shell <command>"""
    if not is_allowed(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("사용법: /shell <명령어>\n예: /shell df -h")
        return

    cmd = " ".join(context.args)
    shell_cwd = _get_ptm_shell_cwd()
    if not shell_cwd:
        await update.message.reply_text(
            "❌ PTM 작업 경로를 찾을 수 없습니다. ptm.shell_cwd / project_path 또는 "
            "Docker 의 OPENCLAW_PTM_SHELL_CWD 를 확인하세요."
        )
        return
    proc = None
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=shell_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=900)
        output = out.decode("utf-8", errors="replace").strip()
        rc = proc.returncode
        header = f"✅ 완료 (rc={rc})" if rc == 0 else f"❌ 실패 (rc={rc})"
        body = output if output else "(출력 없음)"
        await _telegram_reply_long_pre(update.message, f"{header}\n{body}")
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        await update.message.reply_text("❌ 15분 타임아웃 초과")
    except Exception as e:
        await update.message.reply_text(f"오류: {str(e)[:500]}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⚠️ 사용 권한이 없습니다.")
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⚠️ 이 봇은 등록된 가족만 사용할 수 있습니다.")
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def cmd_latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ptm-platform · PTMQuant 최근 커밋 10개."""
    if not is_allowed(update.effective_user.id):
        return
    from tools.ptm import format_git_log_section, git_log_recent

    sections: list[str] = []
    for title, path in (
        ("ptm-platform", _ptm_platform_path()),
        ("PTMQuant", _ptmquant_path()),
    ):
        body, ok = await git_log_recent(path, count=10)
        sections.append(format_git_log_section(title, body))

    await _telegram_reply_long_pre(update.message, "\n\n".join(sections))


# ── 경로 헬퍼 ──────────────────────────────────────────────────────────────────

def _ptm_platform_path() -> str:
    """ptm-platform 루트 (Docker: OPENCLAW_PTM_PLATFORM_PATH, 네이티브: ptm.project_path)."""
    env = (os.environ.get("OPENCLAW_PTM_PLATFORM_PATH") or "").strip()
    if env:
        return str(Path(env).expanduser().resolve())
    raw = CONFIG.get("ptm", {}).get("project_path", "~/Documents/Work/PTM/ptm-platform")
    return str(Path(str(raw)).expanduser().resolve()) if raw else ""


def _ptmquant_path() -> str:
    """PTMQuant 루트 (Docker: OPENCLAW_PTMQUANT_GIT_PATH, 네이티브: ptm.ptmquant_git_path)."""
    env = (os.environ.get("OPENCLAW_PTMQUANT_GIT_PATH") or "").strip()
    if env:
        return str(Path(env).expanduser().resolve())
    raw = CONFIG.get("ptm", {}).get("ptmquant_git_path", "~/Documents/Work/PTM/PTMQuant")
    return str(Path(str(raw)).expanduser().resolve()) if raw else ""


def _get_ptm_shell_cwd() -> str:
    env = (os.environ.get("OPENCLAW_PTM_SHELL_CWD") or "").strip()
    if env:
        return str(Path(env).expanduser().resolve())
    return _ptm_platform_path()


def _ptm_platform_host_path() -> str:
    """docker compose 가 볼륨 상대경로를 해석할 호스트 실제 경로.
    Docker: PTM_PLATFORM_HOST_PATH 환경변수 (compose 에서 주입).
    네이티브: _ptm_platform_path() 와 동일.
    """
    return (os.environ.get("PTM_PLATFORM_HOST_PATH") or "").strip() or _ptm_platform_path()


def _ptmquant_host_path() -> str:
    return (os.environ.get("PTM_QUANT_HOST_PATH") or "").strip() or _ptmquant_path()


# ── 명령 핸들러 ────────────────────────────────────────────────────────────────

def _user_display_name(update: Update) -> str:
    """Telegram 표시 이름 (first_name 우선, 없으면 username, 없으면 ID)."""
    u = update.effective_user
    if u.first_name:
        return u.first_name
    if u.username:
        return f"@{u.username}"
    return str(u.id)


async def _broadcast(app_or_bot, text: str, exclude_id: int | None = None) -> None:
    """허용된 모든 사용자에게 HTML <pre>로 전송 (특수문자 파싱 실패 방지)."""
    if not text:
        return
    chunk = 3200
    for uid in ALLOWED_IDS:
        if uid == exclude_id:
            continue
        try:
            for i in range(0, len(text), chunk):
                part = text[i : i + chunk]
                await app_or_bot.send_message(
                    chat_id=uid,
                    text=f"<pre>{escape(part)}</pre>",
                    parse_mode="HTML",
                )
        except Exception:
            pass


# ── 스마트 배포 (핵심 명령) ────────────────────────────────────────────────────

async def cmd_ptmplatform_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """pull + 스마트 빌드. 락 보호 + 전체 알림."""
    if not is_allowed(update.effective_user.id):
        return

    from tools.deploy_manager import acquire_lock, release_lock, smart_deploy

    user_id = update.effective_user.id
    user_name = _user_display_name(update)

    ok, deny_msg = acquire_lock(user_id, user_name, scope="ptm")
    if not ok:
        await update.message.reply_text(deny_msg)
        return

    start_notice = f"🚀 {user_name}이(가) ptm-platform 배포를 시작했습니다"
    await update.message.reply_text(start_notice)
    await _broadcast(update.get_bot(), start_notice, exclude_id=user_id)

    try:
        result_msg, _ = await smart_deploy(
            _ptm_platform_path(),
            compose_project_dir=_ptm_platform_host_path(),
        )
    except Exception as e:
        result_msg = f"❌ 배포 중 예외: {str(e)[:300]}"
    finally:
        release_lock(scope="ptm")

    done_notice = f"[ptm-platform 배포 결과 — {user_name}]\n{result_msg}"
    await _telegram_reply_long_pre(update.message, done_notice)
    await _broadcast(update.get_bot(), done_notice[:3800], exclude_id=user_id)


async def cmd_ptmplatform_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """락 + 최근 커밋 + 컨테이너 상태를 HTML pre 박스로 표시."""
    if not is_allowed(update.effective_user.id):
        return
    try:
        from tools.deploy_manager import get_platform_status
        data = await get_platform_status(_ptm_platform_path())
        if data.get("error"):
            await update.message.reply_text(data["error"])
            return
        html = (
            f"<b>PTM Platform 상태</b>\n\n"
            f"Lock: {escape(data['lock'])}\n\n"
            f"<b>Recent commits:</b>\n<pre>{escape(data['commits'])}</pre>\n\n"
            f"<b>Containers:</b>\n<pre>{escape(data['containers'])}</pre>"
        )
        await update.message.reply_text(html[:4000], parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ 상태 조회 오류: {str(e)[:300]}")


async def cmd_ptmplatform_unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """배포 락 강제 해제."""
    if not is_allowed(update.effective_user.id):
        return
    from tools.deploy_manager import get_lock_status, release_lock
    scope = "ptmquant" if (context.args and context.args[0].lower() in ("quant", "ptmquant")) else "ptm"
    lock = get_lock_status(scope=scope)
    user_name = _user_display_name(update)
    label = "PTMQuant" if scope == "ptmquant" else "ptm-platform"
    if lock:
        owner = lock.get("name", "?")
        elapsed = int(lock.get("elapsed", 0))
        mins, secs = divmod(elapsed, 60)
        release_lock(scope=scope)
        msg = (
            f"🔓 {label} 배포 락 해제됨\n"
            f"기존 락: {owner} ({mins}분 {secs}초 전 시작)\n"
            f"해제한 사람: {user_name}"
        )
    else:
        msg = f"ℹ️ 현재 {label} 배포 락이 없습니다."
    await update.message.reply_text(msg)
    await _broadcast(update.get_bot(), msg, exclude_id=update.effective_user.id)


async def cmd_ptmquant_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PTMQuant pull + build.sh 실행. 전용 락 (ptm-platform 과 독립)."""
    if not is_allowed(update.effective_user.id):
        return
    path = _ptmquant_path()
    if not path or not Path(path).exists():
        await update.message.reply_text(f"❌ PTMQuant 경로 없음: {path or '(미설정)'}")
        return

    from tools.deploy_manager import acquire_lock, release_lock
    from tools.ptm import git_pull_only, run_script

    user_id = update.effective_user.id
    user_name = _user_display_name(update)
    ok, deny_msg = acquire_lock(user_id, user_name, scope="ptmquant")
    if not ok:
        await update.message.reply_text(deny_msg)
        return

    start_notice = f"🚀 {user_name}이(가) PTMQuant 배포를 시작했습니다\n📁 {path}"
    await update.message.reply_text(start_notice)
    await _broadcast(update.get_bot(), start_notice, exclude_id=user_id)

    try:
        pull_msg, pull_ok = await git_pull_only(path)
        if not pull_ok:
            result_msg = pull_msg
        else:
            pull_summary = pull_msg.splitlines()[-1] if pull_msg else ""
            host_path = _ptmquant_host_path()
            env_prefix = f'COMPOSE_PROJECT_DIR="{host_path}" ' if host_path else ""
            build_msg, _ = await run_script(path, f"{env_prefix}./build.sh", timeout=900)
            result_msg = f"✅ PTMQuant 배포 완료\n📥 Pull: {pull_summary}\n🔨 Build:\n{build_msg}"
    except Exception as e:
        result_msg = f"오류: {str(e)[:500]}"
    finally:
        release_lock(scope="ptmquant")

    await _telegram_reply_long_pre(update.message, result_msg)
    await _broadcast(update.get_bot(), result_msg[:3800], exclude_id=user_id)


async def log_telegram_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """슬래시 명령·(선택) 일반 메시지 수신 로그.

    반드시 CommandHandler(group 0) 다음에 실행되도록 group=1 로 둠.
    group=-1 에 두면 일부 환경에서 명령 처리가 건너뛰어질 수 있음.
    """
    tg = CONFIG.get("telegram") or {}
    if not tg.get("log_commands", True):
        return
    msg = update.effective_message
    if not msg or not msg.text:
        return
    uid = msg.from_user.id if msg.from_user else None
    username = msg.from_user.username if msg.from_user else ""
    chat_id = msg.chat_id
    text = msg.text
    log = logging.getLogger("telegram.incoming")
    if text.startswith("/"):
        log.info(
            "cmd chat=%s user=%s @%s text=%r",
            chat_id,
            uid,
            username or "",
            text[:1200],
        )
        try:
            Path("/tmp/ptm_agent_cmd.log").write_text(
                f"{uid} {text[:200]}\n", encoding="utf-8"
            )
        except Exception:
            pass
    elif tg.get("log_messages"):
        log.info(
            "msg chat=%s user=%s text=%r",
            chat_id,
            uid,
            text[:600],
        )




# --- 주기적 작업 ---
async def job_admission_check(app: Application):
    """대학 입시 페이지 체크, 새 PDF 발견 시 다운로드 및 알림"""
    sources = CONFIG.get("admission", {}).get("univ_sources", [])
    if not sources:
        return
    from tools.browser import find_admission_pdf_links, download_pdf, register_doc_in_manifest, get_cached_docs_manifest

    manifest = get_cached_docs_manifest()
    existing_urls = {m["url"] for m in manifest}

    for src in sources:
        name = src.get("name", "대학")
        url = src.get("url", "")
        doc_path = src.get("doc_path", "/")
        pdf_urls = src.get("pdf_urls", [])  # 직접 지정된 PDF URL
        links = []
        if pdf_urls:
            for item in pdf_urls:
                u = item.get("url") if isinstance(item, dict) else item
                t = item.get("title", Path(urlparse(str(u)).path).stem) if isinstance(item, dict) else ""
                if u and ".pdf" in str(u).lower():
                    links.append({"url": u, "title": t or "입시요강", "date": ""})
        if not links and url:
            links = await find_admission_pdf_links(url, doc_path)
        new_docs = []
        for link in links:
            if link["url"] in existing_urls:
                continue
            fp = await download_pdf(link["url"])
            if fp:
                register_doc_in_manifest(link["url"], link["title"], fp)
                new_docs.append(link["title"])
                existing_urls.add(link["url"])

        if new_docs:
            msg = f"📄 새 입시 문서\n[{name}]\n" + "\n".join(f"• {t}" for t in new_docs)
            for uid in ALLOWED_IDS:
                try:
                    await app.bot.send_message(chat_id=uid, text=msg)
                except Exception:
                    pass


async def post_init(app: Application):
    """앱 초기화 후 스케줄러 시작, 봇 메뉴 설정"""
    cmds = _telegram_bot_commands()
    try:
        # 기본 언어 + 한국어 UI: 후자가 없으면 / 메뉴가 비어 보이는 클라이언트가 있음
        await app.bot.set_my_commands(cmds)
        await app.bot.set_my_commands(cmds, language_code="ko")
    except Exception as e:
        print(f"⚠️ set_my_commands 실패 (Telegram 명령 메뉴 미반영 가능): {e}")
    cron_admission = str((CONFIG.get("scheduler") or {}).get("admission_check") or "").strip()
    if cron_admission:
        from apscheduler.triggers.cron import CronTrigger
        scheduler.add_job(
            job_admission_check,
            CronTrigger.from_crontab(cron_admission),
            args=[app],
            id="admission",
            misfire_grace_time=120,
            coalesce=True,
        )
        scheduler.start()

    # PTM Platform webhook 수신 서버 (포트 9000)
    webhook_cfg = CONFIG.get("webhook", {})
    if webhook_cfg.get("enabled", True):
        port = webhook_cfg.get("port", 9000)
        notify_ids = [int(x) for x in webhook_cfg.get("notify_telegram_ids", CONFIG["telegram"]["allowed_user_ids"])]

        _STEP_MAP = {
            "preprocessing": "Preprocessing",
            "rag_enrichment": "RAG Enrichment",
            "report_generation": "Report Generation",
            "order": "Pipeline Complete",
        }

        def _fmt_kst(ts: str) -> str:
            """ISO timestamp → KST 표시"""
            from datetime import datetime, timezone, timedelta
            KST = timezone(timedelta(hours=9))
            try:
                dt = datetime.fromisoformat(ts).astimezone(KST)
                return dt.strftime("%Y-%m-%d %H:%M:%S KST")
            except Exception:
                return ts or ""

        async def handle_webhook(request):
            try:
                body = await request.json()
                if "status" in body and body.get("order_code"):
                    order = body["order_code"]
                    step_raw = (body.get("step") or "").strip().lower().replace("-", "_")
                    step = _STEP_MAP.get(step_raw, step_raw.replace("_", " ").title())
                    status_label = (body.get("status") or "").title()
                    ts = _fmt_kst(body.get("timestamp", ""))
                    text = f"[{order}] {step} : {status_label}\n{ts}"
                    if body.get("error_message"):
                        text += f"\n⚠️ {body['error_message']}"
                elif body.get("message"):
                    text = body["message"]
                else:
                    text = body.get("text") or str(body)
            except Exception:
                text = (await request.text()) or "No message"

            for uid in notify_ids:
                try:
                    await app.bot.send_message(chat_id=uid, text=str(text)[:4000])
                except Exception:
                    pass
            return web.Response(text="OK")

        from aiohttp import web
        web_app = web.Application()
        web_app.router.add_post("/webhook/ptm", handle_webhook)
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        try:
            await site.start()
        except OSError as e:
            if e.errno == 48:  # Address already in use
                print(
                    f"⚠️ Webhook 포트 {port} 사용 중 — PTM webhook 은 건너뜁니다. "
                    "이미 봇이 떠 있으면 종료 후 하나만 실행하세요: pkill -f 'python main.py'"
                )
                await runner.cleanup()
            else:
                raise
        else:
            app.bot_data["webhook_runner"] = runner
            print(f"📡 Webhook 수신: http://0.0.0.0:{port}/webhook/ptm")


def _make_telegram_request(*, connection_pool_size: int) -> HTTPXRequest:
    """Telegram API 연결 안정화: IPv4 바인딩, 긴 pool/connect 타임아웃."""
    transport = httpx.AsyncHTTPTransport(
        local_address="0.0.0.0",
        http1=True,
        http2=False,
    )
    return HTTPXRequest(
        connection_pool_size=connection_pool_size,
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=30.0,
        http_version="1.1",
        httpx_kwargs={"transport": transport},
    )


def main():
    _configure_logging()
    _acquire_single_instance_lock()
    # 일반 API / getUpdates 각각 전용 클라이언트 (pool_timeout 1초 기본은 끊김에 취약)
    req = _make_telegram_request(connection_pool_size=256)
    req_updates = _make_telegram_request(connection_pool_size=8)
    app = (
        Application.builder()
        .token(TOKEN)
        .request(req)
        .get_updates_request(req_updates)
        .post_init(post_init)
        .build()
    )
    app.add_handler(MessageHandler(filters.TEXT, log_telegram_text), group=1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ptmplatform_deploy", cmd_ptmplatform_deploy))
    app.add_handler(CommandHandler("ptmplatform_status", cmd_ptmplatform_status))
    app.add_handler(CommandHandler("ptmplatform_unlock", cmd_ptmplatform_unlock))
    app.add_handler(CommandHandler("ptmquant_deploy", cmd_ptmquant_deploy))
    app.add_handler(CommandHandler("latest", cmd_latest))
    app.add_handler(CommandHandler("shell", cmd_shell))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    async def error_handler(update, context):
        import logging
        logger = logging.getLogger(__name__)
        from telegram.error import Conflict, NetworkError
        if isinstance(context.error, NetworkError):
            logger.warning("⚠️ Telegram 연결 실패 (재시도 중): %s", context.error)
            return
        if isinstance(context.error, Conflict):
            logger.warning(
                "⚠️ 다른 봇 인스턴스와 충돌 — 하나만 실행하세요: pkill -f 'python main.py'"
            )
            return
        logger.exception("오류: %s", context.error)

    app.add_error_handler(error_handler)

    print("🤖 OpenClaw-Home 에이전트 가동 중... (Ctrl+C 종료)")
    app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=10)


if __name__ == "__main__":
    main()
