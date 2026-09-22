"""Controllo settimanale dei bandi comunali utili al padel (E-90).

Legge solo pagine pubbliche, senza login, senza token, senza servizi a pagamento
e senza Google; usa solo la libreria standard di Python. Intestazione generica
"nutrie-ricerca/1.0", nessun dato personale.

Fonti per comune (verificate il 18/09/2026, dettaglio nella nota
outputs/output-m3-padel-bandi-comunali-2026.md):
- JCity/Maggioli (Mogliano Veneto, Spresiano): albo su <ente>.trasparenza-valutazione-merito.it,
  esportazione CSV dell'albo in corso, con il link diretto all'atto.
- Halley "mc" (Preganziol, Marcon, Casale sul Sile, Zero Branco, Casier, Quarto d'Altino):
  mc_p_ricerca.php, pagine ?pag=N, link diretto mc_p_dettaglio.php?id_pubbl=...
- Hypersic (Scorzè): albopretorioconsultazione.aspx, tabella senza link per atto.
- J-Ente (Treviso): AlboPretorio, ricerca in POST e pagine "scorri", link diretto per atto.
- Pagina "Novità > Avvisi" dei siti comunali WordPress (il loro RSS risulta vuoto).
- Aggregatore: "Bandi altri enti" di Sport e Salute, solo le voci con regione Veneto.
Venezia non è coperta: il sito risponde con una protezione anti-bot.

Una voce si segnala se nel titolo c'è almeno una parola del gruppo SPORT_AREA
e almeno una del gruppo ATTO (così "concessione" da sola non basta), e nessuna del
gruppo ESCLUDI (lavori, manutenzioni, forniture), salvo che si parli di padel.

Comandi (lanciare dalla radice di Nutrie Brain):
  python workspace/padel/bandi_comunali.py controlla   # legge tutte le fonti, scrive le sole voci nuove
  python workspace/padel/bandi_comunali.py elenco      # stampa tutto ciò che è stato visto finora

File:
  workspace/padel/bandi-visti.csv   memoria: comune, data, titolo, link, parole, prima_vista_il
  workspace/padel/bandi-nuovi.md    ogni giro aggiunge in cima una sezione con la data e le voci nuove,
                                    più l'elenco dei comuni che non hanno risposto.
Programmazione proposta: una volta a settimana, lunedì alle 9.
"""
import csv, datetime as dt, html, http.cookiejar, io, os, re, sys, time, urllib.parse, urllib.request

QUI = os.environ.get("NUTRIE_DATI", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dati"))  # copia per GitHub Actions: i dati stanno in dati/
CSV_VISTI = os.path.join(QUI, "bandi-visti.csv")
MD_NUOVI = os.path.join(QUI, "bandi-nuovi.md")
UA = "nutrie-ricerca/1.0"
CAMPI = ["comune", "data", "titolo", "link", "parole", "prima_vista_il"]

SPORT_AREA = ["padel", "impianto sportivo", "impianti sportivi", "area sportiva", "aree sportive",
              "campo da tennis", "campi da tennis", "campi da gioco", "campo da gioco", "palestra",
              "palazzetto", "centro sportivo", "terreno", "terreni", "area verde", "aree verdi",
              "chiosco", "bar", "campetto", "campetti"]
ATTO = ["concessione", "assegnazione", "gestione", "affidamento", "locazione", "affitto", "alienazione",
        "manifestazione di interesse", "manifestazioni di interesse", "avviso pubblico",
        "project financing", "finanza di progetto", "asta pubblica", "bando"]
# Rumore: lavori e spese su palestre e terreni comunali non sono occasioni per il padel
ESCLUDI = ["manutenzione", "lavori", "fornitura", "liquidazione", "bonifica", "impegno di spesa"]

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
AVVISI = {
    "Preganziol": "https://www.comune.preganziol.tv.it/novita/?tipo=avviso",
    "Marcon": "https://www.comune.marcon.ve.it/novita/?tipo=avviso",
    "Casale sul Sile": "https://www.comune.casalesulsile.tv.it/novita/?tipo=avviso",
    "Zero Branco": "https://www.comune.zerobranco.tv.it/novita/?tipo=avviso",
    "Casier": "https://www.comune.casier.tv.it/novita/?tipo=avviso",
    "Quarto d'Altino": "https://www.comune.quartodaltino.ve.it/novita/?tipo=avviso",
}
ZONA = ["mogliano", "preganziol", "marcon", "casale sul sile", "casalesulsile", "scorz",
        "zero branco", "zerobranco", "casier", "quarto d'altino", "quartodaltino", "spresiano",
        "treviso", "venezia", "mestre"]
NON_COPERTI = {"Venezia": "www.comune.venezia.it risponde con una protezione anti-bot (Incapsula): non si forza"}


def apri(url, dati=None, jar=None, timeout=90, tentativi=3):
    for n in range(tentativi):  # i server Halley a volte chiudono la connessione: si riprova
        try:
            return _apri(url, dati, jar, timeout)
        except OSError:
            if n == tentativi - 1:
                raise
            time.sleep(30)


def _apri(url, dati, jar, timeout):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)) if jar is not None else urllib.request.build_opener()
    req = urllib.request.Request(url, data=urllib.parse.urlencode(dati).encode() if dati else None,
                                 headers={"User-Agent": UA})
    with opener.open(req, timeout=timeout) as r:
        raw = r.read()
        cs = r.headers.get_content_charset() or "utf-8"
    return raw.decode(cs, "replace")


