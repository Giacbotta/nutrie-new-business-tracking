"""How full iVano (PickItUP / Maifermi) is, per shop and box size (board item E-93/E-94).

iVano is an automatic locker operator, 7 Italian shops, two of them on Nutrie's doorstep (Venezia
Cannaregio and San Polo). The i-vano.it site is static and looked unreadable, but the booking runs
on the Ermes platform at ermes-srv.com, which has a clean public API (found 29/09/2026):

  GET  /maifermi/be/v8/locker/list
        -> every shop: lockerCode, lockerName, city, address, latitude/longitude, onlineBooking
  POST /maifermi/be/v8/box/available-by-date  {lockerCode, checkInDate, checkOutDate}
        (dates as "YYYY-MM-DD HH:MM:SS") -> data[] with dimension SMALL/MEDIUM/LARGE and qty = boxes
        still free for that window

So occupied = capacity - free, capacity being the most boxes ever seen free for that shop and size,
the same yardstick as Stow Your Bags and Locker in the City.

COORDINATES: the API gives the real position only for Venezia (001), San Polo (003) and Roma (006).
Napoli, Palermo and Ortigia all carry the same placeholder point, and Torino carries none, so those
four are pinned by hand from their addresses (FALLBACK_COORDS) - otherwise they would miss their
neighbourhood. Torino also has onlineBooking:false, so it is mapped but not read.

ROBOTS.TXT: ermes-srv.com has none. One list call plus one availability call per online shop a round.

COMMANDS (run from the repository root)
  python ivano_occupancy.py shops   # the shops: code, city, address, coordinates, online flag
  python ivano_occupancy.py scan    # free boxes per shop and size, today
  python ivano_occupancy.py daily   # the day's readings per shop, with the peak ever seen
"""
import collections, csv, datetime as dt, json, os, sys, time
import urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
SHOPS_CSV = os.path.join(DATA, "ivano-shops.csv")
OCC_CSV = os.path.join(DATA, "ivano-occupancy.csv")
DAILY_CSV = os.path.join(DATA, "ivano-daily.csv")

BASE = "https://ermes-srv.com/maifermi/be/v8"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Content-Type": "application/json"}
PAUSE = 1.0
SIZES = ("SMALL", "MEDIUM", "LARGE")

# The API returns the same placeholder point for Napoli/Palermo/Ortigia and nothing for Torino, so
# these four are pinned by hand from their addresses. Keyed by lockerCode.
BOGUS_LAT = "40.98151076784957"
FALLBACK_COORDS = {
    "Maifermi-002": ("40.8486", "14.2585"),   # Napoli, Via Tommaso Caravita 12
    "Maifermi-004": ("38.1279", "13.3555"),   # Palermo, Via Pola 7/9
    "Maifermi-005": ("37.0592", "15.2936"),   # Ortigia (Siracusa), Via dei Santi Coronati
    "Maifermi-007": ("45.0655", "7.6835"),    # Torino, Via Sant'Antonio da Padova 8
}

