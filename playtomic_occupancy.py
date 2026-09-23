"""Repeated sampling of padel court occupancy on Playtomic (Nutrie board items E-86 and S-16).

Reads only public pages and data, no login:
- club page       https://playtomic.com/clubs/<slug>  (tenant_id, courts, opening hours)
- free slots      https://playtomic.com/api/clubs/availability?tenant_id=<id>&date=YYYY-MM-DD&sport_id=PADEL

Commands:
  python playtomic_occupancy.py read          # status of the half hours starting in the next 2 hours
  python playtomic_occupancy.py lead          # status of the next 6 days, to measure how far ahead courts get booked
  python playtomic_occupancy.py summary       # printed summary: booked hours per court, by club, cover, day type, time band
  python playtomic_occupancy.py summary_csv   # data/playtomic-summary.csv, read by the Google Sheet

Schedule: "read" every 2 hours from 07:00 to 23:00 Italian time, "lead" once a day at 21:00,
until 09/10/2026. Each half hour takes the status of the last reading made before it starts,
which is close to the final occupancy. A half hour is "booked" when no bookable slot covers it:
this includes matches, classes, club blocks and gaps too short to be booked.
"""
import csv, datetime as dt, json, os, re, sys, time, urllib.error, urllib.request, collections

DATA = os.environ.get("NUTRIE_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
CSV_NOW = os.path.join(DATA, "playtomic-occupancy.csv")
CSV_LEAD = os.path.join(DATA, "playtomic-lead.csv")
CSV_SUMMARY = os.path.join(DATA, "playtomic-summary.csv")
CSV_HEALTH = os.path.join(DATA, "playtomic-health.csv")
# Clubs around Mogliano Veneto that publish availability online, checked on 17/09/2026
# (Padel Circus left out: it does not publish slots)
SLUGS = ["sporting-club-mestre", "padel-by-fitup-di-zero-branco", "aquafit-padel", "sph-venezia",
         "padel-treviso", "altino-sport-village", "barchessa-pickleball", "h-farm-campus",
         "sph-treviso-sporting", "marina-sporting"]
FIELDS = ["read_at", "date", "weekday", "day_type", "club", "slug", "court", "cover",
          "start", "time_band", "status", "price_90", "price_60"]


# Why a health log: a failed fetch returns "" and the run still exits 0, so a silent block
# (Playtomic refusing datacenter IPs, for instance) looks exactly like a successful run.
# LAST_REASON keeps why the last fetch gave up; read()/lead() write it to data/playtomic-health.csv.
LAST_REASON = {"why": ""}
HEALTH_FIELDS = ["read_at", "command", "slug", "stage", "outcome"]


def fetch(url, attempts=3):
    why = ""
    for _ in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return urllib.request.urlopen(req, timeout=30).read().decode("utf8", "replace")
        except urllib.error.HTTPError as e:
            why = f"HTTP {e.code}"
        except Exception as e:
            why = f"{type(e).__name__}: {str(e)[:60]}"
        time.sleep(3)
    LAST_REASON["why"] = why or "no answer"
    return ""


def health(stamp, command, lines):
    """One line per club per run, committed with the data: it says whether Playtomic answered."""
    new_file = not os.path.exists(CSV_HEALTH)
    with open(CSV_HEALTH, "a", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=HEALTH_FIELDS)
        if new_file:
            w.writeheader()
        for slug, stage, outcome in lines:
            w.writerow(dict(read_at=stamp, command=command, slug=slug, stage=stage, outcome=outcome))


def minutes(s):
    h, m = map(int, s[:5].split(":"))
    return h * 60 + m


def time_band(t):
    return "morning" if t < 720 else "lunch" if t < 840 else "afternoon" if t < 1080 else "evening"


def club(slug):
    s = fetch(f"https://playtomic.com/clubs/{slug}").replace('\\"', '"')
    tid = re.search(r'"tenant_id":"([0-9a-f-]{36})"', s)
    if not tid:
        if s:
            LAST_REASON["why"] = f"page read ({len(s)} bytes) but no tenant_id: layout changed or block page"
        return None
    name = re.search(r'"tenant_name":"([^"]*)"', s)
    i = s.find('"resources":[')
    courts = []
    for m in re.finditer(r'\{"resourceId":"([0-9a-f-]{36})","name":"([^"]*)","sport":"PADEL","features":\[([^\]]*)\]', s[i:i + 20000] if i >= 0 else ""):
        cover = "outdoor" if "outdoor" in m.group(3) else "indoor"
        courts.append((m.group(1), m.group(2).strip(), cover))
    hours = {}
    k = s.find('opening_hours":{"')
    for m in re.finditer(r'"([A-Z]+)":\{"opening_time":"([\d:]+)","closing_time":"([\d:]+)"\}', s[k:k + 900] if k >= 0 else ""):
        hours[m.group(1)] = (minutes(m.group(2)), minutes(m.group(3)) or 1440)
    return dict(slug=slug, tid=tid.group(1), name=(name.group(1) if name else slug).strip(), courts=courts, hours=hours)


def day_status(c, day, start_min=0, end_min=1440):
    raw = fetch(f"https://playtomic.com/api/clubs/availability?tenant_id={c['tid']}&date={day.isoformat()}&sport_id=PADEL")
    try:
        data = json.loads(raw)
    except Exception:
        if raw:
            LAST_REASON["why"] = f"availability answered {len(raw)} bytes that are not JSON"
        return []
    free = collections.defaultdict(set)
    prices = collections.defaultdict(dict)
    for r in data:
        for sl in r["slots"]:
            st = minutes(sl["start_time"])
            prices[(r["resource_id"], st)][sl["duration"]] = sl["price"]
            for t in range(st, st + sl["duration"], 30):
                free[r["resource_id"]].add(t)
    o = c["hours"].get(day.strftime("%A").upper())
    if not o:
        return []
    opens, closes = o
    if closes <= opens:
        closes = 1440
    rows = []
    for rid, name, cover in c["courts"]:
        for t in range(max(opens, start_min), min(closes, end_min), 30):
            p = prices.get((rid, t), {})
            rows.append(dict(date=day.isoformat(), weekday=day.strftime("%a"),
                             day_type="weekend" if day.weekday() >= 5 else "weekday",
                             club=c["name"], slug=c["slug"], court=name, cover=cover,
                             start=f"{t // 60:02d}:{t % 60:02d}", time_band=time_band(t),
                             status="free" if t in free[rid] else "booked",
                             price_90=p.get(90, ""), price_60=p.get(60, "")))
    return rows


def append(path, rows):
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def read(window=120):
    now = dt.datetime.now()
    now_min = now.hour * 60 + now.minute
    stamp = now.strftime("%Y-%m-%d %H:%M")
    total = 0
    report = []
    for slug in SLUGS:
        LAST_REASON["why"] = ""
        c = club(slug)
        if not c:
            report.append((slug, "club page", LAST_REASON["why"] or "no answer"))
            print("club page not readable:", slug, LAST_REASON["why"]); continue
        start_min = (now_min // 30 + 1) * 30
        rows = day_status(c, now.date(), start_min, start_min + window)
        append(CSV_NOW, rows); total += len(rows)
        report.append((slug, "availability",
                       f"{len(rows)} half hours" if rows else (LAST_REASON["why"] or "closed now or no slots")))
    health(stamp, "read", report)
    print(f"{stamp}: {total} half hours written to {CSV_NOW}")


def lead():
    now = dt.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    report = []
    for slug in SLUGS:
        LAST_REASON["why"] = ""
        c = club(slug)
        if not c:
            report.append((slug, "club page", LAST_REASON["why"] or "no answer"))
            continue
        total = 0
        for d in range(1, 7):
            rows = day_status(c, now.date() + dt.timedelta(d))
            for r in rows:
                r["read_at"] = stamp
            append(CSV_LEAD, rows); total += len(rows)
        report.append((slug, "availability",
                       f"{total} half hours" if total else (LAST_REASON["why"] or "no slots")))
    health(stamp, "lead", report)
    print(f"{stamp}: lead readings written to {CSV_LEAD}")


def latest_readings():
    """For each court, day and half hour, keep only the last reading."""
    latest = {}
    with open(CSV_NOW, encoding="utf8") as f:
        for r in csv.DictReader(f):
            k = (r["slug"], r["court"], r["date"], r["start"])
            if k not in latest or r["read_at"] > latest[k]["read_at"]:
                latest[k] = r
    return latest.values()


def summary():
    agg = collections.defaultdict(lambda: [0, 0])
    court_days = collections.defaultdict(set)
    for r in latest_readings():
        k = (r["club"], r["cover"], r["day_type"], r["time_band"])
        agg[k][0] += 1
        agg[k][1] += r["status"] == "booked"
        court_days[(r["club"], r["cover"], r["day_type"])].add((r["court"], r["date"]))
    hours = collections.defaultdict(float)
    for (club_, cover, day_type, band), (n, booked) in agg.items():
        hours[(club_, cover, day_type)] += booked / 2
    print("club;cover;day_type;time_band;half_hours_observed;booked;share")
    for k in sorted(agg):
        n, booked = agg[k]
        print(";".join(k) + f";{n};{booked};{booked / n:.0%}")
    print("\nclub;cover;day_type;booked_hours_per_court_per_day (observed half hours only)")
    for k in sorted(hours):
        print(";".join(k) + f";{hours[k] / max(1, len(court_days[k])):.1f}")


def summary_csv():
    """Writes data/playtomic-summary.csv for the Google Sheet: one row per club, cover, day type and time band."""
    agg = collections.defaultdict(lambda: [0, 0])
    days = collections.defaultdict(set)
    for r in latest_readings():
        k = (r["club"], r["cover"], r["day_type"], r["time_band"])
        agg[k][0] += 1
        agg[k][1] += r["status"] == "booked"
        days[k].add(r["date"])
    with open(CSV_SUMMARY, "w", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(["updated_at", "club", "cover", "day_type", "time_band", "days_observed",
                    "half_hours_observed", "half_hours_booked", "booked_share"])
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        for k in sorted(agg):
            n, booked = agg[k]
            w.writerow([stamp, *k, len(days[k]), n, booked, round(booked / n, 3)])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "read"
    {"read": read, "lead": lead, "summary": summary, "summary_csv": summary_csv}[cmd]()
