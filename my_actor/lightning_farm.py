import json
import os
import random
import re
import time
from urllib.parse import urlparse


LIGHTNING_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lightning_state.json")
SITE_ORIGIN = "https://remanga.org"
API_ORIGIN = "https://api.remanga.org"


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


def get_auth_token(page):
    return page.evaluate(
        """() => {
            const keys = ['token', 'accessToken', 'access_token'];
            for (const key of keys) {
                try {
                    const value = localStorage.getItem(key);
                    if (value && value !== 'null' && value !== 'undefined') {
                        return value.startsWith('Bearer ') ? value : 'Bearer ' + value;
                    }
                } catch (_) {}
            }
            try {
                const match = document.cookie.match(/(?:^|; )token=([^;]*)/);
                if (match && match[1]) {
                    const value = decodeURIComponent(match[1]);
                    return value.startsWith('Bearer ') ? value : 'Bearer ' + value;
                }
            } catch (_) {}
            return '';
        }"""
    )


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
    token = get_auth_token(page)
    if not token:
        raise RuntimeError("ReManga Bearer token not found")

    payload = {"chapter": chapter_id, "chapter_id": chapter_id, "progress": 100}

    result = page.evaluate(
        """async ({apiOrigin, token, payload}) => {
            const endpoints = [
                apiOrigin + '/api/activity/views/',
                apiOrigin + '/api/v2/activity/views/',
                '/api/activity/views/'
            ];

            for (const endpoint of endpoints) {
                try {
                    const response = await fetch(endpoint, {
                        method: 'POST',
                        headers: {
                            'Accept': 'application/json, text/plain, */*',
                            'Content-Type': 'application/json',
                            'Authorization': token
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
                } catch (_) {}
            }

            return {ok: false, status: 0, endpoint: null};
        }""",
        {
            "apiOrigin": API_ORIGIN,
            "token": token,
            "payload": payload,
        },
    )

    if not result.get("ok"):
        raise RuntimeError(f"read API failed: {result}")

    return result


def get_lightning_items(page):
    token = get_auth_token(page)
    if not token:
        return []

    result = page.evaluate(
        """async ({apiOrigin, token}) => {
            const endpoints = [
                apiOrigin + '/api/v2/billing/lightning-payments/',
                apiOrigin + '/api/v2/billing/lightning-payments/?ordering=-created_at&count=20&page=1',
                '/api/v2/billing/lightning-payments/'
            ];

            for (const endpoint of endpoints) {
                try {
                    const response = await fetch(endpoint, {
                        method: 'GET',
                        headers: {
                            'Accept': 'application/json, text/plain, */*',
                            'Authorization': token
                        },
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
        }""",
        {"apiOrigin": API_ORIGIN, "token": token},
    )

    return result.get("items", []) if result.get("ok") else []


def lightning_item_key(item):
    return str(
        item.get("uuid")
        or item.get("id")
        or item.get("transaction")
        or (
            str(item.get("type")) + "_" + str(item.get("created_at"))
            if item.get("created_at")
            else ""
        )
    )


def is_read_reward(item):
    raw_type = item.get("type")
    if isinstance(raw_type, dict):
        raw_type = (
            raw_type.get("id")
            or raw_type.get("value")
            or raw_type.get("type")
            or raw_type.get("code")
        )

    text = " ".join(
        str(item.get(key) or "")
        for key in ("description", "comment", "name", "text", "title", "label")
    )

    return str(raw_type) == "27" or bool(re.search(r"чтен|глав|read", text, re.I))


def read_chapter(page, url, chapter_id):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)

    page.wait_for_timeout(100)
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")

    before_items = get_lightning_items(page)
    before_ids = {lightning_item_key(item) for item in before_items}

    result = mark_chapter_read(page, chapter_id)
    print(
        f"[lightning] marked chapter={chapter_id} "
        f"progress=100 status={result['status']} endpoint={result['endpoint']}"
    )

    page.wait_for_timeout(1500)

    after_items = get_lightning_items(page)
    rewards = [
        item
        for item in after_items
        if is_read_reward(item) and lightning_item_key(item) not in before_ids
    ]

    if rewards:
        print(f"[lightning] REWARD RECEIVED: {len(rewards)} new reading reward(s)")
    else:
        print("[lightning] chapter marked 100%; no new reading reward visible yet")


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
