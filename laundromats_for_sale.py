"""Weekly check of laundromats for sale in the provinces of Padova, Treviso and Venezia (Nutrie board item E-91).

Requested on 22/09/2026. Reads only public listings, no login and no tokens.
Portals read (checked on 22/09/2026):
- Subito.it: public search API hades.subito.it (Veneto region, word in the title), categories
  commercial premises, equipment and appliances (a 2025 listing had been filed under appliances).
  Standard library.
- immobiliare.it: the "Lavanderie - tintorie" category of businesses for sale, one page per province.
  Keyword search does not work on immobiliare, the category does.
- Trovit (case.trovit.it): "lavanderia" search in Veneto; it also aggregates casa.it and other portals.
immobiliare and Trovit refuse plain requests (HTTP 403): they need curl_cffi, free, which presents
itself as a browser. If it is missing or a portal blocks, the run goes on and the portal is listed
as "not read" in the report.
Not read, with the reason: idealista (no laundromat category), Kijiji (closed in 2022),
comprovendoattivita.com (does not resolve since August 2026), Waa2 and b2scout (no readable search),
business-transfer brokers (anonymous listings, a contact is needed).
Known limit: a Subito listing without "lavanderia" (or the other words) in its title is missed.
A full-text search was tried on 22/09/2026 and dropped: it only added warehouses and buildings.

Each listing gets a "type" (self-service, staffed, equipment, to check): nothing is dropped, it is
classified. The type is a hint from keywords, not a verified fact. Only results outside the three
provinces and, among Subito equipment and appliances, household items (furniture, sinks) are dropped.
Search words and category names stay in Italian: they are matched against Italian listings.

Commands:
  python laundromats_for_sale.py check   # reads the portals, updates the files
  python laundromats_for_sale.py list    # prints the active listings

Files in data/:
  laundromats.csv          every listing seen, active and gone (read by the Google Sheet)
  laundromats-new.md       each run adds a section on top with the changes
  laundromats-email.md     text of the last run, used for the email notification
"""
import csv, datetime as dt, html, json, os, re, sys, time, urllib.parse, urllib.request

try:
    from curl_cffi import requests as browser  # immobiliare and Trovit
except ImportError:
    browser = None

