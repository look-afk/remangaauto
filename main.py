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

    result = {
        "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    }

    if parsed.username:
        result["username"] = parsed.username

    if parsed.password:
        result["password"] = parsed.password

    return result


def send_telegram_photo(path, caption=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            "⚠️ Telegram не настроен: нужны "
            "TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID."
        )
        return False

    try:
        url = (
            f"https://api.telegram.org/"
            f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        )

        with open(path, "rb") as photo:
            response = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption or "",
                },
                files={
                    "photo": photo
                },
                timeout=30,
            )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            print(
                "⚠️ Telegram API вернул ошибку: "
                f"{result.get('description', 'unknown error')}"
            )
            return False

        print("📨 Скриншот отправлен в Telegram.")
        return True

    except Exception as exc:
        print(
            f"⚠️ Не удалось отправить скриншот в Telegram: {exc}"
        )
        return False


def safe_screenshot(page, filename, message=None):
    try:
        path = get_file_path(filename)

        page.screenshot(
            path=path,
            full_page=False
        )

        print(f"📸 Скриншот сохранён: {path}")

        if message:
            print(message)

        send_telegram_photo(path, message)

    except Exception as exc:
        print(
            f"⚠️ Не удалось сохранить скриншот: {exc}"
        )


def is_authenticated(page):
    try:
        page.wait_for_timeout(1000)

        body_text = page.locator(
            "body"
        ).inner_text(timeout=5000)

        login_texts = [
            "Войти",
            "Авторизация",
            "Login",
            "Sign in",
        ]

        if (
            any(text in body_text for text in login_texts)
            and "寺" not in body_text
        ):
            return False

        return "寺" in body_text

    except Exception:
        return False


def close_open_dialog(page):
    try:
        dialogs = page.locator(
            '[role="dialog"][data-state="open"]'
        )

        if dialogs.count() > 0:
            print(
                "🔒 Обнаружен открытый Dialog — "
                "пытаюсь закрыть его."
            )

            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)

            if page.locator(
                '[role="dialog"][data-state="open"]'
            ).count() > 0:

                print(
                    "⚠️ Dialog всё ещё открыт, "
                    "продолжаю повторный поиск кнопки 寺."
                )

                return False

            print("✅ Dialog закрыт.")

        return True

    except Exception as exc:
        print(
            f"⚠️ Не удалось проверить/закрыть Dialog: {exc}"
        )

        return False


# --- ПРЯМЫЕ И ПРОСТЫЕ КЛИКИ (ИСПРАВЛЕНО) ---

