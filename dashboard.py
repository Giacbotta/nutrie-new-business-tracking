"""Builds docs/index.html: how full the luggage competitors are, hour by hour (board item E-93).

  python dashboard.py

Three sources, one yardstick. Every reading becomes the same three numbers — capacity, occupied,
free — so the providers can be compared side by side. Whether the number arrives by addition or by
subtraction is this script's problem, not the reader's:

  Radical Storage  occupied = bags booked in that hour, narrowed down from the booking page.
                   capacity = what the point declares.
  Bounce           occupied = luggage in the shop at the moment of the reading (reservationCount,
                   which moves up and down). capacity = what the point declares.
  Stow Your Bags   the form gives free lockers, so occupied = capacity - free. Capacity is not
                   published: it is the most lockers ever seen free for that shop and size, so the
                   figure gets more accurate as the series grows.

Occupied means bags for Radical and Bounce, lockers for Stow Your Bags. The page says so.

The drill is the same for every provider — city, neighbourhood, exact location — and the
neighbourhood comes from one shared geocoding of the coordinates (areas.py), never from each
provider's own naming, or the levels would not line up. Locker size is a fourth level under a Stow
Your Bags location, so the first three levels stay parallel across providers.
"""
import collections, csv, datetime as dt, json, os
import areas as shared_areas

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DOCS = os.path.join(HERE, "docs")

PROVIDERS = {
    "radical": dict(label="Radical Storage", unit="bags",
                    note="Occupied = bags booked for that hour, found by narrowing down how many more "
                         "bags still fit. Capacity is what each point declares."),
    "bounce": dict(label="Bounce", unit="bags",
                   note="Occupied = luggage in the shop at the moment of the reading. Capacity is what "
                        "each point declares."),
    "stow": dict(label="Stow Your Bags", unit="lockers",
                 note="The only real locker operator of the three. The booking form gives free lockers, "
                      "so occupied is capacity minus free; capacity is the most ever seen free for that "
                      "shop and locker size, so it sharpens as the series grows."),
}


