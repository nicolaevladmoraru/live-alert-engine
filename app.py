import os
import time
import json
import re
import requests
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

API_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

API_BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

DATA_DIR = os.getenv("DATA_DIR", "/data").strip() or "/data"

LEAGUE_CACHE_FILE = os.path.join(DATA_DIR, "league_cache.json")
SENT_ALERTS_FILE = os.path.join(DATA_DIR, "sent_alerts.json")
ALERT_LOG_FILE = os.path.join(DATA_DIR, "alert_log.json")
DAILY_STATS_FILE = os.path.join(DATA_DIR, "daily_stats.json")

LEAGUE_CACHE_TTL_SEC = 24 * 60 * 60
STATS_COOLDOWN_SEC = 60

REPORT_TZ = os.getenv("REPORT_TZ", "Europe/London").strip() or "Europe/London"
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "22").strip() or "22")
REPORT_MINUTE = int(os.getenv("REPORT_MINUTE", "0").strip() or "0")

FINISH_CHECK_COOLDOWN_SEC = 300
HT_CHECK_COOLDOWN_SEC = 60

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

ALERT_META = {
    "GOAL_1H": {"title": "🟢 GOAL 1H", "pick": "Over 0.5 goals (1st Half)"},
    "TWO_GOALS_2H": {"title": "🟡 2 GOALS 2H", "pick": "Over 1.5 goals (2nd Half)"},
    "OVER_2_5_GOALS": {"title": "🔵 OVER 2.5 GOALS", "pick": "Over 2.5 goals (Full Time)"},
    "GOAL_PUSH_2H": {"title": "🟠 GOAL PUSH 2H", "pick": "Over 0.5 goals (2nd Half)"},
    "LATE_GOAL": {"title": "🔴 LATE GOAL", "pick": "1 more goal"},
    "LAST_MINUTE_GOAL": {"title": "🟣 LAST MINUTE GOAL", "pick": "Goal (Last Minutes)"},
}


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


def now_local():
    if ZoneInfo is None:
        return datetime.utcnow()
    try:
        return datetime.now(ZoneInfo(REPORT_TZ))
    except Exception:
        return datetime.utcnow()


def today_key():
    return now_local().date().isoformat()


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


def get_fixture_by_id(fixture_id: int):
    url = f"{API_BASE}/fixtures?id={fixture_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        data = r.json()
        resp = data.get("response", [])
        if isinstance(resp, list) and len(resp) > 0:
            return resp[0]
        return None
    except Exception as ex:
        print(f"Fixture lookup failed for {fixture_id}: {ex}")
        return None


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
    now_ts = time.time()
    cached = STATS_CACHE.get(fixture_id)
    if cached and (now_ts - cached["ts"] < STATS_COOLDOWN_SEC):
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

        STATS_CACHE[fixture_id] = {"home": home_sot, "away": away_sot, "ts": now_ts}
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
    now_ts = int(time.time())

    if isinstance(cache, dict):
        ts = _safe_int(cache.get("ts"), 0)
        ids = cache.get("ids", [])
        if ts > 0 and (now_ts - ts) < LEAGUE_CACHE_TTL_SEC and isinstance(ids, list) and ids:
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

        save_json_file(LEAGUE_CACHE_FILE, {"ts": now_ts, "ids": sorted(list(allowed_ids))})
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


def parse_score(score_str: str):
    try:
        s = (score_str or "").strip()
        if "-" not in s:
            return 0, 0
        left, right = s.split("-", 1)
        return _safe_int(left.strip(), 0), _safe_int(right.strip(), 0)
    except Exception:
        return 0, 0


def load_alert_log():
    payload = load_json_file(ALERT_LOG_FILE)
    if isinstance(payload, dict):
        if "alerts" not in payload or not isinstance(payload.get("alerts"), dict):
            payload["alerts"] = {}
        return payload
    return {"alerts": {}}


def save_alert_log(log_data: dict):
    save_json_file(ALERT_LOG_FILE, log_data)


def load_daily_stats():
    payload = load_json_file(DAILY_STATS_FILE)
    if isinstance(payload, dict):
        return payload
    return {
        "date": today_key(),
        "matches_scanned": 0,
        "report_sent_date": ""
    }


def save_daily_stats(stats: dict):
    save_json_file(DAILY_STATS_FILE, stats)


