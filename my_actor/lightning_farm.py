import json
import os
import random
import time

import requests


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
    progress = state.get("progress", {})
    if state.get("date") != _today():
        # новый день: дневной счётчик сбрасывается, прогресс по тайтлам остаётся,
        # чтобы продолжать читать главы по порядку, а не с начала.
        state = {"date": _today(), "count": 0, "progress": progress}
    state.setdefault("progress", progress)
    return state


def save_state(state):
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


def build_session(page, token):
    """requests.Session с теми же cookies, что в браузере — на случай, если API их тоже проверяет."""
    session = requests.Session()
    try:
        for cookie in page.context.cookies():
            try:
                session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain") or "")
            except Exception:
                pass
    except Exception as exc:
        print(f"[lightning] не удалось скопировать cookies в сессию: {exc}")

    session.headers.update(
        {
            "Authorization": token,
            "Accept": "application/json, text/plain, */*",
            "Referer": SITE_ORIGIN + "/",
            "Origin": SITE_ORIGIN,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        }
    )
    return session


def _goto_partial(page, url, timeout=20000):
    target = url.rstrip("/")
    try:
        page.goto(url, wait_until="commit", timeout=timeout)
        return
    except Exception as exc:
        current = page.url.rstrip("/")
        if current == target or current.startswith(target + "?") or current.startswith(target + "#"):
            print(f"[lightning] navigation timeout, using partially loaded page: {url}")
            return
        raise exc


def collect_collection_titles(page, collection_url):
    """Собирает ссылки на тайтлы из коллекции (DOM, рендерится сразу при загрузке)."""
    _goto_partial(page, collection_url)
    page.wait_for_timeout(2000)
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


# ---------------------------------------------------------------------------
# Список глав тайтла: DOM (a[href]) не отдаёт полный список — он подгружается
# через JS-виджет. Сайт использует публичный JSON API, который сразу отдаёт
# главы отсортированными по номеру (index), поэтому читаем через него.
# ---------------------------------------------------------------------------

def get_title_info(session, slug):
    resp = session.get(f"{API_ORIGIN}/api/titles/{slug}/", timeout=20)
    resp.raise_for_status()
    content = resp.json().get("content", {})
    branches = content.get("branches") or []
    if not branches:
        return None
    return {
        "branch_id": branches[0]["id"],
        "count_chapters": content.get("count_chapters", 0),
    }


def get_all_chapters(session, branch_id):
    """Все главы ветки перевода, отсортированные по возрастанию (глава 1 первая)."""
    chapters = []
    page_num = 1
    page_size = 100
    while True:
        resp = session.get(
            f"{API_ORIGIN}/api/titles/chapters/",
            params={"branch_id": branch_id, "count": page_size, "page": page_num, "ordering": "index"},
            timeout=20,
        )
        if resp.status_code != 200:
            break
        items = resp.json().get("content") or []
        if not items:
            break
        chapters.extend(items)
        if len(items) < page_size:
            break
        page_num += 1
    chapters.sort(key=lambda c: c.get("index", 0))
    return chapters


def is_locked_chapter(chapter):
    """Платные/ещё не выкупленные главы — не читаем автоматически."""
    if chapter.get("is_bought"):
        return False
    if chapter.get("is_paid"):
        return True
    price = chapter.get("price")
    return bool(price)


def mark_chapter_read(session, chapter_id):
    payload = {"chapter": chapter_id, "chapter_id": chapter_id, "progress": 100}
    endpoints = [
        f"{API_ORIGIN}/api/activity/views/",
        f"{API_ORIGIN}/api/v2/activity/views/",
    ]
    last_status = None
    for endpoint in endpoints:
        try:
            resp = session.post(endpoint, json=payload, timeout=20)
        except Exception as exc:
            last_status = str(exc)
            continue
        if resp.ok:
            return True, resp.status_code, endpoint
        last_status = resp.status_code
        if resp.status_code not in (404, 405):
            return False, resp.status_code, endpoint
    return False, last_status, None


