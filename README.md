## What does ReManga Auto do?

**ReManga Auto** is a Playwright automation Actor for [ReManga](https://remanga.org) that farms in-game currency and reads chapters for you. It spends your energy on the *Murim Cards* map through the official API (silver = event points), claims daily quest rewards, and reads manga chapters with a real fast scroll so the site marks them as read — which unlocks lightning, daily reading quests and progress on your bookmarks.

The Actor is built for long unattended runs: it works with your own cookies, backs up its progress to a local state file, restarts the browser if Chromium crashes, and backs off automatically when the site rate-limits an action.

## Why use ReManga Auto?

- **Earn silver while you sleep** — every point of energy is converted into event points (silver) via the fastest possible path, the JSON API, instead of slow UI clicks.
- **Never miss a daily** — completed daily quests (battle wins, reading tasks) are claimed automatically for extra silver and lightning.
- **Read at machine speed** — chapters are opened, scrolled to the official "chapter end" marker and verified through the API, so `viewed` is set even when the site's own observer misfires.
- **Safe by default** — the farm only raids locations your squad can actually win, respects per-day limits and pauses between actions.

## How to use ReManga Auto

1. Export your ReManga cookies (browser extension or DevTools) as `cookies.json` (Playwright `storage_state`) or `cookies.txt` (Netscape format). The `auth:token` cookie is required.
2. Paste the cookie file content into the **Cookies** field of the Input tab (or keep `cookies.txt` in the project folder for local runs).
3. Leave **Farm silver** and **Farm lightning** enabled and press **Start**.
4. Watch the run log: you will see `[silver] … серебро 211 -> 283 (+72)` and `[lightning] … прочитано 10` lines, followed by claimed daily quests.
5. Schedule the Actor (e.g. once an hour) so regenerated energy is always spent.

## Input

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `cookies` | string | — | Contents of `cookies.json` / `cookies.txt` for your ReManga account. |
| `farmSilver` | boolean | `true` | Spend energy on map locations via API and claim daily quests. |
| `silverMaxRaids` | integer | `0` | Raid cap per run (`0` = spend all available energy). |
| `farmLightning` | boolean | `true` | Read chapters with the fast-scroll reader. |
| `maxChaptersPerDay` | integer | `96` | Daily reading limit. |
| `readTitleUrls` | array | `[]` | Titles to read (URLs or slugs). Empty = collect from `readCollectionUrl`. |
| `readCollectionUrl` | string | `…/collections/2197` | Collection page used to discover titles. |
| `dungeonUiLoop` | boolean | `false` | Legacy UI click-loop (kept for debugging). |

The same settings are available as environment variables for local runs: `FARM_SILVER`, `SILVER_MAX_RAIDS`, `SILVER_PAUSE_MS`, `SILVER_LOCATION`, `SILVER_BUY_LIGHTNING`, `CLAIM_DAILIES`, `FARM_LIGHTNING`, `LIGHTNING_MAX_CHAPTERS`, `LIGHTNING_SKIP_PAID`, `READ_TITLE_URLS`, `READ_COLLECTION_URL`, `FARM_DUNGEON_UI`, `REMANGA_COOKIES_RAW` / `REMANGA_COOKIES_JSON`.

## Output

The Actor prints a structured run log; the final lines summarize the run:

```json
{"ok": true, "raids": 9, "won": 9, "energy_spent": 72,
 "silver_before": 211, "silver_after": 283, "silver_delta": 72,
 "dailies_claimed": 0, "energy_left": 0, "location": "Долина Кровавого Ветра"}
```

```json
{"ok": true, "read": 10, "total_today": 10, "errors": 0, "seconds": 143.5}
```

Progress for the reader is persisted in `lightning_state.json`, so restarting the Actor never re-reads chapters.

## Tips or Advanced options

- **Run it hourly.** Energy regenerates over time; short frequent runs convert it into silver at the same rate but keep the cap from overflowing.
- **Pin a title list** (`readTitleUrls`) instead of the collection page — it saves a browser navigation per run.
- **Keep `farmSilver` and `farmLightning` together**: reading 10 chapters completes the reading quests, which are worth +55 silver and +100 lightning per day on top of the raids.
- Set `SILVER_MAX_RAIDS` to a small number if you only want to top up a specific quest.

## FAQ, disclaimers, and support

**Is this allowed?** The Actor only uses your own account, the public ReManga API and normal page scrolling. Use it at your own risk and keep the built-in delays — hammering the site can trigger temporary blocks (the code already pauses and backs off when the API answers `403`).

**Why did nothing happen?** Check the log: `куки не найдены` means the cookies field is empty, `авторизация: ПРОВАЛЕНА` means the `auth:token` cookie is missing or expired.

**Bugs and feature requests** — please open an issue in the repository.
