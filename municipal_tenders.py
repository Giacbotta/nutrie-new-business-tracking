"""Weekly check of municipal tenders useful for a padel court (Nutrie board item E-90).

Reads only public pages, no login, no tokens, no paid services, standard library only.
Generic user agent "nutrie-ricerca/1.0", no personal data.

Sources by municipality (checked on 18/09/2026):
- JCity/Maggioli (Mogliano Veneto, Spresiano): notice board on <entity>.trasparenza-valutazione-merito.it,
  CSV export of the current notices, with a direct link to each act.
- Halley "mc" (Preganziol, Marcon, Casale sul Sile, Zero Branco, Casier, Quarto d'Altino):
  mc_p_ricerca.php, pages ?pag=N, direct link mc_p_dettaglio.php?id_pubbl=...
- Hypersic (Scorzè): albopretorioconsultazione.aspx, table without a link per act.
- J-Ente (Treviso): AlboPretorio, POST search and "scorri" pages, direct link per act.
- "Novità > Avvisi" page of the municipal WordPress sites (their RSS feed is empty).
- Aggregator: "Bandi altri enti" by Sport e Salute, only items in the Veneto region.
Venice is not covered: its site answers with an anti-bot protection.

An item is reported when its title contains at least one word of SPORT_AREA and at least one
of ACT (so "concessione" alone is not enough), and none of EXCLUDE (works, maintenance,
supplies), unless it mentions padel. The keyword lists stay in Italian on purpose: they are
matched against the Italian titles published by the municipalities.

Commands:
  python municipal_tenders.py check   # reads every source, records only the new items
  python municipal_tenders.py list    # prints everything seen so far

Files in data/:
  tenders-seen.csv   memory: municipality, date, title, link, keywords, first_seen
  tenders-new.md     each run adds a section on top with the date, the new items and the
                     sources that did not answer.
Schedule: once a week, Monday at 9:00 Italian time.
"""
import csv, datetime as dt, html, http.cookiejar, io, os, re, ssl, sys, time, urllib.parse, urllib.request

