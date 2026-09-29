"""How full StowCity is, per shop and locker size (board item E-93/E-94).

StowCity is a small automatic locker operator with two shops, both where Nutrie actually competes:
Venice Old Town (Cannaregio, a few minutes from Nutrie's own point) and Mestre Central Station. Its
booking widget talks to a clean public API, and unlike the others it hands us the real capacity too:

  GET /api/v8/index.php?action=config&location=:loc
        -> lockers[].defaultAvailable = the size's total capacity, plus name, address, rating
  GET /api/v8/index.php?action=availability&date=YYYY-MM-DD&checkIn=HH:MM&checkOut=HH:MM
        &tierHours=4&requestedDays=1&location=:loc
        -> availability{small,medium,large} = lockers free for that window

So occupied = capacity - free with a *declared* capacity, not the peak-seen proxy the other locker
operators need. Two requests per shop per round, four calls an hour.

Coordinates are not in the API, so the two known addresses are pinned by hand (Cannaregio and Mestre
station) - enough for the shared neighbourhood match.

ROBOTS.TXT: stowcity.com has none. The reading is four calls an hour, the same a visitor makes.

COMMANDS (run from the repository root)
  python stowcity_occupancy.py shops   # the shops: id, city, address, coordinates, capacity per size
  python stowcity_occupancy.py scan    # free lockers per shop and size, today
  python stowcity_occupancy.py daily   # the day's readings per shop, with capacity and max occupied
"""
import collections, csv, datetime as dt, json, os, sys, time
import urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
SHOPS_CSV = os.path.join(DATA, "stowcity-shops.csv")
OCC_CSV = os.path.join(DATA, "stowcity-occupancy.csv")
DAILY_CSV = os.path.join(DATA, "stowcity-daily.csv")

API = "https://www.stowcity.com/api/v8/index.php"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
PAUSE = 1.0
SIZES = ("small", "medium", "large")

# The API gives no coordinates; these two addresses are pinned by hand so the points get a zone.
COORDS = {
    "venice-old-town": ("45.4437", "12.3270"),   # Calle dell'Aseo, Cannaregio
    "mestre": ("45.4816", "12.2333"),            # Via Col di Lana, Mestre station
}

SHOP_FIELDS = ["shop_id", "city", "name", "address", "lat", "lng",
               "cap_small", "cap_medium", "cap_large", "rating", "reviews", "url"]
OCC_FIELDS = ["read_at", "city", "shop_id", "name", "day", "hour", "weekday",
              "locker_type", "free", "capacity", "status"]

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


def get(url):
    for attempt in range(3):
        try:
            time.sleep(PAUSE)
            resp = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40)
            return resp.getcode(), json.loads(resp.read().decode("utf8", "replace"))
        except urllib.error.HTTPError as e:
            return e.code, None
        except Exception:
            time.sleep(3)
    return 0, None


def number(x, default=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def locations():
    """The shop ids, discovered from the config of either one (both list all availableLocations)."""
    _, cfg = get(f"{API}?action=config&location=venice-old-town")
    locs = [l["id"] for l in (cfg or {}).get("availableLocations", [])]
    return locs or list(COORDS)


def shops():
    """Each shop with its declared capacity per size and pinned coordinates."""
    rows = []
    for loc in locations():
        code, cfg = get(f"{API}?action=config&location={loc}")
        if not cfg:
            print(f"  {loc}: config http-{code}")
            continue
        caps = {l["id"]: number(l.get("defaultAvailable")) for l in cfg.get("lockers", [])}
        info = cfg.get("location", {})
        lat, lng = COORDS.get(loc, ("", ""))
        rows.append(dict(shop_id=loc, city=info.get("city", ""), name=info.get("name", loc),
                         address=info.get("address", ""), lat=lat, lng=lng,
                         cap_small=caps.get("small", ""), cap_medium=caps.get("medium", ""),
                         cap_large=caps.get("large", ""), rating=info.get("rating", ""),
                         reviews=info.get("reviewCount", ""),
                         url=f"https://www.stowcity.com/booking/v8/?location={loc}"))
        print(f"  {loc:16} {info.get('name','')[:28]:28} caps S/M/L={caps.get('small')}/{caps.get('medium')}/{caps.get('large')}")
    rows_to_csv(SHOPS_CSV, SHOP_FIELDS, rows, append=False)
    print(f"{len(rows)} StowCity shops -> {SHOPS_CSV}")
    return rows


def shop_list():
    return read_csv(SHOPS_CSV) or shops()


def window(now):
    """A 4-hour window inside opening hours (07:00-23:00), starting at the current half hour."""
    start_h = min(max(now.hour, 7), 19)
    end_h = min(start_h + 4, 23)
    return f"{start_h:02d}:00", f"{end_h:02d}:00"


def scan():
    """Free lockers per shop and size for today, occupied read against the declared capacity."""
    now = rome_now()
    read_at = now.strftime("%Y-%m-%d %H:%M")
    weekday = now.strftime("%a")
    day, hour = now.strftime("%Y-%m-%d"), now.hour
    check_in, check_out = window(now)

    shops_by_id = {s["shop_id"]: s for s in shop_list()}
    rows = []
    for loc, shop in shops_by_id.items():
        q = (f"{API}?action=availability&date={day}&checkIn={check_in}&checkOut={check_out}"
             f"&tierHours=4&requestedDays=1&location={loc}")
        code, data = get(q)
        free = (data or {}).get("availability") if data else None
        if not free:
            rows.append(dict(read_at=read_at, city=shop["city"], shop_id=loc, name=shop["name"], day=day,
                             hour=hour, weekday=weekday, locker_type="", free="", capacity="",
                             status=f"http-{code}" if code else "no-response"))
            print(f"  {loc:16} http-{code}")
            continue
        for size in SIZES:
            if size in free and free[size] is not None:
                rows.append(dict(read_at=read_at, city=shop["city"], shop_id=loc, name=shop["name"], day=day,
                                 hour=hour, weekday=weekday, locker_type=size, free=int(free[size]),
                                 capacity=number(shop.get(f"cap_{size}")), status="read"))
        print(f"  {loc:16} free S/M/L={free.get('small')}/{free.get('medium')}/{free.get('large')}")
    rows_to_csv(OCC_CSV, OCC_FIELDS, rows, append=True)
    read = sum(1 for r in rows if r["status"] == "read")
    print(f"{read_at} slot {hour}: {read} size-readings -> {OCC_CSV}")
    return rows


def daily():
    rows = [r for r in read_csv(OCC_CSV) if r.get("status") == "read" and r.get("free") != ""]
    by_day = collections.defaultdict(list)
    for r in rows:
        by_day[(r["day"], r["shop_id"], r["locker_type"])].append(r)
    out = []
    for (d, shop_id, size), rs in sorted(by_day.items()):
        cap = max(number(r["capacity"]) for r in rs)
        least_free = min(number(r["free"]) for r in rs)
        out.append(dict(day=d, shop_id=shop_id, name=rs[0]["name"], city=rs[0]["city"], locker_type=size,
                        capacity=cap, min_free=least_free, max_occupied=cap - least_free, readings=len(rs)))
    rows_to_csv(DAILY_CSV, ["day", "shop_id", "name", "city", "locker_type", "capacity",
                            "min_free", "max_occupied", "readings"], out, append=False)
    print(f"{len(out)} shop-size-days -> {DAILY_CSV}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    {"shops": shops, "scan": scan, "daily": daily}.get(cmd, scan)()
