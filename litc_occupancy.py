"""How many lockers are still free at Locker in the City, shop by shop (board item E-93/E-94).

Locker in the City is a real locker operator, 13 Italian shops in 8 cities. Like Stow Your Bags it
publishes prices and addresses but not how full it is; the booking flow does. The marketing site is
Astro, the booking app is Next.js, and the booking app reads availability through its own route, the
same one a visitor's browser calls once a city and dates are chosen:

  GET /api/stores/availability?cityId=1&citySlug=:citySlug&checkIn=:checkIn&checkOut=:checkOut

Each store in the reply carries `availability: {M, XL, XXL}` - the lockers still free per size for
that window, exactly the "Numero di lockers" the booking page shows - plus its id, slug and prices.
Occupancy is read against the most ever seen free for that shop and size (`peak_seen` in the daily
file), the same yardstick as Stow Your Bags, so occupied = capacity - free.

TWO THINGS THAT MUST BE RIGHT (learned 28/09/2026, after ISO dates gave a 500 for days):
  - checkIn/checkOut are DDMMYYYYHHMM, not ISO. A full day is DDMMYYYY0000 to DDMMYYYY2359. An ISO
    date answers 500 "Error fetching stores", which looked like their backend was down but was our
    format. Verified from plain Python (no browser, no cookies): the call answers 200.
  - cityId is ignored (roma answers the same with 0, 1, 17 or 999); citySlug is what matters. And
    Bologna's citySlug is the Spanish "bolonia", not "bologna" - see CITY_SLUG_ALIAS.

scan stays tolerant: if a city ever answers non-200 it writes a status row ("http-500" etc.) and
nothing wrong reaches the comparison, because the dashboard only ingests rows with status == "read",
as it does for Stow Your Bags.

ROBOTS.TXT: lockerinthecity.com has no robots.txt (nothing disallowed). The reading is one request
per city per round, a handful of calls an hour.

COMMANDS (run from the repository root)
  python litc_occupancy.py shops     # the Italian shops: store id, slug, city, lat/lng
  python litc_occupancy.py scan      # free lockers per shop and size, today
  python litc_occupancy.py daily     # the day's readings per shop, with the peak ever seen
"""
import collections, csv, datetime as dt, json, os, re, sys, time
import urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OTHER_CSV = os.path.join(DATA, "other-operators.csv")
SHOPS_CSV = os.path.join(DATA, "litc-shops.csv")
OCC_CSV = os.path.join(DATA, "litc-occupancy.csv")
DAILY_CSV = os.path.join(DATA, "litc-daily.csv")

SITE = "https://lockerinthecity.com"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
PAUSE = 1.2
SIZES = ("M", "XL", "XXL")

# citySlug is what the availability route keys on; cityId is ignored, so any value works.
# Bologna is the only Italian shop whose API citySlug differs from its page slug: it is "bolonia".
CITY_SLUG_ALIAS = {"bologna": "bolonia"}

SHOP_FIELDS = ["shop_id", "city", "city_slug", "slug", "name", "address", "lat", "lng", "url"]
OCC_FIELDS = ["read_at", "city", "city_slug", "shop_id", "slug", "name", "day", "hour", "weekday",
              "locker_type", "free", "status"]

csv.field_size_limit(10 ** 8)


def rome_now():
    """Italian time, worked out here: the machine may sit in another zone and Windows has no tz data."""
    utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    def last_sunday(month):
        d = dt.date(utc.year, month + 1, 1) - dt.timedelta(days=1)
        return d - dt.timedelta(days=(d.weekday() + 1) % 7)
    summer = dt.datetime.combine(last_sunday(3), dt.time(1)) <= utc < dt.datetime.combine(last_sunday(10), dt.time(1))
    return utc + dt.timedelta(hours=2 if summer else 1)


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf8") as f:
        return list(csv.DictReader(f))


