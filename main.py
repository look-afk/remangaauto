import json
import os
import random
import time
from datetime import datetime
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


def send_telegram_text(text):
    """Отправка текстового отчёта в Telegram (TELEGRAM_* уже в env)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            "⚠️ Telegram не настроен: нужны "
            "TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID."
        )
        return False

    try:
        url = (
            f"https://api.telegram.org/"
            f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        )

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
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

        print("📨 Отчёт отправлен в Telegram.")
        return True

    except Exception as exc:
        print(f"⚠️ Не удалось отправить отчёт в Telegram: {exc}")
        return False


def _fmt_duration(seconds):
    if seconds is None:
        return "—"
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h} ч {m} мин"
    if m:
        return f"{m} мин {s} с"
    return f"{s} с"


def build_report(silver=None, lightning=None, li_before=None, li_after=None,
                 started_ts=None, now_ts=None):
    """Собирает текст отчёта о прогоне. Чистая функция — тестируется отдельно.

    silver    — result из farm_silver (или None/{"ok": False, "reason": ...})
    lightning — result из farm_lightning
    li_before / li_after — баланс молний до и после прогона (api.lightning_balance)
    started_ts — time.time() в начале прогона (для строки «⏱»)
    now_ts     — время отчёта (для заголовка), по умолчанию текущее
    """
    ts = datetime.fromtimestamp(now_ts if now_ts is not None else time.time())
    lines = [f"📊 Remanga — итог прогона ({ts.strftime('%d.%m %H:%M')})"]

    silver = silver if isinstance(silver, dict) else {}
    lightning = lightning if isinstance(lightning, dict) else {}

    e_left = silver.get("energy_left")
    e_max = silver.get("energy_max")
    if e_left is not None and e_max is not None:
        lines.append(f"🔋 энергия: {e_left}/{e_max}")
    else:
        lines.append("🔋 энергия: —")

    if "silver_before" in silver:
        delta = int(silver.get("silver_delta") or 0)
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"🪙 серебро: {silver['silver_before']} → "
            f"{silver.get('silver_after')} ({sign}{delta})"
        )
    else:
        lines.append("🪙 серебро: —")

    if li_before is not None and li_after is not None:
        delta = int(li_after) - int(li_before)
        lines.append(f"⚡ молнии: {li_before} → {li_after} (+{delta})")
    else:
        lines.append("⚡ молнии: —")

    if "total_today" in lightning:
        max_today = lightning.get("max_today")
        tail = f"/{max_today}" if max_today else ""
        lines.append(f"📖 глав сегодня: {lightning['total_today']}{tail}")
    else:
        lines.append("📖 глав сегодня: —")

    claimed = (int(silver.get("dailies_claimed") or 0)
               + int(lightning.get("dailies_claimed") or 0))
    lines.append(f"🎁 дневок забрано: {claimed}")

    for name, res in (("серебро", silver), ("молнии", lightning)):
        if res.get("ok") is False:
            lines.append(
                f"⚠ {name}: пропущено ({res.get('reason') or 'ошибка'})"
            )

    if started_ts:
        lines.append(f"⏱ {_fmt_duration(time.time() - started_ts)}")
    return "\n".join(lines)


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
    started_ts = time.time()

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

        # Прокси: сперва проверяем прокси из env (он может быть мёртвым),
        # если не работает — подбираем бесплатный прокси стран СНГ.
        from my_actor.free_proxies import resolve_proxy

        proxy_url = resolve_proxy(proxy_url)
        proxy = parse_proxy(proxy_url)

        if proxy_url:
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

        # Сессия умеет перезапускать браузер, если Chromium вылетел —
        # её же передаём фармеру молний.
        from my_actor.reader import BrowserSession

        session = BrowserSession.from_existing(
            p,
            browser,
            context,
            page,
            launch_kwargs=launch_kwargs,
            context_kwargs={
                "viewport": {
                    "width": 1440,
                    "height": 900,
                }
            },
            cookies=cookies,
        )

        # Один API-клиент на прогон. Привязываем браузерный контекст:
        # при отказе прямого запроса (401/403/нет соединения) повтор пойдёт
        # через тот же прокси и куки, что и страница.
        from my_actor.remanga_api import api_from_env

        api = api_from_env()
        if api is not None:
            api.bind_context(context)

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

            if response and response.status in (401, 403, 429):
                route = (
                    f"через прокси {proxy_url}" if proxy_url
                    else "напрямую (IP без прокси)"
                )
                print(
                    "⚠️ Сайт закрыт DDoS-Guard/антиботом ("
                    f"{response.status}) {route}. "
                    "Если повторяется — задай свой прокси в поле proxyUrl."
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

            # 🪙 Серебро: тратим энергию на локации через API и забираем
            # ежедневные задания. Работает без браузера — секунды вместо минут.
            silver_res = None
            li_res = None
            try:
                li_before = (
                    api.lightning_balance() if api is not None else None
                )
            except Exception:
                li_before = None

            if os.getenv("FARM_SILVER", "1") == "1":
                try:
                    from my_actor.silver_farm import farm_silver

                    silver_res = farm_silver(api=api)
                except Exception as exc:
                    print(f"⚠️ Ошибка фарма серебра: {exc}")
                    silver_res = {"ok": False, "reason": str(exc)[:120]}

            # ⚡ Молнии: используем отдельный актуальный фармер,
            # не меняя рабочую инициализацию браузера/прокси.
            if os.getenv("FARM_LIGHTNING", "1") == "1":
                try:
                    from my_actor.lightning_farm import farm_lightning

                    li_res = farm_lightning(page, session=session, api=api)
                    page = session.get()
                except Exception as exc:
                    print(f"⚠️ Ошибка фарма молний: {exc}")
                    li_res = {"ok": False, "reason": str(exc)[:120]}
                    page = session.get()

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

            # 📨 Отчёт в Telegram: энергия, серебро, молнии, главы, дневки.
            if os.getenv("TG_REPORT", "1") == "1":
                try:
                    li_after = (
                        api.lightning_balance() if api is not None else None
                    )
                except Exception:
                    li_after = None
                try:
                    send_telegram_text(
                        build_report(
                            silver_res, li_res,
                            li_before, li_after, started_ts,
                        )
                    )
                except Exception as exc:
                    print(f"⚠️ Не удалось отправить TG-отчёт: {exc}")

            run_count = 0

            max_runs = int(
                os.getenv(
                    "MAX_RUNS",
                    "0"
                )
            )

            # Старый UI-цикл катакомб: серебро теперь фармится через API
            # (FARM_SILVER) — быстрее и без зависимости от анимаций.
            dungeon_ui = os.getenv("FARM_DUNGEON_UI", "0") == "1"
            if not dungeon_ui:
                print(
                    "⏭ UI-цикл катакомб выключен "
                    "(FARM_DUNGEON_UI=1 — включить обратно)."
                )

            while dungeon_ui:

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