DATA = os.environ.get("NUTRIE_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
CSV_SEEN = os.path.join(DATA, "laundromats.csv")
MD_NEW = os.path.join(DATA, "laundromats-new.md")
MD_EMAIL = os.path.join(DATA, "laundromats-email.md")
FIELDS = ["status", "type", "province", "town", "title", "price", "sqm", "portal", "link",
          "published", "first_seen", "last_seen", "id", "possible_duplicate"]

PROVINCES = {"PD": "Padova", "TV": "Treviso", "VE": "Venezia"}
CODE_OF = {v: k for k, v in PROVINCES.items()}
TODAY = dt.date.today().isoformat()

SUBITO_WORDS = ["lavanderia", "lavanderie", "lavasecco", "tintoria"]
SUBITO_CATEGORIES = {"uffici-locali-commerciali", "attrezzature", "elettrodomestici"}
# Among equipment and appliances keep only coin-laundry items, not household furniture
EQUIPMENT_OK = re.compile(r"self|gettoni|automatic|industrial|professional|attivit|cassa|lavatric|asciugatric|essiccat", re.I)
HOUSEHOLD = re.compile(r"^\s*(mobil|lavell|lavandin|lavabo|armadi|colonna|vasca|pensile|cest)", re.I)
# Trovit mixes in homes with a "laundry room": keep only what talks about a business
TROVIT_BUSINESS = re.compile(r"lavanderia (self|a gettoni|automatica|industriale)|lavasecco|attivit[aà] (di )?lavanderia|lavanderia avviata|cedesi.*lavanderia|lavanderia in vendita", re.I)
IMMOBILIARE_PROVINCES = ["padova-provincia", "treviso-provincia", "venezia-provincia"]
TROVIT_SEARCHES = ["lavanderia-veneto", "lavanderia-self-service-veneto"]


def clean_town(s):
    """"30035, Mirano" and "a San Giuseppe, Cavarzere" become "Mirano" and "Cavarzere"."""
    return re.sub(r"^(a |\d+\s*)", "", s.split(",")[-1].strip()).strip()


def type_of(text, category=""):
    t = text.lower()
    if category in ("attrezzature", "elettrodomestici") and not re.search(r"attivit|avviat|cedo|cessione|locale", t):
        return "equipment"
    if re.search(r"self|gettoni|automatic", t):
        return "self-service"
    if re.search(r"lavasecco|pulitura|pulisecco|tintoria|stiro|professional", t):
        return "staffed"
    return "to check"


def number(s):
    s = re.sub(r"[^\d]", "", str(s or ""))
    return int(s) if s else ""


# ---------- Subito ----------

def read_subito():
    found, seen = [], set()
    h = {"User-Agent": "Mozilla/5.0 (nutrie-ricerca/1.0)", "X-Subito-Channel": "web", "Accept": "application/json"}
    for word in SUBITO_WORDS:
        for ad_type in ("s", "u"):  # for sale and for rent (some businesses are leased rather than sold)
            start = 0
            while True:
                q = urllib.parse.urlencode({"q": word, "r": 6, "t": ad_type, "qso": "true", "lim": 100, "start": start})
                try:
                    with urllib.request.urlopen(urllib.request.Request("https://hades.subito.it/v1/search/items?" + q, headers=h), timeout=30) as r:
                        d = json.loads(r.read().decode("utf-8"))
                except Exception:
                    if ad_type == "s":
                        raise
                    break
                ads = d.get("ads", [])
                for a in ads:
                    cat = a.get("category", {}).get("friendly_name", "")
                    geo = a.get("geo", {})
                    prov = geo.get("city", {}).get("short_name", "")
                    link = a.get("urls", {}).get("default", "")
                    if cat not in SUBITO_CATEGORIES or prov not in PROVINCES or link in seen:
                        continue
                    title = a.get("subject", "").strip()
                    body = a.get("body", "")
                    price = number((next((f for f in a.get("features", []) if f.get("uri") == "/price"), {}).get("values") or [{}])[0].get("key"))
                    if cat != "uffici-locali-commerciali" and (not EQUIPMENT_OK.search(title) or HOUSEHOLD.search(title)
                                                              or (price != "" and price < 500)):
                        continue
                    seen.add(link)
                    feat = {f.get("uri"): f.get("values", [{}])[0] for f in a.get("features", [])}
                    found.append({
                        "id": "subito-" + link.rsplit("-", 1)[-1].replace(".htm", ""),
                        "portal": "Subito", "province": prov,
                        "town": geo.get("town", {}).get("value", "") or geo.get("city", {}).get("value", ""),
                        "title": title + (" [rent]" if ad_type == "u" else ""), "price": price,
                        "sqm": number(feat.get("/size", {}).get("key")), "link": link,
                        "published": a.get("dates", {}).get("display", "")[:10],
                        "type": type_of(title + " " + body, cat),
                    })
                start += len(ads)
                if not ads or start >= d.get("count_all", 0) or start >= 500:
                    break
    return found


def download(url, attempts=4):
    """immobiliare answers 403 intermittently (seen on 22/09/2026): retry with growing pauses."""
    for i in range(attempts):
        r = browser.get(url, impersonate="chrome", timeout=40)
        if r.status_code not in (403, 429, 503):
            break
        time.sleep(15 * (i + 1))
    time.sleep(3)
    return r


# ---------- immobiliare.it ----------

def _next_data(page):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    return json.loads(m.group(1)) if m else None


def _results(o):
    if isinstance(o, dict):
        v = o.get("results")
        if isinstance(v, list) and v and isinstance(v[0], dict) and "realEstate" in v[0]:
            return o
        for x in o.values():
            r = _results(x)
            if r:
                return r
    elif isinstance(o, list):
        for x in o:
            r = _results(x)
            if r:
                return r
    return None


def read_immobiliare():
    """In the list the title is generic ("Attività commerciale...") and the detail page answers 403
    even to curl_cffi (22/09/2026): the type stays "to check", unless the same deal is on another
    portal (see mark_duplicates) or it was corrected by hand in the CSV."""
    found = []
    already_seen = load()
    for prov in IMMOBILIARE_PROVINCES:
        page = 1
        while page <= 10:
            url = f"https://www.immobiliare.it/vendita-attivita/{prov}/con-lavanderia-tintoria/" + (f"?pag={page}" if page > 1 else "")
            r = download(url)
            if r.status_code != 200:
                raise RuntimeError(f"{prov}: HTTP {r.status_code}")
            block = _results(_next_data(r.text) or {})
            if not block:
                break
            for x in block["results"]:
                est = x["realEstate"]
                p = (est.get("properties") or [{}])[0]
                loc = p.get("location", {})
                code = CODE_OF.get(loc.get("province", ""), "")
                if not code:
                    continue
                title = est.get("title", "")
                old = already_seen.get(f"immobiliare-{est['id']}")
                found.append({
                    "id": f"immobiliare-{est['id']}", "portal": "immobiliare.it", "province": code,
                    "town": loc.get("city", ""), "title": title,
                    "price": number((est.get("price") or {}).get("value")), "sqm": number(p.get("surface")),
                    "link": f"https://www.immobiliare.it/annunci/{est['id']}/", "published": "",
                    "type": old["type"] if old else type_of(title),
                })
            if page >= int(block.get("maxPages") or block.get("lastPage") or 1):
                break
            page += 1
    return found


# ---------- Trovit ----------

def read_trovit():
    found, seen = [], set()
    for search in TROVIT_SEARCHES:
        r = download("https://case.trovit.it/" + search)
        if r.status_code == 404:
            continue
        if r.status_code != 200:
            raise RuntimeError(f"{search}: HTTP {r.status_code}")
        for art in re.findall(r"<article.*?</article>", r.text, re.S):
            m_id = re.search(r'data-id="([^"]+)"', art)
            if not m_id or m_id.group(1) in seen:
                continue
            m_tit = re.search(r'class="js-listing"[^>]*title="([^"]*)"', art) or re.search(r'title="([^"]*)" class="js-listing"', art)
            m_addr = re.search(r'class="address_property-type">(.*?)</span>', art, re.S)
            address = html.unescape(re.sub(r"<[^>]+>", "", m_addr.group(1))) if m_addr else ""
            m_prov = re.search(r"a (.+?), Provincia di (\w+)", address)
            if not m_prov:
                continue
            code = CODE_OF.get(m_prov.group(2), "")
            if not code:
                continue
            seen.add(m_id.group(1))
            m_price = re.search(r'data-test="price__actual">([^<]*)<', art)
            m_sqm = re.search(r"<p>(\d+) m", art)
            m_desc = re.search(r"</span>\s*<p>(.*?)</p>", art, re.S)
            source = re.search(r"<small>([^<]*)</small>", art)
            title = html.unescape(m_tit.group(1)) if m_tit else address
            desc = html.unescape(re.sub(r"<[^>]+>", "", m_desc.group(1))) if m_desc else ""
            if not TROVIT_BUSINESS.search(title + " " + desc):
                continue
            found.append({
                "id": "trovit-" + m_id.group(1), "portal": "Trovit" + (f" ({source.group(1).strip()})" if source else ""),
                "province": code, "town": clean_town(m_prov.group(1)), "title": title,
                "price": number(m_price.group(1)) if m_price else "", "sqm": number(m_sqm.group(1)) if m_sqm else "",
                "link": "https://case.trovit.it/detail/" + m_id.group(1), "published": "",
                "type": type_of(title + " " + desc),
            })
    return found


# ---------- memory and report ----------

def load():
    if not os.path.exists(CSV_SEEN):
        return {}
    with open(CSV_SEEN, encoding="utf-8", newline="") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def save(rows):
    order = {"active": 0, "gone": 1}
    rows = sorted(rows, key=lambda r: (order.get(r["status"], 2), r["first_seen"], r["published"]))
    rows = sorted(rows, key=lambda r: order.get(r["status"], 2))
    with open(CSV_SEEN, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def mark_duplicates(rows):
    """Same town and same price on different portals: probably the same deal."""
    for r in rows:
        r["possible_duplicate"] = ""
    for a in rows:
        for b in rows:
            if a is not b and a["portal"] != b["portal"] and a["price"] and str(a["price"]) == str(b["price"]) \
                    and a["town"].lower() == b["town"].lower():
                a["possible_duplicate"] = b["link"]
                if a["type"] == "to check" and b["type"] != "to check":
                    a["type"] = b["type"] + " (from duplicate)"


def md_line(r):
    price = f"€{int(r['price']):,}" if str(r["price"]).isdigit() else "price not stated"
    sqm = f", {r['sqm']} m²" if r.get("sqm") else ""
    return f"- **{r['town']} ({r['province']})**, {r['title']}: {price}{sqm}. Type: {r['type']}. {r['portal']}: {r['link']}"


def check():
    readers = [("Subito", read_subito)]
    if browser:
        readers += [("immobiliare.it", read_immobiliare), ("Trovit", read_trovit)]
    not_read = [] if browser else ["immobiliare.it and Trovit: curl_cffi missing (pip install curl_cffi)"]
    read_ok, found = [], []
    for name, f in readers:
        try:
            x = f()
            found += x
            read_ok.append(name)
            print(f"{name}: {len(x)} listings in the three provinces")
        except Exception as e:
            not_read.append(f"{name}: {str(e)[:120]}")
            print(f"{name}: NOT READ, {e}")

    memory = load()
    new, back = [], []
    by_id = {r["id"]: r for r in found}
    for id_, r in by_id.items():
        old = memory.get(id_)
        if not old:
            r.update(status="active", first_seen=TODAY, last_seen=TODAY)
            new.append(r)
        else:
            if old["status"] != "active":
                back.append(r)
            if str(old.get("price")) != str(r["price"]) and old.get("price") and r["price"]:
                r["title"] = r["title"] + f" [price changed from €{old['price']}]"
            r.update(status="active", first_seen=old["first_seen"], last_seen=TODAY,
                     published=r["published"] or old.get("published", ""))
        memory[id_] = r
    # Gone only if its portal was actually read in this run
    gone = []
    for id_, r in memory.items():
        portal = r["portal"].split(" (")[0]
        if id_ not in by_id and r["status"] == "active" and portal in read_ok:
            r["status"] = "gone"
            gone.append(r)
    rows = list(memory.values())
    mark_duplicates(rows)
    save(rows)

    active = [r for r in rows if r["status"] == "active"]
    sec = [f"## Run of {TODAY}", "",
           f"Portals read: {', '.join(read_ok) or 'none'}. Active listings in the provinces of Padova, Treviso and Venezia: {len(active)}.", ""]
    if new:
        sec += ["**New:**", ""] + [md_line(r) for r in new] + [""]
    if back:
        sec += ["**Back online:**", ""] + [md_line(r) for r in back] + [""]
    if gone:
        sec += ["**No longer online** (sold, withdrawn or expired):", ""] + [md_line(r) for r in gone] + [""]
    if not (new or back or gone):
        sec += ["No changes since the previous run.", ""]
    if not_read:
        sec += ["**Portals not read in this run:** " + "; ".join(not_read), ""]
    previous = open(MD_NEW, encoding="utf-8").read() if os.path.exists(MD_NEW) else "# Laundromats for sale, weekly changes (E-91)\n\n"
    head, _, tail = previous.partition("\n\n")
    with open(MD_NEW, "w", encoding="utf-8") as f:
        f.write(head + "\n\n" + "\n".join(sec) + "\n" + tail)
    changes = bool(new or back or gone)  # a portal not read goes in the report, it does not send an email
    with open(MD_EMAIL, "w", encoding="utf-8") as f:
        f.write("\n".join(sec[2:]) + "\nFull list in the Google Sheet (Laundromats tab) and in data/laundromats.csv.\n")
    # For GitHub Actions: tells the next step whether there is something to notify
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"changes={'yes' if changes else 'no'}\nnew={len(new)}\n")
    print(f"New {len(new)}, back {len(back)}, gone {len(gone)}, active {len(active)}")


def list_active():
    for r in load().values():
        if r["status"] == "active":
            print(md_line(r))


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    {"check": check, "list": list_active}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
