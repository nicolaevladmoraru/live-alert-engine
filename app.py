import os
import time
import requests

API_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

API_BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

ALLOWED_LEAGUES = {
    39,   # England - Premier League
    40,   # England - Championship
    41,   # England - League One
    42,   # England - League Two
    45,   # England - FA Cup
    140,  # Spain - La Liga
    141,  # Spain - Segunda División
    143,  # Spain - Copa del Rey
    135,  # Italy - Serie A
    136,  # Italy - Serie B
    137,  # Italy - Coppa Italia
    61,   # France - Ligue 1
    62,   # France - Ligue 2
    66,   # France - Coupe de France
    78,   # Germany - Bundesliga
    88,   # Netherlands - Eredivisie
    96,   # Netherlands - KNVB Beker
    94,   # Portugal - Primeira Liga
    203,  # Turkey - Süper Lig
    204,  # Turkey - 1. Lig
    179,  # Scotland - Premiership
    207,  # Switzerland - Super League
    210,  # Greece - Super League 1
    218,  # Czech Liga
    119,  # Poland - Ekstraklasa
    271,  # Romania - Liga I
    197,  # Croatia - HNL
    286,  # Serbia - Super Liga
    253,  # USA - Major League Soccer
}

SENT_ALERTS = set()

# Stats cache: fixture_id -> {"home": int, "away": int, "ts": float}
STATS_CACHE = {}

# Cooldown for stats per fixture (seconds)
STATS_COOLDOWN_SEC = 60


def _safe_int(v, default=0):
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


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
    # Fetch stats only when the match is in a relevant window or at HT.
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

        if not isinstance(resp, list) or len(resp) < 2:
            home_sot, away_sot = 0, 0
        else:
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
        # If the API fails, return cached values if available; otherwise zeros.
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


def check_alerts_for_match(match: dict):
    fixture = match.get("fixture", {}) or {}
    league = match.get("league", {}) or {}
    teams = match.get("teams", {}) or {}
    goals = match.get("goals", {}) or {}

    fixture_id = _safe_int(fixture.get("id"), 0)
    if fixture_id <= 0:
        return

    league_id = _safe_int(league.get("id"), 0)
    if league_id not in ALLOWED_LEAGUES:
        return

    status = fixture.get("status", {}) or {}
    status_short = (status.get("short") or "").strip().upper()

    minute = _safe_int(status.get("elapsed"), -1)

    # Skip matches that are not in relevant windows and not HT.
    if not should_fetch_stats(minute, status_short):
        return

    home_name = (teams.get("home", {}) or {}).get("name", "Home")
    away_name = (teams.get("away", {}) or {}).get("name", "Away")

    home_goals = _safe_int(goals.get("home"), 0)
    away_goals = _safe_int(goals.get("away"), 0)
    score = f"{home_goals} - {away_goals}"

    home_sot, away_sot = get_sot_cached(fixture_id)
    sot_total = home_sot + away_sot

    # Alert 1: GOAL 1H
    if 20 <= minute <= 30 and score == "0 - 0" and sot_total >= 3:
        if not already_sent(fixture_id, "GOAL_1H"):
            msg = (
                f"🟢 GOAL 1H\n"
                f"{home_name} vs {away_name}\n"
                f"Minute: {minute}\n"
                f"SOT: {home_sot} - {away_sot}"
            )
            send_telegram(msg)

    # Alert 2: 2 GOALS 2H (HT exact)
    if status_short == "HT" and score == "0 - 0" and sot_total >= 5:
        if not already_sent(fixture_id, "TWO_GOALS_2H"):
            msg = (
                f"🟡 2 GOALS 2H\n"
                f"{home_name} vs {away_name}\n"
                f"Status: HT\n"
                f"SOT: {home_sot} - {away_sot}"
            )
            send_telegram(msg)

    # Alert 3: OVER 2.5 GOALS (HT exact)
    if status_short == "HT" and score in ("1 - 0", "0 - 1") and sot_total >= 4:
        if not already_sent(fixture_id, "OVER_2_5_GOALS"):
            msg = (
                f"🔵 OVER 2.5 GOALS\n"
                f"{home_name} vs {away_name}\n"
                f"Status: HT\n"
                f"SOT: {home_sot} - {away_sot}"
            )
            send_telegram(msg)

    # Alert 4: GOAL PUSH 2H
    if 50 <= minute <= 70 and score == "1 - 1" and sot_total >= 6:
        if not already_sent(fixture_id, "GOAL_PUSH_2H"):
            msg = (
                f"🟠 GOAL PUSH 2H\n"
                f"{home_name} vs {away_name}\n"
                f"Minute: {minute}\n"
                f"SOT: {home_sot} - {away_sot}"
            )
            send_telegram(msg)

    # Alert 5: LATE GOAL
    if 70 <= minute <= 85 and abs(home_goals - away_goals) == 1 and sot_total >= 8:
        if not already_sent(fixture_id, "LATE_GOAL"):
            msg = (
                f"🔴 LATE GOAL\n"
                f"{home_name} vs {away_name}\n"
                f"Minute: {minute}\n"
                f"SOT: {home_sot} - {away_sot}"
            )
            send_telegram(msg)

    # Alert 6: LAST MINUTE GOAL
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
    print("Live Alert Engine v3 Started (Relevant windows + 60s stats cooldown)")
    validate_env()

    while True:
        matches = get_live_fixtures()

        for m in matches:
            try:
                check_alerts_for_match(m)
            except Exception as ex:
                print(f"Alert check error: {ex}")

        time.sleep(60)


if __name__ == "__main__":
    main()
