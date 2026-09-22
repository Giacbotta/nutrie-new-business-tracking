"""Rilevamento ripetuto dell'occupazione dei campi da padel su Playtomic (E-86).

Legge solo pagine e dati pubblici, senza login:
- pagina del club  https://playtomic.com/clubs/<slug>  (tenant_id, campi, orari)
- slot liberi      https://playtomic.com/api/clubs/availability?tenant_id=<id>&date=AAAA-MM-GG&sport_id=PADEL

Comandi (lanciare dalla radice di Nutrie Brain):
  python workspace/padel/rilevamento_playtomic.py rileva            # stato delle mezz'ore che iniziano nelle prossime 2 ore
  python workspace/padel/rilevamento_playtomic.py anticipo          # stato di domani e dei 6 giorni dopo, per misurare la sottostima
  python workspace/padel/rilevamento_playtomic.py riepilogo         # ore occupate per campo, per circolo, tipo, fascia, feriale/festivo

Programmazione proposta: "rileva" ogni 2 ore dalle 07:00 alle 23:00 (9 letture al giorno),
"anticipo" una volta alle 21:00, per 2-3 settimane. Ogni mezz'ora prende lo stato
dell'ultima lettura fatta prima che inizi: è quasi l'occupazione finale.
Una mezz'ora è "occupata" se nessuno slot prenotabile la copre: include partite,
corsi e blocchi del club, e i buchi troppo corti per essere prenotati.
"""
import csv, datetime as dt, json, math, os, re, sys, time, urllib.request, collections

