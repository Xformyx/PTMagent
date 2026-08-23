"""
Playwright 기반 웹 스크래핑 도구
- Instagram: query Response의 follower_count 추출 (네트워크 인터셉트)
- 대학 입시 홈페이지 PDF 링크 탐지 및 다운로드
"""
import asyncio
import hashlib
import json
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# 프로젝트 루트 기준 data 경로
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
UNIV_CACHE = DATA_DIR / "univ_cache"
INSTAGRAM_HISTORY = DATA_DIR / "instagram_history.json"


def _ensure_dirs():
    UNIV_CACHE.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _extract_from_json(obj, *keys) -> dict:
    """JSON 객체에서 follower_count, following_count 등 재귀 탐색"""
    found = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "follower_count" and isinstance(v, (int, float)):
                found["followers"] = int(v)
            elif k == "following_count" and isinstance(v, (int, float)):
                found["following"] = int(v)
            elif k == "edge_followed_by" and isinstance(v, dict) and "count" in v:
                found["followers"] = int(v["count"])
            elif k == "edge_follow" and isinstance(v, dict) and "count" in v:
                found["following"] = int(v["count"])
            elif k == "edge_owner_to_timeline_media" and isinstance(v, dict) and "count" in v:
                found["posts"] = int(v["count"])
            if len(found) < 3:
                found.update(_extract_from_json(v))
    elif isinstance(obj, list):
        for item in obj:
            found.update(_extract_from_json(item))
            if len(found) >= 3:
                break
    return found


async def get_instagram_followers(user_id: str) -> dict:
    """
    Instagram 프로필 페이지 로드 시 query/GraphQL Response에서 follower_count 추출.
    개발자 도구 Network > query Response의 JSON에서 정확한 수치 사용.
    Returns: {"followers": int, "following": int, "posts": int, "error": str|None}
    """
    url = f"https://www.instagram.com/{user_id}/"
    result = {"followers": None, "following": None, "posts": None, "error": None}
    captured = {}
    done = asyncio.Event()

    async def on_response(response):
        nonlocal captured, done
        if captured.get("followers") is not None:
            return
        try:
            resp_url = response.url
            if "instagram.com" not in resp_url:
                return
            # query, graphql, web_profile_info, api 등 프로필 데이터 응답
            if any(x in resp_url.lower() for x in ["query", "graphql", "web_profile_info", "profile", "/api/"]):
                body = await response.json()
                data = _extract_from_json(body)
                if data.get("followers") is not None:
                    captured = data
                    done.set()
        except Exception:
            pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            page.on("response", on_response)
            await page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            })
            await page.goto(url, wait_until="networkidle", timeout=20000)
            # 응답 캡처 대기 (최대 3초)
            try:
                await asyncio.wait_for(done.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
            if captured:
                result["followers"] = captured.get("followers")
                result["following"] = captured.get("following")
                result["posts"] = captured.get("posts")
        except PlaywrightTimeout:
            result["error"] = "페이지 로드 타임아웃"
        except Exception as e:
            result["error"] = str(e)
        finally:
            await browser.close()

    return result


def save_instagram_history(user_id: str, data: dict) -> None:
    """팔로워 히스토리 저장 (트렌드 분석용)"""
    _ensure_dirs()
    history = {}
    if INSTAGRAM_HISTORY.exists():
        with open(INSTAGRAM_HISTORY) as f:
            history = json.load(f)
    if user_id not in history:
        history[user_id] = []
    history[user_id].append({
        "ts": datetime.now().isoformat(),
        "followers": data.get("followers"),
        "following": data.get("following"),
        "posts": data.get("posts"),
    })
    # 최근 1000개만 유지
    history[user_id] = history[user_id][-1000:]
    with open(INSTAGRAM_HISTORY, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def get_instagram_trend(user_id: str) -> dict:
    """급등/급락 여부 분석"""
    if not INSTAGRAM_HISTORY.exists():
        return {"trend": "unknown", "change": None, "message": "히스토리 없음"}
    with open(INSTAGRAM_HISTORY) as f:
        history = json.load(f).get(user_id, [])
    if len(history) < 2:
        return {"trend": "unknown", "change": None, "message": "데이터 부족"}
    prev = history[-2].get("followers")
    curr = history[-1].get("followers")
    if prev is None or curr is None:
        return {"trend": "unknown", "change": None}
    diff = curr - prev
    pct = (diff / prev * 100) if prev else 0
    if pct >= 10:
        return {"trend": "spike", "change": pct, "message": f"급등 +{pct:.1f}%"}
    if pct <= -10:
        return {"trend": "drop", "change": pct, "message": f"급락 {pct:.1f}%"}
    return {"trend": "stable", "change": pct, "message": f"변동 {pct:+.1f}%"}


async def find_admission_pdf_links(base_url: str, doc_path: str) -> list[dict]:
    """
    대학 입시 페이지에서 PDF 링크 탐지.
    Returns: [{"url": str, "title": str, "date": str}, ...]
    """
    full_url = urljoin(base_url, doc_path)
    links = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
            for a in await page.query_selector_all('a[href*=".pdf"], a[href*="pdf"]'):
                href = await a.get_attribute("href")
                if not href or ".pdf" not in href.lower():
                    continue
                abs_url = urljoin(full_url, href)
                title = (await a.inner_text()).strip() or Path(urlparse(abs_url).path).stem
                links.append({"url": abs_url, "title": title, "date": datetime.now().isoformat()})
        except Exception:
            pass
        finally:
            await browser.close()

    return links


async def download_pdf(url: str, save_dir: Path | None = None) -> Path | None:
    """PDF 다운로드 후 univ_cache에 저장. 중복 시 스킵."""
    _ensure_dirs()
    save_dir = save_dir or UNIV_CACHE
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    filename = f"{url_hash}.pdf"
    filepath = save_dir / filename

    if filepath.exists():
        return filepath

    try:
        import httpx
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        async with httpx.AsyncClient(follow_redirects=True, timeout=30, headers=headers) as client:
            r = await client.get(url)
            if r.status_code == 200 and len(r.content) > 100:
                filepath.write_bytes(r.content)
                return filepath
    except ImportError:
        return None
    except Exception:
        return None


def get_cached_docs_manifest() -> list[dict]:
    """캐시된 입시 문서 목록"""
    _ensure_dirs()
    manifest_path = UNIV_CACHE / "_manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            return json.load(f)
    return []


def register_doc_in_manifest(url: str, title: str, filepath: Path) -> None:
    """다운로드한 문서를 manifest에 등록"""
    manifest = get_cached_docs_manifest()
    manifest.append({
        "url": url,
        "title": title,
        "filepath": str(filepath),
        "downloaded_at": datetime.now().isoformat(),
    })
    with open(UNIV_CACHE / "_manifest.json", "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