def read_csv(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf8") as f:
        return list(csv.DictReader(f))


def number(x, default=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def when(read_at):
    """Day and hour a reading belongs to: when the run happened, Italian time.

    The collectors ask about slightly different windows — Radical and Stow Your Bags about the hour
    that is starting, Bounce about this very moment — so the run itself is the only label that puts
    all three on the same axis. Without it a run at 23:16 lands on two different days."""
    return read_at[:10], int(read_at[11:13])


def readings():
    """Every point-hour reading of every provider, as
    (provider, day, hour, city, area, point id, location, size, capacity, occupied, read_at)."""
    nearest = shared_areas.nearest_lookup()

    def area(lat, lng):
        return nearest(lat, lng) or "—"

    out = []
    where = {r["storage_id"]: (r["lat"], r["lng"]) for r in read_csv("radical-points.csv")}
    for r in read_csv("radical-occupancy.csv"):
        if r.get("status") != "read" or r["booked"] == "":
            continue
        lat, lng = where.get(r["storage_id"], (None, None))
        out.append(("radical", *when(r["read_at"]), r["city"], area(lat, lng), r["storage_id"],
                    r["name"], "", number(r["capacity"]), number(r["booked"]), r["read_at"]))

    bounce_rows = read_csv("bounce-occupancy.csv")
    caps = collections.defaultdict(int)
    for r in bounce_rows:
        caps[r["spot_id"]] = max(caps[r["spot_id"]], number(r["capacity"]))
    for r in bounce_rows:
        if r["reservations"] == "":
            continue
        out.append(("bounce", *when(r["read_at"]), r["city"], area(r["lat"], r["lng"]),
                    r["spot_id"], r["name"], "", caps[r["spot_id"]], number(r["reservations"]), r["read_at"]))

    shops = {r["shop_id"]: r for r in read_csv("stow-shops.csv")}
    stow_rows = [r for r in read_csv("stow-occupancy.csv") if r.get("status") == "read" and r["free"] != ""]
    best = collections.defaultdict(int)
    for r in stow_rows:
        best[(r["shop_id"], r["locker_type"])] = max(best[(r["shop_id"], r["locker_type"])], number(r["free"]))
    for r in stow_rows:
        cap = best[(r["shop_id"], r["locker_type"])]
        shop = shops.get(r["shop_id"], {})
        out.append(("stow", *when(r["read_at"]), r["city"], area(shop.get("lat"), shop.get("lng")),
                    r["shop_id"], r["name"], r["locker_type"], cap, cap - number(r["free"]), r["read_at"]))
    return out


def build():
    rows = readings()
    latest_hour = {}
    for p, day, hour, city, area, pid, name, size, cap, occ, read_at in rows:
        key = (p, city, area, pid, name, size, day, hour)
        if key not in latest_hour or read_at >= latest_hour[key][0]:
            latest_hour[key] = (read_at, cap, occ)

    hourly = collections.defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    points = {}
    for (p, city, area, pid, name, size, day, hour), (read_at, cap, occ) in latest_hour.items():
        agg = hourly[(p, day, hour, city)]
        agg[0] += cap
        agg[1] += occ
        agg[2] += 1
        if cap > 0:                     # 84% of Bounce points declare no capacity: a percentage
            agg[3] += cap               # can only be worked out on those that do
            agg[4] += occ
            agg[5] += 1
        key = (p, city, area, pid, name, size)
        if key not in points or (day, hour) >= points[key][0]:
            points[key] = ((day, hour), cap, occ)

    last_read = {}
    for p, day, hour, city, area, pid, name, size, cap, occ, read_at in rows:
        last_read[p] = max(last_read.get(p, ""), read_at)

    payload = dict(
        built=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        providers=[dict(id=k, label=v["label"], unit=v["unit"], note=v["note"],
                        last=last_read.get(k, "never")) for k, v in PROVIDERS.items()],
        hourly=[dict(p=p, day=day, hour=hour, city=city, cap=v[0], occ=v[1], pts=v[2],
                     capk=v[3], occk=v[4], ptsk=v[5])
                for (p, day, hour, city), v in sorted(hourly.items())],
        points=[dict(p=p, city=city, area=area, id=pid, name=name, size=size, day=d[0], hour=d[1],
                     cap=cap, occ=occ)
                for (p, city, area, pid, name, size), (d, cap, occ) in sorted(points.items())],
    )
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf8") as f:
        f.write(TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":"))))
    days = sorted({h["day"] for h in payload["hourly"]})
    print(f"{len(payload['hourly'])} city-hours, {len(payload['points'])} points, days {days} "
          f"-> {os.path.join(DOCS, 'index.html')}")
    for p in payload["providers"]:
        print(f"  {p['label']:18} last reading {p['last']}")


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Luggage storage occupancy in Italy</title>
<style>
:root{--bg:#fff;--fg:#14171f;--mut:#6b7280;--line:#e5e7eb;--card:#f8fafc;--soft:#eef2f7;
--radical:#2f6df6;--bounce:#e0682a;--stow:#0f9d77}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1218;--fg:#eef1f6;--mut:#98a2b3;
--line:#242a35;--card:#161a22;--soft:#1b2029;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0f1218;--fg:#eef1f6;--mut:#98a2b3;--line:#242a35;--card:#161a22;--soft:#1b2029;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;padding:24px 16px 72px;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1140px;margin:0 auto}
h1{font-size:23px;margin:0 0 4px;letter-spacing:-.01em}p.sub{color:var(--mut);margin:0 0 20px;font-size:14px}
h2{font-size:13px;margin:28px 0 10px;text-transform:uppercase;letter-spacing:.07em;color:var(--mut)}
.controls{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
select,input{padding:8px 11px;border:1px solid var(--line);border-radius:9px;background:var(--card);color:var(--fg);font:inherit}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card .who{display:flex;align-items:center;gap:8px;font-weight:600;margin-bottom:6px}
.dot{width:10px;height:10px;border-radius:50%;flex:none}
.big{font-size:27px;font-weight:650;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.card small{color:var(--mut);display:block;margin-top:3px;font-size:12.5px}
.meter{height:7px;background:var(--soft);border-radius:4px;overflow:hidden;margin:9px 0 2px}
.meter>i{display:block;height:100%}
table{width:100%;border-collapse:collapse}#hourtbl{margin:14px 0 0;max-width:720px;min-width:420px}#tbl{min-width:640px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%}
#hourtbl th{cursor:default;text-align:right}#hourtbl th:first-child,#hourtbl td:first-child{text-align:left;width:88px}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right;
font-variant-numeric:tabular-nums}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{cursor:pointer;color:var(--mut);font-weight:600;font-size:13px;white-space:nowrap}
tr.l0{cursor:pointer;font-weight:600}tr.l1{cursor:pointer}tr.l1 td:first-child{padding-left:22px}
tr.l2 td:first-child{padding-left:44px}tr.l3 td:first-child{padding-left:66px;color:var(--mut)}
.bar{height:7px;background:var(--soft);border-radius:4px;overflow:hidden;min-width:56px}.bar>i{display:block;height:100%}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--mut);font-size:13px;margin:8px 0 0}
.legend span{display:flex;align-items:center;gap:6px}
svg{width:100%;height:240px;display:block}
.note{color:var(--mut);font-size:13px;margin:10px 0 0}
.tag{font-size:12px;color:var(--mut)}
@media (max-width:640px){body{padding:16px 12px 48px}table{font-size:13px}th,td{padding:6px 4px}
#tbl th:nth-child(2),#tbl td:nth-child(2),#tbl th:nth-child(4),#tbl td:nth-child(4){display:none}
svg{height:200px}.legend{font-size:12px;gap:10px}
tr.l1 td:first-child{padding-left:12px}tr.l2 td:first-child{padding-left:24px}tr.l3 td:first-child{padding-left:36px}}
</style></head><body><div class="wrap">
<h1>Luggage storage in Italy — how full the competition is</h1>
<p class="sub" id="sub"></p>

