import os
import time
import json
import requests
from datetime import datetime

API_KEY = os.getenv("API_FOOTBALL_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HEADERS = {"x-apisports-key": API_KEY}

TRACKED_MATCHES = {}
SENT_ALERTS = set()

DATA_DIR = "/data"
SENT_FILE = f"{DATA_DIR}/sent_alerts.json"

API_BASE = "https://v3.football.api-sports.io"

ALLOWED_LEAGUES = [
("Egypt","Premier League"),
("USA","Major League Soccer"),
("Belgium","Jupiler Pro League"),
("Spain","La Liga"),
("Netherlands","Eredivisie"),
("France","Ligue 1"),
("Spain","Segunda División"),
("Argentina","Liga Profesional Argentina"),
("Portugal","Primeira Liga"),
("Chile","Primera División"),
("Colombia","Primera A"),
("Paraguay","Division Profesional - Apertura"),
("Ecuador","Liga Pro"),
("Switzerland","Super League"),
("Turkey","Süper Lig"),
("Turkey","1. Lig"),
("South-Africa","Premier Soccer League"),
("Romania","Liga I"),
("Poland","Ekstraklasa"),
("India","Indian Super League"),
("Croatia","HNL"),
("Italy","Serie B"),
("Italy","Serie A"),
("Greece","Super League 1"),
("Serbia","Super Liga"),
("Germany","Bundesliga"),
("Czech-Republic","Czech Liga"),
("Peru","Primera División"),
("Brazil","Carioca - 1"),
("Ireland","Premier Division"),
("France","Ligue 2"),
("England","Premier League"),
("England","Championship"),
("England","League One"),
("England","League Two"),
("England","FA Cup"),
("Netherlands","KNVB Beker"),
("Scotland","Premiership"),
("France","Coupe de France"),
("Italy","Coppa Italia"),
("Spain","Copa del Rey"),
("Saudi-Arabia","Pro League")
]


def load_sent():
    global SENT_ALERTS
    try:
        with open(SENT_FILE) as f:
            SENT_ALERTS=set(json.load(f))
    except:
        SENT_ALERTS=set()


def save_sent():
    with open(SENT_FILE,"w") as f:
        json.dump(list(SENT_ALERTS),f)


def send_telegram(msg):

    url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    try:
        r=requests.post(url,data={"chat_id":TELEGRAM_CHAT_ID,"text":msg},timeout=10)
        return r.status_code==200
    except:
        return False


def live_matches():

    url=f"{API_BASE}/fixtures?live=all"

    try:
        r=requests.get(url,headers=HEADERS,timeout=20)
        return r.json()["response"]
    except:
        return []


def get_stats(fid):

    url=f"{API_BASE}/fixtures/statistics?fixture={fid}"

    try:
        r=requests.get(url,headers=HEADERS,timeout=20)
        data=r.json()["response"]

        h=0
        a=0

        for i in data[0]["statistics"]:
            if i["type"]=="Shots on Target":
                h=int(i["value"] or 0)

        for i in data[1]["statistics"]:
            if i["type"]=="Shots on Target":
                a=int(i["value"] or 0)

        return h,a

    except:
        return 0,0


def allow_league(country,name):

    for c,n in ALLOWED_LEAGUES:
        if c==country and n==name:
            return True

    return False


def already_sent(fid,code):

    key=f"{fid}_{code}"

    return key in SENT_ALERTS


def mark_sent(fid,code):

    key=f"{fid}_{code}"

    SENT_ALERTS.add(key)

    save_sent()


def goal1h(minute,score,sot):

    return 20<=minute<=30 and score=="0 - 0" and sot>=3


def two_goals_2h(status,score,sot):

    return status=="HT" and score=="0 - 0" and sot>=5


def over25(status,score,sot):

    return status=="HT" and score in ["1 - 0","0 - 1"] and sot>=4


def goal_push(minute,score,sot):

    return 50<=minute<=70 and score=="1 - 1" and sot>=6


def late_goal(minute,home,away,sot):

    return 70<=minute<=85 and abs(home-away)==1 and sot>=8


def last_minute(minute,sot):

    return 85<=minute<=88 and sot>=10


def evaluate(match):

    fixture=match["fixture"]
    league=match["league"]
    teams=match["teams"]
    goals=match["goals"]

    fid=fixture["id"]

    minute=fixture["status"]["elapsed"] or 0
    status=fixture["status"]["short"]

    country=league["country"]
    lname=league["name"]

    if not allow_league(country,lname):
        return

    home=teams["home"]["name"]
    away=teams["away"]["name"]

    hg=goals["home"] or 0
    ag=goals["away"] or 0

    score=f"{hg} - {ag}"

    if status in ["FT","AET","PEN","ET"]:
        if fid in TRACKED_MATCHES:
            del TRACKED_MATCHES[fid]
        return

    if minute>=18:
        TRACKED_MATCHES[fid]=True

    if fid not in TRACKED_MATCHES:
        return

    h,a=get_stats(fid)

    sot=h+a

    if goal1h(minute,score,sot):

        code="GOAL1H"

        if not already_sent(fid,code):

            msg=f"GOAL 1H\n{home} vs {away}\nMinute {minute}\nScore {score}\nSOT {h}-{a}\nPick Over 0.5 1H"

            if send_telegram(msg):
                mark_sent(fid,code)

    if two_goals_2h(status,score,sot):

        code="2GOALS2H"

        if not already_sent(fid,code):

            msg=f"2 GOALS 2H\n{home} vs {away}\nHT {score}\nSOT {h}-{a}\nPick Over 1.5 2H"

            if send_telegram(msg):
                mark_sent(fid,code)

    if over25(status,score,sot):

        code="OVER25"

        if not already_sent(fid,code):

            msg=f"OVER 2.5 GOALS\n{home} vs {away}\nHT {score}\nSOT {h}-{a}"

            if send_telegram(msg):
                mark_sent(fid,code)

    if goal_push(minute,score,sot):

        code="GOALPUSH"

        if not already_sent(fid,code):

            msg=f"GOAL PUSH 2H\n{home} vs {away}\nMinute {minute}\nScore {score}\nSOT {h}-{a}"

            if send_telegram(msg):
                mark_sent(fid,code)

    if late_goal(minute,hg,ag,sot):

        code="LATEGOAL"

        if not already_sent(fid,code):

            msg=f"LATE GOAL\n{home} vs {away}\nMinute {minute}\nScore {score}\nSOT {h}-{a}"

            if send_telegram(msg):
                mark_sent(fid,code)

    if last_minute(minute,sot):

        code="LASTMIN"

        if not already_sent(fid,code):

            msg=f"LAST MINUTE GOAL\n{home} vs {away}\nMinute {minute}\nScore {score}\nSOT {h}-{a}"

            if send_telegram(msg):
                mark_sent(fid,code)


def sleep_to_next_minute():

    now=time.time()

    delay=60-(now%60)

    time.sleep(delay)


def main():

    print("LIVE ENGINE v10 STARTED")

    load_sent()

    while True:

        matches=live_matches()

        for m in matches:
            evaluate(m)

        sleep_to_next_minute()


if __name__=="__main__":
    main()