DATA = os.environ.get("NUTRIE_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
CSV_SEEN = os.path.join(DATA, "tenders-seen.csv")
MD_NEW = os.path.join(DATA, "tenders-new.md")
UA = "nutrie-ricerca/1.0"
FIELDS = ["municipality", "date", "title", "link", "keywords", "first_seen"]

# Italian keywords, matched against Italian titles (see the docstring)
SPORT_AREA = ["padel", "impianto sportivo", "impianti sportivi", "area sportiva", "aree sportive",
              "campo da tennis", "campi da tennis", "campi da gioco", "campo da gioco", "palestra",
              "palazzetto", "centro sportivo", "terreno", "terreni", "area verde", "aree verdi",
              "chiosco", "bar", "campetto", "campetti"]
ACT = ["concessione", "assegnazione", "gestione", "affidamento", "locazione", "affitto", "alienazione",
       "manifestazione di interesse", "manifestazioni di interesse", "avviso pubblico",
       "project financing", "finanza di progetto", "asta pubblica", "bando"]
# Noise: works and expenses on municipal gyms and land are not padel opportunities
EXCLUDE = ["manutenzione", "lavori", "fornitura", "liquidazione", "bonifica", "impegno di spesa"]

JCITY = {"Mogliano Veneto": "moglianoveneto", "Spresiano": "spresiano"}
HALLEY_MC = {
    "Preganziol": "https://servizionline.comune.preganziol.tv.it/mc/mc_p_ricerca.php",
    "Marcon": "https://servizionline.comune.marcon.ve.it/c027020/mc/mc_p_ricerca.php",
    "Casale sul Sile": "https://servizionline.comune.casalesulsile.tv.it/mc/mc_p_ricerca.php",
    "Zero Branco": "https://servizionline.comune.zerobranco.tv.it/mc/mc_p_ricerca.php",
    "Casier": "https://servizionline.comune.casier.tv.it/casier/mc/mc_p_ricerca.php",
    "Quarto d'Altino": "https://sac3.halleysac.it/c027031/mc/mc_p_ricerca.php",
}
HYPERSIC = {"Scorzè": "https://servizionline.hspromilaprod.hypersicapp.net/cmsscorze/portale/albopretorio/albopretorioconsultazione.aspx?P=400"}
JENTE = {"Treviso": "https://servizitreviso.jentecloud.net/jalbopretorio01/AlboPretorio"}
NOTICES = {
    "Preganziol": "https://www.comune.preganziol.tv.it/novita/?tipo=avviso",
    "Marcon": "https://www.comune.marcon.ve.it/novita/?tipo=avviso",
    "Casale sul Sile": "https://www.comune.casalesulsile.tv.it/novita/?tipo=avviso",
    "Zero Branco": "https://www.comune.zerobranco.tv.it/novita/?tipo=avviso",
    "Casier": "https://www.comune.casier.tv.it/novita/?tipo=avviso",
    "Quarto d'Altino": "https://www.comune.quartodaltino.ve.it/novita/?tipo=avviso",
}
AREA = ["mogliano", "preganziol", "marcon", "casale sul sile", "casalesulsile", "scorz",
        "zero branco", "zerobranco", "casier", "quarto d'altino", "quartodaltino", "spresiano",
        "treviso", "venezia", "mestre"]
NOT_COVERED = {"Venezia": "www.comune.venezia.it answers with an anti-bot protection (Incapsula): not forced"}


def fetch(url, data=None, jar=None, timeout=90, attempts=3):
    for n in range(attempts):  # Halley servers sometimes close the connection: retry
        try:
            return _fetch(url, data, jar, timeout)
        except OSError:
            if n == attempts - 1:
                raise
            time.sleep(30)


# Halley servers do not send the Sectigo intermediate certificate: Windows fetches it by itself, Linux (GitHub Actions) does not.
SSL_CTX = ssl.create_default_context()
SSL_CTX.load_verify_locations(os.path.join(os.path.dirname(os.path.abspath(__file__)), "certificates", "sectigo-intermediates.pem"))


def _fetch(url, data, jar, timeout):
    https = urllib.request.HTTPSHandler(context=SSL_CTX)
    opener = urllib.request.build_opener(https, urllib.request.HTTPCookieProcessor(jar)) if jar is not None else urllib.request.build_opener(https)
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None,
                                 headers={"User-Agent": UA})
    with opener.open(req, timeout=timeout) as r:
        raw = r.read()
        cs = r.headers.get_content_charset() or "utf-8"
    return raw.decode(cs, "replace")


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def keywords(title):
    t = title.lower().replace("’", "'")
    find = lambda words: [p for p in words if re.search(r"(?<![a-zà-ù])" + re.escape(p) + r"(?![a-zà-ù])", t)]
    a, b = find(SPORT_AREA), find(ACT)
    if find(EXCLUDE) and "padel" not in a:
        return []
    return (a + b) if a and b else []


# ---------- readers, one per platform ----------

def jcity(host):
    base = f"https://{host}.trasparenza-valutazione-merito.it/web/trasparenza"
    s = fetch(base + "/albo-pretorio")
    detail = None
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', s, re.S):
        if text(m.group(2)).lower() == "albo pretorio" and "dettaglio" in m.group(1):
            detail = html.unescape(m.group(1)); break
    if not detail:
        raise RuntimeError("link to the notice board not found")
    grid = re.findall(r"papca(?:-ap)?/-/papca/igrid/(\d+)", fetch(detail))
    if not grid:
        raise RuntimeError("notice board grid not found")
    jar = http.cookiejar.CookieJar()
    fetch(f"{base}/papca-ap/-/papca/igrid/{grid[0]}", jar=jar)
    export = (f"{base}/papca-ap?p_p_id=jcitygovalbopubblicazioni_WAR_jcitygovalbiportlet&p_p_lifecycle=2"
              "&p_p_state=pop_up&p_p_mode=view&p_p_resource_id=exportList&p_p_cacheability=cacheLevelPage"
              "&_jcitygovalbopubblicazioni_WAR_jcitygovalbiportlet_format=csv"
              "&_jcitygovalbopubblicazioni_WAR_jcitygovalbiportlet_action=mostraLista"
              "&_jcitygovalbopubblicazioni_WAR_jcitygovalbiportlet_fromAction=eseguiFiltro")
    rows = list(csv.DictReader(io.StringIO(fetch(export, jar=jar).lstrip("﻿"))))
    return [dict(date=r.get("Data inizio pubblicazione", ""), title=r.get("Oggetto", "").strip(),
                 link=r.get("Url atto", "").strip()) for r in rows if r.get("Oggetto")]


