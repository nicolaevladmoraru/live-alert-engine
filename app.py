import os
import time
import json
import re
import requests

API_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

API_BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# Mapping cache file (stored in repo container filesystem; resets if container rebuilds)
LEAGUE_CACHE_FILE = "league_cache.json"
LEAGUE_CACHE_TTL_SEC = 24 * 60 * 60  # 24h

# Stats cache: fixture_id -> {"home": int, "away": int, "ts": float}
STATS_CACHE = {}
STATS_COOLDOWN_SEC = 60  # 1 minute cooldown (as requested)

# In-memory anti-duplicate: fixture_id|alert_code
SENT_ALERTS = set()

# Your premium whitelist (Country + League Name) exactly as your project memory
# The engine will resolve these into stable league IDs.
ALLOWED_LEAGUE_KEYS = [
    ("Egypt", "Premier League"),
    ("USA", "Major League Soccer"),
    ("Belgium", "Jupiler Pro League"),
    ("Spain", "La Liga"),
    ("Netherlands", "Eredivisie"),
    ("France", "Ligue 1"),
    ("Spain", "Segunda División"),
    ("Argentina", "Liga Profesional Argentina"),
    ("Portugal", "Primeira Liga"),
    ("Chile", "Primera División"),
    ("Colombia", "Primera A"),
    ("Paraguay", "Division Profesional - Apertura"),
    ("Ecuador", "Liga Pro"),
    ("Switzerland", "Super League"),
    ("Turkey", "Süper Lig"),
    ("Turkey", "1. Lig"),
    ("South-Africa", "Premier Soccer League"),
    ("Romania", "Liga I"),
    ("Poland", "Ekstraklasa"),
    ("India", "Indian Super League"),
    ("Croatia", "HNL"),
    ("Italy", "Serie B"),
    ("Italy", "Serie A"),
    ("Greece", "Super League 1"),
    ("Serbia", "Super Liga"),
    ("Germany", "Bundesliga"),
    ("Czech-Republic", "Czech Liga"),
    ("Peru", "Primera División"),
    ("Brazil", "Carioca - 1"),
    ("Ireland", "Premier Division"),
    ("France", "Ligue 2"),
    ("England", "Premier League"),
    ("England", "Championship"),
    ("England", "League One"),
    ("England", "League Two"),
    ("England", "FA Cup"),
    ("Netherlands", "KNVB Beker"),
    ("Scotland", "Premiership"),
    ("France", "Coupe de France"),
    ("Italy", "Coppa Italia"),
    ("Spain", "Copa del Rey"),
]


def _safe_int(v, default=0):
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("–", "-").replace("—", "-").replace("’", "'")
    s = s.replace(".", "")
    s = re.sub(r"\s+", " ", s)
    return s


def validate_env():
    missing = []
    if not API_KEY:
        missing.append("API_FOOTBALL_KEY")
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")

    if missing:
        print("Missing environment variables: " + ", ".join(missing))
    else:
        print("All required environment variables are set.")


def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, data=payload, timeout=15)
        ok = (resp.status_code == 200)
        if not ok:
            print(f"Telegram send failed: {resp.status_code} {resp.text[:200]}")
        return ok
    except Exception as ex:
        print(f"Telegram send exception: {ex}")
        return False


def get_live_fixtures():
    url = f"{API_BASE}/fixtures?live=all"
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        data = r.json()
        return data.get("response", [])
    except Exception as ex:
        print(f"Live fixtures request failed: {ex}")
        return []


def should_fetch_stats(minute: int, status_short: str) -> bool:
    if status_short == "HT":
        return True
    if minute < 0:
        return False
    if 20 <= minute <= 30:
        return True
    if 50 <= minute <= 70:
        return True
    if 70 <= minute <= 85:
        return True
    if 85 <= minute <= 88:
        return True
    return False


def get_sot_cached(fixture_id: int):
    now = time.time()
    cached = STATS_CACHE.get(fixture_id)
    if cached and (now - cached["ts"] < STATS_COOLDOWN_SEC):
        return cached["home"], cached["away"]

    url = f"{API_BASE}/fixtures/statistics?fixture={fixture_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        data = r.json()
        resp = data.get("response", [])

        home_sot, away_sot = 0, 0
        if isinstance(resp, list) and len(resp) >= 2:
            def extract_sot(team_block):
                for item in team_block.get("statistics", []):
                    if item.get("type") == "Shots on Target":
                        return _safe_int(item.get("value"), 0)
                return 0

            home_sot = extract_sot(resp[0])
            away_sot = extract_sot(resp[1])

        STATS_CACHE[fixture_id] = {"home": home_sot, "away": away_sot, "ts": now}
        return home_sot, away_sot

    except Exception as ex:
        if cached:
            return cached["home"], cached["away"]
        print(f"Stats request failed for fixture {fixture_id}: {ex}")
        return 0, 0


def already_sent(fixture_id: int, alert_code: str) -> bool:
    key = f"{fixture_id}|{alert_code}"
    if key in SENT_ALERTS:
        return True
    SENT_ALERTS.add(key)
    return False


