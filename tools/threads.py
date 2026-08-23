"""
Threads.net 모니터링
- 검색 "최근검색" 탭에서 게시물 수집 (인기검색 아님)
- fingerprint(내용 해시)로 새 글 감지 → 중국어 한국어 번역 → Telegram 알림
"""
import asyncio
import hashlib
import json
import re
from pathlib import Path
from datetime import datetime
from urllib.parse import quote

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
THREADS_STATE_FILE = DATA_DIR / "threads_state.json"


def _ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_state(key: str) -> dict:
    _ensure_dirs()
    if not THREADS_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(THREADS_STATE_FILE.read_text())
        return data.get(key, {})
    except Exception:
        return {}


def _save_state(key: str, state: dict):
    _ensure_dirs()
    all_data = {}
    if THREADS_STATE_FILE.exists():
        try:
            all_data = json.loads(THREADS_STATE_FILE.read_text())
        except Exception:
            pass
    all_data[key] = state
    THREADS_STATE_FILE.write_text(json.dumps(all_data, ensure_ascii=False, indent=2))


def _extract_image_urls(obj, urls: list) -> None:
    """post 객체에서 이미지 URL 재귀 추출 (image_versions2, carousel_media, media_url 등)"""
    if isinstance(obj, dict):
        if "url" in obj and isinstance(obj.get("url"), str) and obj["url"].startswith("http"):
            u = obj["url"]
            if "cdninstagram.com" in u or "fbcdn.net" in u or "threads" in u:
                urls.append(u)
        if "image_versions2" in obj:
            iv = obj.get("image_versions2")
            cands = (iv.get("candidates", []) if isinstance(iv, dict) else []) or []
            for c in cands[:2]:
                if isinstance(c, dict) and c.get("url"):
                    urls.append(c["url"])
        if "carousel_media" in obj:
            cm = obj.get("carousel_media") or []
            for m in (cm if isinstance(cm, list) else [])[:5]:
                _extract_image_urls(m, urls)
        for v in obj.values():
            _extract_image_urls(v, urls)
    elif isinstance(obj, list):
        for item in obj[:5]:
            _extract_image_urls(item, urls)


def _extract_post(post: dict) -> dict | None:
    """단일 post 객체에서 텍스트/메타/이미지 추출"""
    text = None
    if "caption" in post and isinstance(post["caption"], dict):
        text = post["caption"].get("text")
    elif "text" in post:
        text = post.get("text")
    if not text or len(str(text).strip()) < 3:
        return None
    text = str(text).strip()
    code = post.get("code") or post.get("shortcode", "")
    username = ""
    for u in (post.get("user"), post):
        if isinstance(u, dict) and u.get("username"):
            username = u["username"]
            break
    ts = post.get("taken_at") or post.get("timestamp") or post.get("created_at")
    ts_unix = None
    if isinstance(ts, (int, float)):
        ts_unix = ts
        ts_str = datetime.fromtimestamp(ts).isoformat() if ts else ""
    else:
        ts_str = str(ts) if ts else ""
    image_urls = []
    _extract_image_urls(post, image_urls)
    image_urls = list(dict.fromkeys(image_urls))[:4]  # 중복 제거, 최대 4장
    return {
        "text": text,
        "username": username,
        "code": code,
        "timestamp": ts_str,
        "taken_at_unix": ts_unix,
        "permalink": f"https://www.threads.net/@{username}/post/{code}" if username and code else "",
        "image_urls": image_urls,
    }


def _extract_threads_from_json(obj, acc: list) -> None:
    """재귀적으로 JSON에서 thread_items 또는 caption/text 추출"""
    if isinstance(obj, dict):
        if "thread_items" in obj:
            # 멀티파트 스레드: 작성자 본인의 연속 파트만 합침 (다른 사람 댓글 제외)
            parts = []
            author_username = None
            for item in obj.get("thread_items", []):
                if isinstance(item, dict):
                    post = item.get("post", item)
                    if isinstance(post, dict):
                        p = _extract_post(post)
                        if p:
                            uname = p.get("username", "")
                            if author_username is None:
                                author_username = uname
                            if uname == author_username:  # 같은 작성자 파트만
                                parts.append(p)
                            else:
                                break  # 다른 사람 댓글 나오면 여기서 끊음
            if parts:
                if len(parts) == 1:
                    acc.append(parts[0])
                else:
                    combined = parts[0].copy()
                    combined["text"] = "\n\n".join(p["text"] for p in parts)
                    combined["image_urls"] = []
                    for p in parts:
                        combined["image_urls"].extend(p.get("image_urls") or [])
                    combined["image_urls"] = list(dict.fromkeys(combined["image_urls"]))[:4]
                    # taken_at_unix는 첫 파트(본문) 기준
                    combined["taken_at_unix"] = parts[0].get("taken_at_unix")
                    acc.append(combined)
            return
        # post 자체가 있는 경우
        if "code" in obj and ("text" in obj or "caption" in obj):
            p = _extract_post(obj)
            if p:
                acc.append(p)
            return
        for v in obj.values():
            _extract_threads_from_json(v, acc)
            if len(acc) >= 20:
                return
    elif isinstance(obj, list):
        for item in obj:
            _extract_threads_from_json(item, acc)
            if len(acc) >= 20:
                return


