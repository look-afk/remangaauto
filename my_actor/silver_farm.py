"""Фарм серебра (event points) на карте Murim Cards — через API, без браузера.

Серебро на карте = event points (`/api/v2/events/eventpoint-balance/`).
Источники:
  * прохождение локаций: `POST /api/v2/events/card-battle/locations/{id}/raid/`
    (тратит энергию, даёт ровно столько же серебра, что и потрачено энергии);
  * дневные задания: за победы в боях и за прочитанные главы
    (`/api/v2/events/card-battle/daily/{id}/claim/`);
  * опциональный обмен молний на серебро.

Логика фарма — «лестница»: начинаем с самого сложного открытого данжа,
при поражении спускаемся на ступень ниже, а на первой проходимой остаёмся
и добиваем её до конца энергии. Потом забираем дневные задания.
"""

from __future__ import annotations

import os
import random
import time

from .log import log as _log
from .remanga_api import RemangaApi, api_from_env

# прогнозы, при которых бой считаем выигрышным
SAFE_FORECASTS = {"almost_guaranteed", "has_chance"}
# прогнозы, при которых лучше не лезть (энергия уйдёт без награды)
BAD_FORECASTS = {"need_more", "no_chance"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


def _env_pause(name: str, default: tuple[int, int]) -> tuple[int, int]:
    """Пауза в мс: 'SILVER_PAUSE_MS=350-1200' либо просто '500'."""
    v = (os.getenv(name) or "").strip()
    if not v:
        return default
    try:
        if "-" in v:
            a, b = v.split("-", 1)
            lo, hi = int(a), int(b)
        else:
            lo = hi = int(v)
        return max(0, lo), max(0, hi)
    except ValueError:
        return default


# ---------------------------------------------------------------- выбор локации

def _worth_trying(loc: dict, squad_power: int, safe_only: bool) -> bool:
    """Отсекаем заведомо безнадёжные локации, если включён safe_only."""
    if not safe_only:
        return True
    forecast = str(loc.get("forecast") or "")
    boss = int(loc.get("boss_power") or 0)
    if forecast in BAD_FORECASTS and boss > squad_power:
        return False
    return True


def ladder_locations(locations: list[dict], squad_power: int = 0, *,
                     safe_only: bool = True, forced_id=None) -> list[dict]:
    """Открытые локации по убыванию сложности: сверху — самый сложный данж.

    Фарм идёт «лестницей»: начинаем с верхней ступени, при каждом поражении
    спускаемся на одну ниже, а на первой проходимой остаёмся и добиваем её
    до конца энергии.

    forced_id — принудительно оставляем только эту локацию.
    """
    if not locations:
        return []

    if forced_id:
        forced = [loc for loc in locations
                  if int(loc.get("id") or -1) == int(forced_id)]
        if forced:
            return forced

    ladder = [loc for loc in locations
              if loc.get("unlocked") and _worth_trying(loc, squad_power, safe_only)]
    ladder.sort(
        key=lambda loc: (int(loc.get("order") or 0), int(loc.get("id") or 0)),
        reverse=True,
    )
    return ladder


# ---------------------------------------------------------------- дневные задания

def claim_dailies(api: RemangaApi, log=_log) -> list[dict]:
    """Забираем награды за выполненные ежедневные задания."""
    claimed = []
    try:
        tasks = api.daily()
    except Exception as exc:
        log(f"[silver] не удалось получить задания дня: {exc}")
        return claimed

    for task in tasks:
        try:
            target = int(task.get("target_value") or 0)
            progress = int(task.get("progress") or 0)
        except (TypeError, ValueError):
            continue
        if task.get("is_claimed") or progress < target or not target:
            continue

        code, data = api.claim_daily(task.get("id"))
        reward_ep = task.get("event_points_reward") or 0
        reward_li = task.get("lightning_reward") or 0
        if code in (200, 201):
            claimed.append(task)
            log(f"[silver] задание «{task.get('name')}» ({task.get('code')}) забрано: "
                f"+{reward_ep} серебра, +{reward_li} молний")
        else:
            detail = ""
            if isinstance(data, dict):
                detail = str(data.get("detail") or "")[:160]
            log(f"[silver] не удалось забрать задание {task.get('id')}: HTTP {code} {detail}")
        time.sleep(random.uniform(0.4, 0.9))
    return claimed


# ---------------------------------------------------------------- обмен молний

def exchange_lightning(api: RemangaApi, amount: int, log=_log) -> int:
    """Меняем молнии на серебро (rate ~2.0). Возвращает серебро или 0."""
    if amount <= 0:
        return 0
    code, data = api.buy_event_points(amount)
    if code not in (200, 201):
        detail = ""
        if isinstance(data, dict):
            detail = str(data.get("detail") or "")[:200]
        log(f"[silver] обмен {amount} молний не удался: HTTP {code} {detail}")
        return 0
    earned = 0
    if isinstance(data, dict):
        earned = int(data.get("event_points") or data.get("earned")
                     or data.get("balance") or 0)
    log(f"[silver] обменяно {amount} молний -> +{earned} серебра")
    return earned


# ---------------------------------------------------------------- основной фарм

def farm_silver(api: RemangaApi | None = None, *, log=_log, **overrides) -> dict:
    """Тратит всю энергию «лестницей» и забирает дневные задания.

    Идём с самого сложного открытого данжа вниз: проигрыш -> ступень ниже,
    победа -> остаёмся на этом данже и добиваем его до конца энергии.

    Env:
      SILVER_MAX_RAIDS      — максимум рейдов за запуск (0 = пока есть энергия)
      SILVER_LOCATION       — id локации принудительно (лестница выключена)
      SILVER_SAFE_ONLY      — 1 (по умолчанию) пропускать заведомо безнадёжные
      SILVER_PAUSE_MS       — пауза между рейдами, '350-1200' или '500'
      SILVER_MIN_ENERGY     — сколько энергии не трогать (по умолчанию 0)
      SILVER_BUY_LIGHTNING  — сколько молний обменять на серебро (0 = выкл)
      CLAIM_DAILIES         — 1 (по умолчанию) забирать награды заданий
    """
    started = time.time()
    if api is None:
        api = api_from_env(log=log)
    if api is None:
        log("[silver] куки не найдены — фарм серебра пропущен")
        return {"ok": False, "reason": "no_cookies"}

    auth_ok, auth_detail = api.authorized()
    if not auth_ok:
        log(f"[silver] API не авторизован ({auth_detail}) — фарм серебра пропущен")
        return {"ok": False, "reason": "unauthorized", "detail": auth_detail}

    max_raids = overrides.get("max_raids", _env_int("SILVER_MAX_RAIDS", 0))
    forced = overrides.get("location_id", _env_int("SILVER_LOCATION", 0)) or None
    safe_only = overrides.get("safe_only", _env_bool("SILVER_SAFE_ONLY", True))
    min_energy = overrides.get("min_energy", _env_int("SILVER_MIN_ENERGY", 0))
    pause = overrides.get("pause_ms", _env_pause("SILVER_PAUSE_MS", (350, 1200)))
    buy_lightning = overrides.get("buy_lightning",
                                  _env_int("SILVER_BUY_LIGHTNING", 0))
    do_claim = overrides.get("claim_dailies", _env_bool("CLAIM_DAILIES", True))

    profile = api.profile()
    squad_power = int(profile.get("squad_power") or 0)
    energy = int(profile.get("energy_current") or 0)
    energy_max = int(profile.get("energy_max") or 0)
    silver_before = api.event_points()

    log(f"[silver] старт: энергия {energy}/{energy_max}, "
        f"серебро {silver_before}, сила отряда {squad_power}")

    claimed = claim_dailies(api, log=log) if do_claim else []

    locations = api.locations()
    ladder = ladder_locations(locations, squad_power,
                              safe_only=safe_only, forced_id=forced)
    if not ladder:
        log("[silver] нет доступных локаций")
        return {"ok": False, "reason": "no_locations", "silver_before": silver_before}

    raids = won = lost = 0
    energy_spent = 0
    silver_earned_raid = 0
    note = ""

    rung = 0
    loc = ladder[rung]
    log(f"[silver] старт с данжа {loc.get('order')} «{loc.get('name')}» "
        f"(босс {int(loc.get('boss_power') or 0)}, отряд {squad_power})")

    while True:
        cost = int(loc.get("energy_cost") or 0)
        if cost <= 0:
            note = f"локация {loc.get('id')} без цены входа"
            break
        if energy < cost + min_energy:
            break
        if max_raids and raids >= max_raids:
            note = "достигнут SILVER_MAX_RAIDS"
            break

        code, data = api.raid(int(loc["id"]))
        raids += 1
        if code != 200 or not isinstance(data, dict):
            detail = ""
            if isinstance(data, dict):
                detail = str(data.get("detail") or "")[:200]
            log(f"[silver] рейд локации {loc['id']} -> HTTP {code} {detail}")
            if code in (400, 403, 404):
                note = f"рейд отклонён (HTTP {code})"
                break
            time.sleep(1.5)
            continue

        status = data.get("status")
        spent = int(data.get("energy_spent") or cost)
        earned = int(data.get("event_points_earned") or 0)
        energy -= spent
        energy_spent += spent

        if status == "won":
            # проходимый данж найден — остаёмся на нём до конца энергии
            won += 1
            silver_earned_raid += earned
        else:
            lost += 1
            prev = loc
            rung += 1
            if rung >= len(ladder):
                log(f"[silver] данж {prev.get('order')} «{prev.get('name')}» "
                    f"проигран — проще уже некуда, останавливаюсь")
                note = "поражение на самой простой локации"
                break
            loc = ladder[rung]
            log(f"[silver] данж {prev.get('order')} «{prev.get('name')}» "
                f"проигран (status={status}) — спускаюсь на данж "
                f"{loc.get('order')} «{loc.get('name')}» "
                f"(босс {int(loc.get('boss_power') or 0)})")
            continue

        if raids % 10 == 0:
            # энергия могла подкапываться — сверяемся с сервером
            energy = int(api.profile().get("energy_current") or energy)

        lo, hi = pause
        if hi:
            time.sleep(random.uniform(lo, hi) / 1000.0)

    if do_claim:
        claimed += claim_dailies(api, log=log)

    if buy_lightning > 0:
        silver_earned_raid += exchange_lightning(api, buy_lightning, log=log)

    silver_after = api.event_points()
    profile = api.profile()
    dailies_lightning = sum(int(t.get("lightning_reward") or 0) for t in claimed)
    result = {
        "ok": True,
        "raids": raids,
        "won": won,
        "lost": lost,
        "energy_spent": energy_spent,
        "energy_left": int(profile.get("energy_current") or 0),
        "energy_max": int(profile.get("energy_max") or 0),
        "silver_before": silver_before,
        "silver_after": silver_after,
        "silver_delta": silver_after - silver_before,
        "dailies_claimed": len(claimed),
        "dailies_lightning": dailies_lightning,
        "location": loc.get("name"),
        "note": note,
        "seconds": round(time.time() - started, 1),
    }
    log(f"[silver] готово: рейдов {raids} (побед {won}), потрачено энергии "
        f"{energy_spent}, серебро {silver_before} -> {silver_after} "
        f"(+{result['silver_delta']}), заданий забрано {len(claimed)} "
        f"(+{dailies_lightning} молний), "
        f"энергии осталось {result['energy_left']}/{result['energy_max']}"
        + (f", {note}" if note else ""))
    return result


if __name__ == "__main__":
    farm_silver()
