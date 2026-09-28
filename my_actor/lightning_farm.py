import json
import os
import random
import time
from urllib.parse import urlparse


LIGHTNING_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lightning_state.json")
SITE_ORIGIN = "https://remanga.org"


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))


def load_state():
    try:
        with open(LIGHTNING_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}
    if state.get("date") != _today():
        state = {"date": _today(), "count": 0, "read": state.get("read", [])}
    return state


def save_state(state):
    state["read"] = state.get("read", [])[-2000:]
    with open(LIGHTNING_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def collect_collection_titles(page, collection_url):
    page.goto(collection_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    titles = []
    stale = 0
    for _ in range(40):
        hrefs = page.eval_on_selector_all('a[href*="/manga/"]', "els => els.map(e => e.href)")
        before = len(titles)
        for href in hrefs:
            parts = href.split("/manga/", 1)
            if len(parts) != 2:
                continue
            slug = parts[1].split("/")[0].split("?")[0].split("#")[0]
            if slug:
                url = f"{SITE_ORIGIN}/manga/{slug}"
                if url not in titles:
                    titles.append(url)
        stale = stale + 1 if len(titles) == before else 0
        if stale >= 3:
            break
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(1200)
    print(f"[lightning] titles={len(titles)}")
    return titles


def collect_chapters(page, title_url):
    """Current ReManga chapter links are /manga/<slug>/<numeric-id>, not /chapter/."""
    page.goto(title_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    for _ in range(8):
        page.mouse.wheel(0, 1600)
        page.wait_for_timeout(500)

    hrefs = page.eval_on_selector_all(
        'a[href*="/manga/"]',
        "els => els.map(e => e.href)",
    )
    chapters = []
    seen_ids = set()
    for href in hrefs:
        parsed = urlparse(href)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) != 3 or parts[0] != "manga" or not parts[2].isdigit():
            continue
        chapter_id = parts[2]
        if chapter_id in seen_ids:
            continue
        seen_ids.add(chapter_id)
        chapters.append((href, int(chapter_id)))

    chapters.sort(key=lambda item: item[1], reverse=True)
    print(f"[lightning] {title_url}: chapters={len(chapters)}")
    return chapters


def mark_chapter_read(page, chapter_id):
    """The real ReManga trigger: activity/views with progress=100."""
    payload = {"chapter": chapter_id, "chapter_id": chapter_id, "progress": 100}
    result = page.evaluate(
        """async ({payload}) => {
            const endpoints = [
                '/api/activity/views/',
                '/api/v2/activity/views/'
            ];
            for (const endpoint of endpoints) {
                try {
                    const response = await fetch(endpoint, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Accept': 'application/json, text/plain, */*'
                        },
                        credentials: 'include',
                        body: JSON.stringify(payload)
                    });
                    let data = null;
                    try { data = await response.json(); } catch (_) {}
                    if (response.ok) {
                        return {ok: true, status: response.status, endpoint, data};
                    }
                    if (response.status !== 404 && response.status !== 405) {
                        return {ok: false, status: response.status, endpoint, data};
                    }
                } catch (error) {
                    return {ok: false, status: 0, endpoint, error: String(error)};
                }
            }
            return {ok: false, status: 404, endpoint: null};
        }""",
        {"payload": payload},
    )
    if not result.get("ok"):
        raise RuntimeError(f"read API failed: {result}")
    return result


def check_lightning_reward(page, previous_ids):
    """Verify whether ReManga created a new reading-lightning billing entry."""
    result = page.evaluate(
        """async () => {
            const urls = [
                '/api/v2/billing/lightning-payments/',
                '/api/v2/billing/lightning-payments/?ordering=-created_at&count=20&page=1'
            ];
            for (const url of urls) {
                try {
                    const response = await fetch(url, {
                        method: 'GET',
                        headers: {'Accept': 'application/json, text/plain, */*'},
                        credentials: 'include'
                    });
                    if (!response.ok) continue;
                    const data = await response.json();
                    const items = Array.isArray(data)
                        ? data
                        : (Array.isArray(data.results) ? data.results
                        : (Array.isArray(data.content) ? data.content
                        : (Array.isArray(data.data) ? data.data : [])));
                    return {ok: true, items};
                } catch (_) {}
            }
            return {ok: false, items: []};
        }"""
    )

    if not result.get("ok"):
        return []

    new_items = []
    for item in result.get("items", []):
        raw_type = item.get("type")
        if isinstance(raw_type, dict):
            raw_type = (
                raw_type.get("id")
                or raw_type.get("value")
                or raw_type.get("type")
                or raw_type.get("code")
            )
        description = str(
            item.get("description")
            or item.get("comment")
            or item.get("name")
            or item.get("text")
            or item.get("title")
            or item.get("label")
            or ""
        )
        is_read_reward = str(raw_type) == "27" or bool(
            __import__("re").search(r"чтен|глав|read", description, __import__("re").I)
        )
        if not is_read_reward:
            continue

        key = str(
            item.get("uuid")
            or item.get("id")
            or item.get("transaction")
            or item.get("created_at")
            or ""
        )
        if key and key not in previous_ids:
            new_items.append(item)

    return new_items


def read_chapter(page, url, chapter_id):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)

    # Full-read trigger: jump to the end, then send the same 100% activity
    # request used by the current ReManga reader.
    page.wait_for_timeout(50)
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")

    previous = check_lightning_reward(page, set())
    previous_ids = {
        str(
            item.get("uuid")
            or item.get("id")
            or item.get("transaction")
            or item.get("created_at")
            or ""
        )
        for item in previous
    }

    result = mark_chapter_read(page, chapter_id)
    print(f"[lightning] marked chapter={chapter_id} progress=100 status={result['status']}")

    # The reward is created asynchronously by ReManga. Check after the
    # server has had a moment to process the completed read.
    page.wait_for_timeout(1200)
    rewards = check_lightning_reward(page, previous_ids)

    if rewards:
        amount = sum(
            abs(
                float(
                    item.get("amount")
                    or item.get("count")
                    or item.get("value")
                    or item.get("sum")
                    or 15
                )
            )
            for item in rewards
        )
        print(f"[lightning] REWARD RECEIVED: +{amount:g} lightning")
    else:
        print("[lightning] chapter completed; no new reading reward detected")


def farm_lightning(page):
    collection_url = os.getenv("READ_COLLECTION_URL", f"{SITE_ORIGIN}/collections/2197")
    titles = [x.strip() for x in os.getenv("READ_TITLE_URLS", "").split(",") if x.strip()]
    if not titles:
        titles = collect_collection_titles(page, collection_url)
    if not titles:
        print("[lightning] no titles found")
        return

    max_chapters = int(os.getenv("LIGHTNING_MAX_CHAPTERS", "96"))
    state = load_state()
    if state["count"] >= max_chapters:
        print(f"[lightning] daily limit already reached: {state['count']}")
        return

    read_set = set(str(x) for x in state.get("read", []))
    random.shuffle(titles)

    for title_url in titles:
        if state["count"] >= max_chapters:
            break
        try:
            chapters = collect_chapters(page, title_url)
        except Exception as exc:
            print(f"[lightning] chapter discovery failed: {title_url}: {exc}")
            continue

        for url, chapter_id in chapters:
            if state["count"] >= max_chapters:
                break
            key = str(chapter_id)
            if key in read_set:
                continue
            try:
                read_chapter(page, url, chapter_id)
            except Exception as exc:
                print(f"[lightning] failed chapter={chapter_id}: {exc}")
                continue

            state["count"] += 1
            state.setdefault("read", []).append(key)
            read_set.add(key)
            save_state(state)
            print(f"[lightning] completed {state['count']}/{max_chapters}: {url}")

    print(f"[lightning] finished: {state['count']} chapters today")
