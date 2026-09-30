import json
import os
import random
import re
import time

import requests


LIGHTNING_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lightning_state.json",
)

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
        # новый день: дневной счётчик сбрасывается,
        # прогресс по тайтлам остаётся, чтобы продолжать
        # читать главы по порядку, а не с начала.
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


def public_session():
    """requests.Session для публичных, не требующих входа эндпоинтов."""
    session = requests.Session()
    proxy = os.getenv("CUSTOM_PROXY") or os.getenv("PROXY_URL")
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Referer": SITE_ORIGIN + "/",
            "Origin": SITE_ORIGIN,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
        }
    )
    return session


def browser_fetch(page, method, url, token=None, json_body=None):
    """
    Выполняет fetch() ВНУТРИ страницы браузера, а не с сервера.

    Сайт защищён анти-бот системой (похоже на DataDome — характерные
    cookies __ddg1_/__ddg8_/__ddg9_/__ddg10_). Прямой запрос с сервера
    (requests.Session), даже со скопированными cookies и токеном,
    получает 401 — анти-бот отличает настоящий браузер от HTTP-клиента.
    fetch() из page.evaluate идёт от имени уже прошедшей проверку
    вкладки, с её же TLS/JS-отпечатком и cookies.
    """
    try:
        result = page.evaluate(
            """async ({method, url, token, jsonBody}) => {
                const headers = {'Accept': 'application/json, text/plain, */*'};
                if (token) headers['Authorization'] = token;
                let body;
                if (jsonBody !== null && jsonBody !== undefined) {
                    headers['Content-Type'] = 'application/json';
                    body = JSON.stringify(jsonBody);
                }
                try {
                    const res = await fetch(url, {method, headers, body, credentials: 'include'});
                    const text = await res.text();
                    return {status: res.status, text: text};
                } catch (e) {
                    return {status: 0, text: String(e)};
                }
            }""",
            {"method": method, "url": url, "token": token, "jsonBody": json_body},
        )
        return result
    except Exception as exc:
        return {"status": 0, "text": str(exc)}


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
    """Собирает ссылки на тайтлы из коллекции."""
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
# Список глав тайтла — публичные эндпоинты, подтверждены рабочими напрямую
# ---------------------------------------------------------------------------


def get_title_info(session, slug):
    resp = session.get(f"{API_ORIGIN}/api/titles/{slug}/", timeout=20)
    resp.raise_for_status()
    content = resp.json().get("content", {})
    branches = content.get("branches") or []
    if not branches:
        return None
    return {"branch_id": branches[0]["id"], "count_chapters": content.get("count_chapters", 0)}


def get_all_chapters(session, branch_id):
    """Все главы ветки перевода, отсортированные по возрастанию."""
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
    """Платные/ещё не выкупленные главы."""
    if chapter.get("is_bought"):
        return False
    if chapter.get("is_paid"):
        return True
    return bool(chapter.get("price"))


# ---------------------------------------------------------------------------
# Отметка главы прочитанной и проверка баланса — идут ЧЕРЕЗ БРАУЗЕР
# ---------------------------------------------------------------------------


def mark_chapter_read(page, token, chapter_id, log_raw=False):
    payload_variants = [
        ("POST", f"{API_ORIGIN}/api/activity/views/", {"chapter": chapter_id}),
        ("POST", f"{API_ORIGIN}/api/v2/activity/views/", {"chapter_id": chapter_id}),
        ("POST", f"{API_ORIGIN}/api/titles/chapters/{chapter_id}/view/", None),
    ]

    last_status = None
    last_text = None
    for method, url, payload in payload_variants:
        result = browser_fetch(page, method, url, token=token, json_body=payload)
        status = result.get("status")
        text = result.get("text", "")
        last_status, last_text = status, text

        if log_raw:
            snippet = (text or "")[:300]
            print(f"[lightning] диагностика {method} {url} -> {status}: {snippet}")

        if status and 200 <= status < 300:
            return True, status, url, text
        if status not in (404, 405, 0):
            # похоже на реальный эндпоинт (401/403/400 и т.п.) — дальше не гадаем
            return False, status, url, text

    return False, last_status, None, last_text


def get_lightning_items(page, token):
    result = browser_fetch(page, "GET", f"{API_ORIGIN}/api/v2/billing/lightning-payments/", token=token)
    if not (result.get("status") and 200 <= result["status"] < 300):
        return []
    try:
        data = json.loads(result.get("text") or "null")
    except Exception:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "content", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def lightning_item_key(item):
    return str(
        item.get("uuid")
        or item.get("id")
        or item.get("transaction")
        or (str(item.get("type")) + "_" + str(item.get("created_at")) if item.get("created_at") else "")
    )


def is_read_reward(item):
    raw_type = item.get("type")
    if isinstance(raw_type, dict):
        raw_type = raw_type.get("id") or raw_type.get("value") or raw_type.get("type") or raw_type.get("code")
    text = " ".join(
        str(item.get(key) or "") for key in ("description", "comment", "name", "text", "title", "label")
    )
    return str(raw_type) == "27" or bool(re.search(r"чтен|глав|read", text, re.I))


def farm_lightning(page):
    """
    Читает тайтлы по очереди строго по порядку глав, отмечая их через
    браузерный fetch(), максимально быстро (без прокрутки страниц глав).

    Прогресс по каждому тайтлу сохраняется в lightning_state.json,
    чтобы следующий запуск продолжал с того же места.
    """
    token = get_auth_token(page)
    if not token:
        print("[lightning] Bearer-токен не найден — фарм молний пропущен")
        return

    session = public_session()

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

    before_items = get_lightning_items(page, token)
    before_keys = {lightning_item_key(i) for i in before_items}
    print(f"[lightning] начислений за чтение до старта: {len(before_items)}")

    progress = state["progress"]
    diagnosed = False  # печатаем подробный ответ сайта только на первой главе

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
            ok, status, endpoint, text = mark_chapter_read(
                page, token, chapter_id, log_raw=not diagnosed
            )
            diagnosed = True
            last_index = chapter.get("index", last_index)

            if not ok:
                print(
                    f"[lightning] отметка не прошла глава={chapter_id} "
                    f"status={status} эндпоинт={endpoint} — пропускаю. "
                    f"Ответ сайта: {(text or '')[:200]}"
                )
                progress[slug] = {"last_index": last_index, "done": False}
                save_state(state)
                continue

            state["count"] += 1
            progress[slug] = {"last_index": last_index, "done": False}
            save_state(state)

            print(
                f"[lightning] {slug} глава {chapter.get('chapter')} "
                f"(id={chapter_id}) [{state['count']}/{max_chapters or '∞'}] "
                f"эндпоинт={endpoint}"
            )

            time.sleep(random.uniform(delay_min, delay_max))

        if last_index >= chapters[-1].get("index", last_index):
            progress[slug] = {"last_index": last_index, "done": True}
            save_state(state)

    after_items = get_lightning_items(page, token)
    new_rewards = [i for i in after_items if is_read_reward(i) and lightning_item_key(i) not in before_keys]

    print(
        f"[lightning] завершено: {state['count']} глав сегодня, "
        f"новых начислений за чтение: {len(new_rewards)}"
    )
    if state["count"] > 0 and not new_rewards:
        print(
            "[lightning] ⚠️ главы отмечены, но начислений за чтение не видно — "
            "либо баланс проверяется не тем эндпоинтом, либо отметка на самом "
            "деле не засчитывается сайтом. Смотри 'диагностика' в логах выше."
        )
