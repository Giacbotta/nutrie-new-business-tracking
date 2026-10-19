"""How full Store My Luggage is, per point and locker size (board item E-100).

Store My Luggage is an automatic locker operator with 14 "Lockers Points" in 10 Italian cities, two
of them where Nutrie competes: Venezia Santa Lucia and Pisa Stazione Centrale. Its booking app
(Angular, www.storemyluggage.it/client/) reads a public backend without any login:

  GET  /backend/public/api/v1/client/rooms
        -> every point: id, name, city, street, coordinates, count_boxs_type_lockers
  POST /backend/public/api/v1/client/rooms/:id/get_all_count_free_boxs  {start_date, end_date}
        -> {"count": {"700": n, "1000": n}, "free": {"700": n, "1000": n}}

700 is the "standard" locker and 1000 the "maxi" (constant in the app bundle). `count` is the real
capacity per size, so occupied = capacity - free with a declared capacity, as for StowCity. With
null dates the answer is "now", which is what an hourly round wants.

THE CAP (found 19/10/2026, read it before trusting any number): `free` never goes above 6. On the
first reading 12 points out of 13 answered exactly 6, whether they have 11 lockers or 38; the app only
needs to know whether "a few" are left. So:
  - free < 6  -> exact: occupied = capacity - free
  - free == 6 -> "6 or more free": occupied is somewhere between 0 and capacity - 6
The `capped` column marks those rows. The dashboard counts a capped reading as 0 occupied, so the
figure is a floor that only moves when a point gets close to full: a saturation signal, not a
headcount. Nothing better is exposed without logging in.

A point with no lockers at all (Firenze Santa Croce, id 19, on 19/10/2026) is written as status
"no-lockers" and never reaches the comparison.

ROBOTS.TXT: www.storemyluggage.it disallows nothing; the backend host has none. The reading is one
request per point per round, about 15 calls an hour.

COMMANDS (run from the repository root)
  python smluggage_occupancy.py shops   # the points: id, city, address, coordinates, capacity per size
  python smluggage_occupancy.py scan    # free lockers per point and size, now
  python smluggage_occupancy.py daily   # the day's readings per point, with capacity and max occupied
"""
import collections, csv, datetime as dt, json, os, sys, time
import urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
SHOPS_CSV = os.path.join(DATA, "smluggage-shops.csv")
OCC_CSV = os.path.join(DATA, "smluggage-occupancy.csv")
DAILY_CSV = os.path.join(DATA, "smluggage-daily.csv")

API = "https://storemyluggage.it1.eur.aruba.jenv-aruba.cloud/backend/public/api/v1/client"
BOOKING = "https://www.storemyluggage.it/client/create-online/{id}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Accept": "application/json", "Content-Type": "application/json"}
PAUSE = 1.0
SIZES = {"700": "standard", "1000": "maxi"}
FREE_CAP = 6        # the API never answers more than this many free lockers per size

SHOP_FIELDS = ["shop_id", "city", "name", "address", "lat", "lng",
               "cap_standard", "cap_maxi", "url"]
OCC_FIELDS = ["read_at", "city", "shop_id", "name", "day", "hour", "weekday",
              "locker_type", "free", "capacity", "capped", "status"]

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


def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(3):
        try:
            time.sleep(PAUSE)
            req = urllib.request.Request(f"{API}/{path}", data=data, headers=UA,
                                         method="POST" if data is not None else "GET")
            resp = urllib.request.urlopen(req, timeout=40)
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