def halley_mc(url):
    base = url.rsplit("/", 1)[0]
    s = fetch(url)
    pages = [int(x) for x in re.findall(r"pag=(\d+)", s)] or [0]
    items = []
    for p in range(0, min(max(pages), 8) + 1):  # pag starts at 0; the first pages are enough for a weekly run
        if p > 0:
            s = fetch(f"{url}?&pag={p}")
        for r in re.findall(r"<tr[^>]*>(.*?)</tr>", s, re.S):
            link = re.search(r'href="([^"]*mc_p_dettaglio\.php\?id_pubbl=\d+)"', r)
            if not link:
                continue
            subject = re.search(r"Oggetto\s*</[^>]+>(.*?)(?:Numero atto|Data atto|$)", r, re.S)
            start = re.search(r"Data inizio\s*</[^>]+>\s*(?:<[^>]+>\s*)*(\d{2}/\d{2}/\d{4})", r)
            dates = re.findall(r"\d{2}/\d{2}/\d{4}", text(r))
            items.append(dict(date=(start.group(1) if start else (dates[-2] if len(dates) >= 2 else "")),
                              title=text(subject.group(1)) if subject else text(r)[:300],
                              link=urllib.parse.urljoin(base + "/", html.unescape(link.group(1)))))
    return items


def hypersic(url):
    s = fetch(url)
    items = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", s, re.S):
        cells = [text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        cells = [c for c in cells if c]
        long_cells = [c for c in cells if len(c) > 40]
        if not long_cells:
            continue
        dates = [c for c in cells if re.fullmatch(r"\d{2}/\d{2}/\d{4}", c)]
        items.append(dict(date=dates[0] if dates else "", title=long_cells[0], link=url))
    return items


def jente(url):
    jar = http.cookiejar.CookieJar()
    fetch(url, jar=jar)
    s = fetch(url, data={"servizio": "cerca", "tipoPratica": "", "codiceRichiedente": "", "dadata": "",
                         "adata": "", "oggetto": "", "ordina": "ANNO_REGISTRO,NUMERO_REGISTRO",
                         "delXPag": "50", "sort": "DESC"}, jar=jar)
    total = re.search(r"Elementi trovati:\s*(\d+)", text(s))
    total = int(total.group(1)) if total else 50
    base = url.rsplit("/", 1)[0] + "/"
    items = []
    first = 1
    while True:
        for li in re.findall(r'<li class="list-group-item">(.*?)</li>', s, re.S):
            a = re.search(r'<a href="([^"]*idPratica=\d+[^"]*)"[^>]*>(.*?)</a>', li, re.S)
            if not a:
                continue
            pub = re.search(r"(\d{2}/\d{2}/\d{4}) al", text(li))
            items.append(dict(date=pub.group(1) if pub else "", title=text(a.group(2)),
                              link=urllib.parse.urljoin(base, html.unescape(a.group(1)))))
        first += 50
        if first > total or first > 1000:
            break
        s = fetch(f"{url}?servizio=cerca&azione=scorri&daRecord={first}&aRecord={min(first + 49, total)}", jar=jar)
    return items


def site_notices(url):
    """Public "Novità > Avvisi" page of the municipal WordPress sites (Design Comuni Italia).
    Their RSS feed was empty on 18/09/2026, so the HTML page is read instead."""
    s = fetch(url)
    items, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="(https?://[^"]*/novita/[^"?#]+/)"[^>]*>(.*?)</a>', s, re.S):
        t = text(m.group(2)).lstrip("​")
        if len(t) > 15 and m.group(1) not in seen:
            seen.add(m.group(1))
            items.append(dict(date="", title=t, link=m.group(1)))
    return items


def sport_e_salute(url):
    """National list "Bandi altri enti" by Sport e Salute: keeps only the Veneto items that name a
    municipality of the area (AREA). It is an aggregator: it does not replace the notice boards,
    but it partly covers Venice, whose site blocks automated reads."""
    s = fetch(url)
    items, seen = [], set()
    for card in re.split(r'<div class="sppb-addon-image-layouts"', s)[1:]:
        if 'data-regione="Veneto"' not in card[:400]:
            continue
        tit = re.search(r'<h5 class="title_card">(.*?)</h5>', card, re.S)
        link = re.search(r'<a href="([^"]+)"[^>]*>\s*Scopri di pi', card)
        deadline = re.search(r"Termine di presentazione domanda:</strong>\s*([^<]+)<", card)
        if not tit or not link:
            continue
        title, href = text(tit.group(1)), html.unescape(link.group(1))
        if href in seen or not any(z in (title + " " + href).lower() for z in AREA):
            continue
        seen.add(href)
        items.append(dict(date=("deadline " + deadline.group(1).strip()) if deadline else "", title=title, link=href))
    return items


def sources():
    for c, h in JCITY.items():
        yield c, "JCity notice board", lambda h=h: jcity(h)
    for c, u in HALLEY_MC.items():
        yield c, "Halley notice board", lambda u=u: halley_mc(u)
    for c, u in HYPERSIC.items():
        yield c, "Hypersic notice board", lambda u=u: hypersic(u)
    for c, u in JENTE.items():
        yield c, "J-Ente notice board", lambda u=u: jente(u)
    yield "Veneto (Sport e Salute)", "aggregator", lambda: sport_e_salute(
        "https://www.sportesalute.eu/bandi-e-avvisi/bandi-altri-enti.html")
    for c, u in NOTICES.items():
        yield c, "site notices", lambda u=u: site_notices(u)


# ---------- memory ----------

def load_seen():
    if not os.path.exists(CSV_SEEN):
        return []
    with open(CSV_SEEN, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def key(r):
    link = r["link"]
    generic = "albopretorioconsultazione" in link
    return (r["municipality"], r["title"].lower() if generic or not link else link)


def check():
    today = dt.date.today().isoformat()
    seen = load_seen()
    known = {key(r) for r in seen}
    new, outcomes = [], []
    for municipality, kind, read in sources():
        try:
            items = read()
        except Exception as e:  # a municipality that does not answer does not stop the run
            outcomes.append((municipality, kind, f"NOT ANSWERING: {type(e).__name__}: {str(e)[:120]}"))
            continue
        matched = 0
        for v in items:
            k = keywords(v["title"])
            if not k:
                continue
            matched += 1
            r = dict(municipality=municipality, date=v["date"], title=v["title"][:400], link=v["link"],
                     keywords=", ".join(k), first_seen=today)
            if key(r) not in known:
                known.add(key(r)); new.append(r); seen.append(r)
        outcomes.append((municipality, kind, f"{len(items)} items read, {matched} with the keywords"))
    with open(CSV_SEEN, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(seen)
    block = [f"## Run of {today}", ""]
    if new:
        block += [f"**{len(new)} new items.**", ""]
        for r in new:
            block.append(f"- **{r['municipality']}**, {r['date']}: {r['title']} — {r['link']} (keywords: {r['keywords']})")
    else:
        block.append("No new items.")
    block += ["", "Sources read:", ""] + [f"- {c} ({t}): {e}" for c, t, e in outcomes]
    block += [f"- {c}: not covered, {m}" for c, m in NOT_COVERED.items()] + ["", ""]
    old = open(MD_NEW, encoding="utf-8").read() if os.path.exists(MD_NEW) else ""
    header = "# New municipal tenders (padel)\n\nWritten by `municipal_tenders.py`; the latest runs are on top.\n\n"
    body = old.replace(header, "", 1) if old.startswith(header) else old
    with open(MD_NEW, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(block) + body)
    print("\n".join(block))


def list_seen():
    for r in load_seen():
        print(f"{r['first_seen']} | {r['municipality']} | {r['date']} | {r['title'][:120]} | {r['link']}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    {"check": check, "list": list_seen}[sys.argv[1] if len(sys.argv) > 1 else "check"]()
