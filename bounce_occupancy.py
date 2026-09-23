"""How busy the Bounce luggage points are in Italian cities (board item E-93, second source).

Companion to radical_occupancy.py. Public GraphQL API, no login, no token, standard library only.

WHAT IT READS
  https://graphql.usebounce.com , query `stores(citySlug: "...", first: 250)`
  Per point: maxCapacity (what the site shows as "Current availability"), capacityStatus,
  reservationCount, reviewCount, rating, coordinates, opening hours, price.

WHY IT IS LIGHTER THAN RADICAL
  Radical answers one point and one time window per request, so a national round costs about
  2.800 requests. Bounce returns a whole city in one request: all of Italy is roughly 120.

WHAT reservationCount MEANS: NOT SETTLED YET (23/09/2026)
  The field is not used anywhere on Bounce's own site, so its window is unknown. The numbers are
  far too small to be lifetime totals (Venice 1.348 against 813 reviews on a single point), so it
  is some recent window. `daily` therefore writes both the level and the change since the previous
  reading, and only calls the change "deposits" once a few days of history say the series is
  monotonic inside a day. Until then, read the level, not the derived figure.

ROBOTS.TXT
  bounce.com/robots.txt disallows only /packages/location, *.md and llms.txt, none of which this
  script reads, and it says nothing about the API host. Lighter footing than Radical, where the
  API paths are disallowed.

COMMANDS (run from the repository root)
  python bounce_occupancy.py cities      # which Italian cities Bounce covers, and their slugs
  python bounce_occupancy.py scan        # one reading of every point in every covered city
  python bounce_occupancy.py scan pisa,venice
  python bounce_occupancy.py daily       # level and change per point, per area and per city
"""
import collections, csv, datetime as dt, json, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CITIES_CSV = os.path.join(DATA, "bounce-cities.csv")
SPOTS_CSV = os.path.join(DATA, "bounce-occupancy.csv")
DAILY_CSV = os.path.join(DATA, "bounce-daily.csv")
CITY_CSV = os.path.join(DATA, "bounce-daily-city.csv")