<div class="cards" id="cards"></div>

<h2>Occupied by hour of day</h2>
<div class="controls">
<select id="day"></select><select id="city"></select><select id="metric">
<option value="pct">show fill %</option><option value="abs">show how many occupied</option></select>
</div>
<div id="chart"></div>
<div class="legend" id="legend"></div>
<p class="note" id="chartnote"></p>
<div class="scroll"><table id="hourtbl"><thead><tr><th>Hour</th></tr></thead><tbody></tbody></table></div>

<h2>City by city, provider by provider</h2>
<div class="controls">
<select id="prov"></select><input id="q" placeholder="filter by name or area">
</div>
<div class="scroll"><table id="tbl"><thead><tr>
<th data-k="label">City / neighbourhood / location</th><th data-k="kind">What</th><th data-k="pts">Points</th>
<th data-k="capk">Capacity</th><th data-k="occ">Occupied</th><th data-k="free">Free</th>
<th data-k="fill">Fill</th><th data-k="fillpct">%</th></tr></thead><tbody></tbody></table></div>
<p class="note" id="note"></p>
</div><script>
const D=__DATA__;
const COLOR={radical:'#2f6df6',bounce:'#e0682a',stow:'#0f9d77'};
const LABEL={},UNIT={};D.providers.forEach(p=>{LABEL[p.id]=p.label;UNIT[p.id]=p.unit});
const day=document.getElementById('day'),prov=document.getElementById('prov'),city=document.getElementById('city'),
      q=document.getElementById('q'),metric=document.getElementById('metric');
