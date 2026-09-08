import os
import random
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

TARGET_URL = "https://remanga.org/murim-cards#/map"


def send_telegram_message(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message},
            timeout=10,
        )
    except Exception:
        pass


def human_sleep(min_sec=2, max_sec=4):
    time.sleep(random.uniform(min_sec, max_sec))


def get_file_path(filename: str) -> str:
    return str(Path(__file__).resolve().parent / filename)


def parse_proxy_url(proxy_url: str):
    """Convert http://user:pass@host:port to Playwright proxy settings."""
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        raise ValueError("Invalid proxy URL")

    proxy = {
        "server": f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port}"
    }
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


def parse_netscape_cookies(file_path: str):
    cookies = []
    if not os.path.exists(file_path):
        return cookies

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue

            domain, _flag, path, secure, expiration, name, value = parts[:7]
            target_domains = [domain]

            if "xn--" in domain or "реманга" in domain:
                target_domains.extend([
                    ".remanga.org",
                    "remanga.org",
                    ".xn--80aaig9ahr.xn--c1avg",
                    "xn--80aaig9ahr.xn--c1avg",
                ])
            elif "remanga.org" in domain:
                target_domains.extend([
                    ".xn--80aaig9ahr.xn--c1avg",
                    "xn--80aaig9ahr.xn--c1avg",
                    ".remanga.org",
                    "remanga.org",
                ])

            for d in set(target_domains):
                cookie = {
                    "name": name,
                    "value": value,
                    "domain": d,
                    "path": path,
                    "secure": secure.upper() == "TRUE",
                    "httpOnly": False,
                }
                if expiration.isdigit() and int(expiration) > 0:
                    cookie["expires"] = int(expiration)
                cookies.append(cookie)

    return cookies


def safe_screenshot(page, filename="error_screenshot.png"):
    try:
        page.screenshot(
            path=get_file_path(filename),
            timeout=5000,
            animations="disabled",
        )
    except Exception:
        pass


