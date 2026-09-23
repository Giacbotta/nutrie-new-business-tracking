"""Weekly census of the two smaller luggage brands in Italy: Stasher and Stow Your Bags (E-93).

Neither publishes live occupancy, so this is a census, not an hourly tracker. What moves week by
week is the review count, and that is the demand signal: on Radical, where Nutrie's own bookings
are known, one native review came every 23-24 bookings (2024 and 2025). The same ratio is not
assumed here — the growth is written raw and the reading is left to whoever compares the brands.

STASHER  city pages listed in https://stasher.com/sitemap/italy-cities.xml (635 of them, the
  province capitals among them are read), each
  carrying its points in the page data: declared capacity (`space_available`, which does NOT move
  with the dates asked, checked 23/09/2026), a coarse booking bucket, an "often fully booked" flag,
  rating and rating count. robots.txt blocks /en/, /book/, /checkout/ — not these pages.
  Stasher answers 429 when pushed (seen on 23/09/2026 with three threads), so it is read one page
  at a time with a two second pause and a long wait after a 429.

STOW YOUR BAGS  a real operator with automated lockers, not a marketplace. Shops are listed in
  https://www.stowyourbags.com/sitemaps/sitemap_en.xml and each shop page carries address,
  coordinates, opening hours, prices per locker size and a review count. **Live availability is not
  readable**: it lives in customer.stowyourbags.com, whose robots.txt disallows everything to
  robots, so this script never goes there. Its www site allows everything.

COMMANDS (run from the repository root)
  python competitors_census.py stasher
  python competitors_census.py stow
  python competitors_census.py all
"""
import csv, datetime as dt, json, os, re, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CSV_PATH = os.path.join(DATA, "competitors-census.csv")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
THREADS = 3
PAUSE = 0.4
STASHER_PAUSE = 2.0    # one page at a time: Stasher answers 429 when pushed
CAPITALS = set("""agrigento alessandria ancona andria aosta arezzo ascoli-piceno asti avellino bari
barletta belluno benevento bergamo bologna bolzano brescia brindisi cagliari caltanissetta campobasso
carbonia carrara caserta catania catanzaro cesena chieti como cosenza cremona crotone cuneo enna fermo
ferrara firenze florence foggia forli frosinone genoa genova gorizia grosseto imperia isernia la-spezia
laquila l-aquila latina lecce lecco livorno lodi lucca macerata mantova mantua massa matera messina
milan milano modena monza naples napoli novara nuoro olbia oristano padova padua palermo parma pavia
perugia pesaro pescara piacenza pisa pistoia pordenone potenza prato ragusa ravenna reggio-calabria
reggio-emilia rieti rimini roma rome rovigo salerno sassari savona siena siracusa syracuse sondrio
taranto teramo terni torino trani trapani trento treviso trieste turin udine urbino varese venezia
venice verbania vercelli verona vibo-valentia vicenza viterbo""".split())
FIELDS = ["seen_on", "brand", "city", "name", "lat", "lng", "capacity", "reviews", "rating",
          "price_from", "note", "url"]


def get(url, pause=PAUSE):
    """One page. A 429 means "slow down", so it waits much longer before trying again."""
    for attempt in range(3):
        try:
            time.sleep(pause)
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read().decode("utf8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(30 * (attempt + 1))
            else:
                time.sleep(3)
        except Exception:
            time.sleep(3)
    return ""


def sitemap(url):
    return re.findall(r"<loc>([^<]+)</loc>", get(url))


def write(rows):
    os.makedirs(DATA, exist_ok=True)
    new = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows -> {CSV_PATH}")


def stasher():
    today = dt.date.today().isoformat()
    every = sorted(set(sitemap("https://stasher.com/sitemap/italy-cities.xml")))
    cities = [u for u in every if u.rstrip("/").split("/")[-1] in CAPITALS]
    print(f"{len(every)} Stasher city pages in Italy, {len(cities)} of them province capitals")

    def one(url):
        s = get(url, STASHER_PAUSE)
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', s, re.S)
        if not m:
            return []
        try:
            items = json.loads(m.group(1))["props"]["initialStateEncoded"]["stashpoints"][
                "locationStashpointsIO"]["data"]["data"]["items"]
        except Exception:
            return []
        city = url.rstrip("/").split("/")[-1]
        out = []
        for x in items:
            flags = x.get("features") or {}
            out.append(dict(seen_on=today, brand="stasher", city=city, name=x.get("name"),
                            lat=x.get("latitude"), lng=x.get("longitude"),
                            capacity=x.get("space_available"), reviews=x.get("rating_count"),
                            rating=x.get("rating"), price_from=(x.get("price") or 0) / 100,
                            note=f"bookings_bucket={x.get('booking_count_group')};"
                                 f"often_full={flags.get('often_fully_booked')}",
                            url=url))
        return out
    rows = []
    for url in cities:               # one at a time, on purpose
        rows += one(url)
    print(f"  {len(rows)} Stasher points, {sum(1 for r in rows if (r['reviews'] or 0) > 0)} with reviews")
    write(rows)


def stow():
    today = dt.date.today().isoformat()
    shops = [u for u in sitemap("https://www.stowyourbags.com/sitemaps/sitemap_en.xml")
             if re.search(r"/en/shop/[a-z-]+/[a-z0-9-]+/$", u)]
    print(f"{len(shops)} Stow Your Bags shop pages")

    def one(url):
        s = get(url)
        for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', s, re.S):
            try:
                d = json.loads(m.group(1))
            except Exception:
                continue
            if d.get("@type") not in ("SelfStorage", "LocalBusiness", "Store"):
                continue
            geo, rat = d.get("geo") or {}, d.get("aggregateRating") or {}
            offers = d.get("makesOffer") or []
            prices = [o.get("price") for o in offers if o.get("price")]
            addr = (d.get("address") or {}).get("streetAddress", "")
            country = (d.get("address") or {}).get("addressCountry", "")
            if country and country != "IT":
                return None
            return dict(seen_on=today, brand="stowyourbags", city=url.split("/en/shop/")[1].split("/")[0],
                        name=(d.get("name") or "").strip(), lat=geo.get("latitude"), lng=geo.get("longitude"),
                        capacity="", reviews=rat.get("ratingCount"), rating=rat.get("ratingValue"),
                        price_from=min(prices) if prices else "",
                        note=f"sizes={len(offers)};address={addr}", url=url)
        return None
    rows = []
    with ThreadPoolExecutor(THREADS) as ex:
        rows = [r for r in ex.map(one, shops) if r]
    print(f"  {len(rows)} Italian shops, {sum(r['reviews'] or 0 for r in rows)} reviews in total")
    write(rows)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("stasher", "all"):
        stasher()
    if cmd in ("stow", "all"):
        stow()
