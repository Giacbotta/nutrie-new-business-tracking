"""How busy the Bounce luggage points are in Italian cities (board item E-93, second source).

Companion to radical_occupancy.py. Public GraphQL API, no login, no token, standard library only.

WHAT IT READS
  https://graphql.usebounce.com , query `stores(citySlug: "...", first: 250)`
  Per point: maxCapacity (what the site shows as "Current availability"), capacityStatus,
  reservationCount, reviewCount, rating, coordinates, opening hours, price.

WHY IT IS LIGHTER THAN RADICAL
  Radical answers one point and one time window per request, so a national round costs about
  2.800 requests. Bounce returns a whole city in one request: all of Italy is roughly 120.

WHAT reservationCount MEANS: SETTLED ON 23/09/2026
  Three readings a few hours apart answered it: maxCapacity never moves (500, 100, 50 ... on the
  same points), while reservationCount moves **both up and down** between readings (24 -> 25,
  66 -> 65, 242 -> 244). It is the luggage in the shop right now, not a running total. So Bounce
  reads like the other two: occupied = reservationCount, capacity = maxCapacity, free = the rest.

ROBOTS.TXT
  bounce.com/robots.txt disallows only /packages/location, *.md and llms.txt, none of which this
  script reads, and it says nothing about the API host. Lighter footing than Radical, where the
  API paths are disallowed.

COMMANDS (run from the repository root)
  python bounce_occupancy.py cities      # which Italian cities Bounce covers, and their slugs
  python bounce_occupancy.py scan        # one reading of every point in every covered city
  python bounce_occupancy.py scan pisa,venice
  python bounce_occupancy.py areas       # give every point a neighbourhood, once, and cache it
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
AREAS_CSV = os.path.join(DATA, "bounce-areas.csv")

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


# a torn row must not stop a round: the limit is raised and unreadable rows are skipped
csv.field_size_limit(10 ** 8)


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


NEAR_KM = 0.5          # a Radical point this close names the same neighbourhood
NOMINATIM_PER_RUN = 400  # new points geocoded in one run, at one request per second


def km(a, b, c, d):
    """Rough distance in km, good enough at city scale."""
    import math
    return math.dist(((a - c) * 111.0, (b - d) * 111.0 * math.cos(math.radians(a))), (0, 0))


def areas():
    """Each point gets a neighbourhood once and it is cached in bounce-areas.csv.

    Bounce shows an approximate position for every store and the API gives its coordinates, so the
    neighbourhood can be worked out offline. Two free steps, in order:
      1. the nearest Radical Storage point within 500 m lends its own area name (same tourist
         zones, no external service, instant);
      2. what is left goes to OpenStreetMap's Nominatim, one request per second as its policy asks.
    Only points missing from the cache are looked up, so a run after the first costs almost nothing."""
    cached = {r["spot_id"]: r for r in read_csv(AREAS_CSV)}
    points, seen = [], set()
    for r in read_csv(SPOTS_CSV):
        if r["spot_id"] not in seen and r["lat"]:
            seen.add(r["spot_id"])
            points.append(r)
    todo = [p for p in points if p["spot_id"] not in cached]
    print(f"{len(points)} points, {len(cached)} already placed, {len(todo)} to do")
    radical = [r for r in read_csv(os.path.join(DATA, "radical-points.csv")) if r.get("lat")]
    rows, left = [], []
    for p in todo:
        lat, lng = float(p["lat"]), float(p["lng"])
        near = min(((km(lat, lng, float(r["lat"]), float(r["lng"])), r) for r in radical
                    if abs(float(r["lat"]) - lat) < 0.02 and abs(float(r["lng"]) - lng) < 0.03),
                   key=lambda t: t[0], default=None)
        if near and near[0] <= NEAR_KM and near[1]["area"]:
            rows.append(dict(spot_id=p["spot_id"], city=p["city"], lat=lat, lng=lng,
                             area=near[1]["area"], source="radical"))
        else:
            left.append(p)
    print(f"  {len(rows)} from a Radical point nearby, {len(left)} to look up on OpenStreetMap")
    for p in left[:NOMINATIM_PER_RUN]:
        time.sleep(1.1)   # Nominatim asks for at most one request per second
        url = ("https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=16&addressdetails=1"
               f"&lat={p['lat']}&lon={p['lng']}")
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "nutrie-new-business-tracking (https://github.com/Giacbotta/nutrie-new-business-tracking)"})
            a = json.loads(urllib.request.urlopen(req, timeout=40).read()).get("address", {})
        except Exception:
            a = {}
        name = (a.get("neighbourhood") or a.get("quarter") or a.get("suburb") or a.get("city_district")
                or a.get("borough") or a.get("village") or a.get("town") or "")
        rows.append(dict(spot_id=p["spot_id"], city=p["city"], lat=p["lat"], lng=p["lng"],
                         area=name.lower().replace(" ", "-"), source="osm" if name else "unknown"))
    rows_to_csv(AREAS_CSV, ["spot_id", "city", "lat", "lng", "area", "source"],
                list(cached.values()) + rows, append=False)
    named = sum(1 for r in list(cached.values()) + rows if r["area"])
    print(f"{len(cached) + len(rows)} points placed, {named} with a neighbourhood -> {AREAS_CSV}")
    if len(left) > NOMINATIM_PER_RUN:
        print(f"  {len(left) - NOMINATIM_PER_RUN} left for the next run")


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
    """Occupancy by point and by city: luggage in the shop, against the declared capacity.

    For each point and day: the fullest hour of the day (peak), what was there at the last reading,
    and the rises of the hourly curve, which are luggage arriving."""
    area_of = {r["spot_id"]: r["area"] for r in read_csv(AREAS_CSV)}
    hours = collections.defaultdict(dict)      # (city, spot, day) -> hour -> (read_at, occupied)
    meta, caps = {}, collections.defaultdict(int)
    for r in read_csv(SPOTS_CSV):
        if r["reservations"] == "":
            continue
        key = (r["city"], r["spot_id"], r["day"])
        hour = int(r["hour"])
        seen = hours[key].get(hour)
        if not seen or r["read_at"] >= seen[0]:
            hours[key][hour] = (r["read_at"], int(r["reservations"]))
        meta[(r["city"], r["spot_id"])] = r["name"]
        caps[(r["city"], r["spot_id"])] = max(caps[(r["city"], r["spot_id"])], int(r["capacity"] or 0))
    rows = []
    for (city, spot, day), per_hour in sorted(hours.items()):
        seq = [per_hour[h][1] for h in sorted(per_hour)]
        cap = caps[(city, spot)]
        rows.append(dict(day=day, city=city, area=area_of.get(spot, ""), spot_id=spot,
                         name=meta[(city, spot)], capacity=cap, readings=len(seq),
                         peak=max(seq), last=seq[-1],
                         peak_pct=round(100 * max(seq) / cap, 1) if cap else "",
                         arrivals=seq[0] + sum(max(0, b - a) for a, b in zip(seq, seq[1:]))))
    rows_to_csv(DAILY_CSV, ["day", "city", "area", "spot_id", "name", "capacity", "readings",
                            "peak", "last", "peak_pct", "arrivals"], rows, append=False)
    agg = collections.defaultdict(lambda: dict(points=0, capacity=0, peak=0, last=0, arrivals=0))
    for r in rows:
        a = agg[(r["day"], r["city"])]
        a["points"] += 1
        for f in ("capacity", "peak", "last", "arrivals"):
            a[f] += r[f]
    out = [dict(day=d, city=c, points=a["points"], capacity=a["capacity"], peak=a["peak"],
                peak_pct=round(100 * a["peak"] / a["capacity"], 1) if a["capacity"] else "",
                last=a["last"], arrivals=a["arrivals"]) for (d, c), a in sorted(agg.items())]
    rows_to_csv(CITY_CSV, ["day", "city", "points", "capacity", "peak", "peak_pct", "last", "arrivals"],
                out, append=False)
    print(f"{len(rows)} point-days -> {DAILY_CSV}; {len(out)} city-days -> {CITY_CSV}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    only = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    {"cities": cities, "scan": lambda: scan(only), "areas": areas, "daily": daily}[cmd]()