def setup_browser(p, proxy_url=None):
    """Create a Playwright browser suitable for an Apify Actor."""
    browser_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-first-run",
        "--disable-blink-features=AutomationControlled",
    ]

    # Video is optional because it consumes considerable Actor storage.
    record_video = os.getenv("RECORD_VIDEO", "false").lower() == "true"
    context_options = {
        "viewport": {"width": 1280, "height": 720},
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    if record_video:
        video_dir = get_file_path("videos")
        os.makedirs(video_dir, exist_ok=True)
        context_options["record_video_dir"] = video_dir
        context_options["record_video_size"] = {"width": 1280, "height": 720}

    launch_options = {"headless": True, "args": browser_args}
    if proxy_url:
        launch_options["proxy"] = parse_proxy_url(proxy_url)
        print("🌐 Playwright запускается через настроенный прокси.")
    else:
        print("⚠️ Прокси не задан. Apify IP может получить HTTP 403 от сайта.")

    browser = p.chromium.launch(**launch_options)
    context = browser.new_context(**context_options)
    page = context.new_page()

    try:
        Stealth().apply_stealth_sync(page)
    except Exception as exc:
        print(f"⚠️ Stealth не применён: {exc}")

    return browser, context, page


def ensure_authenticated(page):
    print("🔍 Проверка авторизации...")
    human_sleep(2, 3)

    try:
        if page.locator("span.font-kanji", has_text="寺").count() > 0:
            print("✅ Уже авторизованы! Иероглиф катакомб найден.")
            return True
    except Exception:
        pass

    email_val = os.getenv("REMANGA_EMAIL")
    pass_val = os.getenv("REMANGA_PASSWORD")

    if not email_val or not pass_val:
        print("❌ REMANGA_EMAIL / REMANGA_PASSWORD не заданы.")
        send_telegram_message("❌ Remanga: не заданы REMANGA_EMAIL / REMANGA_PASSWORD.")
        return False

    try:
        email_field = page.locator(
            'input[type="email"], input[name="username"], input[name="email"], '
            'input[placeholder*="Почта"], input[placeholder*="Email"], input[type="text"]'
        ).first
        pass_field = page.locator('input[type="password"]').first

        if not (email_field.is_visible(timeout=2000) and pass_field.is_visible(timeout=2000)):
            print("🔑 Ищу кнопку 'Войти'...")
            open_btns = page.locator(
                'header button:has-text("Войти"), header a:has-text("Войти"), '
                'button:has-text("Войти"), a:has-text("Войти")'
            )
            if open_btns.count() > 0:
                open_btns.first.click()
                human_sleep(2, 3)

        email_field = page.locator(
            'input[type="email"], input[name="username"], input[name="email"], '
            'input[placeholder*="Почта"], input[placeholder*="Email"], input[type="text"]'
        ).first
        pass_field = page.locator('input[type="password"]').first

        if not (email_field.is_visible(timeout=5000) and pass_field.is_visible(timeout=5000)):
            print("ℹ️ Форма входа не найдена.")
            return False

        print("✍️ Ввожу логин...")
        email_field.click()
        email_field.press_sequentially(email_val, delay=random.randint(60, 110))
        human_sleep(0.8, 1.5)

        print("✍️ Ввожу пароль...")
        pass_field.click()
        pass_field.press_sequentially(pass_val, delay=random.randint(60, 110))
        human_sleep(1, 2)

        submit_btn = page.locator(
            'form button[type="submit"], form button:has-text("Войти"), '
            'button[type="submit"], button:has-text("Войти")'
        ).last
        submit_btn.click()

        print("⏳ Ожидание авторизации...")
        human_sleep(6, 8)
        page.goto(TARGET_URL, timeout=60000, wait_until="commit")
        human_sleep(4, 6)
        return True

    except Exception as exc:
        print(f"⚠️ Ошибка авторизации: {exc}")
        return False


def run_dungeon_bot(proxy_url=None):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{timestamp}] Запуск задачи фарма катакомб...")

    max_runs = int(os.getenv("MAX_RUNS", "0"))
    run_count = 0

    with sync_playwright() as p:
        browser, context, page = setup_browser(p, proxy_url)

        try:
            cookies_path = os.getenv("REMANGA_COOKIES_FILE", get_file_path("cookies.txt"))
            netscape_cookies = parse_netscape_cookies(cookies_path)
            if netscape_cookies:
                context.add_cookies(netscape_cookies)
                print(f"🍪 Загружено cookies: {len(netscape_cookies)}")

            print(f"🔗 Переход на {TARGET_URL}...")
            resp = page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")

            status = resp.status if resp else None
            print(f"🌐 HTTP status: {status}")

            if status in (403, 502, 503):
                message = (
                    f"❌ Remanga вернул HTTP {status}. "
                    "Проверь СНГ/подходящий прокси в Apify."
                )
                print(message)
                send_telegram_message(message)
                safe_screenshot(page)
                return

            print("⏳ Ожидаю интерфейс...")
            human_sleep(5, 7)

            if not ensure_authenticated(page):
                print("⚠️ Авторизация не подтверждена, продолжаю проверку страницы.")

            safe_screenshot(page)

            try:
                close_btn = page.locator('button[aria-label="Закрыть"]')
                if close_btn.is_visible(timeout=5000):
                    close_btn.click()
                    human_sleep(2, 3)
            except Exception:
                pass

            while True:
                run_count += 1
                print(f"\n--- Запуск цикла прохода №{run_count} ---")

                if max_runs > 0 and run_count > max_runs:
                    print(f"🛑 Достигнут MAX_RUNS={max_runs}.")
                    break

                kanji_element = page.locator("span.font-kanji", has_text="寺").first
                kanji_element.wait_for(state="visible", timeout=20000)
                kanji_element.click()
                print("✅ Кликнул по иероглифу 寺!")
                human_sleep(2, 3)

                pass_button = page.locator("text=ПРОЙТИ СНОВА")
                pass_button.wait_for(state="visible", timeout=5000)
                is_disabled = pass_button.evaluate(
                    "node => node.disabled || node.getAttribute('aria-disabled') === 'true'"
                )
                if is_disabled:
                    print("🛑 Энергия закончилась.")
                    break
                pass_button.click()
                human_sleep(2, 3)

                battle_btn = page.locator("text=戰")
                battle_btn.wait_for(state="visible", timeout=5000)
                battle_btn.click()
                print("⚔️ Нажата кнопка боя (戰)!")

                print("⏳ Жду завершения боя...")
                human_sleep(12, 16)

                page.locator("text=К результатам").click(timeout=8000)
                print("✅ Нажато 'К результатам'.")
                human_sleep(2, 3)

                page.locator(
                    'button[data-sentry-source-file="pve-result-overlay.tsx"]'
                ).click(timeout=8000)
                print("✅ Возвращаемся на карту.")
                human_sleep(4, 6)

        except Exception as exc:
            print(f"❌ Ошибка Actor-задачи: {exc}")
            safe_screenshot(page)
            send_telegram_message(f"❌ Ошибка Remanga Actor: {exc}")
        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            print("🏁 Браузер закрыт.")