def get_lightning_items(session):
    try:
        resp = session.get(f"{API_ORIGIN}/api/v2/billing/lightning-payments/", timeout=20)
        if not resp.ok:
            return []
        data = resp.json()
        if isinstance(data, list):
            return data
        for key in ("results", "content", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    except Exception:
        return []


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
    import re

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


def farm_lightning(page):
    """
    Читает тайтлы по очереди строго по порядку глав (1 -> 2 -> 3 ...) через
    публичный JSON API сайта (api.remanga.org), без открытия каждой главы в
    браузере — поэтому быстро. Прогресс по каждому тайтлу (последний
    прочитанный номер главы) сохраняется, чтобы на следующий запуск
    продолжать с того же места, а не читать заново.

    Лимит: начисление идёт за 1-ю, 6-ю, 11-ю... прочитанную сегодня главу,
    максимум 60 молний в день без подписки (96 глав) или 240 с подпиской
    (196 глав). LIGHTNING_MAX_CHAPTERS=0 — без лимита.
    """
    token = get_auth_token(page)
    if not token:
        print("[lightning] Bearer-токен не найден — фарм молний пропущен")
        return

    session = build_session(page, token)

    collection_url = os.getenv("READ_COLLECTION_URL", f"{SITE_ORIGIN}/collections/2197")
    titles = [x.strip() for x in os.getenv("READ_TITLE_URLS", "").split(",") if x.strip()]
    if not titles:
        titles = collect_collection_titles(page, collection_url)
    if not titles:
        print("[lightning] тайтлы не найдены")
        return

    max_chapters = int(os.getenv("LIGHTNING_MAX_CHAPTERS", "96"))
    state = load_state()
    if max_chapters and state["count"] >= max_chapters:
        print(f"[lightning] дневная норма уже выполнена: {state['count']}")
        return

    delay_min = float(os.getenv("LIGHTNING_DELAY_MIN", "0.5"))
    delay_max = float(os.getenv("LIGHTNING_DELAY_MAX", "1.2"))

    before_items = get_lightning_items(session)
    before_keys = {lightning_item_key(i) for i in before_items}

    progress = state["progress"]

    def limit_reached():
        return bool(max_chapters) and state["count"] >= max_chapters

    for title_url in titles:
        if limit_reached():
            break

        slug = title_url.rstrip("/").split("/manga/")[-1].split("/")[0]
        if progress.get(slug, {}).get("done"):
            continue

        try:
            info = get_title_info(session, slug)
        except Exception as exc:
            print(f"[lightning] не удалось получить инфо о тайтле {slug}: {exc}")
            continue
        if not info:
            print(f"[lightning] у тайтла {slug} нет ветки перевода — пропуск")
            continue

        try:
            chapters = get_all_chapters(session, info["branch_id"])
        except Exception as exc:
            print(f"[lightning] не удалось получить список глав {slug}: {exc}")
            continue
        if not chapters:
            continue

        last_index = progress.get(slug, {}).get("last_index", 0)
        pending = [c for c in chapters if c.get("index", 0) > last_index and not is_locked_chapter(c)]
        if not pending:
            progress[slug] = {"last_index": last_index, "done": True}
            save_state(state)
            continue

        print(f"[lightning] {slug}: новых глав {len(pending)} из {len(chapters)}")

        for chapter in pending:
            if limit_reached():
                break
            chapter_id = chapter["id"]
            ok, status, endpoint = mark_chapter_read(session, chapter_id)
            last_index = chapter.get("index", last_index)
            if not ok:
                print(f"[lightning] отметка не прошла глава={chapter_id} status={status} — пропускаю")
                progress[slug] = {"last_index": last_index, "done": False}
                save_state(state)
                continue

            state["count"] += 1
            progress[slug] = {"last_index": last_index, "done": False}
            save_state(state)
            print(
                f"[lightning] {slug} глава {chapter.get('chapter')} (id={chapter_id}) "
                f"[{state['count']}/{max_chapters or '∞'}]"
            )
            time.sleep(random.uniform(delay_min, delay_max))

        if last_index >= chapters[-1].get("index", last_index):
            progress[slug] = {"last_index": last_index, "done": True}
            save_state(state)

    after_items = get_lightning_items(session)
    new_rewards = [i for i in after_items if is_read_reward(i) and lightning_item_key(i) not in before_keys]
    print(
        f"[lightning] завершено: {state['count']} глав сегодня, "
        f"новых начислений за чтение: {len(new_rewards)}"
    )
