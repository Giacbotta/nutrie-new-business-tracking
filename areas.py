"""One shared neighbourhood for every luggage point, whoever runs it (board item E-93).

The names come from **Radical Storage**, which is the only one of the three that names zones in a
way a person recognises ("Santa Lucia Station", "Termini", "City Center"). Giacomo asked for those
on 24/09/2026, after a night of OpenStreetMap names that read like nonsense in most cities.

So: every Radical point carries its own zone. Every Bounce point and every Stow Your Bags shop
takes the zone of the nearest Radical point within RADIUS_KM. What is left keeps the label
"Unknown neighbourhood" and is searched for in the background — the nearest OpenStreetMap place is
still written next to it, as a candidate to look at, never as the label.

  python areas.py fill          # match every point to a Radical zone (offline, instant)
  python areas.py places        # refresh the OpenStreetMap candidates (one Overpass request)
  python areas.py report        # coverage, and what is still unknown
"""
import collections, csv, json, os, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(DATA, "areas.csv")
PER_RUN = 600
PAUSE = 1.1
RADIUS_KM = 1.0        # how far a point may be from the Radical point that names its zone
SPREAD_KM = 0.4        # a point already named can pass its zone to a very close neighbour
SPREAD_ROUNDS = 3
PLACES = os.path.join(DATA, "osm-places.csv")
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.osm.ch/api/interpreter"]
ITALY_BBOX = (35.0, 6.0, 47.3, 19.0)   # a bounding box is far cheaper than an area lookup, which
                                        # timed out; anything it catches outside Italy is harmless
# What counts as a neighbourhood, from the widest down. A point takes the nearest of these.
PLACE_KINDS = ["borough", "city_district", "suburb", "quarter", "neighbourhood"]
MAX_KM = 2.5
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


