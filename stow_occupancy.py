"""How many lockers are still free at Stow Your Bags, shop by shop (board item E-93).

Stow Your Bags is the competitor closest to Nutrie: a real operator with automated lockers, not a
marketplace. Its public pages show prices and addresses but no availability. The booking app does,
and this walks it the same way a visitor does:

  GET  customer.stowyourbags.com/shop/<id>/booking?lang=en   -> CSRF token and the Livewire snapshots
  POST customer.stowyourbags.com/livewire/update             -> setDate, setTime, goToNextStep

Step two of the form carries `lockersAvailability`, which per locker type gives **max_qta**, the
number of lockers that can still be booked in that slot, plus the price list. Capacity is not
published, so the yardstick is the largest max_qta ever seen for that shop and type (`peak_seen` in
the daily file): occupancy is read against that, and it can only improve as the series grows.

A round is four requests per shop, about 130 for the 33 Italian shops.

ROBOTS.TXT: www.stowyourbags.com allows everything, but customer.stowyourbags.com — the booking app
this script reads — disallows all robots. Giacomo asked for the reading anyway on 23/09/2026,
knowing that; it is kept to one slot per shop every two hours, one shop at a time.

COMMANDS (run from the repository root)
  python stow_occupancy.py shops     # the Italian shops and their booking ids
  python stow_occupancy.py scan      # free lockers per shop and locker type, in the next slot
  python stow_occupancy.py daily     # the day's readings per shop, with the peak ever seen
"""
import collections, csv, datetime as dt, html, json, os, re, sys, time
import http.cookiejar, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
SHOPS_CSV = os.path.join(DATA, "stow-shops.csv")
OCC_CSV = os.path.join(DATA, "stow-occupancy.csv")
DAILY_CSV = os.path.join(DATA, "stow-daily.csv")

SITE = "https://www.stowyourbags.com"
APP = "https://customer.stowyourbags.com"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
PAUSE = 1.0
OCC_FIELDS = ["read_at", "city", "shop_id", "name", "day", "hour", "weekday", "locker_type",
              "free", "min_qta", "price_1h", "status"]


def rome_now():
    """Italian time, worked out here: the machine may sit in another zone and Windows has no tz data."""
    utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    def last_sunday(month):
        d = dt.date(utc.year, month + 1, 1) - dt.timedelta(days=1)
        return d - dt.timedelta(days=(d.weekday() + 1) % 7)
    summer = dt.datetime.combine(last_sunday(3), dt.time(1)) <= utc < dt.datetime.combine(last_sunday(10), dt.time(1))
    return utc + dt.timedelta(hours=2 if summer else 1)


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


