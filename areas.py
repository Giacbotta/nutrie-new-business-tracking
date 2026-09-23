"""One shared neighbourhood for every luggage point, whoever runs it (board item E-93).

Radical, Bounce and Stow Your Bags all publish a position for their points, but each names zones
its own way — or not at all. Comparing them needs the same geography on all three, so every point
is given a neighbourhood here, from its coordinates, through OpenStreetMap's Nominatim, and the
answer is cached in data/areas.csv. The drill on the page is then the same for everybody:

    city  ->  neighbourhood  ->  exact location  ->  (locker size, Stow Your Bags only)

Nominatim asks for at most one request per second and a real user agent, so that is what it gets.
Coordinates are rounded to four decimals (about 11 m) before the lookup, so points in the same
doorway cost one request, and a run only looks up what the cache does not already hold.

  python areas.py fill          # look up whatever is missing (default 600 per run)
  python areas.py fill 2000     # ... up to this many
  python areas.py report        # what is covered, and the biggest neighbourhoods
"""
import csv, json, os, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(DATA, "areas.csv")
PER_RUN = 600
PAUSE = 1.1
UA = {"User-Agent": "nutrie-new-business-tracking (https://github.com/Giacbotta/nutrie-new-business-tracking)"}

# Bigger units first: two points a few streets apart should land in the same area, otherwise the
# providers are not comparable. "neighbourhood" alone is too fine-grained for that.
LEVELS = ["suburb", "city_district", "borough", "quarter", "neighbourhood", "town", "village", "municipality"]

SOURCES = [
    # file, id column, lat column, lng column, city column
    ("radical-points.csv", "storage_id", "lat", "lng", "city"),
    ("bounce-occupancy.csv", "spot_id", "lat", "lng", "city"),
    ("stow-shops.csv", "shop_id", "lat", "lng", "city"),
]


def read_csv(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf8") as f:
        return list(csv.DictReader(f))


def key(lat, lng):
    return f"{float(lat):.4f},{float(lng):.4f}"


def load():
    return {r["key"]: r for r in read_csv("areas.csv")}


def save(cache):
    os.makedirs(DATA, exist_ok=True)
    with open(CACHE, "w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=["key", "area", "city_osm", "source"])
        w.writeheader()
        w.writerows(sorted(cache.values(), key=lambda r: r["key"]))


def wanted():
    """Every distinct rounded position across the three providers."""
    out = {}
    for name, ident, lat_col, lng_col, city_col in SOURCES:
        for r in read_csv(name):
            if not r.get(lat_col) or not r.get(lng_col):
                continue
            try:
                out[key(r[lat_col], r[lng_col])] = r.get(city_col, "")
            except ValueError:
                continue
    return out


def lookup(k):
    lat, lng = k.split(",")
    url = (f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=16&addressdetails=1"
           f"&lat={lat}&lon={lng}")
    try:
        a = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read())
        addr = a.get("address", {})
    except Exception:
        return None, ""
    name = next((addr[l] for l in LEVELS if addr.get(l)), "")
    town = addr.get("city") or addr.get("town") or addr.get("municipality") or ""
    return name, town


def fill(limit=PER_RUN):
    cache = load()
    todo = [k for k in wanted() if k not in cache]
    print(f"{len(wanted())} positions in total, {len(cache)} already known, {len(todo)} missing")
    for k in todo[:limit]:
        time.sleep(PAUSE)
        name, town = lookup(k)
        cache[k] = dict(key=k, area=(name or "").strip(), city_osm=town, source="osm" if name else "unknown")
    save(cache)
    named = sum(1 for r in cache.values() if r["area"])
    left = max(0, len(todo) - limit)
    print(f"{len(cache)} positions placed, {named} with a neighbourhood -> {CACHE}"
          + (f"; {left} left for the next run" if left else "; nothing left"))


def area_of():
    """Mapping usable by the other scripts: rounded position -> neighbourhood."""
    return {k: (r["area"] or "") for k, r in load().items()}


def report():
    cache = load()
    have = area_of()
    print(f"{len(cache)} positions cached, {sum(1 for v in have.values() if v)} named")
    for name, ident, lat_col, lng_col, city_col in SOURCES:
        rows = [r for r in read_csv(name) if r.get(lat_col)]
        seen = {key(r[lat_col], r[lng_col]) for r in rows if r.get(lat_col) and r.get(lng_col)}
        covered = sum(1 for k in seen if have.get(k))
        print(f"  {name:24} {len(seen):5} positions, {covered:5} with a neighbourhood")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fill"
    if cmd == "fill":
        fill(int(sys.argv[2]) if len(sys.argv) > 2 else PER_RUN)
    else:
        report()