def reset_daily_stats_if_needed(stats: dict):
    tk = today_key()
    if stats.get("date") != tk:
        stats["date"] = tk
        stats["matches_scanned"] = 0
        stats["report_sent_date"] = ""
        save_daily_stats(stats)


def register_alert_send(alert_code: str, fixture_id: int, minute: int, status_short: str,
                        score_str: str, league_name: str, country: str, home: str, away: str):
    log_data = load_alert_log()
    alerts = log_data.get("alerts", {})
    key = f"{fixture_id}|{alert_code}"

    hg, ag = parse_score(score_str)
    total_goals_at_send = hg + ag

    alerts[key] = {
        "fixture_id": fixture_id,
        "alert_code": alert_code,
        "sent_ts": int(time.time()),
        "sent_date": today_key(),
        "minute": minute,
        "status_short": status_short,
        "score_at_send": score_str,
        "total_goals_at_send": total_goals_at_send,
        "league_name": league_name,
        "country": country,
        "home": home,
        "away": away,
        "resolved": False,
        "result": "PENDING",
        "last_finish_check_ts": 0,
        "last_ht_check_ts": 0
    }

    log_data["alerts"] = alerts
    save_alert_log(log_data)


def evaluate_alert_outcome_ft(alert: dict, fixture_obj: dict):
    fixture = fixture_obj.get("fixture", {}) or {}
    status = fixture.get("status", {}) or {}
    status_short = (status.get("short") or "").strip().upper()

    if status_short not in ("FT", "AET", "PEN"):
        return None

    goals = fixture_obj.get("goals", {}) or {}
    ft_home = _safe_int(goals.get("home"), 0)
    ft_away = _safe_int(goals.get("away"), 0)
    ft_total = ft_home + ft_away

    score_obj = fixture_obj.get("score", {}) or {}
    halftime = score_obj.get("halftime", {}) or {}
    ht_home = _safe_int(halftime.get("home"), 0)
    ht_away = _safe_int(halftime.get("away"), 0)
    ht_total = ht_home + ht_away

    alert_code = alert.get("alert_code")

    if alert_code == "TWO_GOALS_2H":
        sh_goals = max(0, ft_total - ht_total)
        return "WIN" if sh_goals >= 2 else "LOSE"

    if alert_code == "OVER_2_5_GOALS":
        return "WIN" if ft_total >= 3 else "LOSE"

    if alert_code == "GOAL_PUSH_2H":
        sh_goals = max(0, ft_total - ht_total)
        return "WIN" if sh_goals >= 1 else "LOSE"

    if alert_code in ("LATE_GOAL", "LAST_MINUTE_GOAL"):
        base_total = _safe_int(alert.get("total_goals_at_send"), 0)
        return "WIN" if ft_total > base_total else "LOSE"

    return None


def resolve_goal1h_at_ht_if_possible():
    log_data = load_alert_log()
    alerts = log_data.get("alerts", {})
    if not isinstance(alerts, dict) or not alerts:
        return

    now_ts = int(time.time())
    changed = False

    for a in alerts.values():
        if not isinstance(a, dict):
            continue

        if a.get("resolved") is True:
            continue

        if a.get("alert_code") != "GOAL_1H":
            continue

        last_ht = _safe_int(a.get("last_ht_check_ts"), 0)
        if last_ht > 0 and (now_ts - last_ht) < HT_CHECK_COOLDOWN_SEC:
            continue

        fixture_id = _safe_int(a.get("fixture_id"), 0)
        if fixture_id <= 0:
            continue

        a["last_ht_check_ts"] = now_ts
        fixture_obj = get_fixture_by_id(fixture_id)
        if not fixture_obj:
            changed = True
            continue

        fixture = fixture_obj.get("fixture", {}) or {}
        status = fixture.get("status", {}) or {}
        status_short = (status.get("short") or "").strip().upper()

        if status_short != "HT":
            changed = True
            continue

        score_obj = fixture_obj.get("score", {}) or {}
        halftime = score_obj.get("halftime", {}) or {}
        ht_home = _safe_int(halftime.get("home"), 0)
        ht_away = _safe_int(halftime.get("away"), 0)
        ht_total = ht_home + ht_away

        a["resolved"] = True
        a["result"] = "WIN" if ht_total >= 1 else "LOSE"
        changed = True

    if changed:
        log_data["alerts"] = alerts
        save_alert_log(log_data)