def testo(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parole(titolo):
    t = titolo.lower().replace("’", "'")
    trova = lambda lista: [p for p in lista if re.search(r"(?<![a-zà-ù])" + re.escape(p) + r"(?![a-zà-ù])", t)]
    a, b = trova(SPORT_AREA), trova(ATTO)
    if trova(ESCLUDI) and "padel" not in a:
        return []
    return (a + b) if a and b else []


# ---------- lettori per piattaforma ----------

def jcity(host):
    base = f"https://{host}.trasparenza-valutazione-merito.it/web/trasparenza"
    s = apri(base + "/albo-pretorio")
    det = None
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', s, re.S):
        if testo(m.group(2)).lower() == "albo pretorio" and "dettaglio" in m.group(1):
            det = html.unescape(m.group(1)); break
    if not det:
        raise RuntimeError("link all'albo non trovato")
    ig = re.findall(r"papca(?:-ap)?/-/papca/igrid/(\d+)", apri(det))
    if not ig:
        raise RuntimeError("griglia dell'albo non trovata")
    jar = http.cookiejar.CookieJar()
    apri(f"{base}/papca-ap/-/papca/igrid/{ig[0]}", jar=jar)
    exp = (f"{base}/papca-ap?p_p_id=jcitygovalbopubblicazioni_WAR_jcitygovalbiportlet&p_p_lifecycle=2"
           "&p_p_state=pop_up&p_p_mode=view&p_p_resource_id=exportList&p_p_cacheability=cacheLevelPage"
           "&_jcitygovalbopubblicazioni_WAR_jcitygovalbiportlet_format=csv"
           "&_jcitygovalbopubblicazioni_WAR_jcitygovalbiportlet_action=mostraLista"
           "&_jcitygovalbopubblicazioni_WAR_jcitygovalbiportlet_fromAction=eseguiFiltro")
    righe = list(csv.DictReader(io.StringIO(apri(exp, jar=jar).lstrip("﻿"))))
    return [dict(data=r.get("Data inizio pubblicazione", ""), titolo=r.get("Oggetto", "").strip(),
                 link=r.get("Url atto", "").strip()) for r in righe if r.get("Oggetto")]


def halley_mc(url):
    base = url.rsplit("/", 1)[0]
    s = apri(url)
    pagine = [int(x) for x in re.findall(r"pag=(\d+)", s)] or [0]
    voci = []
    for p in range(0, min(max(pagine), 8) + 1):  # pag parte da 0; bastano le prime pagine per un giro settimanale
        if p > 0:
            s = apri(f"{url}?&pag={p}")
        for r in re.findall(r"<tr[^>]*>(.*?)</tr>", s, re.S):
            link = re.search(r'href="([^"]*mc_p_dettaglio\.php\?id_pubbl=\d+)"', r)
            if not link:
                continue
            ogg = re.search(r"Oggetto\s*</[^>]+>(.*?)(?:Numero atto|Data atto|$)", r, re.S)
            data = re.search(r"Data inizio\s*</[^>]+>\s*(?:<[^>]+>\s*)*(\d{2}/\d{2}/\d{4})", r)
            date = re.findall(r"\d{2}/\d{2}/\d{4}", testo(r))
            voci.append(dict(data=(data.group(1) if data else (date[-2] if len(date) >= 2 else "")),
                             titolo=testo(ogg.group(1)) if ogg else testo(r)[:300],
                             link=urllib.parse.urljoin(base + "/", html.unescape(link.group(1)))))
    return voci


def hypersic(url):
    s = apri(url)
    voci = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", s, re.S):
        celle = [testo(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        celle = [c for c in celle if c]
        lunghe = [c for c in celle if len(c) > 40]
        if not lunghe:
            continue
        date = [c for c in celle if re.fullmatch(r"\d{2}/\d{2}/\d{4}", c)]
        voci.append(dict(data=date[0] if date else "", titolo=lunghe[0], link=url))
    return voci


def jente(url):
    jar = http.cookiejar.CookieJar()
    apri(url, jar=jar)
    s = apri(url, dati={"servizio": "cerca", "tipoPratica": "", "codiceRichiedente": "", "dadata": "",
                        "adata": "", "oggetto": "", "ordina": "ANNO_REGISTRO,NUMERO_REGISTRO",
                        "delXPag": "50", "sort": "DESC"}, jar=jar)
    tot = re.search(r"Elementi trovati:\s*(\d+)", testo(s))
    tot = int(tot.group(1)) if tot else 50
    base = url.rsplit("/", 1)[0] + "/"
    voci = []
    da = 1
    while True:
        for li in re.findall(r'<li class="list-group-item">(.*?)</li>', s, re.S):
            a = re.search(r'<a href="([^"]*idPratica=\d+[^"]*)"[^>]*>(.*?)</a>', li, re.S)
            if not a:
                continue
            pub = re.search(r"(\d{2}/\d{2}/\d{4}) al", testo(li))
            voci.append(dict(data=pub.group(1) if pub else "", titolo=testo(a.group(2)),
                             link=urllib.parse.urljoin(base, html.unescape(a.group(1)))))
        da += 50
        if da > tot or da > 1000:
            break
        s = apri(f"{url}?servizio=cerca&azione=scorri&daRecord={da}&aRecord={min(da + 49, tot)}", jar=jar)
    return voci


def avvisi_sito(url):
    """Pagina pubblica "Novità > Avvisi" dei siti comunali WordPress (Design Comuni Italia).
    Il loro feed RSS risulta vuoto il 18/09/2026, quindi si legge la pagina HTML."""
    s = apri(url)
    voci, visti = [], set()
    for m in re.finditer(r'<a[^>]+href="(https?://[^"]*/novita/[^"?#]+/)"[^>]*>(.*?)</a>', s, re.S):
        t = testo(m.group(2)).lstrip("​")
        if len(t) > 15 and m.group(1) not in visti:
            visti.add(m.group(1))
            voci.append(dict(data="", titolo=t, link=m.group(1)))
    return voci


def sport_e_salute(url):
    """Elenco nazionale "Bandi altri enti" di Sport e Salute: si tengono solo le voci con
    regione Veneto che nominano un comune della zona (ZONA). È un aggregatore: non
    sostituisce gli albi, ma copre in parte Venezia, il cui sito blocca le letture automatiche."""
    s = apri(url)
    voci, visti = [], set()
    for card in re.split(r'<div class="sppb-addon-image-layouts"', s)[1:]:
        if 'data-regione="Veneto"' not in card[:400]:
            continue
        tit = re.search(r'<h5 class="title_card">(.*?)</h5>', card, re.S)
        link = re.search(r'<a href="([^"]+)"[^>]*>\s*Scopri di pi', card)
        scad = re.search(r"Termine di presentazione domanda:</strong>\s*([^<]+)<", card)
        if not tit or not link:
            continue
        titolo, href = testo(tit.group(1)), html.unescape(link.group(1))
        if href in visti or not any(z in (titolo + " " + href).lower() for z in ZONA):
            continue
        visti.add(href)
        voci.append(dict(data=("scad. " + scad.group(1).strip()) if scad else "", titolo=titolo, link=href))
    return voci


def fonti():
    for c, h in JCITY.items():
        yield c, "albo JCity", lambda h=h: jcity(h)
    for c, u in HALLEY_MC.items():
        yield c, "albo Halley", lambda u=u: halley_mc(u)
    for c, u in HYPERSIC.items():
        yield c, "albo Hypersic", lambda u=u: hypersic(u)
    for c, u in JENTE.items():
        yield c, "albo J-Ente", lambda u=u: jente(u)
    yield "Veneto (Sport e Salute)", "aggregatore", lambda: sport_e_salute(
        "https://www.sportesalute.eu/bandi-e-avvisi/bandi-altri-enti.html")
    for c, u in AVVISI.items():
        yield c, "avvisi del sito", lambda u=u: avvisi_sito(u)


# ---------- memoria ----------

def carica_visti():
    if not os.path.exists(CSV_VISTI):
        return []
    with open(CSV_VISTI, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def chiave(r):
    link = r["link"]
    generico = "albopretorioconsultazione" in link
    return (r["comune"], r["titolo"].lower() if generico or not link else link)


def controlla():
    oggi = dt.date.today().isoformat()
    visti = carica_visti()
    noti = {chiave(r) for r in visti}
    nuovi, esiti = [], []
    for comune, tipo, leggi in fonti():
        try:
            voci = leggi()
        except Exception as e:  # un comune che non risponde non ferma il giro
            esiti.append((comune, tipo, f"NON RISPONDE: {type(e).__name__}: {str(e)[:120]}"))
            continue
        trovate = 0
        for v in voci:
            p = parole(v["titolo"])
            if not p:
                continue
            trovate += 1
            r = dict(comune=comune, data=v["data"], titolo=v["titolo"][:400], link=v["link"],
                     parole=", ".join(p), prima_vista_il=oggi)
            if chiave(r) not in noti:
                noti.add(chiave(r)); nuovi.append(r); visti.append(r)
        esiti.append((comune, tipo, f"{len(voci)} voci lette, {trovate} con le parole chiave"))
    with open(CSV_VISTI, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPI); w.writeheader(); w.writerows(visti)
    blocco = [f"## Giro del {oggi}", ""]
    if nuovi:
        blocco += [f"**{len(nuovi)} voci nuove.**", ""]
        for r in nuovi:
            blocco.append(f"- **{r['comune']}**, {r['data']}: {r['titolo']} — {r['link']} (parole: {r['parole']})")
    else:
        blocco.append("Nessuna voce nuova.")
    blocco += ["", "Fonti lette:", ""] + [f"- {c} ({t}): {e}" for c, t, e in esiti]
    blocco += [f"- {c}: non coperto, {m}" for c, m in NON_COPERTI.items()] + ["", ""]
    vecchio = open(MD_NUOVI, encoding="utf-8").read() if os.path.exists(MD_NUOVI) else ""
    testata = "# Bandi comunali nuovi (padel)\n\nScritto da `workspace/padel/bandi_comunali.py`; le voci più recenti stanno in cima.\n\n"
    corpo = vecchio.replace(testata, "", 1) if vecchio.startswith(testata) else vecchio
    with open(MD_NUOVI, "w", encoding="utf-8") as f:
        f.write(testata + "\n".join(blocco) + corpo)
    print("\n".join(blocco))


def elenco():
    for r in carica_visti():
        print(f"{r['prima_vista_il']} | {r['comune']} | {r['data']} | {r['titolo'][:120]} | {r['link']}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    {"controlla": controlla, "elenco": elenco}[sys.argv[1] if len(sys.argv) > 1 else "controlla"]()