def fetch(url, opener=None, data=None, headers=None):
    for attempt in range(3):
        try:
            time.sleep(PAUSE)
            req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
            o = opener or urllib.request.build_opener()
            return o.open(req, timeout=40).read().decode("utf8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(30 * (attempt + 1))
            else:
                time.sleep(3)
        except Exception:
            time.sleep(3)
    return ""


def shops():
    """The Italian shops, with the booking id the app uses, from the public site."""
    urls = [u for u in re.findall(r"<loc>([^<]+)</loc>", fetch(f"{SITE}/sitemaps/sitemap_en.xml"))
            if re.search(r"/en/shop/[a-z-]+/[a-z0-9-]+/$", u)]
    rows = []
    for url in urls:
        page = fetch(url)
        book = re.search(r"customer\.stowyourbags\.com/shop/(\d+)/booking", page)
        name = re.search(r'"@type":\s*"SelfStorage".*?"name":\s*"([^"]*)"', page, re.S)
        addr = re.search(r'"streetAddress":\s*"([^"]*)"', page)
        country = re.search(r'"addressCountry":\s*"([^"]*)"', page)
        geo = re.search(r'"latitude":\s*([\d.]+),\s*"longitude":\s*([\d.]+)', page)
        if not book or (country and country.group(1) != "IT"):
            continue
        rows.append(dict(shop_id=book.group(1), city=url.split("/en/shop/")[1].split("/")[0],
                         name=(name.group(1) if name else "").strip(),
                         address=addr.group(1) if addr else "",
                         lat=geo.group(1) if geo else "", lng=geo.group(2) if geo else "", url=url))
    rows_to_csv(SHOPS_CSV, ["shop_id", "city", "name", "address", "lat", "lng", "url"], rows, append=False)
    print(f"{len(rows)} Italian shops -> {SHOPS_CSV}")
    return rows


def shop_list():
    return read_csv(SHOPS_CSV) or shops()


def availability(shop_id, day, hhmm):
    """Free lockers per type for that shop and slot, walking the booking form.

    Returns {type_id: {"free": n, "min": n, "price_1h": x}} or None when the shop cannot be read
    (closed that day, no slot, or the form refused)."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    url = f"{APP}/shop/{shop_id}/booking?lang=en"
    page = fetch(url, opener)
    token = re.search(r'name="csrf-token" content="([^"]+)"', page)
    if not token:
        return None
    token = token.group(1)
    snaps = {}
    for m in re.finditer(r'wire:snapshot="([^"]+)"', page):
        raw = html.unescape(m.group(1))
        snaps[json.loads(raw)["memo"]["name"]] = raw
    parent, child = snaps.get("booking.booking-form"), snaps.get("booking.components.step-one-form")
    if not parent or not child:
        return None

    def update(components):
        body = json.dumps({"_token": token, "components": components}).encode()
        out = fetch(f"{APP}/livewire/update", opener, body,
                    {"content-type": "application/json", "X-Livewire": "true",
                     "X-CSRF-TOKEN": token, "Referer": url})
        try:
            return json.loads(out)["components"]
        except Exception:
            return None

    def comp(snapshot, calls=None, updates=None):
        return {"snapshot": snapshot, "updates": updates or {}, "calls": calls or []}

    got = update([comp(parent), comp(child, [{"path": "", "method": "setDate", "params": [day]}])])
    if not got:
        return None
    parent, child = got[0]["snapshot"], got[1]["snapshot"]
    offered = sorted(set(re.findall(r"setTime\('(\d\d:\d\d)'\)", got[1].get("effects", {}).get("html", ""))))
    if not offered:
        return None
    slot = min(offered, key=lambda t: abs(int(t[:2]) * 60 + int(t[3:]) - (int(hhmm[:2]) * 60 + int(hhmm[3:]))))
    got = update([comp(parent), comp(child, [{"path": "", "method": "setTime", "params": [slot]}])])
    if not got:
        return None
    parent, child = got[0]["snapshot"], got[1]["snapshot"]
    model = json.loads(child)["data"]["model"]
    model = model[0] if isinstance(model, list) else model
    got = update([comp(parent, [{"path": "", "method": "goToNextStep", "params": []}], {"model": model}),
                  comp(child)])
    if not got:
        return None
    page_two = got[0].get("effects", {}).get("html", "")
    for m in re.finditer(r'wire:snapshot="([^"]+)"', page_two):
        d = json.loads(html.unescape(m.group(1)))
        if d["memo"]["name"].endswith("step-two-form"):
            raw = d["data"].get("lockersAvailability")
            raw = raw[0] if isinstance(raw, list) else raw
            names = d["data"].get("lockerTypes")
            names = names[0] if isinstance(names, list) else (names or {})
            out = {}
            for type_id, entries in (raw or {}).items():
                e = entries[0] if isinstance(entries, list) and entries else {}
                if not isinstance(e, dict):
                    continue
                price = (e.get("pricing") or [{}])[0].get("60") if isinstance(e.get("pricing"), list) else None
                out[names.get(type_id, type_id)] = dict(free=e.get("max_qta"), min=e.get("min_qta"),
                                                        price_1h=price, slot=slot)
            return out
    return None


def scan():
    now = rome_now()
    slot = (now + dt.timedelta(hours=1)).replace(minute=0)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    rows = []
    for shop in shop_list():
        got = availability(shop["shop_id"], slot.date().isoformat(), slot.strftime("%H:%M"))
        if got is None:
            rows.append(dict(read_at=stamp, city=shop["city"], shop_id=shop["shop_id"], name=shop["name"],
                             day=slot.date().isoformat(), hour=slot.hour,
                             weekday="weekend" if slot.weekday() >= 5 else "weekday",
                             locker_type="", free="", min_qta="", price_1h="", status="closed or refused"))
            print(f"  {shop['city']:12} {shop['name'][:28]:28} no reading", flush=True)
            continue
        for kind, v in got.items():
            rows.append(dict(read_at=stamp, city=shop["city"], shop_id=shop["shop_id"], name=shop["name"],
                             day=slot.date().isoformat(), hour=slot.hour,
                             weekday="weekend" if slot.weekday() >= 5 else "weekday",
                             locker_type=kind, free=v["free"], min_qta=v["min"], price_1h=v["price_1h"],
                             status="read"))
        print(f"  {shop['city']:12} {shop['name'][:28]:28} " +
              ", ".join(f"{k} {v['free']}" for k, v in got.items()), flush=True)
    rows_to_csv(OCC_CSV, OCC_FIELDS, rows)
    free = sum(r["free"] for r in rows if isinstance(r["free"], int))
    print(f"{stamp} slot {slot.hour:02d}: {len(rows)} readings, {free} lockers free -> {OCC_CSV}")


def daily():
    """Per shop, type and day: the free lockers seen, and the most ever seen, which stands in for
    capacity because Stow Your Bags does not publish it."""
    peak = collections.defaultdict(int)
    per_day = collections.defaultdict(list)
    meta = {}
    for r in read_csv(OCC_CSV):
        if r["status"] != "read" or r["free"] == "":
            continue
        key = (r["shop_id"], r["locker_type"])
        free = int(r["free"])
        peak[key] = max(peak[key], free)
        per_day[(r["day"], r["city"], r["shop_id"], r["locker_type"])].append((int(r["hour"]), free))
        meta[r["shop_id"]] = r["name"]
    rows = []
    for (day, city, shop, kind), seen in sorted(per_day.items()):
        seen.sort()
        free_values = [f for _, f in seen]
        top = peak[(shop, kind)]
        rows.append(dict(day=day, city=city, shop_id=shop, name=meta[shop], locker_type=kind,
                         readings=len(seen), free_min=min(free_values), free_max=max(free_values),
                         peak_seen=top, busiest_pct=round(100 * (top - min(free_values)) / top, 1) if top else ""))
    rows_to_csv(DAILY_CSV, ["day", "city", "shop_id", "name", "locker_type", "readings", "free_min",
                            "free_max", "peak_seen", "busiest_pct"], rows, append=False)
    print(f"{len(rows)} shop-type-days -> {DAILY_CSV}")


if __name__ == "__main__":
    {"shops": shops, "scan": scan, "daily": daily}[sys.argv[1] if len(sys.argv) > 1 else "scan"]()