QUI = os.environ.get("NUTRIE_DATI", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dati"))  # copia per GitHub Actions: i dati stanno in dati/
CSV_ORA = os.path.join(QUI, "playtomic-occupazione.csv")
CSV_ANTICIPO = os.path.join(QUI, "playtomic-anticipo.csv")
MOGLIANO = (45.5616, 12.2363)
# Circoli con disponibilità esposta online, verificati il 17/09/2026 (Padel Circus escluso: non espone slot)
SLUGS = ["sporting-club-mestre", "padel-by-fitup-di-zero-branco", "aquafit-padel", "sph-venezia",
         "padel-treviso", "altino-sport-village", "barchessa-pickleball", "h-farm-campus",
         "sph-treviso-sporting", "marina-sporting"]
CAMPI = ["rilevato_il", "giorno", "giorno_sett", "tipo_giorno", "circolo", "slug", "campo", "copertura",
         "inizio", "fascia", "stato", "prezzo_90", "prezzo_60"]


def leggi(url, tentativi=3):
    for i in range(tentativi):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return urllib.request.urlopen(req, timeout=30).read().decode("utf8", "replace")
        except Exception:
            time.sleep(3)
    return ""


def minuti(s):
    h, m = map(int, s[:5].split(":"))
    return h * 60 + m


def fascia(t):
    return "mattina" if t < 720 else "pranzo" if t < 840 else "pomeriggio" if t < 1080 else "sera"


def club(slug):
    s = leggi(f"https://playtomic.com/clubs/{slug}").replace('\\"', '"')
    tid = re.search(r'"tenant_id":"([0-9a-f-]{36})"', s)
    if not tid:
        return None
    nome = re.search(r'"tenant_name":"([^"]*)"', s)
    i = s.find('"resources":[')
    campi = []
    for m in re.finditer(r'\{"resourceId":"([0-9a-f-]{36})","name":"([^"]*)","sport":"PADEL","features":\[([^\]]*)\]', s[i:i + 20000] if i >= 0 else ""):
        f = m.group(3)
        cop = "scoperto" if "outdoor" in f else "coperto"
        campi.append((m.group(1), m.group(2).strip(), cop))
    orari = {}
    k = s.find('opening_hours":{"')
    for m in re.finditer(r'"([A-Z]+)":\{"opening_time":"([\d:]+)","closing_time":"([\d:]+)"\}', s[k:k + 900] if k >= 0 else ""):
        orari[m.group(1)] = (minuti(m.group(2)), minuti(m.group(3)) or 1440)
    return dict(slug=slug, tid=tid.group(1), nome=(nome.group(1) if nome else slug).strip(), campi=campi, orari=orari)


def stato_giorno(c, giorno, dalle=0, alle=1440):
    raw = leggi(f"https://playtomic.com/api/clubs/availability?tenant_id={c['tid']}&date={giorno.isoformat()}&sport_id=PADEL")
    try:
        dati = json.loads(raw)
    except Exception:
        return []
    libere = collections.defaultdict(set)
    prezzi = collections.defaultdict(dict)
    for r in dati:
        for sl in r["slots"]:
            st = minuti(sl["start_time"])
            prezzi[(r["resource_id"], st)][sl["duration"]] = sl["price"]
            for t in range(st, st + sl["duration"], 30):
                libere[r["resource_id"]].add(t)
    o = c["orari"].get(giorno.strftime("%A").upper())
    if not o:
        return []
    apre, chiude = o
    if chiude <= apre:
        chiude = 1440
    righe = []
    for rid, nome, cop in c["campi"]:
        for t in range(max(apre, dalle), min(chiude, alle), 30):
            p = prezzi.get((rid, t), {})
            righe.append(dict(giorno=giorno.isoformat(), giorno_sett=giorno.strftime("%a"),
                              tipo_giorno="festivo" if giorno.weekday() >= 5 else "feriale",
                              circolo=c["nome"], slug=c["slug"], campo=nome, copertura=cop,
                              inizio=f"{t // 60:02d}:{t % 60:02d}", fascia=fascia(t),
                              stato="libero" if t in libere[rid] else "occupato",
                              prezzo_90=p.get(90, ""), prezzo_60=p.get(60, "")))
    return righe


def scrivi(percorso, righe):
    nuovo = not os.path.exists(percorso)
    with open(percorso, "a", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPI)
        if nuovo:
            w.writeheader()
        for r in righe:
            w.writerow(r)


def rileva(finestra=120):
    ora = dt.datetime.now()
    adesso = ora.hour * 60 + ora.minute
    timbro = ora.strftime("%Y-%m-%d %H:%M")
    tot = 0
    for slug in SLUGS:
        c = club(slug)
        if not c:
            print("pagina non leggibile:", slug); continue
        dalle = (adesso // 30 + 1) * 30
        righe = stato_giorno(c, ora.date(), dalle, dalle + finestra)
        for r in righe:
            r["rilevato_il"] = timbro
        scrivi(CSV_ORA, righe); tot += len(righe)
    print(f"{timbro}: {tot} mezz'ore scritte in {CSV_ORA}")


def anticipo():
    ora = dt.datetime.now()
    timbro = ora.strftime("%Y-%m-%d %H:%M")
    for slug in SLUGS:
        c = club(slug)
        if not c:
            continue
        for d in range(1, 7):
            righe = stato_giorno(c, ora.date() + dt.timedelta(d))
            for r in righe:
                r["rilevato_il"] = timbro
            scrivi(CSV_ANTICIPO, righe)
    print(f"{timbro}: anticipo scritto in {CSV_ANTICIPO}")


def riepilogo():
    ultimo = {}
    with open(CSV_ORA, encoding="utf8") as f:
        for r in csv.DictReader(f):
            k = (r["slug"], r["campo"], r["giorno"], r["inizio"])
            if k not in ultimo or r["rilevato_il"] > ultimo[k]["rilevato_il"]:
                ultimo[k] = r
    agg = collections.defaultdict(lambda: [0, 0])
    campi_giorno = collections.defaultdict(set)
    for r in ultimo.values():
        k = (r["circolo"], r["copertura"], r["tipo_giorno"], r["fascia"])
        agg[k][0] += 1
        agg[k][1] += r["stato"] == "occupato"
        campi_giorno[(r["circolo"], r["copertura"], r["tipo_giorno"])].add((r["campo"], r["giorno"]))
    ore = collections.defaultdict(float)
    for (circ, cop, tg, fa), (n, occ) in agg.items():
        ore[(circ, cop, tg)] += occ / 2
    print("circolo;copertura;tipo_giorno;fascia;mezzore_rilevate;occupate;quota")
    for k in sorted(agg):
        n, occ = agg[k]
        print(";".join(k) + f";{n};{occ};{occ / n:.0%}")
    print("\ncircolo;copertura;tipo_giorno;ore_occupate_per_campo_al_giorno (solo mezz'ore rilevate)")
    for k in sorted(ore):
        print(";".join(k) + f";{ore[k] / max(1, len(campi_giorno[k])):.1f}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "rileva"
    {"rileva": rileva, "anticipo": anticipo, "riepilogo": riepilogo}[cmd]()
