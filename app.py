import os
import time
import requests

API_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

API_BASE = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_KEY
}

# Allowed league IDs (whitelist). You can add/remove IDs anytime.
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

# In-memory anti-duplicate: fixture_id|alert_code
# Note: This resets if the container restarts. We can upgrade to persistent storage later.
SENT_ALERTS = set()


def _safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


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
            print(f"Telegram send failed: {resp.status_code} {resp.text[:200]}")
        return ok
    except Exception as ex:
        print(f"Telegram send exception: {ex}")
        return False


def get_live_fixtures():
    if not API_KEY:
        print("API_FOOTBALL_KEY is missing.")
        return []

    url = f"{API_BASE}/fixtures?live=all"
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        data = r.json()
        return data.get("response", [])
    except Exception as ex:
        print(f"Live fixtures request failed: {ex}")
        return []


def get_shots_on_target(fixture_id: int):
    url = f"{API_BASE}/fixtures/statistics?fixture={fixture_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        data = r.json()
        resp = data.get("response", [])

        # Expected shape: [ {team, statistics:[...]}, {team, statistics:[...]} ]
        if not isinstance(resp, list) or len(resp) < 2:
            return 0, 0

        def extract_sot(team_block):
            for item in team_block.get("statistics", []):
                if item.get("type") == "Shots on Target":
                    return _safe_int(item.get("value"), 0)
            return 0

        home_sot = extract_sot(resp[0])
        away_sot = extract_sot(resp[1])
        return home_sot, away_sot

    except Exception:
        return 0, 0


def already_sent(fixture_id: int, alert_code: str) -> bool:
    key = f"{fixture_id}|{alert_code}"
    if key in SENT_ALERTS:
        return True
    SENT_ALERTS.add(key)
    return False


def check_alerts_for_match(match: dict):
    fixture = match.get("fixture", {})
    league = match.get("league", {})
    teams = match.get("teams", {})
    goals = match.get("goals", {})

    fixture_id = _safe_int(fixture.get("id"), 0)
    if fixture_id <= 0:
        return

    league_id = _safe_int(league.get("id"), 0)
    if league_id not in ALLOWED_LEAGUES:
        return

    status = fixture.get("status", {})
    minute = status.get("elapsed")
    minute = _safe_int(minute, -1)
    if minute < 0:
        return

    home_name = (teams.get("home", {}) or {}).get("name", "Home")
    away_name = (teams.get("away", {}) or {}).get("name", "Away")

    home_goals = _safe_int(goals.get("home"), 0)
    away_goals = _safe_int(goals.get("away"), 0)
    score = f"{home_goals} - {away_goals}"

    home_sot, away_sot = get_shots_on_target(fixture_id)
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

    # Alert 2: 2 GOALS 2H (Excel version uses HT; here we approximate with minute==46)
    if minute == 46 and score == "0 - 0" and sot_total >= 5:
        if not already_sent(fixture_id, "TWO_GOALS_2H"):
            msg = (
                f"🟡 2 GOALS 2H\n"
                f"{home_name} vs {away_name}\n"
                f"HT trigger\n"
                f"SOT: {home_sot} - {away_sot}"
            )
            send_telegram(msg)

    # Alert 3: OVER 2.5 GOALS (HT)
    if minute == 46 and score in ("1 - 0", "0 - 1") and sot_total >= 4:
        if not already_sent(fixture_id, "OVER_2_5_GOALS"):
            msg = (
                f"🔵 OVER 2.5 GOALS\n"
                f"{home_name} vs {away_name}\n"
                f"HT trigger\n"
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


def main():
    print("Live Alert Engine v2 Started")
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