const days=[...new Set(D.hourly.map(h=>h.day))].sort();
day.innerHTML=days.map(d=>`<option>${d}</option>`).join('')+'<option value="">all days</option>';
day.value=days[days.length-1]||'';
prov.innerHTML='<option value="">all providers</option>'+D.providers.map(p=>`<option value="${p.id}">${p.label}</option>`).join('');
const cities=[...new Set(D.hourly.map(h=>h.city))].sort();
city.innerHTML='<option value="">all cities</option>'+cities.map(c=>`<option>${c}</option>`).join('');
let sortKey='occ',dir=-1,open=new Set();
const pct=(o,c)=>c?100*o/c:0, fmt=n=>Math.round(n).toLocaleString('en-US');

const hsel=()=>D.hourly.filter(h=>(!day.value||h.day===day.value)&&(!city.value||h.city===city.value));
const psel=()=>D.points.filter(p=>(!day.value||p.day===day.value)&&(!city.value||p.city===city.value)
  &&(!prov.value||p.p===prov.value)&&(!q.value||(p.name+' '+p.area+' '+p.city).toLowerCase().includes(q.value.toLowerCase())));

function cards(){
 const rows=hsel(),latest={},byHour={};
 for(const h of rows){
  const stamp=h.day+' '+String(h.hour).padStart(2,'0');
  const bh=byHour[h.p]=byHour[h.p]||{};bh[stamp]=bh[stamp]||{cap:0,occ:0,capk:0,occk:0};
  bh[stamp].cap+=h.cap;bh[stamp].occ+=h.occ;bh[stamp].capk+=h.capk;bh[stamp].occk+=h.occk;
  if(!latest[h.p]||stamp>latest[h.p].stamp)latest[h.p]={stamp,cap:0,occ:0,pts:0,capk:0,occk:0,ptsk:0};
  if(latest[h.p].stamp===stamp){const L=latest[h.p];L.cap+=h.cap;L.occ+=h.occ;L.pts+=h.pts;L.capk+=h.capk;L.occk+=h.occk;L.ptsk+=h.ptsk}}
 document.getElementById('cards').innerHTML=D.providers.map(p=>{
  const l=latest[p.id];
  if(!l)return `<div class="card"><div class="who"><i class="dot" style="background:${COLOR[p.id]}"></i>${p.label}</div>
   <div class="big">—</div><small>no reading yet</small><small>last reading ${p.last}</small></div>`;
  const f=pct(l.occk,l.capk),hrs=byHour[p.id]||{};
  const best=Object.entries(hrs).sort((a,b)=>b[1].occ-a[1].occ)[0];
  return `<div class="card"><div class="who"><i class="dot" style="background:${COLOR[p.id]}"></i>${p.label}</div>
   <div class="big">${fmt(l.occ)} <span style="font-size:15px;font-weight:500;color:var(--mut)">${UNIT[p.id]} occupied</span></div>
   <div class="meter"><i style="width:${Math.min(100,f).toFixed(1)}%;background:${COLOR[p.id]}"></i></div>
   <small>${fmt(l.pts)} points read at ${l.stamp.split(' ')[1]}:00</small>
   <small>${f.toFixed(1)}% full, counting the ${fmt(l.ptsk)} points that declare a capacity (${fmt(l.capk)} places)</small>
   <small>busiest hour ${best?best[0].split(' ')[1]+':00 with '+fmt(best[1].occ)+' '+UNIT[p.id]:'—'}</small>
   <small>last run ${p.last}</small></div>`}).join('');
 document.getElementById('sub').textContent='Built '+D.built+' · '+fmt(D.points.length)+' points · '
  +fmt(D.hourly.length)+' city-hours collected · occupied means bags on Radical and Bounce, lockers on Stow Your Bags';
}

