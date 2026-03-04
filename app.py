import requests
import time
import os

API_KEY = os.getenv("API_FOOTBALL_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

headers = {
    "x-apisports-key": API_KEY
}

sent_alerts = set()

def send_telegram(message):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    data = {
        "chat_id": CHAT_ID,
        "text": message
    }

    try:
        requests.post(url, data=data, timeout=10)
    except:
        pass


def parse_sot(sot_string):

    if sot_string is None:
        return 0,0

    sot_string = sot_string.replace("–","-")

    parts = sot_string.split("-")

    try:
        home = int(parts[0].strip())
        away = int(parts[1].strip())
    except:
        home = 0
        away = 0

    return home, away


def get_live_matches():

    url = "https://v3.football.api-sports.io/fixtures?live=all"

    try:

        r = requests.get(url, headers=headers, timeout=20)
        data = r.json()

        return data["response"]

    except:
        return []


def check_alerts(match):

    fixture_id = match["fixture"]["id"]

    minute = match["fixture"]["status"]["elapsed"]

    home = match["teams"]["home"]["name"]
    away = match["teams"]["away"]["name"]

    goals_home = match["goals"]["home"]
    goals_away = match["goals"]["away"]

    score = f"{goals_home} - {goals_away}"

    sot_home = 0
    sot_away = 0

    for stat in match["statistics"][0]["statistics"]:

        if stat["type"] == "Shots on Target":
            sot_home = stat["value"] or 0

    for stat in match["statistics"][1]["statistics"]:

        if stat["type"] == "Shots on Target":
            sot_away = stat["value"] or 0

    sot_total = sot_home + sot_away

    key = f"{fixture_id}_{minute}"

    if key in sent_alerts:
        return

    # GOAL 1H
    if 20 <= minute <= 30 and score == "0 - 0" and sot_total >= 3:

        msg = f"🟢 GOAL 1H\n{home} vs {away}\nMinute {minute}\nSOT {sot_home}-{sot_away}"

        send_telegram(msg)
        sent_alerts.add(key)

    # 2 GOALS 2H
    if minute == 46 and score == "0 - 0" and sot_total >= 5:

        msg = f"🟡 2 GOALS 2H\n{home} vs {away}\nSOT {sot_home}-{sot_away}"

        send_telegram(msg)
        sent_alerts.add(key)

    # LAST MINUTE GOAL
    if 85 <= minute <= 88 and sot_total >= 10:

        msg = f"🔴 LAST MINUTE GOAL\n{home} vs {away}\nMinute {minute}\nSOT {sot_home}-{sot_away}"

        send_telegram(msg)
        sent_alerts.add(key)


def main():

    print("Live Alert Engine Started")

    while True:

        matches = get_live_matches()

        for match in matches:

            try:
                check_alerts(match)
            except:
                pass

        time.sleep(60)


main()