API = "https://graphql.usebounce.com"
UA = {"content-type": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
THREADS = 3
PAUSE = 0.3
PAGE = 250

# Same list as radical_occupancy.py, in Bounce's spelling (it uses English names for some cities).
CITIES = """agrigento alessandria ancona andria aosta arezzo ascoli-piceno asti avellino bari
barletta belluno benevento bergamo bologna bolzano brescia brindisi cagliari caltanissetta
campobasso carbonia carrara caserta catania catanzaro cesena chieti como cosenza cremona crotone
cuneo enna fermo ferrara florence foggia forli frosinone genoa gorizia grosseto imperia isernia
la-spezia laquila latina lecce lecco livorno lodi lucca macerata mantua massa matera messina milan
modena monza naples novara nuoro olbia oristano padova palermo parma pavia perugia pesaro pescara
piacenza pisa pistoia pordenone potenza prato ragusa ravenna reggio-calabria reggio-emilia rieti
rimini rome rovigo salerno sassari savona siena sondrio syracuse taranto tempio-pausania teramo
terni trani trapani trento treviso trieste turin udine urbino varese venice verbania vercelli
verona vibo-valentia vicenza viterbo""".split()

STORE_FIELDS = """id name slug maxCapacity capacityStatus reservationCount reviewCount rating
                  isOpen247 coordinates{latitude longitude} city{name country}"""


def ask(query):
    for _ in range(3):
        try:
            time.sleep(PAUSE)
            req = urllib.request.Request(API, data=json.dumps({"query": query}).encode(), headers=UA)
            return json.loads(urllib.request.urlopen(req, timeout=40).read())
        except Exception:
            time.sleep(3)
    return {}


def city_stores(slug):
    """Every point of a city, following the cursor when there are more than one page."""
    out, after = [], None
    while True:
        cursor = f',after:"{after}"' if after else ""
        d = ask('{stores(citySlug:"%s",first:%d%s){pageInfo{hasNextPage endCursor}edges{node{%s}}}}'
                % (slug, PAGE, cursor, STORE_FIELDS))
        spots = ((d.get("data") or {}).get("stores") or {})
        if not spots:
            return out                       # city_not_found, or the API refused
        out += [e["node"] for e in spots["edges"]]
        info = spots["pageInfo"]
        if not info["hasNextPage"]:
            return out
        after = info["endCursor"]


def rows_to_csv(path, fields, rows, append=True):
    os.makedirs(DATA, exist_ok=True)
    new = not (append and os.path.exists(path))
    with open(path, "a" if append else "w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new or not append:
            w.writeheader()
        w.writerows(rows)
    return path


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf8") as f:
        return list(csv.DictReader(f))


def rome_now():
    """Italian time, worked out here: the machine may sit in another zone and Windows has no tz
    database for zoneinfo. Summer time from the last Sunday of March to the last Sunday of October."""
    utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    def last_sunday(month):
        d = dt.date(utc.year, month + 1, 1) - dt.timedelta(days=1)
        return d - dt.timedelta(days=(d.weekday() + 1) % 7)
    summer = dt.datetime.combine(last_sunday(3), dt.time(1)) <= utc < dt.datetime.combine(last_sunday(10), dt.time(1))
    return utc + dt.timedelta(hours=2 if summer else 1)


def cities():
    found = []
    with ThreadPoolExecutor(THREADS) as ex:
        for slug, spots in zip(CITIES, ex.map(city_stores, CITIES)):
            italian = [s for s in spots if (s.get("city") or {}).get("country") == "IT"]
            if italian:
                found.append(dict(city=slug, name=italian[0]["city"]["name"], points=len(italian),
                                  capacity=sum(s["maxCapacity"] or 0 for s in italian)))
    found.sort(key=lambda r: -r["points"])
    rows_to_csv(CITIES_CSV, ["city", "name", "points", "capacity"], found, append=False)
    print(f"{len(found)} cities, {sum(r['points'] for r in found)} points -> {CITIES_CSV}")
    return [r["city"] for r in found]


def city_list():
    rows = read_csv(CITIES_CSV)
    return [r["city"] for r in rows] if rows else cities()


FIELDS = ["read_at", "city", "spot_id", "name", "slug", "lat", "lng", "capacity", "capacity_status",
          "reservations", "reviews", "rating", "open_24_7", "day", "hour"]


def scan(only=None):
    now = rome_now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    wanted = only or city_list()
    rows = []
    for city in wanted:
        spots = [s for s in city_stores(city) if (s.get("city") or {}).get("country") == "IT"]
        fresh = [dict(read_at=stamp, city=city, spot_id=s["id"], name=s["name"], slug=s.get("slug"),
                      lat=(s.get("coordinates") or {}).get("latitude"),
                      lng=(s.get("coordinates") or {}).get("longitude"),
                      capacity=s.get("maxCapacity"), capacity_status=s.get("capacityStatus"),
                      reservations=s.get("reservationCount"), reviews=s.get("reviewCount"),
                      rating=round(s["rating"], 2) if s.get("rating") else "",
                      open_24_7=s.get("isOpen247"), day=now.date().isoformat(), hour=now.hour)
                 for s in spots]
        rows_to_csv(SPOTS_CSV, FIELDS, fresh)
        rows += fresh
        print(f"  {city:20} {len(fresh):4} points, {sum(r['reservations'] or 0 for r in fresh):5} reservations", flush=True)
    print(f"{stamp}: {len(rows)} points in {len(wanted)} cities, "
          f"{sum(r['reservations'] or 0 for r in rows)} reservations -> {SPOTS_CSV}")


def daily():
    """Level and change of reservationCount, by point and by city.

    `reservations_end` is the last reading of the day, `change` how much it moved since the last
    reading of the day before. What the change counts depends on the window of the field, which is
    still unknown — see the note at the top of this file."""
    per_day = collections.defaultdict(dict)
    meta = {}
    for r in read_csv(SPOTS_CSV):
        if r["reservations"] == "":
            continue
        key = (r["city"], r["spot_id"])
        per_day[key][r["day"]] = (r["read_at"], int(r["reservations"]), r["capacity"], r["capacity_status"])
        meta[key] = r["name"]
    rows = []
    for (city, spot), days in sorted(per_day.items()):
        previous = None
        for day in sorted(days):
            _, level, cap, status = days[day]
            rows.append(dict(day=day, city=city, spot_id=spot, name=meta[(city, spot)], capacity=cap,
                             capacity_status=status, reservations_end=level,
                             change="" if previous is None else level - previous))
            previous = level
    rows_to_csv(DAILY_CSV, ["day", "city", "spot_id", "name", "capacity", "capacity_status",
                            "reservations_end", "change"], rows, append=False)
    agg = collections.defaultdict(lambda: dict(points=0, capacity=0, level=0, change=0, known=0))
    for r in rows:
        a = agg[(r["day"], r["city"])]
        a["points"] += 1
        a["capacity"] += int(r["capacity"] or 0)
        a["level"] += r["reservations_end"]
        if r["change"] != "":
            a["change"] += r["change"]
            a["known"] += 1
    out = [dict(day=d, city=c, points=a["points"], capacity=a["capacity"], reservations_end=a["level"],
                change="" if not a["known"] else a["change"]) for (d, c), a in sorted(agg.items())]
    rows_to_csv(CITY_CSV, ["day", "city", "points", "capacity", "reservations_end", "change"], out, append=False)
    print(f"{len(rows)} point-days -> {DAILY_CSV}; {len(out)} city-days -> {CITY_CSV}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    only = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    {"cities": cities, "scan": lambda: scan(only), "daily": daily}[cmd]()