def shops():
    """Every point with its declared capacity per size and the coordinates the API gives."""
    code, rooms = call("rooms")
    if not isinstance(rooms, list):
        print(f"rooms: http-{code}")
        return read_csv(SHOPS_CSV)
    rows = []
    for r in rooms:
        caps = r.get("count_boxs_type_lockers") or {}
        street = " ".join(x for x in (r.get("street"), r.get("street_number")) if x)
        rows.append(dict(shop_id=r["id"], city=r.get("city", ""), name=r.get("name", ""),
                         address=", ".join(x for x in (street, r.get("postal_code"), r.get("city")) if x),
                         lat=r.get("latitude", ""), lng=r.get("longitude", ""),
                         cap_standard=number(caps.get("700")), cap_maxi=number(caps.get("1000")),
                         url=BOOKING.format(id=r["id"])))
        print(f"  {r['id']:>3} {r.get('name','')[:28]:28} standard/maxi={caps.get('700')}/{caps.get('1000')}")
    rows_to_csv(SHOPS_CSV, SHOP_FIELDS, rows, append=False)
    print(f"{len(rows)} Store My Luggage points -> {SHOPS_CSV}")
    return rows


def shop_list():
    return read_csv(SHOPS_CSV) or shops()


def scan():
    """Free lockers per point and size right now, with the capacity the same answer declares."""
    now = rome_now()
    read_at = now.strftime("%Y-%m-%d %H:%M")
    weekday = now.strftime("%a")
    day, hour = now.strftime("%Y-%m-%d"), now.hour
    base = dict(read_at=read_at, day=day, hour=hour, weekday=weekday)

    rows = []
    for shop in shop_list():
        sid = shop["shop_id"]
        who = dict(base, city=shop["city"], shop_id=sid, name=shop["name"])
        code, data = call(f"rooms/{sid}/get_all_count_free_boxs", {"start_date": None, "end_date": None})
        if not data or "free" not in data:
            rows.append(dict(who, locker_type="", free="", capacity="", capped="",
                             status=f"http-{code}" if code else "no-response"))
            print(f"  {sid:>3} http-{code}")
            continue
        count, free = data.get("count") or {}, data.get("free") or {}
        if not any(number(v) for v in count.values()):
            rows.append(dict(who, locker_type="", free="", capacity=0, capped="", status="no-lockers"))
            print(f"  {sid:>3} no lockers")
            continue
        for key, size in SIZES.items():
            cap = number(count.get(key))
            if cap <= 0:
                continue
            f = number(free.get(key))
            rows.append(dict(who, locker_type=size, free=f, capacity=cap,
                             capped=int(f >= FREE_CAP), status="read"))
        print(f"  {sid:>3} {shop['name'][:28]:28} free/cap {free} / {count}")
    rows_to_csv(OCC_CSV, OCC_FIELDS, rows, append=True)
    read = [r for r in rows if r["status"] == "read"]
    exact = sum(1 for r in read if not r["capped"])
    print(f"{read_at} slot {hour}: {len(read)} size-readings, {exact} below the cap of {FREE_CAP} -> {OCC_CSV}")
    return rows


def daily():
    """Per point, size and day: capacity, fewest free seen, and how many readings were exact."""
    rows = [r for r in read_csv(OCC_CSV) if r.get("status") == "read" and r.get("free") != ""]
    by_day = collections.defaultdict(list)
    for r in rows:
        by_day[(r["day"], r["shop_id"], r["locker_type"])].append(r)
    out = []
    for (d, shop_id, size), rs in sorted(by_day.items()):
        cap = max(number(r["capacity"]) for r in rs)
        least_free = min(number(r["free"]) for r in rs)
        exact = sum(1 for r in rs if r.get("capped") == "0")
        out.append(dict(day=d, shop_id=shop_id, name=rs[0]["name"], city=rs[0]["city"], locker_type=size,
                        capacity=cap, min_free=least_free,
                        max_occupied=cap - least_free if least_free < FREE_CAP else "",
                        exact_readings=exact, readings=len(rs)))
    rows_to_csv(DAILY_CSV, ["day", "shop_id", "name", "city", "locker_type", "capacity", "min_free",
                            "max_occupied", "exact_readings", "readings"], out, append=False)
    print(f"{len(out)} point-size-days -> {DAILY_CSV}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    {"shops": shops, "scan": scan, "daily": daily}.get(cmd, scan)()