def click_temple(page, run_count):
    """
    Ищет 寺 до 30 секунд. Кликает прямо по тексту.
    """
    deadline = time.time() + 30
    attempt = 0
    last_error = None

    while time.time() < deadline:
        attempt += 1
        close_open_dialog(page)

        try:
            # Ищем иероглиф напрямую, как в старом рабочем коде
            kanji = page.locator('span.font-kanji', has_text='寺').first
            
            if kanji.is_visible(timeout=2000):
                print(f"🔎 寺 найден — пытаюсь нажать (попытка {attempt})...")
                kanji.click(timeout=5000)
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
    Нажимает на кнопку 血.
    Использует JS click(), потому что внутренний div с иероглифом
    может находиться под другим элементом и Playwright click() получает
    "intercepts pointer events".
    """
    try:
        battle_btn = page.locator('text=血').first
        battle_btn.wait_for(state="visible", timeout=10000)
        battle_btn.evaluate("(el) => el.click()")
    except Exception as exc:
        raise RuntimeError(f"Не удалось нажать кнопку 血: {exc}") from exc


def wait_for_battle_again(page, run_count, attempt):
    """
    Ожидает появления кнопки боя (血)
    """
    print(f"⏳ Жду следующую кнопку 血 (попытка {attempt})...")
    try:
        # Ищем и ждем появления текста 血
        battle_btn = page.locator('text=血').first
        battle_btn.wait_for(state="visible", timeout=30000)
        return True
    except Exception:
        print("ℹ️ 血 больше не появился — вероятно, маны больше не хватает.")
        safe_screenshot(
            page,
            f"cycle_{run_count}_attempt_{attempt}_no_mana.png",
            "🔎 Следующий 血 недоступен",
        )
        return False


def run_dungeon_bot(proxy_url=None):
    print(
        "[2026-09-08] "
        "Запуск задачи фарма катакомб..."
    )

    cookie_json_path = (
        os.getenv("REMANGA_COOKIES_JSON")
        or get_file_path("cookies.json")
    )

    cookie_file_path = (
        os.getenv("REMANGA_COOKIES_FILE")
        or get_file_path("cookies.txt")
    )

    cookies = []

    if os.path.exists(cookie_json_path):
        try:
            cookies = load_cookies(
                cookie_json_path
            )

            print(
                f"🍪 Загружено cookies: "
                f"{len(cookies)} "
                f"({cookie_json_path})"
            )

        except Exception as exc:
            print(
                "⚠️ Не удалось загрузить "
                f"JSON cookies: {exc}"
            )

    if (
        not cookies
        and os.path.exists(cookie_file_path)
    ):
        try:
            cookies = load_cookies(
                cookie_file_path
            )

            print(
                f"🍪 Загружено cookies: "
                f"{len(cookies)} "
                f"({cookie_file_path})"
            )

        except Exception as exc:
            print(
                "⚠️ Не удалось загрузить "
                f"cookies: {exc}"
            )

    with sync_playwright() as p:

        browser_args = [
            "--disable-blink-features=AutomationControlled"
        ]

        proxy = parse_proxy(
            proxy_url
            or os.getenv("CUSTOM_PROXY")
            or os.getenv("PROXY_URL")
        )

        if proxy:
            print(
                "🌐 Playwright запускается "
                "через настроенный прокси."
            )

        launch_kwargs = {
            "headless": True,
            "args": browser_args,
        }

        if proxy:
            launch_kwargs["proxy"] = proxy

        browser = p.chromium.launch(
            **launch_kwargs
        )

        context = browser.new_context(
            viewport={
                "width": 1440,
                "height": 900,
            }
        )

        if cookies:
            try:
                context.add_cookies(
                    cookies
                )

            except Exception as exc:
                print(
                    "⚠️ Не удалось применить "
                    f"cookies: {exc}"
                )

        page = context.new_page()

        Stealth().apply_stealth_sync(page)

        try:
            print(
                "🔗 Переход на "
                "https://remanga.org/murim-cards#/map..."
            )

            response = page.goto(
                "https://remanga.org/murim-cards#/map",
                wait_until="domcontentloaded",
                timeout=60000,
            )

            print(
                "🌐 HTTP status: "
                f"{response.status if response else 'unknown'}"
            )

            print(
                "⏳ Ожидаю интерфейс..."
            )

            try:
                page.wait_for_selector(
                    "span.font-kanji",
                    timeout=30000,
                )

            except Exception:
                page.wait_for_timeout(
                    5000
                )

            safe_screenshot(
                page,
                "page_loaded.png"
            )

            if is_authenticated(page):
                print(
                    "🍪✅ Cookies рабочие — "
                    "вход через логин/пароль "
                    "не требуется."
                )

            else:
                print(
                    "⚠️ Не удалось подтвердить "
                    "авторизацию по cookies."
                )

            # ⚡ Молнии: используем отдельный актуальный фармер,
            # не меняя рабочую инициализацию браузера/прокси.
            if os.getenv("FARM_LIGHTNING", "1") == "1":
                try:
                    from my_actor.lightning_farm import farm_lightning
                    farm_lightning(page)
                except Exception as exc:
                    print(f"⚠️ Ошибка фарма молний: {exc}")

                # Возвращаемся на карту перед основной фармой.
                page.goto(
                    "https://remanga.org/murim-cards#/map",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                try:
                    page.wait_for_selector(
                        "span.font-kanji",
                        timeout=30000,
                    )
                except Exception:
                    page.wait_for_timeout(5000)

            run_count = 0

            max_runs = int(
                os.getenv(
                    "MAX_RUNS",
                    "0"
                )
            )

            while True:

                run_count += 1

                print()
                print(
                    f"--- Запуск цикла "
                    f"прохода №{run_count} ---"
                )

                if (
                    max_runs > 0
                    and run_count > max_runs
                ):
                    print(
                        f"🛑 Достигнут "
                        f"MAX_RUNS={max_runs}."
                    )
                    break

                # Ищем и нажимаем 寺 напрямую
                click_temple(
                    page,
                    run_count
                )

                safe_screenshot(
                    page,
                    f"cycle_{run_count}_dungeon.png",
                    f"🏯 Катакомбы — "
                    f"цикл №{run_count}",
                )

                human_sleep(2, 3)

                attempt = 0

                while True:
                    attempt += 1

                    if not wait_for_battle_again(
                        page,
                        run_count,
                        attempt,
                    ):
                        print(
                            "🛑 Маны недостаточно. "
                            f"Завершено боёв: "
                            f"{attempt - 1}."
                        )
                        break

                    # Нажимаем напрямую на текст 血
                    click_battle(page)

                    print(
                        f"⚔️ Нажата кнопка 血 — "
                        f"бой №{attempt}!"
                    )

                    page.wait_for_timeout(
                        5000
                    )

                # Возвращаемся на карту.
                return_button = page.locator(
                    'button[data-sentry-source-file="pve-result-overlay.tsx"]'
                ).last

                try:
                    return_button.wait_for(
                        state="visible",
                        timeout=5000,
                    )

                    try:
                        return_button.click(
                            timeout=10000
                        )

                    except Exception:
                        return_button.click(
                            timeout=10000,
                            force=True,
                        )

                    print(
                        "✅ Возвращаемся на карту."
                    )

                    human_sleep(4, 6)

                except Exception:

                    close_open_dialog(page)

                    if page.locator(
                        "span.font-kanji"
                    ).count() > 0:

                        print(
                            "ℹ️ Элементы карты "
                            "обнаружены, следующий "
                            "цикл снова будет искать 寺."
                        )

                    else:

                        print(
                            "ℹ️ Кнопка возврата "
                            "на карту сейчас "
                            "недоступна."
                        )

        except Exception as exc:

            print(
                f"❌ Ошибка Actor-задачи: {exc}"
            )

            safe_screenshot(
                page,
                "error_screenshot.png",
                "❌ Ошибка Actor-задачи",
            )

        finally:

            browser.close()

            print(
                "🏁 Браузер закрыт."
            )


if __name__ == "__main__":
    run_dungeon_bot()