async def fetch_threads_posts(
    search_query: str | None = None,
    username: str | None = None,
    limit: int = 10,
) -> tuple[list[dict], str | None]:
    """
    Threads에서 게시물 수집.
    search_query 또는 username 중 하나 지정.
    Returns: (posts, error_msg)
    """
    if search_query:
        url = f"https://www.threads.net/search?q={quote(search_query)}&serp_type=default&filter=recent"
        state_key = f"search:{search_query}"
    elif username:
        uname = username.lstrip("@")
        url = f"https://www.threads.net/@{uname}"
        state_key = f"user:{uname}"
    else:
        return [], "search_query 또는 username 필요"

    posts = []
    error_msg = None

    for attempt in range(2):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 720})
                page = await context.new_page()
                page.set_default_timeout(45000)
                await page.set_extra_http_headers({
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                })
                await page.goto(url, wait_until="load", timeout=40000)
                await page.wait_for_timeout(3000)

                content = await page.content()
                scripts = re.findall(r'<script[^>]*type="application/json"[^>]*data-sjs[^>]*>([^<]+)</script>', content)
                for raw in scripts:
                    try:
                        data = json.loads(raw)
                        _extract_threads_from_json(data, posts)
                        if len(posts) >= limit:
                            break
                    except json.JSONDecodeError:
                        continue

                seen = set()
                unique = []
                for p in posts:
                    if not isinstance(p, dict) or not p.get("text"):
                        continue
                    fp = hashlib.sha256(str(p["text"])[:200].encode()).hexdigest()[:16]
                    if fp not in seen:
                        seen.add(fp)
                        unique.append(p)

                posts = unique[:limit]

            except PlaywrightTimeout:
                error_msg = "페이지 로드 타임아웃"
            except Exception as e:
                error_msg = str(e)
            finally:
                await browser.close()

        if posts or not error_msg:
            break
        error_msg = None

    return posts, error_msg


async def check_threads_and_translate(
    search_query: str | None = None,
    username: str | None = None,
    translate_model: str = "gemma3:27b",
    max_age_hours: int = 24,
) -> tuple[list[dict], str | None]:
    """
    Threads 게시물 수집 → 새 글만 필터 → 중국어면 한국어 번역.
    Returns: (새로 발견된 번역된 게시물들, error_msg)
    """
    try:
        return await _check_threads_and_translate_impl(
            search_query=search_query,
            username=username,
            translate_model=translate_model,
            max_age_hours=max_age_hours,
        )
    except Exception as e:
        return [], str(e)


async def _check_threads_and_translate_impl(
    search_query: str | None = None,
    username: str | None = None,
    translate_model: str = "gemma3:27b",
    max_age_hours: int = 24,
) -> tuple[list[dict], str | None]:
    from tools.translator import translate_to_korean, detect_language

    def _is_chinese(t: str) -> bool:
        return detect_language(t) == "zh"

    key = f"search:{search_query}" if search_query else f"user:{username}"
    prev = _load_state(key)
    items_prev = prev.get("items") if isinstance(prev, dict) else []
    prev_fps = {x["fingerprint"] for x in (items_prev or []) if isinstance(x, dict) and x.get("fingerprint")}
    is_first_run = not prev

    posts, err = await fetch_threads_posts(search_query=search_query, username=username, limit=15)
    if err:
        return [], err

    new_translated = []
    loop = asyncio.get_event_loop()

    for p in posts:
        if not isinstance(p, dict) or not p.get("text"):
            continue
        text = str(p["text"]).strip()
        fp = hashlib.sha256(text[:200].encode()).hexdigest()[:16]
        if is_first_run:
            continue  # 첫 실행은 기준선만 저장, 알림 생략
        if fp not in prev_fps:
            # N시간 이내 글만 알림 (인기검색에 섞인 오래된 글 제외)
            ts_unix = p.get("taken_at_unix")
            if ts_unix is not None:
                age_sec = datetime.now().timestamp() - ts_unix
                if age_sec > max_age_hours * 3600:
                    continue  # 너무 오래된 글은 스킵
            if _is_chinese(text):
                try:
                    translated = await loop.run_in_executor(
                        None,
                        lambda t=text: translate_to_korean(t, translate_model),
                    )
                    if not translated:
                        continue
                    new_translated.append({
                        "fingerprint": fp,
                        "original": text[:150],
                        "translated": translated,
                        "username": p.get("username", ""),
                        "permalink": p.get("permalink", ""),
                        "timestamp": p.get("timestamp", ""),
                        "image_urls": p.get("image_urls") or [],
                    })
                except Exception:
                    pass

    # 상태 저장
    items = []
    for p in posts:
        if isinstance(p, dict) and p.get("text"):
            items.append({"fingerprint": hashlib.sha256(str(p["text"])[:200].encode()).hexdigest()[:16]})
    _save_state(key, {"items": items, "last_check": datetime.now().isoformat()})

    return new_translated, None