def rows_to_csv(path, fields, rows, append=True):
    os.makedirs(DATA, exist_ok=True)
    new = not (append and os.path.exists(path))
    with open(path, "a" if append else "w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)
    return path


def get(url, pause=PAUSE):
    for attempt in range(3):
        try:
            time.sleep(pause)
            req = urllib.request.Request(url, headers=UA)
            resp = urllib.request.urlopen(req, timeout=40)
            return resp.getcode(), resp.read().decode("utf8", "replace")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf8", "replace")
            except Exception:
                pass
            if e.code == 429:
                time.sleep(20 * (attempt + 1))
                continue
            return e.code, body
        except Exception:
            time.sleep(3)
    return 0, ""


# ----------------------------------------------------------------------------- shops

def litc_shop_slugs():
    """The Italian shops from the census file: (city, slug) pairs."""
    rows = [r for r in read_csv(OTHER_CSV) if r.get("brand") == "lockerinthecity"]
    pairs = []
    for r in rows:
        m = re.search(r"/it/locker/italia/([a-z0-9-]+)/([a-z0-9-]+)/?$", r.get("url", ""))
        if m:
            pairs.append((m.group(1), m.group(2)))
    return sorted(set(pairs))


# A couple of shop pages do not carry lat/lng (Bologna, Verona), so the census would leave them
# blank and they would drop off the map. These are the known landmark coordinates, used as a
# fallback so every shop keeps a position across the Monday census.
FALLBACK_COORDS = {
    "stazione-centrale-portici-piazza-maggiore": ("44.5058", "11.3430"),  # Bologna Centrale
    "arena-casa-di-giulietta": ("45.4390", "10.9944"),                    # Verona, Arena
}


def shops():
    """Store id, slug, city, address and coordinates for each Italian shop, from its own page."""
    rows = []
    for city, slug in litc_shop_slugs():
        code, page = get(f"{SITE}/it/locker/italia/{city}/{slug}/")
        shop_id = re.search(r'"id"\s*:\s*(\d+)', page)
        name = re.search(r'"name"\s*:\s*"([^"]+)"', page)
        geo = re.search(r'"lat"\s*:\s*(-?\d+\.\d+)\s*,\s*"lng"\s*:\s*(-?\d+\.\d+)', page)
        addr = re.search(r"((?:Via|Viale|Piazza|Largo|Corso|Stazione)\s+[A-Za-zÀ-ú0-9.,' ]{3,50})", page)
        lat, lng = (geo.group(1), geo.group(2)) if geo else FALLBACK_COORDS.get(slug, ("", ""))
        rows.append(dict(shop_id=shop_id.group(1) if shop_id else "", city=city, city_slug=city,
                         slug=slug, name=(name.group(1).strip() if name else slug.replace("-", " ").title()),
                         address=addr.group(1).strip() if addr else "",
                         lat=lat, lng=lng,
                         url=f"{SITE}/it/locker/italia/{city}/{slug}/"))
        print(f"  {city:20} {slug:34} id={rows[-1]['shop_id'] or '-':>4}  {rows[-1]['lat'] or '-'}")
    rows_to_csv(SHOPS_CSV, SHOP_FIELDS, rows, append=False)
    missing = [r["slug"] for r in rows if not r["lat"]]
    print(f"{len(rows)} Locker in the City shops -> {SHOPS_CSV}" + (f"; senza coord: {missing}" if missing else "; tutte con coordinate"))
    return rows


def shop_list():
    return read_csv(SHOPS_CSV) or shops()


def number(x, default=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------------- availability

def city_availability(city_slug, check_in, check_out):
    """The city's stores for the window, or (status, None). status is 'ok'/'http-<code>'/'empty'.

    cityId is ignored by the route, so 1 is sent for every city; citySlug is what identifies it."""
    q = f"cityId=1&citySlug={city_slug}&checkIn={check_in}&checkOut={check_out}"
    code, body = get(f"{SITE}/api/stores/availability?{q}")
    if code != 200:
        return (f"http-{code}" if code else "no-response"), None
    try:
        payload = json.loads(body)
    except Exception:
        return "bad-json", None
    stores = payload if isinstance(payload, list) else (payload.get("stores") or payload.get("data") or [])
    stores = [s for s in stores if isinstance(s, dict)]
    return ("ok" if stores else "empty"), stores


# ----------------------------------------------------------------------------- scan

def scan():
    """One availability call per city, matched to the shops, free lockers per size written down.

    The window is the whole of today (DDMMYYYY0000 to DDMMYYYY2359): the reply gives the lockers free
    for that day, which shrinks as the day fills - the occupancy signal, read against the peak free
    ever seen for that shop and size in daily()."""
    now = rome_now()
    read_at = now.strftime("%Y-%m-%d %H:%M")
    weekday = now.strftime("%a")
    day, hour = now.strftime("%Y-%m-%d"), now.hour
    stamp = now.strftime("%d%m%Y")
    check_in, check_out = f"{stamp}0000", f"{stamp}2359"

    by_city = collections.defaultdict(list)
    for s in shop_list():
        by_city[s["city_slug"]].append(s)

    rows = []
    for city_slug, city_shops in sorted(by_city.items()):
        status, stores = city_availability(CITY_SLUG_ALIAS.get(city_slug, city_slug), check_in, check_out)
        if stores is None:
            for s in city_shops:                       # log the miss, keep the shop on record
                rows.append(dict(read_at=read_at, city=s["city"], city_slug=city_slug,
                                 shop_id=s["shop_id"], slug=s["slug"], name=s["name"], day=day,
                                 hour=hour, weekday=weekday, locker_type="", free="", status=status))
            print(f"  {city_slug:20} {status}")
            continue
        by_id = {str(st.get("id")): st for st in stores}
        by_slug = {st.get("slug"): st for st in stores}
        matched = 0
        for shop in city_shops:
            st = by_id.get(shop["shop_id"]) or by_slug.get(shop["slug"])
            if not st:
                rows.append(dict(read_at=read_at, city=shop["city"], city_slug=city_slug,
                                 shop_id=shop["shop_id"], slug=shop["slug"], name=shop["name"], day=day,
                                 hour=hour, weekday=weekday, locker_type="", free="", status="no-match"))
                continue
            matched += 1
            avail = st.get("availability") or {}
            for size in SIZES:
                if size in avail and avail[size] is not None:
                    rows.append(dict(read_at=read_at, city=shop["city"], city_slug=city_slug,
                                     shop_id=shop["shop_id"], slug=shop["slug"], name=shop["name"], day=day,
                                     hour=hour, weekday=weekday, locker_type=size,
                                     free=int(avail[size]), status="read"))
        print(f"  {city_slug:20} {len(stores)} stores, {matched} matched")
    rows_to_csv(OCC_CSV, OCC_FIELDS, rows, append=True)
    read = sum(1 for r in rows if r["status"] == "read")
    print(f"{read_at} slot {hour}: {read} size-readings, {len(rows)} rows -> {OCC_CSV}")
    return rows


# ----------------------------------------------------------------------------- daily

def daily():
    rows = [r for r in read_csv(OCC_CSV) if r.get("status") == "read" and r.get("free") != ""]
    peak = collections.defaultdict(int)
    for r in rows:
        peak[(r["shop_id"], r["locker_type"])] = max(peak[(r["shop_id"], r["locker_type"])], number(r["free"]))
    by_day = collections.defaultdict(list)
    for r in rows:
        by_day[(r["day"], r["shop_id"], r["locker_type"])].append(r)
    out = []
    for (d, shop_id, size), rs in sorted(by_day.items()):
        cap = peak[(shop_id, size)]
        least_free = min(number(r["free"]) for r in rs)
        out.append(dict(day=d, shop_id=shop_id, slug=rs[0]["slug"], name=rs[0]["name"], city=rs[0]["city"],
                        locker_type=size, peak_seen=cap, min_free=least_free,
                        max_occupied=cap - least_free, readings=len(rs)))
    rows_to_csv(DAILY_CSV, ["day", "shop_id", "slug", "name", "city", "locker_type", "peak_seen",
                            "min_free", "max_occupied", "readings"], out, append=False)
    print(f"{len(out)} shop-size-days -> {DAILY_CSV}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    {"shops": shops, "scan": scan, "daily": daily}.get(cmd, scan)()