def zones():
    """Radical's own neighbourhood list per city, with the coordinates it gives each one.

    The city pages carry chips like "Cannaregio" with a lat/lng, which is a centre for the zone —
    many more zones than the points themselves cover, so far more of the other providers' points
    can be matched. One request per city, refreshed with the Monday census."""
    import html as htmlmod
    cities = [r["city"] for r in read_csv("radical-cities.csv")]
    rows = []
    for city in cities:
        time.sleep(0.4)
        try:
            # a browser user agent on purpose: with the project's own one Radical serves a lighter
            # page with no zone chips at all (24/09/2026: 119 zones found instead of around 1.500)
            req = urllib.request.Request(f"https://radicalstorage.com/luggage-storage/{city}",
                                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            page = htmlmod.unescape(urllib.request.urlopen(req, timeout=40).read().decode("utf8", "replace"))
            page = urllib.parse.unquote(page)
        except Exception as err:
            print(f"  {city}: {err}")
            continue
        found = set()
        for m in re.finditer(r'"label":\[0,"([^"]+)"\],"href":\[0,"[^"]*?lat=([\d.]+)&lng=([\d.]+)&s=', page):
            found.add((m.group(1), m.group(2), m.group(3)))
        for m in re.finditer(r'storage-list/[a-z-]+\?lat=([\d.]+)&lng=([\d.]+)&s=([^"&]+)', page):
            found.add((m.group(3).replace("+", " "), m.group(1), m.group(2)))
        for name, lat, lng in found:
            rows.append(dict(city=city, name=name.strip(), lat=lat, lng=lng))
    with open(os.path.join(DATA, "radical-zones.csv"), "w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=["city", "name", "lat", "lng"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} Radical zones over {len(cities)} cities -> data/radical-zones.csv")
    return rows


def places():
    """The named places of Italy, in one Overpass request, cached in data/osm-places.csv."""
    s, w, n, e = ITALY_BBOX
    # nwr, not node: Venice's sestieri and many other neighbourhoods are mapped as areas, so a
    # node-only query left the whole historic centre without a name (checked 24/09/2026).
    query = (f'[out:json][timeout:240];nwr["place"~"^({"|".join(PLACE_KINDS)})$"]'
             f'({s},{w},{n},{e});out center;')
    raw = None
    for host in OVERPASS:
        try:
            req = urllib.request.Request(host, data=("data=" + urllib.parse.quote(query)).encode(),
                                         headers={**UA, "Content-Type": "application/x-www-form-urlencoded"})
            raw = json.loads(urllib.request.urlopen(req, timeout=300).read())
            print(f"  answered by {host}")
            break
        except Exception as err:
            print(f"  {host} did not answer ({err})")
            time.sleep(5)
    if raw is None:
        raise SystemExit("no Overpass mirror answered")
    rows = []
    for e in raw.get("elements", []):
        tags = e.get("tags") or {}
        centre = e if "lat" in e else (e.get("center") or {})
        if tags.get("name") and centre.get("lat") is not None:
            rows.append(dict(name=tags["name"], kind=tags.get("place", ""),
                             lat=centre["lat"], lng=centre.get("lon")))
    os.makedirs(DATA, exist_ok=True)
    with open(PLACES, "w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "kind", "lat", "lng"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} named places in Italy -> {PLACES}")
    return rows


def pretty(slug):
    """"santa-lucia-station" -> "Santa Lucia Station"."""
    return " ".join(w.capitalize() for w in (slug or "").replace("_", "-").split("-") if w)


def grid_of(rows, size=0.02):
    out = {}
    for lat, lng, name in rows:
        out.setdefault((round(lat / size), round(lng / size)), []).append((lat, lng, name))
    return out


def nearest(grid, lat, lng, limit_km, size=0.02):
    import math
    best, best_km = "", limit_km
    gl, gg = round(lat / size), round(lng / size)
    for dl in (-1, 0, 1):
        for dg in (-1, 0, 1):
            for plat, plng, name in grid.get((gl + dl, gg + dg), ()):
                km = math.dist(((plat - lat) * 111.0, (plng - lng) * 111.0 * math.cos(math.radians(lat))), (0, 0))
                if km < best_km:
                    best, best_km = name, km
    return best, (best_km if best else None)


def fill_from_radical():
    """Give every point the zone of the nearest Radical point, and keep an OSM candidate for the rest."""
    radical = [(float(r["lat"]), float(r["lng"]), pretty(r["area"]))
               for r in read_csv("radical-points.csv") if r.get("lat") and r.get("area")]
    # Radical's published zone list covers far more ground than its own points
    radical += [(float(r["lat"]), float(r["lng"]), r["name"])
                for r in read_csv("radical-zones.csv") if r.get("lat") and r.get("name")]
    zones = grid_of(radical)
    osm = grid_of([(float(r["lat"]), float(r["lng"]), r["name"]) for r in read_csv("osm-places.csv")
                   if r.get("lat") and r.get("name")], size=0.05)
    cache = {}
    for k in wanted():
        lat, lng = (float(x) for x in k.split(","))
        name, km = nearest(zones, lat, lng, RADIUS_KM)
        guess, _ = nearest(osm, lat, lng, 3.0, size=0.05)
        cache[k] = dict(key=k, area=name, city_osm=guess,
                        source=f"radical {km:.2f} km" if name else "unknown")
    direct = sum(1 for r in cache.values() if r["area"])

    # A point 200 m from one already named is in the same neighbourhood: let the names spread from
    # neighbour to neighbour, in short hops, so a Bounce or Stow point just outside the reach of a
    # Radical point still lands in the right zone instead of "unknown".
    for _ in range(SPREAD_ROUNDS):
        named = grid_of([(float(k.split(",")[0]), float(k.split(",")[1]), r["area"])
                         for k, r in cache.items() if r["area"]], size=0.01)
        moved = 0
        for k, r in cache.items():
            if r["area"]:
                continue
            lat, lng = (float(x) for x in k.split(","))
            name, km = nearest(named, lat, lng, SPREAD_KM, size=0.01)
            if name:
                r["area"], r["source"], moved = name, f"spread {km:.2f} km", moved + 1
        if not moved:
            break
    unknown = sum(1 for r in cache.values() if not r["area"])
    save(cache)
    print(f"{len(cache)} positions: {direct} straight from a Radical point, "
          f"{len(cache) - unknown - direct} passed on from a named neighbour, {unknown} still unknown -> {CACHE}")


def fill(limit=PER_RUN):
    cache = load()
    want = wanted()
    # busiest cities first: Nominatim answers about one position a second, so Rome, Milan and the
    # other big ones should be complete long before the small towns
    size = collections.Counter(want.values())
    todo = sorted((k for k in want if k not in cache), key=lambda k: -size[want[k]])
    print(f"{len(wanted())} positions in total, {len(cache)} already known, {len(todo)} missing")
    for n, k in enumerate(todo[:limit], 1):
        time.sleep(PAUSE)
        name, town = lookup(k)
        cache[k] = dict(key=k, area=(name or "").strip(), city_osm=town, source="osm" if name else "unknown")
        if n % 25 == 0:          # save as it goes: a run that is interrupted keeps what it found
            save(cache)
            print(f"  {n} of {min(limit, len(todo))} done", flush=True)
    save(cache)
    named = sum(1 for r in cache.values() if r["area"])
    left = max(0, len(todo) - limit)
    print(f"{len(cache)} positions placed, {named} with a neighbourhood -> {CACHE}"
          + (f"; {left} left for the next run" if left else "; nothing left"))


def area_of():
    """Mapping usable by the other scripts: rounded position -> neighbourhood."""
    return {k: (r["area"] or "") for k, r in load().items()}


def nearest_lookup(radius_km=0.6):
    """area(lat, lng) for every point, even one not looked up yet.

    Exact position first; otherwise the nearest position already in the cache within `radius_km`
    lends its name. Nominatim answers about one position per second, so on the first night the
    cache is still filling: without this fallback most points would show no neighbourhood at all,
    and two points on the same street would land in different rows. Buckets of 0.01 degrees keep
    the search local instead of scanning the whole cache."""
    import math
    cache = {k: r["area"] for k, r in load().items() if r["area"]}
    grid = {}
    for k, name in cache.items():
        lat, lng = (float(x) for x in k.split(","))
        grid.setdefault((round(lat, 2), round(lng, 2)), []).append((lat, lng, name))

    def area(lat, lng):
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            return ""
        exact = cache.get(f"{lat:.4f},{lng:.4f}")
        if exact:
            return exact
        best, best_km = "", radius_km
        for dlat in (-0.01, 0, 0.01):
            for dlng in (-0.01, 0, 0.01):
                for plat, plng, name in grid.get((round(lat + dlat, 2), round(lng + dlng, 2)), ()):
                    km = math.dist(((plat - lat) * 111.0, (plng - lng) * 111.0 * math.cos(math.radians(lat))), (0, 0))
                    if km < best_km:
                        best, best_km = name, km
        return best

    return area


def report():
    cache = load()
    have = area_of()
    unknown = [r for r in cache.values() if not r["area"]]
    if unknown:
        print(f"{len(unknown)} positions with no Radical zone nearby; OpenStreetMap candidates for the first few:")
        for r in unknown[:8]:
            print(f"   {r['key']}  candidate: {r['city_osm'] or '(none)'}")
    print(f"{len(cache)} positions cached, {sum(1 for v in have.values() if v)} named")
    for name, ident, lat_col, lng_col, city_col in SOURCES:
        rows = [r for r in read_csv(name) if r.get(lat_col)]
        seen = {key(r[lat_col], r[lng_col]) for r in rows if r.get(lat_col) and r.get(lng_col)}
        covered = sum(1 for k in seen if have.get(k))
        print(f"  {name:24} {len(seen):5} positions, {covered:5} with a neighbourhood")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fill"
    if cmd == "zones":
        zones()
    elif cmd == "places":
        places()
    elif cmd == "fill":
        fill_from_radical()
    elif cmd == "nominatim":          # the old one-by-one way, kept for spot checks
        fill(int(sys.argv[2]) if len(sys.argv) > 2 else PER_RUN)
    else:
        report()
