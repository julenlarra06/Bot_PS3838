import os
import base64
import requests
import time
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
from rapidfuzz import fuzz

# ======================================================
#   CONFIGURACIÓN
# ======================================================

load_dotenv()

USERNAME = os.getenv("PS3838_USERNAME")
PASSWORD = os.getenv("PS3838_PASSWORD")

BASE_URL = "https://api.ps3838.com"

# ➜ modificar fácilmente desde aquí
min_value = 0.0          # % mínimo de value requerido
bankroll = 500           # banca inicial

# ======================================================
#   AUTENTICACIÓN
# ======================================================

if not USERNAME or not PASSWORD:
    raise ValueError("PS3838 credentials missing in .env")

session = requests.Session()
encoded = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()

session.headers.update({
    "Authorization": f"Basic {encoded}",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0"
})

# ======================================================
#   FETCH
# ======================================================

def fetch(path, params=None):
    url = BASE_URL + path
    try:
        r = session.get(url, params=params, timeout=10)
    except Exception as e:
        print(f"[ERROR] Connection: {e}")
        return None

    ctype = r.headers.get("Content-Type", "")
    if "json" not in ctype.lower():
        print(f"[ERROR] Non-JSON returned from {url}")
        print(r.text[:300])
        return None

    try:
        return r.json()
    except Exception:
        print(f"[ERROR] JSON parse failed from {url}")
        print(r.text[:300])
        return None

# ======================================================
#   HELPERS
# ======================================================

BLACKLIST_WORDS = [
    "corners","bookings","cards","card",
    "shots","saves","player","props","prop",
    "race","special","specials","goalscorer",
    "correct score","first to score","penalty"
]

def is_special_league(name):
    lname = name.lower()
    return any(w in lname for w in BLACKLIST_WORDS)

def parse_date(date_raw):
    year = datetime.now(timezone.utc).year
    return datetime.strptime(f"{date_raw} {year}", "%d %b %Y").replace(tzinfo=timezone.utc)

SPORT_MAP = {
    "baseball": 3,
    "basketball": 4,
    "boxing": 6,
    "football": 15,
    "hockey": 19,
    "mixed martial arts": 22,
    "soccer": 29,
    "tennis": 33,
    "volleyball": 34
}

def get_sport_id(sport_name):
    return SPORT_MAP.get(sport_name.lower().strip())

# ======================================================
#   STAKE CALCULATOR
# ======================================================

def calculate_stake(odds, value_real, bankroll=500):
    edge = value_real / 100

    kelly = edge / (odds - 1)
    kelly_fraction = kelly * 0.25

    stake = bankroll * kelly_fraction
    stake /= odds

    stake = min(stake, bankroll * 0.02)
    stake = max(stake, 5)

    max_stake_profit = 30 / (odds - 1)
    stake = min(stake, max_stake_profit)

    return round(stake, 2)

# ======================================================
#   COMPROBAR SI EVENT_ID EXISTE EN ODDS
# ======================================================

def event_has_odds(sport_id, event_id):
    odds_raw = fetch("/v3/odds", params={"sportId": sport_id, "oddsFormat": "DECIMAL"})
    if not odds_raw or "leagues" not in odds_raw:
        return False

    for lg in odds_raw["leagues"]:
        for ev in lg.get("events", []):
            if ev.get("id") == event_id:
                return True

    return False

# ======================================================
#   BUSCADOR LIVE CON FUZY
# ======================================================

def search_event_live(sport_input, home_input, away_input, date_input):
    print("\n=== BUSCANDO EVENTO EN API (LIVE FIXTURES) ===\n")

    sport_id = get_sport_id(sport_input)
    if not sport_id:
        print("❌ Deporte no reconocido.")
        return None

    fixtures_raw = fetch("/v3/fixtures", params={"sportId": sport_id})
    if not fixtures_raw or "league" not in fixtures_raw:
        print("❌ No fixtures recibidos.")
        return None

    leagues = fixtures_raw["league"]
    target_date = parse_date(date_input)

    candidates = []

    for lg in leagues:
        if is_special_league(lg["name"]):
            continue

        for ev in lg["events"]:
            try:
                ev_date = datetime.fromisoformat(ev["starts"].replace("Z","+00:00"))
            except:
                continue

            if ev_date.date() != target_date.date():
                continue

            sh = fuzz.partial_ratio(home_input.lower(), ev["home"].lower())
            sa = fuzz.partial_ratio(away_input.lower(), ev["away"].lower())
            if sh < 60 or sa < 60:
                continue

            score = sh + sa
            candidates.append((score, lg, ev, sh, sa))

    if not candidates:
        print("❌ No se encontraron candidatos.")
        return None

    print("\n=== CANDIDATOS DETECTADOS ===")
    for score, lg, ev, sh, sa in candidates:
        print(f"ID={ev['id']} | {ev['home']} vs {ev['away']} | score={score} | starts={ev['starts']} | league={lg['name']}")

    candidates_with_odds = [(s,l,e,sh,sa) for (s,l,e,sh,sa) in candidates if event_has_odds(sport_id, e["id"])]

    if candidates_with_odds:
        best = max(candidates_with_odds, key=lambda x: x[0])
    else:
        best = max(candidates, key=lambda x: x[0])

    score, lg, ev, sh, sa = best

    print("\n=== EVENTO SELECCIONADO ===")
    print(f"ID: {ev['id']}")
    print(f"Equipos: {ev['home']} - {ev['away']}")
    print(f"Liga: {lg['name']}")
    print(f"Fecha: {ev['starts']}")

    return {
        "sportId": sport_id,
        "eventId": ev["id"],
        "leagueId": lg["id"],
        "home": ev["home"],
        "away": ev["away"],
        "starts": ev["starts"]
    }