function chart(){
 const rows=hsel(),series={},abs=metric.value==='abs';
 for(const h of rows){const s=series[h.p]=series[h.p]||{};const a=s[h.hour]=s[h.hour]||{cap:0,occ:0,capk:0,occk:0};
  a.cap+=h.cap;a.occ+=h.occ;a.capk+=h.capk;a.occk+=h.occk}
 const val=v=>abs?v.occ:pct(v.occk,v.capk);
 const W=1000,H=240,L=46,B=26,T=10;
 const raw=Math.max(1,...Object.values(series).flatMap(s=>Object.values(s).map(val)));
 const step=Math.pow(10,Math.floor(Math.log10(raw)))/2||1, ymax=Math.ceil(raw/step)*step;
 const x=h=>L+(W-L-8)*h/23, y=v=>T+(H-B-T)*(1-v/ymax);
 let g='';
 for(let i=0;i<=4;i++){const t=ymax*i/4;
  g+=`<line x1="${L}" x2="${W-8}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}" stroke="var(--line)"/>
  <text x="6" y="${(y(t)+4).toFixed(1)}" fill="var(--mut)" font-size="11">${abs?fmt(t):t.toFixed(0)+'%'}</text>`}
 for(let h=0;h<24;h+=3)g+=`<text x="${x(h).toFixed(1)}" y="${H-6}" fill="var(--mut)" font-size="11" text-anchor="middle">${h}</text>`;
 let paths='';
 for(const [p,s] of Object.entries(series)){
  const hs=Object.keys(s).map(Number).sort((a,b)=>a-b);if(!hs.length)continue;
  paths+='<path fill="none" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" stroke="'+COLOR[p]+'" d="'+
   hs.map((h,i)=>(i?'L':'M')+x(h).toFixed(1)+' '+y(val(s[h])).toFixed(1)).join(' ')+'"/>';
  paths+=hs.map(h=>`<circle cx="${x(h).toFixed(1)}" cy="${y(val(s[h])).toFixed(1)}" r="3.5" fill="${COLOR[p]}"><title>${LABEL[p]} ${h}:00 — ${fmt(s[h].occ)} ${UNIT[p]} occupied, ${pct(s[h].occk,s[h].capk).toFixed(1)}% of the ${fmt(s[h].capk)} places with a declared capacity</title></circle>`).join('');
 }
 document.getElementById('chart').innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Occupied by hour of day">${g}${paths}</svg>`;
 document.getElementById('legend').innerHTML=D.providers.map(p=>`<span><i class="dot" style="background:${COLOR[p.id]}"></i>${p.label} <em style="font-style:normal;opacity:.7">(${p.unit})</em></span>`).join('');
 hourTable(series);
 const hrs=[...new Set(rows.map(r=>r.hour))].length;
 document.getElementById('chartnote').textContent=hrs<3
  ? 'Only '+hrs+' hour'+(hrs===1?'':'s')+' collected so far — the curve fills in as the hourly runs come in.'
  : 'One point per hourly reading, summed over the selection. Hover a point for the exact numbers.';
}

function hourTable(series){
 const hours=[...new Set(Object.values(series).flatMap(s=>Object.keys(s).map(Number)))].sort((a,b)=>a-b);
 const head='<tr><th>Hour</th>'+D.providers.map(p=>`<th style="color:${COLOR[p.id]}">${p.label}<br><span style="font-weight:400">${p.unit} occupied</span></th>`).join('')+'</tr>';
 const body=hours.map(h=>'<tr><td>'+String(h).padStart(2,'0')+':00</td>'+D.providers.map(p=>{
   const v=(series[p.id]||{})[h];
   if(!v)return '<td class="tag">no reading</td>';
   const f=pct(v.occk,v.capk);
   return `<td>${fmt(v.occ)}<span class="tag"> · ${f.toFixed(1)}%</span></td>`}).join('')+'</tr>').join('');
 document.querySelector('#hourtbl thead').innerHTML=head;
 document.querySelector('#hourtbl tbody').innerHTML=body||'<tr><td colspan="4">No readings yet.</td></tr>';
}

