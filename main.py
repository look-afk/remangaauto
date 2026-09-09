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


def human_sleep(a=1, b=2):
    time.sleep(random.uniform(a, b))


def get_file_path(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


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
    """Send a screenshot to Telegram without exposing credentials in logs."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram не настроен: нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(path, "rb") as photo:
            response = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption or "",
                },
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
        page.screenshot(path=path, full_page=True)
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


def find_clickable_battle_button(page):
    """Find a visible and enabled button whose accessible name/text is 戰."""
    # Prefer the semantic button locator. This avoids selecting a nested text node
    # and then climbing to a stale/non-clickable ancestor.
    try:
        buttons = page.get_by_role("button", name="戰", exact=True)
        for index in range(buttons.count() - 1, -1, -1):
            button = buttons.nth(index)
            try:
                if button.is_visible() and button.is_enabled():
                    return button
            except Exception:
                continue
    except Exception:
        pass

    # Fallback for buttons whose accessible name is not exposed exactly as 戰.
    try:
        buttons = page.locator("button").filter(has_text="戰")
        for index in range(buttons.count() - 1, -1, -1):
            button = buttons.nth(index)
            try:
                if not button.is_visible() or not button.is_enabled():
                    continue
                text = button.inner_text(timeout=1000).strip()
                if text == "戰":
                    return button
            except Exception:
                continue
    except Exception:
        pass

    return None


def click_battle(page):
    """Click only a visible/enabled battle button identified by 戰."""
    deadline = time.time() + 30
    last_error = None

    while time.time() < deadline:
        battle_button = find_clickable_battle_button(page)
        if battle_button is not None:
            try:
                battle_button.scroll_into_view_if_needed(timeout=2000)
                battle_button.click(timeout=5000)
                return
            except Exception as exc:
                last_error = exc

        page.wait_for_timeout(500)

    if last_error:
        raise RuntimeError(f"Не удалось нажать кнопку 戰: {last_error}") from last_error
    raise RuntimeError("Не удалось найти активную кнопку 戰 за 30 секунд")


def wait_for_battle_again(page, run_count, attempt):
    """Wait until an enabled 戰 button is available; absence means mana is insufficient."""
    print(f"⏳ Жду следующую кнопку 戰 (попытка {attempt})...")
    deadline = time.time() + 30

    while time.time() < deadline:
        if find_clickable_battle_button(page) is not None:
            return True
        page.wait_for_timeout(500)

    print("ℹ️ 戰 больше не появился — вероятно, маны больше не хватает.")
    safe_screenshot(
        page,
        f"cycle_{run_count}_attempt_{attempt}_no_mana.png",
        "🔎 Следующий 戰 недоступен",
    )
    return False


def run_dungeon_bot(proxy_url=None):
    print("[2026-09-08] Запуск задачи фарма катакомб...")

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
            print("🔗 Переход на https://remanga.org/murim-cards#/map...")
            response = page.goto(
                "https://remanga.org/murim-cards#/map",
                wait_until="domcontentloaded",
                timeout=60000,
            )
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

            run_count = 0
            max_runs = int(os.getenv("MAX_RUNS", "0"))

            while True:
                run_count += 1
                print()
                print(f"--- Запуск цикла прохода №{run_count} ---")
                if max_runs > 0 and run_count > max_runs:
                    print(f"🛑 Достигнут MAX_RUNS={max_runs}.")
                    break

                # 1. Find and click only 寺 on the map.
                kanji_element = page.locator("span.font-kanji", has_text="寺").first
                kanji_element.wait_for(state="visible", timeout=20000)
                kanji_element.click()
                print("✅ Кликнул по иероглифу 寺!")
                safe_screenshot(page, f"cycle_{run_count}_dungeon.png", f"🏯 Катакомбы — цикл №{run_count}")
                human_sleep(2, 3)

                # 2. После 寺 сразу ищем и нажимаем 戰.
                # Никакого поиска или нажатия ПРОЙТИ СНОВА здесь нет.
                attempt = 0
                while True:
                    attempt += 1

                    if not wait_for_battle_again(page, run_count, attempt):
                        print(f"🛑 Маны недостаточно. Завершено боёв: {attempt - 1}.")
                        break

                    click_battle(page)
                    print(f"⚔️ Нажата кнопка 戰 — бой №{attempt}!")

                    # Ждём переход результата боя перед следующим поиском 戰.
                    page.wait_for_timeout(5000)

                # The current screen is left untouched when 戰 disappears.
                # Try to return to the map only if the result overlay exposes the map button.
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
                    if "murim-cards" in page.url:
                        print("ℹ️ Интерфейс уже вернулся на карту.")
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
