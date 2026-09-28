import json
import os
import random
import time
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

MAP_URL = "https://remanga.org/murim-cards#/map"
COLLECTION_URL = os.getenv("READ_COLLECTION_URL", "https://remanga.org/collections/2197")


def human_sleep(a=1, b=2):
    time.sleep(random.uniform(a, b))


def get_file_path(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


LIGHTNING_STATE_FILE = get_file_path("lightning_state.json")


def parse_netscape_cookies(file_path):
    cookies = []
    with open(file_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 7:
                continue
            domain, flag, path, secure, expires, name, value = parts
            try:
                expires_value = int(expires)
            except ValueError:
                expires_value = 0
            cookie = {"domain": domain, "path": path, "name": name, "value": value}
            if expires_value > 0:
                cookie["expires"] = expires_value
            if secure.upper() == "TRUE":
                cookie["secure"] = True
            cookies.append(cookie)
    return cookies


def load_cookies(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "cookies" in data:
            data = data["cookies"]
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    return parse_netscape_cookies(file_path)


def parse_proxy(proxy_url):
    if not proxy_url:
        return None
    proxy_url = proxy_url.strip()
    if not proxy_url:
        return None
    if "://" not in proxy_url:
        proxy_url = "http://" + proxy_url
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        raise ValueError("Неверный формат прокси")
    result = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        result["username"] = parsed.username
    if parsed.password:
        result["password"] = parsed.password
    return result


def send_telegram_photo(path, caption=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram не настроен: нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(path, "rb") as photo:
            response = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption or ""},
                files={"photo": photo},
                timeout=30,
            )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            print(f"⚠️ Telegram API вернул ошибку: {result.get('description', 'unknown error')}")
            return False
        print("📨 Скриншот отправлен в Telegram.")
        return True
    except Exception as exc:
        print(f"⚠️ Не удалось отправить скриншот в Telegram: {exc}")
        return False


def safe_screenshot(page, filename, message=None):
    try:
        path = get_file_path(filename)
        page.screenshot(path=path, full_page=False)
        print(f"📸 Скриншот сохранён: {path}")
        if message:
            print(message)
        send_telegram_photo(path, message)
    except Exception as exc:
        print(f"⚠️ Не удалось сохранить скриншот: {exc}")


def is_authenticated(page):
    try:
        page.wait_for_timeout(1000)
        body_text = page.locator("body").inner_text(timeout=5000)
        login_texts = ["Войти", "Авторизация", "Login", "Sign in"]
        if any(text in body_text for text in login_texts) and "寺" not in body_text:
            return False
        return "寺" in body_text
    except Exception:
        return False


def close_open_dialog(page):
    try:
        dialogs = page.locator('[role="dialog"][data-state="open"]')
        if dialogs.count() > 0:
            print("🔒 Обнаружен открытый Dialog — пытаюсь закрыть его.")
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
            if page.locator('[role="dialog"][data-state="open"]').count() > 0:
                print("⚠️ Dialog всё ещё открыт, продолжаю повторный поиск кнопки 寺.")
                return False
            print("✅ Dialog закрыт.")
        return True
    except Exception as exc:
        print(f"⚠️ Не удалось проверить/закрыть Dialog: {exc}")
        return False


# --- МОЛНИИ: ежедневный вход + чтение глав ---

def load_lightning_state():
    today = time.strftime("%Y-%m-%d")
    try:
        with open(LIGHTNING_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}
    if state.get("date") != today:
        # новый день: счётчик сбрасываем, список прочитанных глав оставляем
        state = {"date": today, "count": 0, "read": state.get("read", [])}
    return state


def save_lightning_state(state):
    state["read"] = state.get("read", [])[-2000:]
    try:
        with open(LIGHTNING_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as exc:
        print(f"⚠️ Не удалось сохранить состояние молний: {exc}")


def collect_collection_titles(page, collection_url):
    """Собирает ссылки на все тайтлы из коллекции."""
    print(f"🗂 Открываю коллекцию: {collection_url}")
    page.goto(collection_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)

    titles = []
    stale_rounds = 0
    for _ in range(40):  # прокрутка для подгрузки списка
        hrefs = page.eval_on_selector_all(
            'a[href*="/manga/"]', "els => els.map(e => e.href)"
        )
        for h in hrefs:
            parts = h.split("/manga/", 1)
            if len(parts) != 2:
                continue
            slug = parts[1].split("/")[0].split("?")[0].split("#")[0]
            if slug:
                url = f"https://remanga.org/manga/{slug}"
                if url not in titles:
                    titles.append(url)
                    stale_rounds = -1
        stale_rounds += 1
        if stale_rounds >= 3:
            break
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(1200)

    print(f"🗂 Найдено тайтлов: {len(titles)}")
    return titles


def collect_chapter_urls(page, title_url):
    """Открывает страницу тайтла и собирает ссылки на главы."""
    print(f"📚 Собираю главы: {title_url}")
    page.goto(title_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    for _ in range(6):
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(700)
    hrefs = page.eval_on_selector_all(
        'a[href*="/chapter/"]', "els => els.map(e => e.href)"
    )
    urls = list(dict.fromkeys(hrefs))
    print(f"📚 Найдено глав: {len(urls)}")
    return urls


def read_chapter(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(int(random.uniform(3000, 5000)))
    # плавно прокручиваем главу до конца, как читатель
    last_y = -1
    for _ in range(60):
        page.mouse.wheel(0, random.randint(900, 1400))
        page.wait_for_timeout(int(random.uniform(300, 700)))
        y = page.evaluate("window.scrollY")
        if y == last_y:
            break
        last_y = y
    page.wait_for_timeout(int(random.uniform(2000, 3500)))


def farm_lightning(page):
    """
    Ежедневный вход засчитывается самим заходом на сайт (уже сделан выше).
    Здесь читаем главы до дневного лимита.
    Начисление идёт за 1-ю, 6-ю, 11-ю... главу дня, поэтому лимит
    считаем по числу глав: 96 глав = 20 выплат = 60 молний (без подписки),
    196 глав = 40 выплат = 240 молний (с подпиской).
    """
    titles = [t.strip() for t in os.getenv("READ_TITLE_URLS", "").split(",") if t.strip()]
    if not titles:
        try:
            titles = collect_collection_titles(page, COLLECTION_URL)
        except Exception as exc:
            print(f"⚠️ Не удалось собрать коллекцию: {exc}")
    if not titles:
        print("ℹ️ Тайтлы не найдены — чтение глав пропущено.")
        return
    random.shuffle(titles)  # чтобы не читать всегда одно и то же

    max_chapters = int(os.getenv("LIGHTNING_MAX_CHAPTERS", "96"))
    state = load_lightning_state()
    if state["count"] >= max_chapters:
        print(f"✅ Норма глав на сегодня уже выполнена ({state['count']}).")
        return

    read_set = set(state["read"])
    for title_url in titles:
        if state["count"] >= max_chapters:
            break
        try:
            chapters = [u for u in collect_chapter_urls(page, title_url) if u not in read_set]
        except Exception as exc:
            print(f"⚠️ Не удалось получить главы {title_url}: {exc}")
            continue

        for url in chapters:
            if state["count"] >= max_chapters:
                break
            try:
                read_chapter(page, url)
            except Exception as exc:
                print(f"⚠️ Ошибка чтения {url}: {exc}")
                continue
            state["count"] += 1
            state["read"].append(url)
            read_set.add(url)
            save_lightning_state(state)
            print(f"📖 Глава {state['count']}/{max_chapters}: {url}")
            if state["count"] == 1:
                safe_screenshot(page, "first_chapter.png", "📖 Первая глава дня прочитана")
            human_sleep(1, 3)

    print(f"⚡ Чтение завершено: {state['count']} глав за сегодня.")
    safe_screenshot(page, "lightning_done.png", f"⚡ Прочитано глав: {state['count']}")


# --- ПРЯМЫЕ И ПРОСТЫЕ КЛИКИ (СЕРЕБРО) ---

def click_temple(page, run_count):
    """
    Ищет 寺 до 30 секунд. Кликает через чистый JavaScript, игнорируя всё.
    """
    deadline = time.time() + 30
    attempt = 0
    last_error = None

    while time.time() < deadline:
        attempt += 1
        close_open_dialog(page)

        try:
            kanji = page.locator('span.font-kanji', has_text='寺').first

            if kanji.is_visible(timeout=2000):
                print(f"🔎 寺 найден — пытаюсь нажать (попытка {attempt})...")
                kanji.evaluate("node => node.click()")
                print("✅ Кликнул по кнопке с иероглифом 寺!")
                return True
            else:
                print(f"🔎 寺 пока не виден — жду (попытка {attempt})...")

        except Exception as exc:
            last_error = exc
            print(f"⚠️ Ошибка при клике на 寺 (попытка {attempt}): {exc}")

        page.wait_for_timeout(1000)

    if last_error:
        raise RuntimeError(f"Не удалось нажать кнопку с 寺 за 30 секунд: {last_error}") from last_error
    raise RuntimeError("Иероглиф 寺 не найден за 30 секунд")


def click_battle(page):
    """
    Нажимает на кнопку 戰 с использованием чистого JavaScript (node.click).
    """
    last_error = None
    for _ in range(3):
        try:
            battle_btn = page.locator('text=戰').locator("visible=true").first
            battle_btn.evaluate("node => node.click()")
            return
        except Exception as exc:
            last_error = exc
            page.wait_for_timeout(1000)

    raise RuntimeError(f"Не удалось нажать кнопку 戰: {last_error}")


def wait_for_battle_again(page, run_count, attempt):
    """
    Ожидает появления кнопки боя (戰)
    """
    print(f"⏳ Жду следующую кнопку 戰 (попытка {attempt})...")
    try:
        battle_btn = page.locator('text=戰').first
        battle_btn.wait_for(state="visible", timeout=30000)
        return True
    except Exception:
        print("ℹ️ 戰 больше не появился — вероятно, маны больше не хватает.")
        safe_screenshot(page, f"cycle_{run_count}_attempt_{attempt}_no_mana.png", "🔎 Следующий 戰 недоступен")
        return False


def run_dungeon_bot(proxy_url=None):
    print("[2026-09-28] Запуск задачи фарма катакомб и молний...")
    cookie_json_path = os.getenv("REMANGA_COOKIES_JSON") or get_file_path("cookies.json")
    cookie_file_path = os.getenv("REMANGA_COOKIES_FILE") or get_file_path("cookies.txt")
    cookies = []

    if os.path.exists(cookie_json_path):
        try:
            cookies = load_cookies(cookie_json_path)
            print(f"🍪 Загружено cookies: {len(cookies)} ({cookie_json_path})")
        except Exception as exc:
            print(f"⚠️ Не удалось загрузить JSON cookies: {exc}")

    if not cookies and os.path.exists(cookie_file_path):
        try:
            cookies = load_cookies(cookie_file_path)
            print(f"🍪 Загружено cookies: {len(cookies)} ({cookie_file_path})")
        except Exception as exc:
            print(f"⚠️ Не удалось загрузить cookies: {exc}")

    with sync_playwright() as p:
        browser_args = ["--disable-blink-features=AutomationControlled"]
        proxy = parse_proxy(proxy_url or os.getenv("CUSTOM_PROXY") or os.getenv("PROXY_URL"))
        if proxy:
            print("🌐 Playwright запускается через настроенный прокси.")
        launch_kwargs = {"headless": True, "args": browser_args}
        if proxy:
            launch_kwargs["proxy"] = proxy
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        if cookies:
            try:
                context.add_cookies(cookies)
            except Exception as exc:
                print(f"⚠️ Не удалось применить cookies: {exc}")
        page = context.new_page()
        Stealth().apply_stealth_sync(page)

        try:
            print(f"🔗 Переход на {MAP_URL}...")
            response = page.goto(MAP_URL, wait_until="domcontentloaded", timeout=60000)
            print(f"🌐 HTTP status: {response.status if response else 'unknown'}")
            print("⏳ Ожидаю интерфейс...")
            try:
                page.wait_for_selector("span.font-kanji", timeout=30000)
            except Exception:
                page.wait_for_timeout(5000)
            safe_screenshot(page, "page_loaded.png")

            if is_authenticated(page):
                print("🍪✅ Cookies рабочие — вход через логин/пароль не требуется.")
            else:
                print("⚠️ Не удалось подтвердить авторизацию по cookies.")

            # ⚡ молнии: ежедневный вход уже засчитан, читаем главы
            if os.getenv("FARM_LIGHTNING", "1") == "1":
                try:
                    farm_lightning(page)
                except Exception as exc:
                    print(f"⚠️ Ошибка фарма молний: {exc}")
                # возвращаемся на карту катакомб для серебра
                page.goto(MAP_URL, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_selector("span.font-kanji", timeout=30000)
                except Exception:
                    page.wait_for_timeout(5000)

            run_count = 0
            max_runs = int(os.getenv("MAX_RUNS", "0"))

            while True:
                run_count += 1
                print()
                print(f"--- Запуск цикла прохода №{run_count} ---")
                if max_runs > 0 and run_count > max_runs:
                    print(f"🛑 Достигнут MAX_RUNS={max_runs}.")
                    break

                click_temple(page, run_count)
                safe_screenshot(page, f"cycle_{run_count}_dungeon.png", f"🏯 Катакомбы — цикл №{run_count}")
                human_sleep(2, 3)

                attempt = 0
                while True:
                    attempt += 1
                    if not wait_for_battle_again(page, run_count, attempt):
                        print(f"🛑 Маны недостаточно. Завершено боёв: {attempt - 1}.")
                        break
                    click_battle(page)
                    print(f"⚔️ Нажата кнопка 戰 — бой №{attempt}!")
                    page.wait_for_timeout(5000)

                return_button = page.locator('button[data-sentry-source-file="pve-result-overlay.tsx"]').last
                try:
                    return_button.wait_for(state="visible", timeout=5000)
                    try:
                        return_button.click(timeout=10000)
                    except Exception:
                        return_button.click(timeout=10000, force=True)
                    print("✅ Возвращаемся на карту.")
                    human_sleep(4, 6)
                except Exception:
                    close_open_dialog(page)
                    if page.locator("span.font-kanji").count() > 0:
                        print("ℹ️ Элементы карты обнаружены, следующий цикл снова будет искать 寺.")
                    else:
                        print("ℹ️ Кнопка возврата на карту сейчас недоступна.")

        except Exception as exc:
            print(f"❌ Ошибка Actor-задачи: {exc}")
            safe_screenshot(page, "error_screenshot.png", "❌ Ошибка Actor-задачи")
        finally:
            browser.close()
            print("🏁 Браузер закрыт.")


if __name__ == "__main__":
    run_dungeon_bot()