SHOP_FIELDS = ["shop_id", "city", "name", "address", "lat", "lng", "online_booking", "url"]
OCC_FIELDS = ["read_at", "city", "shop_id", "name", "day", "hour", "weekday",
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


def number(x, default=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def api_get(path):
    for _ in range(3):
        try:
            time.sleep(PAUSE)
            resp = urllib.request.urlopen(urllib.request.Request(f"{BASE}{path}", headers=UA), timeout=40)
            return resp.getcode(), json.loads(resp.read().decode("utf8", "replace"))
        except urllib.error.HTTPError as e:
            return e.code, None
        except Exception:
            time.sleep(3)
    return 0, None


def api_post(path, body):
    data = json.dumps(body).encode()
    for _ in range(3):
        try:
            time.sleep(PAUSE)
            req = urllib.request.Request(f"{BASE}{path}", data=data, headers=UA, method="POST")
            resp = urllib.request.urlopen(req, timeout=40)
            return resp.getcode(), json.loads(resp.read().decode("utf8", "replace"))
        except urllib.error.HTTPError as e:
            return e.code, None
        except Exception:
            time.sleep(3)
    return 0, None


def shops():
    """Every iVano shop with its real position (pinned by hand where the API's is a placeholder)."""
    code, data = api_get("/locker/list")
    rows = []
    for s in (data or {}).get("data", []):
        lid = s.get("lockerCode", "")
        lat, lng = str(s.get("latitude") or ""), str(s.get("longitude") or "")
        if not lat or lat.startswith(BOGUS_LAT[:7]):     # placeholder or empty -> pin by hand
            lat, lng = FALLBACK_COORDS.get(lid, (lat, lng))
        rows.append(dict(shop_id=lid, city=s.get("city", ""), name=s.get("lockerName", lid),
                         address=s.get("address", ""), lat=lat, lng=lng,
                         online_booking=str(bool(s.get("onlineBooking"))).lower(),
                         url="https://ermes-srv.com/maifermi_booking_1/it/"))
        print(f"  {lid} {s.get('city',''):12} {lat or '-':>10} online={s.get('onlineBooking')}")
    rows_to_csv(SHOPS_CSV, SHOP_FIELDS, rows, append=False)
    print(f"{len(rows)} iVano shops -> {SHOPS_CSV}")
    return rows


def shop_list():
    return read_csv(SHOPS_CSV) or shops()


def window(now):
    """A 4-hour window from the current hour, as the API wants it: 'YYYY-MM-DD HH:MM:SS'."""
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(hours=4)
    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def scan():
    """Free boxes per shop and size for today, from the Ermes availability endpoint."""
    now = rome_now()
    read_at = now.strftime("%Y-%m-%d %H:%M")
    weekday = now.strftime("%a")
    day, hour = now.strftime("%Y-%m-%d"), now.hour
    check_in, check_out = window(now)

    rows = []
    for shop in shop_list():
        if shop.get("online_booking") != "true":
            rows.append(dict(read_at=read_at, city=shop["city"], shop_id=shop["shop_id"], name=shop["name"],
                             day=day, hour=hour, weekday=weekday, locker_type="", free="",
                             status="no-online-booking"))
            print(f"  {shop['shop_id']} {shop['city']:12} no online booking")
            continue
        code, data = api_post("/box/available-by-date",
                              {"lockerCode": shop["shop_id"], "checkInDate": check_in, "checkOutDate": check_out})
        items = (data or {}).get("data") if data else None
        if not items:
            rows.append(dict(read_at=read_at, city=shop["city"], shop_id=shop["shop_id"], name=shop["name"],
                             day=day, hour=hour, weekday=weekday, locker_type="", free="",
                             status=f"http-{code}" if code else "no-response"))
            print(f"  {shop['shop_id']} {shop['city']:12} http-{code}")
            continue
        free = {d.get("dimension"): number(d.get("qty")) for d in items}
        for size in SIZES:
            if size in free:
                rows.append(dict(read_at=read_at, city=shop["city"], shop_id=shop["shop_id"], name=shop["name"],
                                 day=day, hour=hour, weekday=weekday, locker_type=size.lower(),
                                 free=free[size], status="read"))
        print(f"  {shop['shop_id']} {shop['city']:12} free S/M/L={free.get('SMALL')}/{free.get('MEDIUM')}/{free.get('LARGE')}")
    rows_to_csv(OCC_CSV, OCC_FIELDS, rows, append=True)
    read = sum(1 for r in rows if r["status"] == "read")
    print(f"{read_at} slot {hour}: {read} size-readings -> {OCC_CSV}")
    return rows


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
        out.append(dict(day=d, shop_id=shop_id, name=rs[0]["name"], city=rs[0]["city"], locker_type=size,
                        peak_seen=cap, min_free=least_free, max_occupied=cap - least_free, readings=len(rs)))
    rows_to_csv(DAILY_CSV, ["day", "shop_id", "name", "city", "locker_type", "peak_seen",
                            "min_free", "max_occupied", "readings"], out, append=False)
    print(f"{len(out)} shop-size-days -> {DAILY_CSV}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    {"shops": shops, "scan": scan, "daily": daily}.get(cmd, scan)()