function group(rows,keys){const m=new Map();
 for(const r of rows){const k=keys.map(f=>f(r)).join(' / ');
  const a=m.get(k)||{key:k,label:keys[keys.length-1](r),cap:0,occ:0,capk:0,occk:0,pts:new Set(),provs:new Set()};
  a.cap+=r.cap;a.occ+=r.occ;if(r.cap>0){a.capk+=r.cap;a.occk+=r.occ}
  a.pts.add(r.p+'|'+r.id+'|'+r.size);a.provs.add(r.p);m.set(k,a)}
 return [...m.values()].map(a=>({...a,pts:a.pts.size,free:Math.max(0,a.capk-a.occk),
   fill:pct(a.occk,a.capk),fillpct:pct(a.occk,a.capk),prov:a.provs.size===1?[...a.provs][0]:''}))}
const srt=r=>r.sort((a,b)=>((a[sortKey]>b[sortKey])?1:(a[sortKey]<b[sortKey])?-1:0)*dir);

function row(r,kind,cls){
 const color=r.prov?COLOR[r.prov]:'var(--mut)';
 const tag=kind==='location'?(LABEL[r.prov]||kind):kind==='size'?r.label:kind;
 return `<tr class="${cls}" data-key="${r.open?r.key:''}"><td>${r.label}</td>
 <td><span class="tag" ${r.prov?`style="color:${color}"`:''}>${tag}</span></td><td>${fmt(r.pts)}</td>
 <td>${r.capk?fmt(r.capk):'—'}</td><td>${fmt(r.occ)}</td><td>${r.capk?fmt(r.free):'—'}</td>
 <td><div class="bar"><i style="width:${Math.min(100,r.fill).toFixed(0)}%;background:${color}"></i></div></td>
 <td>${r.fillpct.toFixed(1)}%</td></tr>`}

function table(){
 const rows=psel(),out=[];
 const cityKey=r=>r.city, areaKey=r=>r.area, locKey=r=>r.p+'|'+r.id, sizeKey=r=>r.size||'—';
 for(const c of srt(group(rows,[cityKey]))){
  out.push(row({...c,open:1},'city','l0'));
  if(!open.has(c.key))continue;
  const inCity=rows.filter(r=>r.city===c.key);
  for(const a of srt(group(inCity,[cityKey,areaKey]))){
   out.push(row({...a,open:1},'neighbourhood','l1'));
   if(!open.has(a.key))continue;
   const inArea=inCity.filter(r=>r.area===a.label);
   for(const l of srt(group(inArea,[cityKey,areaKey,locKey]))){
    const inLoc=inArea.filter(r=>LABEL[r.p]+' · '+r.name===l.label);
    const sizes=inLoc.filter(r=>r.size);
    out.push(row({...l,label:l.label.split(' · ').slice(1).join(' · '),open:sizes.length?1:0},'location','l2'));
    if(sizes.length&&open.has(l.key))
     for(const z of srt(group(sizes,[cityKey,areaKey,locKey,sizeKey])))
      out.push(row({...z,prov:l.prov},'size','l3'));
   }}}
 const body=document.querySelector('#tbl tbody');
 body.innerHTML=out.join('')||'<tr><td colspan="8">No readings for this selection yet.</td></tr>';
 [...body.querySelectorAll('tr')].forEach(tr=>{if(!tr.dataset.key)return;
  tr.style.cursor='pointer';
  tr.onclick=()=>{const k=tr.dataset.key;open.has(k)?open.delete(k):open.add(k);table()}});
 document.getElementById('note').textContent=(prov.value
  ? D.providers.find(p=>p.id===prov.value).note
  : D.providers.map(p=>p.label+' — '+p.note).join('   '))
  + '   Occupied is always the comparable number: bags on Radical and Bounce, lockers on Stow Your Bags. Capacity, free and % are shown only where the provider declares a capacity — 84% of Bounce points do not.'
  + '   Drill: city, then neighbourhood (the same geography for every provider), then the exact location, then locker size where there is one.';
}
function draw(){cards();chart();table()}
[day,city,metric].forEach(e=>e.oninput=draw);
[prov,q].forEach(e=>e.oninput=table);
document.querySelectorAll('#tbl th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;dir=(k===sortKey)?-dir:-1;sortKey=k;table()});
draw();
</script></body></html>"""


if __name__ == "__main__":
    build()
