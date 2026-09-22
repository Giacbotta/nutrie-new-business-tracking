"""Controllo settimanale delle lavanderie in vendita nelle province di Padova, Treviso e Venezia (E-91).

Richiesta di Giacomo del 22/09/2026. Legge solo annunci pubblici, senza login e senza token.
Portali letti (verificati il 22/09/2026, dettaglio in outputs/output-m3-lavanderie-treviso-venezia-2026.md):
- Subito.it: API pubblica di ricerca hades.subito.it (regione Veneto, parola nel titolo),
  categorie locali commerciali, attrezzature ed elettrodomestici (un annuncio del 2025 era finito
  fra gli elettrodomestici). Libreria standard.
- immobiliare.it: categoria "Lavanderie - tintorie" delle attività in vendita, per provincia.
  La ricerca per parola non funziona su immobiliare, la categoria sì.
- Trovit (case.trovit.it): ricerca "lavanderia" in Veneto; aggrega anche casa.it e altri portali.
immobiliare e Trovit rifiutano le richieste semplici (403): per loro serve curl_cffi, gratuito,
che si presenta come un browser. Se manca o il portale blocca, il giro continua e il portale
finisce nell'elenco "non letti" del rapporto.
Non letti, con il motivo: idealista (nessuna categoria lavanderia), Kijiji (chiuso nel 2022),
comprovendoattivita.com (non risolve da agosto 2026), Waa2 e b2scout (nessuna ricerca leggibile),
broker di cessione (schede anonime, serve un contatto).

Ogni annuncio porta una colonna "tipo" (self-service, presidiata, attrezzatura, da verificare):
non si scarta nulla, si classifica. Si scartano solo i risultati fuori dalle tre province e,
fra attrezzature ed elettrodomestici di Subito, gli oggetti di casa (mobili, lavelli).

Comandi:
  python lavanderie_vendita.py controlla   # legge i portali, aggiorna i file, esce con 0
  python lavanderie_vendita.py elenco      # stampa gli annunci attivi

File in dati/:
  lavanderie.csv          tutti gli annunci visti, attivi e spariti (letto dal foglio Google)
  lavanderie-nuove.md     ogni giro aggiunge in cima una sezione con le novità
  lavanderie-email.md     testo dell'ultimo giro con novità, usato per la notifica
"""
import csv, datetime as dt, html, json, os, re, sys, time, urllib.parse, urllib.request

try:
    from curl_cffi import requests as browser  # immobiliare e Trovit
except ImportError:
    browser = None