def load_league_cache():
    try:
        with open(LEAGUE_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_league_cache(payload: dict):
    try:
        with open(LEAGUE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as ex:
        print(f"Failed to save league cache: {ex}")


def resolve_allowed_league_ids():
    desired = set((_norm(c), _norm(n)) for (c, n) in ALLOWED_LEAGUE_KEYS)

    cache = load_league_cache()
    now = int(time.time())

    if cache and isinstance(cache, dict):
        ts = _safe_int(cache.get("ts"), 0)
        ids = cache.get("ids", [])
        if ts > 0 and (now - ts) < LEAGUE_CACHE_TTL_SEC and isinstance(ids, list) and ids:
            allowed_ids = set(_safe_int(x, 0) for x in ids if _safe_int(x, 0) > 0)
            print(f"Loaded {len(allowed_ids)} league IDs from cache.")
            return allowed_ids

    print("Resolving league IDs from API (cache miss/expired)...")

    url = f"{API_BASE}/leagues"
    allowed_ids = set()

    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        data = r.json()
        resp = data.get("response", [])

        for item in resp:
            league = item.get("league", {}) or {}
            country = item.get("country", {}) or {}

            league_id = _safe_int(league.get("id"), 0)
            league_name = _norm(league.get("name") or "")
            country_name = _norm(country.get("name") or "")

            if league_id > 0 and (country_name, league_name) in desired:
                allowed_ids.add(league_id)

        save_league_cache({"ts": now, "ids": sorted(list(allowed_ids))})
        print(f"Resolved and cached {len(allowed_ids)} league IDs.")
        return allowed_ids

    except Exception as ex:
        print(f"League resolve failed: {ex}")
        return allowed_ids


def check_alerts_for_match(match: dict, allowed_league_ids: set):
    fixture = match.get("fixture", {}) or {}
    league = match.get("league", {}) or {}
    teams = match.get("teams", {}) or {}
    goals = match.get("goals", {}) or {}

    fixture_id = _safe_int(fixture.get("id"), 0)
    if fixture_id <= 0:
        return

    league_id = _safe_int(league.get("id"), 0)
    if league_id not in allowed_league_ids:
        return

    status = fixture.get("status", {}) or {}
    status_short = (status.get("short") or "").strip().upper()
    minute = _safe_int(status.get("elapsed"), -1)

    if not should_fetch_stats(minute, status_short):
        return

    home_name = (teams.get("home", {}) or {}).get("name", "Home")
    away_name = (teams.get("away", {}) or {}).get("name", "Away")

    home_goals = _safe_int(goals.get("home"), 0)
    away_goals = _safe_int(goals.get("away"), 0)
    score = f"{home_goals} - {away_goals}"

    home_sot, away_sot = get_sot_cached(fixture_id)
    sot_total = home_sot + away_sot

    if 20 <= minute <= 30 and score == "0 - 0" and sot_total >= 3:
        if not already_sent(fixture_id, "GOAL_1H"):
            msg = (
                f"🟢 GOAL 1H\n"
                f"{home_name} vs {away_name}\n"
                f"Minute: {minute}\n"
                f"SOT: {home_sot} - {away_sot}"
            )
            send_telegram(msg)

    if status_short == "HT" and score == "0 - 0" and sot_total >= 5:
        if not already_sent(fixture_id, "TWO_GOALS_2H"):
            msg = (
                f"🟡 2 GOALS 2H\n"
                f"{home_name} vs {away_name}\n"
                f"Status: HT\n"
                f"SOT: {home_sot} - {away_sot}"
            )
            send_telegram(msg)

    if status_short == "HT" and score in ("1 - 0", "0 - 1") and sot_total >= 4:
        if not already_sent(fixture_id, "OVER_2_5_GOALS"):
            msg = (
                f"🔵 OVER 2.5 GOALS\n"
                f"{home_name} vs {away_name}\n"
                f"Status: HT\n"
                f"SOT: {home_sot} - {away_sot}"
            )
            send_telegram(msg)

    if 50 <= minute <= 70 and score == "1 - 1" and sot_total >= 6:
        if not already_sent(fixture_id, "GOAL_PUSH_2H"):
            msg = (
                f"🟠 GOAL PUSH 2H\n"
                f"{home_name} vs {away_name}\n"
                f"Minute: {minute}\n"
                f"SOT: {home_sot} - {away_sot}"
            )
            send_telegram(msg)

    if 70 <= minute <= 85 and abs(home_goals - away_goals) == 1 and sot_total >= 8:
        if not already_sent(fixture_id, "LATE_GOAL"):
            msg = (
                f"🔴 LATE GOAL\n"
                f"{home_name} vs {away_name}\n"
                f"Minute: {minute}\n"
                f"SOT: {home_sot} - {away_sot}"
            )
            send_telegram(msg)

    if 85 <= minute <= 88 and sot_total >= 10:
        if not already_sent(fixture_id, "LAST_MINUTE_GOAL"):
            msg = (
                f"🟣 LAST MINUTE GOAL\n"
                f"{home_name} vs {away_name}\n"
                f"Minute: {minute}\n"
                f"SOT: {home_sot} - {away_sot}"
            )
            send_telegram(msg)


def main():
    print("Live Alert Engine Premium Started (IDs resolved + relevant windows + 60s stats cooldown)")
    validate_env()

    allowed_league_ids = resolve_allowed_league_ids()
    print(f"Allowed league IDs active: {len(allowed_league_ids)}")

    while True:
        matches = get_live_fixtures()
        for m in matches:
            try:
                check_alerts_for_match(m, allowed_league_ids)
            except Exception as ex:
                print(f"Alert check error: {ex}")
        time.sleep(60)


if __name__ == "__main__":
    main()
