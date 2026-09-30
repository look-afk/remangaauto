import json
import os
import random
import time
from urllib.parse import urlparse


LIGHTNING_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lightning_state.json",
)
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
        hrefs = page.eval_on_selector_all(
            'a[href*="/manga/"]',
            "els => els.map(e => e.href)",
        )

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


def read_chapter(page, url, chapter_id):
    print(f"[lightning] открываю главу {chapter_id}: {url}")

    page.goto(url, wait_until="domcontentloaded", timeout=60000)

    # Старый режим: глава действительно открывается в браузере,
    # reader успевает загрузить страницы, затем мы физически прокручиваем
    # её от начала до конца. Никакого ручного POST progress=100.
    page.wait_for_timeout(3500)

    last_y = -1
    stable_count = 0

    for _ in range(160):
        y = page.evaluate("window.scrollY")
        height = page.evaluate("document.documentElement.scrollHeight")
        viewport = page.evaluate("window.innerHeight")

        if y + viewport >= height - 5:
            # Даём lazy-loaded последним страницам дорендериться.
            page.wait_for_timeout(1200)

            new_height = page.evaluate("document.documentElement.scrollHeight")
            new_y = page.evaluate("window.scrollY")

            if new_y + viewport >= new_height - 5:
                break

        if y == last_y:
            stable_count += 1
            if stable_count >= 3:
                page.wait_for_timeout(500)
                height = page.evaluate("document.documentElement.scrollHeight")
                y = page.evaluate("window.scrollY")
                viewport = page.evaluate("window.innerHeight")
                if y + viewport >= height - 5:
                    break
        else:
            stable_count = 0

        last_y = y

        # Именно автоскролл, а не прыжок сразу в самый низ.
        step = max(int(viewport * 2.5), 1200)
        page.mouse.wheel(0, step)
        page.wait_for_timeout(180)

    # Последний проход до самого низа, чтобы reader получил финальное событие scroll.
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(1500)

    print(f"[lightning] глава {chapter_id} реально проскроллена до конца")


def farm_lightning(page):
    collection_url = os.getenv(
        "READ_COLLECTION_URL",
        f"{SITE_ORIGIN}/collections/2197",
    )

    titles = [
        x.strip()
        for x in os.getenv("READ_TITLE_URLS", "").split(",")
        if x.strip()
    ]

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

            print(
                f"[lightning] completed "
                f"{state['count']}/{max_chapters}: {url}"
            )

            time.sleep(random.uniform(1, 3))

    print(f"[lightning] finished: {state['count']} chapters today")
