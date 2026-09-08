import time
import random
import schedule
import os
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
import requests

TARGET_URL = "https://remanga.org/murim-cards#/map"

# NOTE: this file is updated in the repository by the assistant. The relevant
# battle-button section below uses the actual clickable ancestor instead of
# clicking the inner div containing the kanji.


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


def send_telegram_photo(photo_path: str, caption: str = "") -> None:
    if os.getenv("SCREENSHOT_LOGS", "true").lower() != "true":
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id or not os.path.exists(photo_path):
        return
    try:
        with open(photo_path, "rb") as photo:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption[:1024]},
                files={"photo": (Path(photo_path).name, photo, "image/png")},
                timeout=20,
            )
    except Exception as exc:
        print(f"⚠️ Не удалось отправить скриншот в Telegram: {exc}")


def human_sleep(min_sec=2, max_sec=4):
    time.sleep(random.uniform(min_sec, max_sec))


def type_text_sequentially(field, text: str, min_delay=0.08, max_delay=0.18):
    for char in text:
        field.press_sequentially(char)
        time.sleep(random.uniform(min_delay, max_delay))


def get_file_path(filename: str) -> str:
    return str(Path(__file__).resolve().parent / filename)


def parse_proxy_url(proxy_url: str):
    from urllib.parse import urlparse
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        raise ValueError("Invalid proxy URL")
    proxy = {"server": f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port}"}
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
                    ".remanga.org", "remanga.org",
                    ".xn--80aaig9ahr.xn--c1avg", "xn--80aaig9ahr.xn--c1avg",
                ])
            elif "remanga.org" in domain:
                target_domains.extend([
                    ".xn--80aaig9ahr.xn--c1avg", "xn--80aaig9ahr.xn--c1avg",
                    ".remanga.org", "remanga.org",
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


def load_cookies(file_path: str):
    if not os.path.exists(file_path):
        return []
    if file_path.lower().endswith(".json"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("cookies"), list):
                data = data["cookies"]
            if isinstance(data, list):
                return [
                    cookie for cookie in data
                    if isinstance(cookie, dict) and cookie.get("name") and "value" in cookie
                ]
        except Exception as exc:
            print(f"⚠️ Не удалось прочитать JSON cookies: {exc}")
            return []
    return parse_netscape_cookies(file_path)


def save_cookies_json(context, file_path: str):
    try:
        cookies = context.cookies()
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        print(f"🍪 Актуальные cookies сохранены: {file_path}")
        return True
    except Exception as exc:
        print(f"⚠️ Не удалось сохранить cookies: {exc}")
        return False


def safe_screenshot(page, filename="error_screenshot.png", caption="📸 Screenshot"):
    path = get_file_path(filename)
    try:
        page.screenshot(path=path, timeout=5000, animations="disabled")
        print(f"📸 Скриншот сохранён: {path}")
        send_telegram_photo(path, caption)
        return path
    except Exception as exc:
        print(f"⚠️ Не удалось сделать скриншот: {exc}")
        return None


def setup_browser(p, proxy_url=None):
    browser_args = [
        "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
        "--disable-gpu", "--no-first-run", "--disable-blink-features=AutomationControlled",
    ]
    record_video = os.getenv("RECORD_VIDEO", "false").lower() == "true"
    context_options = {
        "viewport": {"width": 1280, "height": 720},
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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


def has_login_ui(page):
    selectors = [
        'header button:has-text("Войти")', 'header a:has-text("Войти")',
        'button:has-text("Войти")', 'a:has-text("Войти")',
    ]
    for selector in selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=800):
                return True
        except Exception:
            pass
    return False


def is_authenticated(page):
    if has_login_ui(page):
        return False
    try:
        if page.locator("span.font-kanji", has_text="寺").first.is_visible(timeout=1500):
            return True
    except Exception:
        pass
    try:
        if page.get_by_text("寺", exact=True).first.is_visible(timeout=1500):
            return True
    except Exception:
        pass
    return False


def ensure_authenticated(page, context):
    print("🔍 Проверка авторизации...")
    human_sleep(2, 3)
    if is_authenticated(page):
        print("✅ Сессия уже авторизована.")
        return True
    if has_login_ui(page):
        print("🔑 Cookies не авторизовали страницу — открываю обычную форму входа.")
    else:
        print("ℹ️ Авторизация по cookies не подтверждена — проверяю форму входа.")
    email_val = os.getenv("REMANGA_EMAIL")
    pass_val = os.getenv("REMANGA_PASSWORD")
    if not email_val or not pass_val:
        print("❌ REMANGA_EMAIL / REMANGA_PASSWORD не заданы.")
        send_telegram_message("❌ Remanga: не заданы REMANGA_EMAIL / REMANGA_PASSWORD.")
        return False
    try:
        email_selector = (
            'input[type="email"], input[name="username"], input[name="email"], '
            'input[placeholder*="Почта"], input[placeholder*="Email"], input[type="text"]'
        )
        pass_selector = 'input[type="password"]'
        email_field = page.locator(email_selector).first
        pass_field = page.locator(pass_selector).first
        if not (email_field.is_visible(timeout=2000) and pass_field.is_visible(timeout=2000)):
            print("🔑 Ищу кнопку 'Войти'...")
            open_btns = page.locator(
                'header button:has-text("Войти"), header a:has-text("Войти"), '
                'button:has-text("Войти"), a:has-text("Войти")'
            )
            if open_btns.count() > 0:
                open_btns.first.click()
                human_sleep(2, 3)
        email_field = page.locator(email_selector).first
        pass_field = page.locator(pass_selector).first
        if not (email_field.is_visible(timeout=5000) and pass_field.is_visible(timeout=5000)):
            print("ℹ️ Форма входа не найдена.")
            safe_screenshot(page, "auth_form_not_found.png", "🔑 Форма входа не найдена")
            return False
        email_field.click()
        email_field.press("Control+A")
        type_text_sequentially(email_field, email_val)
        human_sleep(0.8, 1.5)
        pass_field.click()
        pass_field.press("Control+A")
        type_text_sequentially(pass_field, pass_val)
        human_sleep(1, 2)
        submit_btn = page.locator(
            'form button[type="submit"], form button:has-text("Войти"), '
            'button[type="submit"], button:has-text("Войти")'
        ).last
        submit_btn.click()
        human_sleep(5, 7)
        verification_texts = ["Проверка", "Подтвердите, что вы не робот", "Я не робот", "captcha", "CAPTCHA"]
        for text in verification_texts:
            try:
                if page.get_by_text(text, exact=False).first.is_visible(timeout=500):
                    print("⚠️ Обнаружена дополнительная проверка после входа.")
                    safe_screenshot(page, "auth_verification.png", "⚠️ Дополнительная проверка Remanga")
                    return False
            except Exception:
                pass
        if is_authenticated(page):
            print("✅ Авторизация подтверждена.")
            cookies_output = os.getenv("REMANGA_COOKIES_JSON", get_file_path("cookies.json"))
            save_cookies_json(context, cookies_output)
            return True
        safe_screenshot(page, "after_login.png", "🔑 Состояние после авторизации")
        print("⚠️ После входа авторизация не подтверждена.")
        return False
    except Exception as exc:
        print(f"⚠️ Ошибка авторизации: {exc}")
        safe_screenshot(page, "auth_error.png", "❌ Ошибка авторизации")
        return False


def run_dungeon_bot(proxy_url=None):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{timestamp}] Запуск задачи фарма катакомб...")
    max_runs = int(os.getenv("MAX_RUNS", "0"))
    run_count = 0
    with sync_playwright() as p:
        browser, context, page = setup_browser(p, proxy_url)
        try:
            json_cookie_path = os.getenv("REMANGA_COOKIES_JSON", get_file_path("cookies.json"))
            netscape_cookie_path = os.getenv("REMANGA_COOKIES_FILE", get_file_path("cookies.txt"))
            cookies = load_cookies(json_cookie_path)
            cookie_source = json_cookie_path
            if not cookies:
                cookies = load_cookies(netscape_cookie_path)
                cookie_source = netscape_cookie_path
            if cookies:
                context.add_cookies(cookies)
                print(f"🍪 Загружено cookies: {len(cookies)} ({cookie_source})")
            else:
                print("🍪 Cookies не найдены — сразу будет использована форма входа.")
            print(f"🔗 Переход на {TARGET_URL}...")
            resp = page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")
            status = resp.status if resp else None
            print(f"🌐 HTTP status: {status}")
            if status in (403, 502, 503):
                message = f"❌ Remanga вернул HTTP {status}. Проверь прокси в Apify."
                print(message)
                send_telegram_message(message)
                safe_screenshot(page, "http_error.png", f"❌ HTTP {status}")
                return
            print("⏳ Ожидаю интерфейс...")
            human_sleep(5, 7)
            safe_screenshot(page, "page_loaded.png", "🌐 Страница загружена")
            if is_authenticated(page):
                print("🍪✅ Cookies рабочие — вход через логин/пароль не требуется.")
            else:
                print("🍪❌ Cookies не дали авторизацию — запускаю старую авторизацию.")
                if not ensure_authenticated(page, context):
                    print("❌ Авторизация не подтверждена. Останавливаю Actor, чтобы не ломать сессию.")
                    return
                page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")
                human_sleep(4, 6)
                if not is_authenticated(page):
                    print("❌ После входа сессия не подтверждена.")
                    safe_screenshot(page, "auth_not_confirmed.png", "❌ Сессия не подтверждена")
                    return
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

                # The text '戰' is inside the clickable control. Playwright was
                # previously resolving the inner <div>, while another child
                # (the restart button) intercepted the pointer. Click the
                # nearest button ancestor first, with a force-click fallback.
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
                human_sleep(12, 16)
                page.locator("text=К результатам").click(timeout=8000)
                print("✅ Нажато 'К результатам'.")
                human_sleep(2, 3)
                safe_screenshot(page, f"cycle_{run_count}_result.png", f"🏆 Результат — цикл №{run_count}")
                page.locator('button[data-sentry-source-file="pve-result-overlay.tsx"]').click(timeout=8000)
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