QUI = os.environ.get("NUTRIE_DATI", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dati"))
CSV_VISTI = os.path.join(QUI, "lavanderie.csv")
MD_NUOVI = os.path.join(QUI, "lavanderie-nuove.md")
MD_EMAIL = os.path.join(QUI, "lavanderie-email.md")
CAMPI = ["stato", "tipo", "provincia", "comune", "titolo", "prezzo", "mq", "portale", "link",
         "pubblicato", "primo_visto", "ultimo_visto", "id", "possibile_doppione"]

PROVINCE = {"PD": "Padova", "TV": "Treviso", "VE": "Venezia"}
OGGI = dt.date.today().isoformat()

SUBITO_PAROLE = ["lavanderia", "lavanderie", "lavasecco", "tintoria"]
SUBITO_CATEGORIE = {"uffici-locali-commerciali", "attrezzature", "elettrodomestici"}
# Fra attrezzature ed elettrodomestici restano solo le cose da lavanderia a gettoni, non i mobili di casa
ATTREZZATURA_OK = re.compile(r"self|gettoni|automatic|industrial|professional|attivit|cassa|lavatric|asciugatric|essiccat", re.I)
OGGETTI_CASA = re.compile(r"^\s*(mobil|lavell|lavandin|lavabo|armadi|colonna|vasca|pensile|cest)", re.I)
# Trovit mescola le case con "zona lavanderia": resta solo ciò che parla di un'attività
TROVIT_ATTIVITA = re.compile(r"lavanderia (self|a gettoni|automatica|industriale)|lavasecco|attivit[aà] (di )?lavanderia|lavanderia avviata|cedesi.*lavanderia|lavanderia in vendita", re.I)
IMMOBILIARE_PROV = ["padova-provincia", "treviso-provincia", "venezia-provincia"]
TROVIT_RICERCHE = ["lavanderia-veneto", "lavanderia-self-service-veneto"]


def pulisci_comune(s):
    """"30035, Mirano" e "a San Giuseppe, Cavarzere" diventano "Mirano" e "Cavarzere"."""
    return re.sub(r"^(a |\d+\s*)", "", s.split(",")[-1].strip()).strip()


def tipo_di(testo, categoria=""):
    t = testo.lower()
    if categoria in ("attrezzature", "elettrodomestici") and not re.search(r"attivit|avviat|cedo|cessione|locale", t):
        return "attrezzatura"
    if re.search(r"self|gettoni|automatic", t):
        return "self-service"
    if re.search(r"lavasecco|pulitura|pulisecco|tintoria|stiro|professional", t):
        return "presidiata"
    return "da verificare"


def numero(s):
    s = re.sub(r"[^\d]", "", str(s or ""))
    return int(s) if s else ""


# ---------- Subito ----------

def leggi_subito():
    trovati, visti = [], set()
    h = {"User-Agent": "Mozilla/5.0 (nutrie-ricerca/1.0)", "X-Subito-Channel": "web", "Accept": "application/json"}
    for parola in SUBITO_PAROLE:
        for tipo_annuncio in ("s", "u"):  # vendita e affitto (alcune attività si cedono in affitto d'azienda)
            start = 0
            while True:
                q = urllib.parse.urlencode({"q": parola, "r": 6, "t": tipo_annuncio, "qso": "true", "lim": 100, "start": start})
                try:
                    with urllib.request.urlopen(urllib.request.Request("https://hades.subito.it/v1/search/items?" + q, headers=h), timeout=30) as r:
                        d = json.loads(r.read().decode("utf-8"))
                except Exception:
                    if tipo_annuncio == "s":
                        raise
                    break
                ads = d.get("ads", [])
                for a in ads:
                    cat = a.get("category", {}).get("friendly_name", "")
                    geo = a.get("geo", {})
                    prov = geo.get("city", {}).get("short_name", "")
                    link = a.get("urls", {}).get("default", "")
                    if cat not in SUBITO_CATEGORIE or prov not in PROVINCE or link in visti:
                        continue
                    titolo = a.get("subject", "").strip()
                    corpo = a.get("body", "")
                    prezzo = numero((next((f for f in a.get("features", []) if f.get("uri") == "/price"), {}).get("values") or [{}])[0].get("key"))
                    if cat != "uffici-locali-commerciali" and (not ATTREZZATURA_OK.search(titolo) or OGGETTI_CASA.search(titolo)
                                                              or (prezzo != "" and prezzo < 500)):
                        continue
                    visti.add(link)
                    feat = {f.get("uri"): f.get("values", [{}])[0] for f in a.get("features", [])}
                    trovati.append({
                        "id": "subito-" + link.rsplit("-", 1)[-1].replace(".htm", ""),
                        "portale": "Subito", "provincia": prov,
                        "comune": geo.get("town", {}).get("value", "") or geo.get("city", {}).get("value", ""),
                        "titolo": titolo + (" [affitto]" if tipo_annuncio == "u" else ""), "prezzo": prezzo,
                        "mq": numero(feat.get("/size", {}).get("key")), "link": link,
                        "pubblicato": a.get("dates", {}).get("display", "")[:10],
                        "tipo": tipo_di(titolo + " " + corpo, cat),
                    })
                start += len(ads)
                if not ads or start >= d.get("count_all", 0) or start >= 500:
                    break
    return trovati


def scarica(url, tentativi=4):
    """immobiliare risponde 403 a intermittenza (visto il 22/09/2026): si riprova con pause crescenti."""
    for i in range(tentativi):
        r = browser.get(url, impersonate="chrome", timeout=40)
        if r.status_code not in (403, 429, 503):
            break
        time.sleep(15 * (i + 1))
    time.sleep(3)
    return r


# ---------- immobiliare.it ----------

def _next_data(testo):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', testo, re.S)
    return json.loads(m.group(1)) if m else None


def _risultati(o):
    if isinstance(o, dict):
        v = o.get("results")
        if isinstance(v, list) and v and isinstance(v[0], dict) and "realEstate" in v[0]:
            return o
        for x in o.values():
            r = _risultati(x)
            if r:
                return r
    elif isinstance(o, list):
        for x in o:
            r = _risultati(x)
            if r:
                return r
    return None


def leggi_immobiliare():
    """In elenco il titolo è generico ("Attività commerciale...") e la scheda risponde 403 anche a
    curl_cffi (22/09/2026): il tipo resta "da verificare", salvo che lo stesso affare stia su un
    altro portale (vedi segna_doppioni) o che sia stato corretto a mano nel CSV."""
    trovati = []
    gia_visti = carica()
    for prov in IMMOBILIARE_PROV:
        pag = 1
        while pag <= 10:
            url = f"https://www.immobiliare.it/vendita-attivita/{prov}/con-lavanderia-tintoria/" + (f"?pag={pag}" if pag > 1 else "")
            r = scarica(url)
            if r.status_code != 200:
                raise RuntimeError(f"{prov}: HTTP {r.status_code}")
            blocco = _risultati(_next_data(r.text) or {})
            if not blocco:
                break
            for x in blocco["results"]:
                re_ = x["realEstate"]
                p = (re_.get("properties") or [{}])[0]
                loc = p.get("location", {})
                sigla = {v: k for k, v in PROVINCE.items()}.get(loc.get("province", ""), "")
                if not sigla:
                    continue
                titolo = re_.get("title", "")
                vecchio = gia_visti.get(f"immobiliare-{re_['id']}")
                tipo = vecchio["tipo"] if vecchio else tipo_di(titolo)
                trovati.append({
                    "id": f"immobiliare-{re_['id']}", "portale": "immobiliare.it", "provincia": sigla,
                    "comune": loc.get("city", ""), "titolo": titolo,
                    "prezzo": numero((re_.get("price") or {}).get("value")), "mq": numero(p.get("surface")),
                    "link": f"https://www.immobiliare.it/annunci/{re_['id']}/", "pubblicato": "",
                    "tipo": tipo,
                })
            if pag >= int(blocco.get("maxPages") or blocco.get("lastPage") or 1):
                break
            pag += 1
    return trovati


# ---------- Trovit ----------

def leggi_trovit():
    trovati, visti = [], set()
    for ricerca in TROVIT_RICERCHE:
        r = scarica("https://case.trovit.it/" + ricerca)
        if r.status_code == 404:
            continue
        if r.status_code != 200:
            raise RuntimeError(f"{ricerca}: HTTP {r.status_code}")
        for art in re.findall(r"<article.*?</article>", r.text, re.S):
            m_id = re.search(r'data-id="([^"]+)"', art)
            if not m_id or m_id.group(1) in visti:
                continue
            m_tit = re.search(r'class="js-listing"[^>]*title="([^"]*)"', art) or re.search(r'title="([^"]*)" class="js-listing"', art)
            m_ind = re.search(r'class="address_property-type">(.*?)</span>', art, re.S)
            indirizzo = html.unescape(re.sub(r"<[^>]+>", "", m_ind.group(1))) if m_ind else ""
            m_prov = re.search(r"a (.+?), Provincia di (\w+)", indirizzo)
            if not m_prov:
                continue
            sigla = {v: k for k, v in PROVINCE.items()}.get(m_prov.group(2), "")
            if not sigla:
                continue
            visti.add(m_id.group(1))
            m_pr = re.search(r'data-test="price__actual">([^<]*)<', art)
            m_mq = re.search(r"<p>(\d+) m", art)
            m_desc = re.search(r"</span>\s*<p>(.*?)</p>", art, re.S)
            fonte = re.search(r"<small>([^<]*)</small>", art)
            titolo = html.unescape(m_tit.group(1)) if m_tit else indirizzo
            descr = html.unescape(re.sub(r"<[^>]+>", "", m_desc.group(1))) if m_desc else ""
            if not TROVIT_ATTIVITA.search(titolo + " " + descr):
                continue
            trovati.append({
                "id": "trovit-" + m_id.group(1), "portale": "Trovit" + (f" ({fonte.group(1).strip()})" if fonte else ""),
                "provincia": sigla, "comune": pulisci_comune(m_prov.group(1)), "titolo": titolo,
                "prezzo": numero(m_pr.group(1)) if m_pr else "", "mq": numero(m_mq.group(1)) if m_mq else "",
                "link": "https://case.trovit.it/detail/" + m_id.group(1), "pubblicato": "",
                "tipo": tipo_di(titolo + " " + descr),
            })
    return trovati


# ---------- memoria e rapporto ----------

def carica():
    if not os.path.exists(CSV_VISTI):
        return {}
    with open(CSV_VISTI, encoding="utf-8", newline="") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def salva(righe):
    ordine = {"attivo": 0, "sparito": 1}
    righe = sorted(righe, key=lambda r: (ordine.get(r["stato"], 2), r["primo_visto"], r["pubblicato"]), reverse=False)
    righe = sorted(righe, key=lambda r: ordine.get(r["stato"], 2))
    with open(CSV_VISTI, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPI)
        w.writeheader()
        for r in righe:
            w.writerow({k: r.get(k, "") for k in CAMPI})


def segna_doppioni(righe):
    """Stesso comune e stesso prezzo su portali diversi: probabilmente lo stesso affare."""
    for r in righe:
        r["possibile_doppione"] = ""
    for a in righe:
        for b in righe:
            if a is not b and a["portale"] != b["portale"] and a["prezzo"] and str(a["prezzo"]) == str(b["prezzo"]) \
                    and a["comune"].lower() == b["comune"].lower():
                a["possibile_doppione"] = b["link"]
                if a["tipo"] == "da verificare" and b["tipo"] != "da verificare":
                    a["tipo"] = b["tipo"] + " (dal doppione)"


def riga_md(r):
    prezzo = f"{int(r['prezzo']):,} €".replace(",", ".") if str(r["prezzo"]).isdigit() else "prezzo non indicato"
    mq = f", {r['mq']} m²" if r.get("mq") else ""
    return f"- **{r['comune']} ({r['provincia']})**, {r['titolo']}: {prezzo}{mq}. Tipo: {r['tipo']}. {r['portale']}: {r['link']}"


def controlla():
    lettori = [("Subito", leggi_subito)]
    if browser:
        lettori += [("immobiliare.it", leggi_immobiliare), ("Trovit", leggi_trovit)]
    non_letti = [] if browser else ["immobiliare.it e Trovit: manca curl_cffi (pip install curl_cffi)"]
    letti, trovati = [], []
    for nome, f in lettori:
        try:
            x = f()
            trovati += x
            letti.append(nome)
            print(f"{nome}: {len(x)} annunci nelle tre province")
        except Exception as e:
            non_letti.append(f"{nome}: {str(e)[:120]}")
            print(f"{nome}: NON LETTO, {e}")

    memoria = carica()
    nuovi, tornati = [], []
    per_id = {r["id"]: r for r in trovati}
    for id_, r in per_id.items():
        vecchio = memoria.get(id_)
        if not vecchio:
            r.update(stato="attivo", primo_visto=OGGI, ultimo_visto=OGGI)
            nuovi.append(r)
        else:
            if vecchio["stato"] != "attivo":
                tornati.append(r)
            if str(vecchio.get("prezzo")) != str(r["prezzo"]) and vecchio.get("prezzo") and r["prezzo"]:
                r["titolo"] = r["titolo"] + f" [prezzo cambiato da {vecchio['prezzo']} €]"
            r.update(stato="attivo", primo_visto=vecchio["primo_visto"], ultimo_visto=OGGI,
                     pubblicato=r["pubblicato"] or vecchio.get("pubblicato", ""))
        memoria[id_] = r
    # Sparito solo se il portale è stato letto davvero in questo giro
    spariti = []
    for id_, r in memoria.items():
        portale_base = r["portale"].split(" (")[0]
        if id_ not in per_id and r["stato"] == "attivo" and portale_base in letti:
            r["stato"] = "sparito"
            spariti.append(r)
    righe = list(memoria.values())
    segna_doppioni(righe)
    salva(righe)

    attivi = [r for r in righe if r["stato"] == "attivo"]
    sez = [f"## Giro del {OGGI}", "",
           f"Portali letti: {', '.join(letti) or 'nessuno'}. Annunci attivi nelle province di Padova, Treviso e Venezia: {len(attivi)}.", ""]
    if nuovi:
        sez += ["**Nuovi:**", ""] + [riga_md(r) for r in nuovi] + [""]
    if tornati:
        sez += ["**Di nuovo online:**", ""] + [riga_md(r) for r in tornati] + [""]
    if spariti:
        sez += ["**Non più online** (venduti, ritirati o scaduti):", ""] + [riga_md(r) for r in spariti] + [""]
    if not (nuovi or tornati or spariti):
        sez += ["Nessuna novità rispetto al giro precedente.", ""]
    if non_letti:
        sez += ["**Portali non letti in questo giro:** " + "; ".join(non_letti), ""]
    precedente = open(MD_NUOVI, encoding="utf-8").read() if os.path.exists(MD_NUOVI) else "# Lavanderie in vendita, novità settimanali (E-91)\n\n"
    testa, _, coda = precedente.partition("\n\n")
    with open(MD_NUOVI, "w", encoding="utf-8") as f:
        f.write(testa + "\n\n" + "\n".join(sez) + "\n" + coda)
    novita = bool(nuovi or tornati or spariti or non_letti)
    with open(MD_EMAIL, "w", encoding="utf-8") as f:
        f.write("\n".join(sez[2:]) + "\nElenco completo nel foglio Google e in dati/lavanderie.csv.\n")
    # Per GitHub Actions: dice al passo successivo se c'è qualcosa da notificare
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"novita={'si' if novita else 'no'}\nnuovi={len(nuovi)}\n")
    print(f"Nuovi {len(nuovi)}, tornati {len(tornati)}, spariti {len(spariti)}, attivi {len(attivi)}")


def elenco():
    for r in carica().values():
        if r["stato"] == "attivo":
            print(riga_md(r))


if __name__ == "__main__":
    os.makedirs(QUI, exist_ok=True)
    {"controlla": controlla, "elenco": elenco}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