def resolve_other_alerts_at_ft():
    log_data = load_alert_log()
    alerts = log_data.get("alerts", {})
    if not isinstance(alerts, dict) or not alerts:
        return

    now_ts = int(time.time())
    changed = False

    for a in alerts.values():
        if not isinstance(a, dict):
            continue

        if a.get("resolved") is True:
            continue

        if a.get("alert_code") == "GOAL_1H":
            continue

        last_check = _safe_int(a.get("last_finish_check_ts"), 0)
        if last_check > 0 and (now_ts - last_check) < FINISH_CHECK_COOLDOWN_SEC:
            continue

        fixture_id = _safe_int(a.get("fixture_id"), 0)
        if fixture_id <= 0:
            continue

        a["last_finish_check_ts"] = now_ts
        fixture_obj = get_fixture_by_id(fixture_id)
        if not fixture_obj:
            changed = True
            continue

        outcome = evaluate_alert_outcome_ft(a, fixture_obj)
        if outcome in ("WIN", "LOSE"):
            a["resolved"] = True
            a["result"] = outcome
            changed = True
        else:
            changed = True

    if changed:
        log_data["alerts"] = alerts
        save_alert_log(log_data)


def compute_today_breakdown_from_log():
    log_data = load_alert_log()
    alerts = log_data.get("alerts", {})
    today_str = today_key()

    per_code = {}
    for code in ALERT_META.keys():
        per_code[code] = {"total": 0, "win": 0, "lose": 0, "pending": 0}

    overall = {"win": 0, "lose": 0, "pending": 0, "total": 0}

    if not isinstance(alerts, dict):
        return per_code, overall

    for a in alerts.values():
        if not isinstance(a, dict):
            continue
        if a.get("sent_date") != today_str:
            continue

        code = a.get("alert_code")
        if code not in per_code:
            continue

        per_code[code]["total"] += 1
        overall["total"] += 1

        res = (a.get("result") or "PENDING").upper()
        if a.get("resolved") is True and res == "WIN":
            per_code[code]["win"] += 1
            overall["win"] += 1
        elif a.get("resolved") is True and res == "LOSE":
            per_code[code]["lose"] += 1
            overall["lose"] += 1
        else:
            per_code[code]["pending"] += 1
            overall["pending"] += 1

    return per_code, overall


def format_win_rate(win: int, lose: int):
    denom = win + lose
    if denom <= 0:
        return None
    return (win / denom) * 100.0