# ======================================================
#   OBTENER ODDS POR SPORT
# ======================================================

def get_event_odds(sport_id, event_id):
    for _ in range(3):
        odds_raw = fetch("/v3/odds", params={"sportId": sport_id, "oddsFormat": "DECIMAL"})
        if odds_raw and "leagues" in odds_raw:
            for lg in odds_raw["leagues"]:
                for ev in lg.get("events", []):
                    if ev["id"] == event_id:
                        return ev
        time.sleep(0.4)

    print("❌ No se encontraron odds.")
    return None

# ======================================================
#   EXTRAER MERCADO
# ======================================================

def extract_market_odds(event_odds, market, line, period):
    market = market.lower()

    period_obj = None
    for p in event_odds["periods"]:
        if p["number"] == period:
            period_obj = p
            break

    if not period_obj:
        print(f"⚠ Periodo {period} no existe.")
        print("Periodos disponibles:", [p["number"] for p in event_odds["periods"]])
        return None

    ml = period_obj.get("moneyline", {})
    if market == "1": return ml.get("home")
    if market == "2": return ml.get("away")
    if market == "x": return ml.get("draw")

    if market in ["over","under"]:
        for t in period_obj.get("totals", []):
            if float(t["points"]) == float(line):
                return t.get(market)

    if market in ["spread_home","spread_away"]:
        for s in period_obj.get("spreads", []):
            if float(s["hdp"]) == float(line):
                return s.get("home" if market=="spread_home" else "away")

    if market in ["team_over_home","team_under_home","team_over_away","team_under_away"]:
        tt_all = period_obj.get("teamTotal", {})
        tt = tt_all["home"] if "home" in market else tt_all["away"]

        if float(tt["points"]) != float(line): return None
        return tt["over"] if "over" in market else tt["under"]

    return None

# ======================================================
#   COMPARAR CUOTAS + VALUE REAL + STAKE
# ======================================================

def compare_odds(event_info, market, line, period, bb_odds, bb_value):
    odds = get_event_odds(event_info["sportId"], event_info["eventId"])
    if not odds:
        return

    current = extract_market_odds(odds, market, line, period)
    if current is None:
        print("⚠ Mercado/línea/período no disponible.\n")
        return

    print("\nCuota Pinnacle actual:", current)

    # Calcular value real
    value_real = ((current / bb_odds) - 1) * 100
    value_real = round(value_real, 2)

    print(f"Value BB: {bb_value}%")
    print(f"Value real actual: {value_real}%")

    # Descartar por min_value
    if value_real < min_value:
        print(f"❌ Value inferior al mínimo ({min_value}%). Apuesta descartada.")
        return

    # Calcular stake
    stake = calculate_stake(current, value_real, bankroll)

    print("\n=== APUESTA VÁLIDA ===")
    print(f"Stake recomendado: {stake}€")

# ======================================================
#   MAIN
# ======================================================

def main():
    print("Introduce: Sport, Home, Away, Date, Market, Line, Period, BB_Odds, BB_Value\n")
    raw = input("> ")

    try:
        sport, home, away, date_raw, market, line, period_num, bb_odds, bb_value = [
            x.strip() for x in raw.split(",")
        ]
        line = float(line)
        period_num = int(period_num)
        bb_odds = float(bb_odds)
        bb_value = float(bb_value)
    except:
        print("❌ Formato incorrecto.")
        return

    match = search_event_live(sport, home, away, date_raw)
    if match:
        compare_odds(match, market, line, period_num, bb_odds, bb_value)

# ======================================================

if __name__ == "__main__":
    main()