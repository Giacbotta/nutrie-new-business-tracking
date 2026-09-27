"""Where the other luggage operators are, when their occupancy cannot be read (board item E-93).

Two brands Giacomo asked for on 27/09/2026. Neither publishes how full it is, and it is worth
saying why, because the answer is different in each case:

  Locker in the City  a real locker operator, 14 shops in 8 Italian cities. Its site is built
                      statically and the booking flow talks to an authenticated API
                      (acrjvculoi.execute-api.eu-central-1.amazonaws.com), which answers
                      "Unauthorized Access" without a key the pages never carry. Its robots.txt
                      does not exist, so nothing is disallowed - there is simply nothing to read.
  iVano               8 locations, among them Cannaregio and San Polo, so on Nutrie's own doorstep.
                      The site is a static Aruba page with no online booking at all. Its robots.txt
                      asks for one request every 30 seconds, and that is what this does.

So they get a census, not an hourly reading: where they are, what the page says, how many reviews.
They stay out of the occupancy comparison, which is about occupied units, and appear on the page
as operators mapped without availability.

  python other_operators.py census
"""
import csv, datetime as dt, json, os, re, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CSV_PATH = os.path.join(DATA, "other-operators.csv")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
FIELDS = ["seen_on", "brand", "city", "name", "address", "lat", "lng", "reviews", "rating", "url"]

LITC_INDEX = "https://lockerinthecity.com/it/dove-siamo/"
IVANO_PAGES = ["napoli", "padova", "roma", "palermo", "ortigia", "cannaregio", "san-polo"]
IVANO_PAUSE = 30.0      # i-vano.it/robots.txt: Crawl-delay: 30


def get(url, pause=0.6):
    for _ in range(3):
        try:
            time.sleep(pause)
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read().decode("utf8", "replace")
        except Exception:
            time.sleep(4)
    return ""


def json_ld(page):
    """Every JSON-LD block of a page, as dicts."""
    out = []
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            d = json.loads(m.group(1))
        except Exception:
            continue
        out += d if isinstance(d, list) else [d]
    return out


def locker_in_the_city(today):
    index = get(LITC_INDEX)
    shops = sorted({(m.group(1), m.group(2))
                    for m in re.finditer(r'href="/it/locker/italia/([a-z-]+)/([a-z0-9-]+)/?"', index)})
    print(f"{len(shops)} Locker in the City shops in Italy")
    rows = []
    for city, slug in shops:
        page = get(f"https://lockerinthecity.com/it/locker/italia/{city}/{slug}/")
        name, addr, lat, lng, reviews, rating = slug.replace("-", " ").title(), "", "", "", "", ""
        for d in json_ld(page):
            if d.get("@type") in ("LocalBusiness", "SelfStorage", "Store"):
                name = (d.get("name") or name).strip()
                a = d.get("address") or {}
                addr = a.get("streetAddress", "") if isinstance(a, dict) else ""
                geo = d.get("geo") or {}
                lat, lng = geo.get("latitude", ""), geo.get("longitude", "")
                rat = d.get("aggregateRating") or {}
                reviews, rating = rat.get("ratingCount", ""), rat.get("ratingValue", "")
                break
        if not lat:
            # the shop pages carry the map centre as "<lat>,"lng":<lng>" in the Mapbox config
            m = re.search(r'(-?\d{2}\.\d{4,})"?\s*,\s*"lng"\s*:\s*(-?\d{1,2}\.\d{4,})', page)
            if m:
                lat, lng = m.group(1), m.group(2)
        if not addr:
            m = re.search(r"((?:Via|Viale|Piazza|Largo|Corso)\s+[A-Za-zÀ-ú.' ]{3,40},\s*\d+[A-Za-z]?)", page)
            addr = m.group(1) if m else ""
        if not reviews:
            m = re.search(r'([\d.]+)\s*recensioni', page)
            reviews = m.group(1).replace(".", "") if m else ""
        rows.append(dict(seen_on=today, brand="lockerinthecity", city=city, name=name, address=addr,
                         lat=lat, lng=lng, reviews=reviews, rating=rating,
                         url=f"https://lockerinthecity.com/it/locker/italia/{city}/{slug}/"))
        print(f"  {city:22} {name[:34]:34} {lat or '-'}")
    return rows


def ivano(today):
    rows = []
    print(f"{len(IVANO_PAGES)} iVano pages, one every {IVANO_PAUSE:.0f}s as its robots.txt asks")
    for slug in IVANO_PAGES:
        page = get(f"https://i-vano.it/{slug}", IVANO_PAUSE)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", page, flags=re.S)))
        addr = (re.search(r"((?:Via|Viale|Piazza|Corso|Calle|Fondamenta|Campo)\s+[A-Za-zàèéìòù'\. ]+,?\s*\d*)", text) or [None, ""])[1]
        rows.append(dict(seen_on=today, brand="ivano", city=slug, name=f"iVano {slug.replace('-', ' ').title()}",
                         address=addr.strip(), lat="", lng="", reviews="", rating="",
                         url=f"https://i-vano.it/{slug}"))
        print(f"  {slug:22} {addr[:44]}")
    return rows


def census():
    today = dt.date.today().isoformat()
    rows = locker_in_the_city(today) + ivano(today)
    os.makedirs(DATA, exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} locations -> {CSV_PATH}")


if __name__ == "__main__":
    census()