def maybe_send_daily_report():
    stats = load_daily_stats()
    reset_daily_stats_if_needed(stats)

    now_dt = now_local()
    if now_dt.hour != REPORT_HOUR or now_dt.minute != REPORT_MINUTE:
        return

    today_str = today_key()
    if stats.get("report_sent_date") == today_str:
        return

    per_code, overall = compute_today_breakdown_from_log()

    lines = []
    lines.append("📊 DAILY REPORT")
    lines.append("")
    lines.append(f"Date: {today_str}")
    lines.append(f"Matches scanned: {int(stats.get('matches_scanned', 0))}")
    lines.append(f"Alerts sent: {int(overall.get('total', 0))}")
    lines.append("")
    lines.append("Alerts breakdown (Win/Total):")

    for code, meta in ALERT_META.items():
        w = int(per_code[code]["win"])
        l = int(per_code[code]["lose"])
        t = int(per_code[code]["total"])

        pct = format_win_rate(w, l)
        if pct is None:
            pct_str = "--"
        else:
            pct_str = f"{pct:.0f}%"

        lines.append(f"{meta['title']}: {w}/{t} ({pct_str})")

    lines.append("")
    lines.append("Results (All alerts):")
    lines.append(f"✅ Wins: {int(overall.get('win', 0))}")
    lines.append(f"❌ Losses: {int(overall.get('lose', 0))}")
    lines.append(f"⏳ Pending: {int(overall.get('pending', 0))}")

    overall_pct = format_win_rate(int(overall.get("win", 0)), int(overall.get("lose", 0)))
    if overall_pct is not None:
        lines.append("")
        lines.append(f"🎯 Winrate: {overall_pct:.0f}%")

    msg = "\n".join(lines)
    if send_telegram(msg):
        stats["report_sent_date"] = today_str
        save_daily_stats(stats)


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
        code = "GOAL_1H"
        if not already_sent(fixture_id, code):
            msg = build_premium_message(
                ALERT_META[code]["title"],
                league_name, country,
                home_name, away_name,
                minute, status_short,
                score, home_sot, away_sot,
                ALERT_META[code]["pick"]
            )
            if send_telegram(msg):
                register_alert_send(code, fixture_id, minute, status_short, score, league_name, country, home_name, away_name)

    if status_short == "HT" and score == "0 - 0" and sot_total >= 5:
        code = "TWO_GOALS_2H"
        if not already_sent(fixture_id, code):
            msg = build_premium_message(
                ALERT_META[code]["title"],
                league_name, country,
                home_name, away_name,
                minute, status_short,
                score, home_sot, away_sot,
                ALERT_META[code]["pick"]
            )
            if send_telegram(msg):
                register_alert_send(code, fixture_id, minute, status_short, score, league_name, country, home_name, away_name)

    if status_short == "HT" and score in ("1 - 0", "0 - 1") and sot_total >= 4:
        code = "OVER_2_5_GOALS"
        if not already_sent(fixture_id, code):
            msg = build_premium_message(
                ALERT_META[code]["title"],
                league_name, country,
                home_name, away_name,
                minute, status_short,
                score, home_sot, away_sot,
                ALERT_META[code]["pick"]
            )
            if send_telegram(msg):
                register_alert_send(code, fixture_id, minute, status_short, score, league_name, country, home_name, away_name)

    if 50 <= minute <= 70 and score == "1 - 1" and sot_total >= 6:
        code = "GOAL_PUSH_2H"
        if not already_sent(fixture_id, code):
            msg = build_premium_message(
                ALERT_META[code]["title"],
                league_name, country,
                home_name, away_name,
                minute, status_short,
                score, home_sot, away_sot,
                ALERT_META[code]["pick"]
            )
            if send_telegram(msg):
                register_alert_send(code, fixture_id, minute, status_short, score, league_name, country, home_name, away_name)

    if 70 <= minute <= 85 and abs(home_goals - away_goals) == 1 and sot_total >= 8:
        code = "LATE_GOAL"
        if not already_sent(fixture_id, code):
            msg = build_premium_message(
                ALERT_META[code]["title"],
                league_name, country,
                home_name, away_name,
                minute, status_short,
                score, home_sot, away_sot,
                ALERT_META[code]["pick"]
            )
            if send_telegram(msg):
                register_alert_send(code, fixture_id, minute, status_short, score, league_name, country, home_name, away_name)

    if 85 <= minute <= 88 and sot_total >= 10:
        code = "LAST_MINUTE_GOAL"
        if not already_sent(fixture_id, code):
            msg = build_premium_message(
                ALERT_META[code]["title"],
                league_name, country,
                home_name, away_name,
                minute, status_short,
                score, home_sot, away_sot,
                ALERT_META[code]["pick"]
            )
            if send_telegram(msg):
                register_alert_send(code, fixture_id, minute, status_short, score, league_name, country, home_name, away_name)


def main():
    ensure_data_dir()
    print("Live Alert Engine v8 Started (Daily Report Win/Total per alert)")
    validate_env()

    load_sent_alerts()
    allowed_league_ids = resolve_allowed_league_ids()
    print(f"Allowed league IDs active: {len(allowed_league_ids)}")

    stats = load_daily_stats()
    reset_daily_stats_if_needed(stats)

    while True:
        stats = load_daily_stats()
        reset_daily_stats_if_needed(stats)

        matches = get_live_fixtures()
        stats["matches_scanned"] = int(stats.get("matches_scanned", 0)) + int(len(matches))
        save_daily_stats(stats)

        for m in matches:
            try:
                check_alerts_for_match(m, allowed_league_ids)
            except Exception as ex:
                print(f"Alert check error: {ex}")

        try:
            resolve_goal1h_at_ht_if_possible()
        except Exception as ex:
            print(f"HT settle error: {ex}")

        try:
            resolve_other_alerts_at_ft()
        except Exception as ex:
            print(f"FT settle error: {ex}")

        try:
            maybe_send_daily_report()
        except Exception as ex:
            print(f"Daily report error: {ex}")

        time.sleep(60)


if __name__ == "__main__":
    main()
