"""How full are the luggage storage points of Radical Storage in Italian cities (board item E-93).

Same spirit as playtomic_occupancy.py: public pages only, no login, no token, standard library only.

WHAT IT READS
  city list     https://radicalstorage.com/v4/en/storage-list/<city>?limit=1000
                every point of a city with capacity, today's opening hours, price, reviews
  availability  https://radicalstorage.com/v4/en/storage/<id>?dropOff=...&pickUp=...&bags=N
                "isAvailable" turns false as soon as N goes past the bags still bookable in that
                window. Capacity minus that number = bags already booked. Checked on 22/09/2026:
                Venice Santa Lucia, 140 places, 113 still bookable at 17-19, so 27 bags booked.

WHAT THE NUMBER MEANS
  The answer is the PEAK of the window, not the sum: asking 10-14 returns the same as the fullest
  hour inside it (measured 22/09/2026 on Venice Santa Lucia: 10-12 = 10, 12-14 = 22, 10-14 = 22).
  So a whole day in one question would only give the daily peak. Deposits per day are rebuilt from
  the hourly curve instead: every hour this script reads the hour that is about to start, and
  "daily" adds up the rises of that curve.

WHAT IT DOES NOT SEE
  Walk-in customers, other marketplaces, and any storage that is not on Radical. It measures
  Radical's online demand, not the whole demand of a city.

ROBOTS.TXT
  radicalstorage.com/robots.txt disallows /v3/ and /v4/ to robots. This script reads them, so it
  stays deliberately light: one hour-slot per point per run, resuming from the previous reading,
  a few requests per second, and a hard ceiling per run (MAX_REQUESTS). Decided with Giacomo.

COMMANDS (run from the repository root)
  python radical_occupancy.py cities                 # which Italian province capitals Radical covers
  python radical_occupancy.py census                 # every point, with capacity and reviews
  python radical_occupancy.py scan                   # bags booked in the hour about to start
  python radical_occupancy.py scan ferrara,pisa      # only these cities
  python radical_occupancy.py daily                  # deposits per day, by point, area and city
  python radical_occupancy.py dashboard              # rebuild docs/index.html
"""
import collections, csv, datetime as dt, json, os, re, sys, threading, time
import unicodedata, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DOCS = os.path.join(HERE, "docs")
CITIES_CSV = os.path.join(DATA, "radical-cities.csv")
POINTS_CSV = os.path.join(DATA, "radical-points.csv")
OCC_CSV = os.path.join(DATA, "radical-occupancy.csv")
DAILY_CSV = os.path.join(DATA, "radical-daily.csv")
AREA_CSV = os.path.join(DATA, "radical-daily-area.csv")
CITY_CSV = os.path.join(DATA, "radical-daily-city.csv")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
THREADS = 4                  # polite: about 8-10 requests per second in total
PAUSE = 0.25                 # seconds before each request, inside every thread
MAX_REQUESTS = 6000          # hard ceiling per run; a full national scan uses about 2.000
MAX_STEPS = 12             # requests spent on a single point in one slot: enough for a 300-place point
CAP_CEILING = 300            # some points declare 9999 places; read them up to here
BAGS_PER_BOOKING = 2.0       # assumption, to calibrate on Nutrie's own Radical bookings (GAP)

# The 107 Italian province capitals. Radical uses English names for a few of them.
CAPITALS = """agrigento alessandria ancona andria aosta arezzo ascoli-piceno asti avellino bari
barletta belluno benevento bergamo biella bologna bolzano brescia brindisi cagliari caltanissetta
campobasso carbonia carrara caserta catania catanzaro cesena chieti como cosenza cremona crotone
cuneo enna fermo ferrara florence foggia forli frosinone genoa gorizia grosseto imperia isernia
la-spezia laquila latina lecce lecco livorno lodi lucca macerata mantua massa matera messina milan
modena monza naples novara nuoro olbia oristano padua palermo parma pavia perugia pesaro pescara
piacenza pisa pistoia pordenone potenza prato ragusa ravenna reggio-calabria reggio-emilia rieti
rimini rome rovigo salerno sassari savona siena sondrio syracuse taranto tempio-pausania teramo
terni trani trapani trento treviso trieste turin udine urbino varese venice verbania vercelli
verona vibo-valentia vicenza viterbo""".split()

