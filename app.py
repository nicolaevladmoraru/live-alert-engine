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

DATA_DIR = os.getenv("DATA_DIR", "/data").strip() or "/data"
LEAGUE_CACHE_FILE = os.path.join(DATA_DIR, "league_cache.json")
SENT_ALERTS_FILE = os.path.join(DATA_DIR, "sent_alerts.json")

LEAGUE_CACHE_TTL_SEC = 24 * 60 * 60
STATS_COOLDOWN_SEC = 60

STATS_CACHE = {}
SENT_ALERTS = set()

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


def ensure_data_dir():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception as ex:
        print(f"Failed to create data dir '{DATA_DIR}': {ex}")


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
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram variables missing (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID).")
        return False

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
            print(f"Telegram send failed: {resp.status_code} {resp.text[:300]}")
        else:
            print("Telegram test message sent successfully.")
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


def load_json_file(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_json_file(path: str, payload):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as ex:
        print(f"Failed to save file '{path}': {ex}")


def load_sent_alerts():
    global SENT_ALERTS
    payload = load_json_file(SENT_ALERTS_FILE)
    if isinstance(payload, list):
        SENT_ALERTS = set(str(x) for x in payload)
        print(f"Loaded {len(SENT_ALERTS)} sent alerts from disk.")
    else:
        print("No sent alerts file found; starting fresh.")


def persist_sent_alerts():
    save_json_file(SENT_ALERTS_FILE, sorted(list(SENT_ALERTS)))


def already_sent(fixture_id: int, alert_code: str) -> bool:
    key = f"{fixture_id}|{alert_code}"
    if key in SENT_ALERTS:
        return True
    SENT_ALERTS.add(key)
    persist_sent_alerts()
    return False


def resolve_allowed_league_ids():
    desired = set((_norm(c), _norm(n)) for (c, n) in ALLOWED_LEAGUE_KEYS)

    cache = load_json_file(LEAGUE_CACHE_FILE)
    now = int(time.time())

    if isinstance(cache, dict):
        ts = _safe_int(cache.get("ts"), 0)
        ids = cache.get("ids", [])
        if ts > 0 and (now - ts) < LEAGUE_CACHE_TTL_SEC and isinstance(ids, list) and ids:
            allowed_ids = set(_safe_int(x, 0) for x in ids if _safe_int(x, 0) > 0)
            print(f"Loaded {len(allowed_ids)} league IDs from disk cache.")
            return allowed_ids

    print("Resolving league IDs from API (disk cache miss/expired)...")

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

        save_json_file(LEAGUE_CACHE_FILE, {"ts": now, "ids": sorted(list(allowed_ids))})
        print(f"Resolved and cached {len(allowed_ids)} league IDs to disk.")
        return allowed_ids

    except Exception as ex:
        print(f"League resolve failed: {ex}")
        return allowed_ids


def build_premium_message(alert_title: str, league_name: str, country: str, home: str, away: str,
                         minute: int, status_short: str, score: str, sot_home: int, sot_away: int,
                         pick_text: str) -> str:
    comp = "🏆 League"
    if country and league_name:
        comp = f"🏆 {country} - {league_name}"
    elif league_name:
        comp = f"🏆 {league_name}"
    elif country:
        comp = f"🏆 {country}"

    timing = f"⏱ Minute: {minute}" if minute >= 0 else f"⏱ Status: {status_short}"
    if status_short == "HT":
        timing = "⏱ Status: HT"

    msg = (
        f"{alert_title}\n\n"
        f"{comp}\n"
        f"{home} vs {away}\n\n"
        f"{timing}\n"
        f"📌 Score: {score}\n"
        f"🎯 Shots on Target: {sot_home}-{sot_away}\n\n"
        f"📊 Pick:\n{pick_text}"
    )
    return msg


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

    league_name = (league.get("name") or "").strip()
    country = (league.get("country") or "").strip()

    home_sot, away_sot = get_sot_cached(fixture_id)
    sot_total = home_sot + away_sot

    if 20 <= minute <= 30 and score == "0 - 0" and sot_total >= 3:
        if not already_sent(fixture_id, "GOAL_1H"):
            msg = build_premium_message(
                "🟢 GOAL 1H",
                league_name, country,
                home_name, away_name,
                minute, status_short,
                score, home_sot, away_sot,
                "Over 0.5 goals (1st Half)"
            )
            send_telegram(msg)

    if status_short == "HT" and score == "0 - 0" and sot_total >= 5:
        if not already_sent(fixture_id, "TWO_GOALS_2H"):
            msg = build_premium_message(
                "🟡 2 GOALS 2H",
                league_name, country,
                home_name, away_name,
                minute, status_short,
                score, home_sot, away_sot,
                "Over 1.5 goals (2nd Half)"
            )
            send_telegram(msg)

    if status_short == "HT" and score in ("1 - 0", "0 - 1") and sot_total >= 4:
        if not already_sent(fixture_id, "OVER_2_5_GOALS"):
            msg = build_premium_message(
                "🔵 OVER 2.5 GOALS",
                league_name, country,
                home_name, away_name,
                minute, status_short,
                score, home_sot, away_sot,
                "Over 2.5 goals (Full Time)"
            )
            send_telegram(msg)

    if 50 <= minute <= 70 and score == "1 - 1" and sot_total >= 6:
        if not already_sent(fixture_id, "GOAL_PUSH_2H"):
            msg = build_premium_message(
                "🟠 GOAL PUSH 2H",
                league_name, country,
                home_name, away_name,
                minute, status_short,
                score, home_sot, away_sot,
                "Over 0.5 goals (2nd Half)"
            )
            send_telegram(msg)

    if 70 <= minute <= 85 and abs(home_goals - away_goals) == 1 and sot_total >= 8:
        if not already_sent(fixture_id, "LATE_GOAL"):
            msg = build_premium_message(
                "🔴 LATE GOAL",
                league_name, country,
                home_name, away_name,
                minute, status_short,
                score, home_sot, away_sot,
                "1 more goal"
            )
            send_telegram(msg)

    if 85 <= minute <= 88 and sot_total >= 10:
        if not already_sent(fixture_id, "LAST_MINUTE_GOAL"):
            msg = build_premium_message(
                "🟣 LAST MINUTE GOAL",
                league_name, country,
                home_name, away_name,
                minute, status_short,
                score, home_sot, away_sot,
                "Goal (Last Minutes)"
            )
            send_telegram(msg)


def main():
    ensure_data_dir()
    print("Live Alert Engine v5-test Started (Startup Telegram test enabled)")
    validate_env()

    send_telegram(
        "✅ Live Alert Engine is running on Railway.\n"
        "Startup test message.\n"
        "If you received this, Telegram delivery is OK."
    )

    load_sent_alerts()
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
