import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
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
            cookie = {
                "domain": domain,
                "path": path,
                "name": name,
                "value": value,
            }
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


def save_cookies_json(context, file_path):
    cookies = context.cookies()
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    print(f"💾 Cookies сохранены: {file_path}")


def parse_proxy(proxy_url):
    if not proxy_url:
        return None
    proxy_url = proxy_url.strip()
    if not proxy_url:
        return None
    if "://" not in proxy_url:
        proxy_url = "http://" + proxy_url
    from urllib.parse import urlparse
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
        login_texts = ["Войти", "Авторизация", "Login", "Sign in"]
        body_text = page.locator("body").inner_text(timeout=5000)
        if any(text in body_text for text in login_texts) and "寺" not in body_text:
            return False
        return "寺" in body_text
    except Exception:
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
            response = page.goto("https://remanga.org/murim-cards#/map", wait_until="domcontentloaded", timeout=60000)
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

                kanji_element = page.locator("span.font-kanji", has_text="寺").first
                try:
                    kanji_element.wait_for(state="visible", timeout=20000)
                except Exception:
                    print(f"🌐 Current URL: {page.url}")
                    print(f"📄 Title: {page.title()}")
                    safe_screenshot(page, f"cycle_{run_count}_before_kanji.png", f"🔎 Не найдено 寺 — цикл №{run_count}")
                    raise
                kanji_element.click()
                print("✅ Кликнул по иероглифу 寺!")
                safe_screenshot(page, f"cycle_{run_count}_dungeon.png", f"🏯 Катакомбы — цикл №{run_count}")
                human_sleep(2, 3)

                pass_button = page.locator("text=ПРОЙТИ СНОВА")
                pass_button.wait_for(state="visible", timeout=5000)
                is_disabled = pass_button.evaluate("node => node.disabled || node.getAttribute('aria-disabled') === 'true'")
                if is_disabled:
                    print("🛑 Энергия закончилась.")
                    break
                pass_button.click()
                human_sleep(2, 3)

                battle_text = page.get_by_text("戰", exact=True).last
                battle_text.wait_for(state="visible", timeout=5000)
                battle_btn = battle_text.locator("xpath=ancestor::button[1]")
                if battle_btn.count() > 0:
                    battle_btn.wait_for(state="visible", timeout=5000)
                    try:
                        battle_btn.click(timeout=8000)
                    except Exception:
                        battle_btn.click(timeout=8000, force=True)
                else:
                    try:
                        battle_text.click(timeout=8000)
                    except Exception:
                        battle_text.click(timeout=8000, force=True)
                print("⚔️ Нажата кнопка боя (戰)!")
                print("⏳ Жду завершения боя...")

                # The previous fixed 12–16 second sleep was not enough for some
                # battles. Wait for the actual result control instead.
                result_button = page.get_by_text("К результатам", exact=True).last
                try:
                    result_button.wait_for(state="visible", timeout=90000)
                except Exception:
                    print("⚠️ 'К результатам' не появился за 90 секунд.")
                    print(f"🌐 Current URL: {page.url}")
                    print(f"📄 Title: {page.title()}")
                    safe_screenshot(page, f"cycle_{run_count}_waiting_result.png", f"🔎 Ожидание результата — цикл №{run_count}")
                    raise

                try:
                    result_button.click(timeout=10000)
                except Exception:
                    result_button.click(timeout=10000, force=True)
                print("✅ Нажато 'К результатам'.")
                human_sleep(2, 3)
                safe_screenshot(page, f"cycle_{run_count}_result.png", f"🏆 Результат — цикл №{run_count}")

                return_button = page.locator('button[data-sentry-source-file="pve-result-overlay.tsx"]').last
                return_button.wait_for(state="visible", timeout=10000)
                try:
                    return_button.click(timeout=10000)
                except Exception:
                    return_button.click(timeout=10000, force=True)
                print("✅ Возвращаемся на карту.")
                human_sleep(4, 6)

        except Exception as exc:
            print(f"❌ Ошибка Actor-задачи: {exc}")
            safe_screenshot(page, "error_screenshot.png", "❌ Ошибка Actor-задачи")
        finally:
            browser.close()
            print("🏁 Браузер закрыт.")


if __name__ == "__main__":
    run_dungeon_bot()