_lock = threading.Lock()
_spent = [0]


def rome_now():
    """Italian time and its UTC offset, worked out here on purpose: the machine running this may
    be in another time zone (on 23/09/2026 the laptop was on +03:00), and Windows has no tz
    database for zoneinfo. Radical's opening hours and windows are Italian local time.
    Summer time: from the last Sunday of March to the last Sunday of October, 01:00 UTC."""
    utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    def last_sunday(month):
        d = dt.date(utc.year, month + 1, 1) - dt.timedelta(days=1)
        return d - dt.timedelta(days=(d.weekday() + 1) % 7)
    start = dt.datetime.combine(last_sunday(3), dt.time(1))
    end = dt.datetime.combine(last_sunday(10), dt.time(1))
    offset = 2 if start <= utc < end else 1
    return utc + dt.timedelta(hours=offset), offset


def fetch(url, tries=3):
    """One GET, JSON back. Counts against the ceiling of the run."""
    with _lock:
        if _spent[0] >= MAX_REQUESTS:
            return None
        _spent[0] += 1
    for _ in range(tries):
        try:
            time.sleep(PAUSE)
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read())
        except Exception:
            time.sleep(3)
    return None


def city_points(city):
    d = fetch(f"https://radicalstorage.com/v4/en/storage-list/{city}?limit=1000")
    return (d or {}).get("storages") or []


def area_of(point):
    """Radical's own neighbourhood, taken from the English URL: /luggage-storage/<city>/<area>/<point>."""
    parts = (point.get("HTMLURIs") or {}).get("en", "").strip("/").split("/")
    return parts[2] if len(parts) > 3 else ""


