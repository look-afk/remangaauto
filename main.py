import json
import os
import random
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth


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


def safe_screenshot(page, filename, message=None):
    try:
        path = get_file_path(filename)
        page.screenshot(path=path, full_page=True)
        print(f"📸 Скриншот сохранён: {path}")
        if message:
            print(message)
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


def click_text(page, text, timeout=10000, exact=True, force_fallback=True):
    locator = page.get_by_text(text, exact=exact).last
    locator.wait_for(state="visible", timeout=timeout)
    try:
        locator.click(timeout=timeout)
    except Exception:
        if not force_fallback:
            raise
        locator.click(timeout=timeout, force=True)
    return locator


def click_battle(page):
    battle_text = page.get_by_text("戰", exact=True).last
    battle_text.wait_for(state="visible", timeout=10000)

    # Prefer the real button containing the kanji rather than the inner div.
    battle_btn = battle_text.locator("xpath=ancestor::button[1]")
    if battle_btn.count() > 0:
        try:
            battle_btn.click(timeout=10000)
        except Exception:
            battle_btn.click(timeout=10000, force=True)
    else:
        try:
            battle_text.click(timeout=10000)
        except Exception:
            battle_text.click(timeout=10000, force=True)


def wait_and_click_result(page, run_count, attempt):
    print(f"⏳ Жду завершения боя (попытка {attempt})...")
    result_button = page.get_by_text("К результатам", exact=True).last
    try:
        result_button.wait_for(state="visible", timeout=90000)
    except Exception:
        print("⚠️ 'К результатам' не появился за 90 секунд.")
        print(f"🌐 Current URL: {page.url}")
        print(f"📄 Title: {page.title()}")
        safe_screenshot(
            page,
            f"cycle_{run_count}_attempt_{attempt}_waiting_result.png",
            f"🔎 Ожидание результата — цикл №{run_count}, попытка №{attempt}",
        )
        raise

    try:
        result_button.click(timeout=10000)
    except Exception:
        result_button.click(timeout=10000, force=True)
    print("✅ Нажато 'К результатам'.")
    human_sleep(2, 3)


def find_retry_button(page):
    # The game displays the next-run control as something like:
    # 'ЕЩЁ РАЗ · 氣8'. Match by the stable Russian part and ignore the energy count.
    candidates = [
        page.get_by_text("ЕЩЁ РАЗ", exact=False).last,
        page.locator("button").filter(has_text="ЕЩЁ РАЗ").last,
    ]
    for candidate in candidates:
        try:
            candidate.wait_for(state="visible", timeout=3000)
            return candidate
        except Exception:
            continue
    return None


def click_retry(page, run_count, attempt):
    retry_button = find_retry_button(page)
    if retry_button is None:
        return False

    try:
        retry_button.click(timeout=10000)
    except Exception:
        retry_button.click(timeout=10000, force=True)

    print(f"🔁 Нажато 'ЕЩЁ РАЗ' — следующая попытка №{attempt}.")
    human_sleep(2, 3)
    return True


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
            max_attempts = int(os.getenv("MAX_ATTEMPTS", "8"))

            while True:
                run_count += 1
                print()
                print(f"--- Запуск цикла прохода №{run_count} ---")
                if max_runs > 0 and run_count > max_runs:
                    print(f"🛑 Достигнут MAX_RUNS={max_runs}.")
                    break

                kanji_element = page.locator("span.font-kanji", has_text="寺").first
                try:
                    kanji_element.wait_for(state="visible", timeout=20000)
                except Exception:
                    print(f"🌐 Current URL: {page.url}")
                    print(f"📄 Title: {page.title()}")
                    safe_screenshot(
                        page,
                        f"cycle_{run_count}_before_kanji.png",
                        f"🔎 Не найдено 寺 — цикл №{run_count}",
                    )
                    raise

                kanji_element.click()
                print("✅ Кликнул по иероглифу 寺!")
                safe_screenshot(page, f"cycle_{run_count}_dungeon.png", f"🏯 Катакомбы — цикл №{run_count}")
                human_sleep(2, 3)

                pass_button = page.get_by_text("ПРОЙТИ СНОВА", exact=True).last
                pass_button.wait_for(state="visible", timeout=10000)
                try:
                    is_disabled = pass_button.evaluate(
                        "node => node.disabled || node.getAttribute('aria-disabled') === 'true'"
                    )
                except Exception:
                    is_disabled = False
                if is_disabled:
                    print("🛑 Энергия закончилась.")
                    break

                try:
                    pass_button.click(timeout=10000)
                except Exception:
                    pass_button.click(timeout=10000, force=True)
                print("▶️ Нажато 'ПРОЙТИ СНОВА'.")
                human_sleep(2, 3)

                # First battle.
                click_battle(page)
                print("⚔️ Нажата кнопка боя (戰)!")
                wait_and_click_result(page, run_count, 1)
                safe_screenshot(page, f"cycle_{run_count}_attempt_1_result.png", f"🏆 Результат — попытка №1")

                # After 'К результатам', the game offers 'ЕЩЁ РАЗ · 氣8'.
                # Keep alternating: К результатам -> ЕЩЁ РАЗ -> battle -> К результатам...
                for attempt in range(2, max_attempts + 1):
                    if not click_retry(page, run_count, attempt):
                        print("ℹ️ Кнопка 'ЕЩЁ РАЗ' больше не доступна — энергия закончилась или этап завершён.")
                        break

                    click_battle(page)
                    print("⚔️ Нажата кнопка боя (戰)!")
                    wait_and_click_result(page, run_count, attempt)
                    safe_screenshot(
                        page,
                        f"cycle_{run_count}_attempt_{attempt}_result.png",
                        f"🏆 Результат — попытка №{attempt}",
                    )

                # Return to the map after all available attempts in this dungeon.
                return_button = page.locator('button[data-sentry-source-file="pve-result-overlay.tsx"]').last
                try:
                    return_button.wait_for(state="visible", timeout=10000)
                    try:
                        return_button.click(timeout=10000)
                    except Exception:
                        return_button.click(timeout=10000, force=True)
                    print("✅ Возвращаемся на карту.")
                except Exception:
                    # Some versions of the UI return to the map automatically.
                    if "murim-cards" in page.url:
                        print("ℹ️ Интерфейс уже вернулся на карту.")
                    else:
                        print("⚠️ Не удалось найти кнопку возврата на карту.")
                        safe_screenshot(page, f"cycle_{run_count}_before_return.png")
                        raise

                human_sleep(4, 6)

        except Exception as exc:
            print(f"❌ Ошибка Actor-задачи: {exc}")
            safe_screenshot(page, "error_screenshot.png", "❌ Ошибка Actor-задачи")
        finally:
            browser.close()
            print("🏁 Браузер закрыт.")


if __name__ == "__main__":
    run_dungeon_bot()