def rows_to_csv(path, fields, rows, append=True):
    os.makedirs(DATA, exist_ok=True)
    new = not (append and os.path.exists(path))
    with open(path, "a" if append else "w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new or not append:
            w.writeheader()
        w.writerows(rows)
    return path


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- cities and census

def cities():
    found = []
    with ThreadPoolExecutor(THREADS) as ex:
        for city, points in zip(CAPITALS, ex.map(city_points, CAPITALS)):
            italian = [p for p in points if p.get("countryISO") == "IT"]
            if italian:
                found.append(dict(city=city, points=len(italian),
                                  capacity=sum(min(p.get("capacity") or 0, CAP_CEILING) for p in italian)))
    found.sort(key=lambda r: -r["points"])
    rows_to_csv(CITIES_CSV, ["city", "points", "capacity"], found, append=False)
    print(f"{len(found)} cities with points, {sum(r['points'] for r in found)} points -> {CITIES_CSV}")
    return [r["city"] for r in found]


def city_list():
    rows = read_csv(CITIES_CSV)
    return [r["city"] for r in rows] if rows else cities()


def census():
    today = rome_now()[0].date().isoformat()
    rows = []
    for city in city_list():
        for p in city_points(city):
            if p.get("countryISO") != "IT":
                continue
            rows.append(dict(seen_on=today, city=city, area=area_of(p), storage_id=p["id"], name=p["name"],
                             lat=p["lat"], lng=p["lng"], capacity=p.get("capacity"),
                             is_locker=p.get("isLocker"), open_24_7=p.get("is247"),
                             reviews=p.get("reviews"), rating=p.get("rating"),
                             price_eur=p["price"]["amount"] / 100))
    rows_to_csv(POINTS_CSV, ["seen_on", "city", "area", "storage_id", "name", "lat", "lng", "capacity",
                             "is_locker", "open_24_7", "reviews", "rating", "price_eur"], rows, append=False)
    print(f"{len(rows)} points -> {POINTS_CSV}")


# ---------------------------------------------------------------- hourly scan

def bookable(storage_id, bags, start, end):
    """True if `bags` more bags can still be booked in that window."""
    q = urllib.parse.urlencode(dict(dropOff=start, pickUp=end, bags=bags, bagsSmall=0, bagsLarge=0))
    d = fetch(f"https://radicalstorage.com/v4/en/storage/{storage_id}?{q}")
    return None if d is None else d["availability"]["isAvailable"]


def booked_bags(storage_id, capacity, start, end, previous=None):
    """Bags already booked in the window. Starts from the previous reading, so an unchanged
    point costs two requests instead of a full binary search."""
    steps = [0]

    def free(n):
        steps[0] += 1
        return bookable(storage_id, n, start, end) if steps[0] <= MAX_STEPS else None

    guess = capacity - previous if previous is not None and 0 <= previous <= capacity else capacity
    first = free(max(1, guess))
    if first is None:
        return None
    if first and guess >= capacity:
        return 0                                     # nothing booked, one request
    lo, hi = (guess, capacity) if first else (0, max(guess - 1, 0))
    if first and free(guess + 1) is False:
        return capacity - guess                      # unchanged since last hour, two requests
    while lo < hi:
        mid = (lo + hi + 1) // 2
        answer = free(mid)
        if answer is None:
            return None
        if answer:
            lo = mid
        else:
            hi = mid - 1
    return capacity - lo


def closed_today(point, day):
    """Radical lists the days a point is shut in specialDays."""
    return any(str(d)[:10] == day for d in (point.get("specialDays") or []))


def opening_today(point):
    """Today's opening window as (from, to) in minutes, or None when closed."""
    ranges = ((point.get("availability") or {}).get("openingTimeRange")) or []
    best = None
    for r in ranges:
        try:
            a = dt.datetime.fromisoformat(r["openingTime"])
            b = dt.datetime.fromisoformat(r["closingTime"])
        except Exception:
            continue
        span = (a.hour * 60 + a.minute, b.hour * 60 + b.minute or 24 * 60)
        best = span if best is None else (min(best[0], span[0]), max(best[1], span[1]))
    return best


def last_readings():
    """Last bags booked seen for each point, to start the search from."""
    out = {}
    for r in read_csv(OCC_CSV):
        if r["booked"] != "":
            out[r["storage_id"]] = int(r["booked"])
    return out


OCC_FIELDS = ["read_at", "city", "area", "storage_id", "name", "day", "hour", "weekday",
              "capacity", "booked", "free", "status"]


def scan(only=None):
    now, offset_hours = rome_now()
    slot = (now + dt.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    offset = f"+{offset_hours:02d}:00"
    start = slot.strftime("%Y-%m-%dT%H:%M:00") + offset
    end = (slot + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:00") + offset
    stamp = now.strftime("%Y-%m-%d %H:%M")   # Italian time
    previous = last_readings()
    wanted = only or city_list()
    rows = []
    for city in wanted:
        points = [p for p in city_points(city) if p.get("countryISO") == "IT"]
        due = []
        for p in points:
            window = opening_today(p)
            minute = slot.hour * 60
            if window and not closed_today(p, slot.date().isoformat()) and window[0] <= minute and minute + 60 <= window[1]:
                due.append(p)

        def one(p):
            cap = min(p.get("capacity") or 0, CAP_CEILING)
            if cap <= 0:
                return None
            n = booked_bags(p["id"], cap, start, end, previous.get(str(p["id"])))
            state = "read" if n is not None else "not measured"
            if n == cap:
                # "not one bag fits" is almost always a point that is not taking bookings at all,
                # not a full one: check the same hour a week ahead before believing it (23/09/2026)
                later = slot + dt.timedelta(days=6)
                if bookable(p["id"], 1, later.strftime("%Y-%m-%dT%H:%M:00") + offset,
                            (later + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:00") + offset) is not True:
                    n, state = None, "unavailable"
                else:
                    state = "full"
            return dict(read_at=stamp, city=city, area=area_of(p), storage_id=p["id"], name=p["name"],
                        day=slot.date().isoformat(), hour=slot.hour,
                        weekday="weekend" if slot.weekday() >= 5 else "weekday",
                        capacity=cap, booked="" if n is None else n,
                        free="" if n is None else cap - n, status=state)

        with ThreadPoolExecutor(THREADS) as ex:
            fresh = [r for r in ex.map(one, due) if r]
        rows_to_csv(OCC_CSV, OCC_FIELDS, fresh)
        rows += fresh
        print(f"  {city:20} {len(fresh):3} points, {sum(r['booked'] for r in fresh if r['booked'] != ''):4} bags, "
              f"{_spent[0]} requests so far", flush=True)
        if _spent[0] >= MAX_REQUESTS:
            print(f"request ceiling reached, stopping after {city}")
            break
    total = sum(r["booked"] for r in rows if r["booked"] != "")
    print(f"{stamp} hour {slot.hour:02d}: {len(rows)} points read in {len(wanted)} cities, "
          f"{total} bags booked, {_spent[0]} requests -> {OCC_CSV}")


# ---------------------------------------------------------------- deposits per day

def daily():
    """Deposits per day, rebuilt from the hourly curve.

    Every point and day has a curve of bags present hour by hour. Each rise of that curve is
    luggage arriving: bags_in = first reading + sum of the rises. It is a floor, not the exact
    number: a bag picked up and another dropped off inside the same hour cancel each other out.
    Deposits = bags_in / BAGS_PER_BOOKING."""
    curve = collections.defaultdict(dict)
    meta = {}
    for r in read_csv(OCC_CSV):
        if r["booked"] == "":
            continue
        key = (r["city"], r["area"], r["storage_id"], r["day"])
        hour = int(r["hour"])
        # keep the last reading taken for that hour
        if hour not in curve[key] or r["read_at"] >= curve[key][hour][1]:
            curve[key][hour] = (int(r["booked"]), r["read_at"])
        meta[key] = (r["name"], int(r["capacity"]), r["weekday"])
    rows = []
    for key, hours in sorted(curve.items()):
        city, area, sid, day = key
        name, cap, weekday = meta[key]
        seq = [hours[h][0] for h in sorted(hours)]
        carry_in = seq[0]                            # already there at the first reading of the day
        bags_in = sum(max(0, b - a) for a, b in zip(seq, seq[1:]))
        rows.append(dict(day=day, weekday=weekday, city=city, area=area, storage_id=sid, name=name,
                         capacity=cap, hours_read=len(seq), peak=max(seq),
                         peak_pct=round(100 * max(seq) / cap, 1) if cap else "",
                         carry_in=carry_in, bags_in=bags_in,
                         deposits=round((carry_in + bags_in) / BAGS_PER_BOOKING, 1)))
    rows_to_csv(DAILY_CSV, ["day", "weekday", "city", "area", "storage_id", "name", "capacity",
                            "hours_read", "peak", "peak_pct", "carry_in", "bags_in", "deposits"],
                rows, append=False)

    def roll(level, fields):
        agg = collections.defaultdict(lambda: dict(points=set(), capacity=0, peak=0, carry_in=0, bags_in=0))
        for r in rows:
            a = agg[tuple(r[f] for f in level)]
            a["points"].add(r["storage_id"])
            for f in ("capacity", "peak", "carry_in", "bags_in"):
                a[f] += r[f]
        out = []
        for k, a in sorted(agg.items()):
            arrived = a["carry_in"] + a["bags_in"]
            out.append(dict(zip(level, k), points=len(a["points"]), capacity=a["capacity"], peak=a["peak"],
                            peak_pct=round(100 * a["peak"] / a["capacity"], 1) if a["capacity"] else "",
                            carry_in=a["carry_in"], bags_in=a["bags_in"],
                            deposits=round(arrived / BAGS_PER_BOOKING, 1)))
        return out

    city_rows = roll(["day", "city"], None)
    area_rows = roll(["day", "city", "area"], None)
    rows_to_csv(CITY_CSV, ["day", "city", "points", "capacity", "peak", "peak_pct", "carry_in", "bags_in", "deposits"],
                city_rows, append=False)
    rows_to_csv(AREA_CSV, ["day", "city", "area", "points", "capacity", "peak", "peak_pct", "carry_in", "bags_in", "deposits"],
                area_rows, append=False)
    print(f"{len(rows)} point-days -> {DAILY_CSV}; {len(city_rows)} city-days; {len(area_rows)} area-days")


# ---------------------------------------------------------------- dashboard

def dashboard():
    """One self-contained page: city, then area, then single point, plus the hour profile."""
    daily_rows = read_csv(DAILY_CSV)
    if not daily_rows:
        print("no data yet: run scan and daily first")
        return
    hours = collections.defaultdict(lambda: [0, 0])
    for r in read_csv(OCC_CSV):
        if r["booked"] != "":
            h = hours[(r["city"], int(r["hour"]))]
            h[0] += int(r["booked"])
            h[1] += int(r["capacity"])
    payload = dict(
        built=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        bags_per_booking=BAGS_PER_BOOKING,
        days=sorted({r["day"] for r in daily_rows}),
        points=[dict(day=r["day"], city=r["city"], area=r["area"] or "—", name=r["name"],
                     capacity=int(r["capacity"]), peak=int(r["peak"]), hours=int(r["hours_read"]),
                     bags=int(r["carry_in"]) + int(r["bags_in"]), deposits=float(r["deposits"])) for r in daily_rows],
        hours=[dict(city=c, hour=h, booked=v[0], capacity=v[1]) for (c, h), v in sorted(hours.items())],
    )
    os.makedirs(DOCS, exist_ok=True)
    html = TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf8") as f:
        f.write(html)
    print(f"{len(payload['points'])} point-days -> {os.path.join(DOCS, 'index.html')}")


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radical Storage occupancy</title>
<style>
:root{--bg:#fff;--fg:#14171f;--mut:#667;--line:#e4e7ee;--bar:#2f6df6;--bar2:#cfdcfb;--card:#f7f8fb}
@media (prefers-color-scheme:dark){:root{--bg:#11141a;--fg:#eef1f6;--mut:#99a;--line:#262b36;--bar:#6b9bff;--bar2:#26344f;--card:#171b23}}
*{box-sizing:border-box}body{margin:0;padding:24px 16px 64px;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}p.sub{color:var(--mut);margin:0 0 20px}
.controls{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}select,input{padding:7px 10px;border:1px solid var(--line);
border-radius:8px;background:var(--card);color:var(--fg);font:inherit}
table{width:100%;border-collapse:collapse;margin-bottom:28px}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}th{cursor:pointer;color:var(--mut);font-weight:600;white-space:nowrap}
tr.parent{cursor:pointer}tr.child td:first-child{padding-left:26px;color:var(--mut)}tr.leaf td:first-child{padding-left:46px}
h2{font-size:16px;margin:24px 0 8px}.bar{height:8px;background:var(--bar2);border-radius:4px;overflow:hidden;min-width:60px}
.bar>i{display:block;height:100%;background:var(--bar)}.hrs{display:grid;grid-template-columns:repeat(24,1fr);gap:3px;align-items:end;height:120px}
.hrs>div{background:var(--bar);border-radius:3px 3px 0 0;min-height:2px}.hlab{display:grid;grid-template-columns:repeat(24,1fr);gap:3px;color:var(--mut);font-size:10px;text-align:center}
@media (max-width:640px){body{padding:16px 12px 48px}table{font-size:13px}
th:nth-child(2),td:nth-child(2),th:nth-child(4),td:nth-child(4){display:none}
th,td{padding:6px 4px}tr.child td:first-child{padding-left:14px}tr.leaf td:first-child{padding-left:26px}}
</style></head><body><div class="wrap">
<h1>Radical Storage — how full the luggage points are</h1>
<p class="sub" id="sub"></p>
<div class="controls">
<select id="day"></select><select id="city"></select><input id="q" placeholder="filter by name or area">
</div>
<h2>Bags present by hour of day</h2>
<div class="hrs" id="hrs"></div><div class="hlab" id="hlab"></div>
<h2>City → area → point</h2>
<table id="tbl"><thead><tr><th data-k="label">Name</th><th data-k="kind">Level</th><th data-k="points">Points</th>
<th data-k="capacity">Places</th><th data-k="peak">Peak bags</th><th data-k="fill">Fill</th>
<th data-k="bags">Bags in</th><th data-k="deposits">Deposits</th></tr></thead><tbody></tbody></table>
<p class="sub">Deposits = bags arrived ÷ __BPB__ bags per booking, a floor: luggage that arrives and leaves inside the same
hour is invisible. Radical only, no walk-ins and no other marketplace.</p>
</div><script>
const D=__DATA__;document.querySelectorAll('.sub')[0].textContent='Built '+D.built+' · '+D.points.length+' point-days · Radical Storage public data';
document.body.innerHTML=document.body.innerHTML.replace('__BPB__',D.bags_per_booking);
const day=document.getElementById('day'),city=document.getElementById('city'),q=document.getElementById('q');
day.innerHTML='<option value="">all days</option>'+D.days.map(d=>`<option>${d}</option>`).join('');
const cities=[...new Set(D.points.map(p=>p.city))].sort();
city.innerHTML='<option value="">all cities</option>'+cities.map(c=>`<option>${c}</option>`).join('');
let sortKey='deposits',dir=-1,open=new Set();
const sel=()=>D.points.filter(p=>(!day.value||p.day===day.value)&&(!city.value||p.city===city.value)
  &&(!q.value||(p.name+' '+p.area).toLowerCase().includes(q.value.toLowerCase())));
function group(rows,keys){const m=new Map();for(const r of rows){const k=keys.map(k=>r[k]).join(' / ');
  const a=m.get(k)||{label:keys.length>1?r[keys[keys.length-1]]:r[keys[0]],ids:new Set(),capacity:0,peak:0,bags:0,deposits:0,key:k};
  a.ids.add(r.city+r.area+r.name);a.capacity+=r.capacity;a.peak+=r.peak;a.bags+=r.bags;a.deposits+=r.deposits;m.set(k,a)}
  return [...m.values()].map(a=>({...a,points:a.ids.size,fill:a.capacity?100*a.peak/a.capacity:0}))}
function sortRows(r){return r.sort((a,b)=>(a[sortKey]>b[sortKey]?1:a[sortKey]<b[sortKey]?-1:0)*dir)}
function draw(){const rows=sel(),body=document.querySelector('#tbl tbody'),out=[];
 for(const c of sortRows(group(rows,['city']))){out.push(row(c,'city','parent'));
  if(open.has(c.key)){for(const a of sortRows(group(rows.filter(r=>r.city===c.key),['city','area'])))
   {out.push(row(a,'area','child'));if(open.has(a.key)){for(const p of sortRows(group(rows.filter(r=>r.city+' / '+r.area===a.key),['city','area','name'])))
     out.push(row(p,'point','leaf'))}}}}
 body.innerHTML=out.join('');
 [...body.querySelectorAll('tr.parent,tr.child')].forEach(tr=>tr.onclick=()=>{const k=tr.dataset.key;open.has(k)?open.delete(k):open.add(k);draw()});
 const hs={};for(const h of D.hours){if(city.value&&h.city!==city.value)continue;hs[h.hour]=hs[h.hour]||[0,0];hs[h.hour][0]+=h.booked;hs[h.hour][1]+=h.capacity}
 const mx=Math.max(1,...Object.values(hs).map(v=>v[0]));
 document.getElementById('hrs').innerHTML=[...Array(24).keys()].map(h=>`<div style="height:${100*(hs[h]?hs[h][0]:0)/mx}%" title="${h}:00 — ${hs[h]?hs[h][0]:0} bags"></div>`).join('');
 document.getElementById('hlab').innerHTML=[...Array(24).keys()].map(h=>`<div>${h%3?'':h}</div>`).join('');}
function row(r,kind,cls){return `<tr class="${cls}" data-key="${r.key||''}"><td>${r.label}</td><td>${kind}</td><td>${r.points}</td>
 <td>${r.capacity}</td><td>${r.peak}</td><td><div class="bar"><i style="width:${Math.min(100,r.fill).toFixed(0)}%"></i></div></td>
 <td>${r.bags}</td><td>${r.deposits.toFixed(1)}</td></tr>`}
document.querySelectorAll('#tbl th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;dir=(k===sortKey)?-dir:-1;sortKey=k;draw()});
[day,city,q].forEach(e=>e.oninput=draw);draw();
</script></body></html>"""


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    only = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    {"cities": cities, "census": census, "scan": lambda: scan(only), "daily": daily,
     "dashboard": dashboard}[cmd]()
