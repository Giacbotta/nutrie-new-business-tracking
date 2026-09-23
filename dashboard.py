"""Builds docs/index.html from what the two luggage trackers have collected (board item E-93).

  python dashboard.py

Sources, chosen from the page itself:
  Radical Storage  bags booked per hour, from radical_occupancy.py. Deepest data: city, area,
                   single point, and the hour-of-day profile.
  Bounce           reservations and capacity per point, from bounce_occupancy.py. Wider coverage
                   (3.381 points against 1.386) but no hourly curve, and the window the
                   reservation counter refers to is still unknown, so its change is shown raw.
"""
import collections, csv, datetime as dt, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DOCS = os.path.join(HERE, "docs")
BAGS_PER_BOOKING = 2.0


def read_csv(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf8") as f:
        return list(csv.DictReader(f))


def build():
    radical = [dict(day=r["day"], city=r["city"], area=r["area"] or "—", name=r["name"],
                    capacity=int(r["capacity"]), peak=int(r["peak"]), hours=int(r["hours_read"]),
                    bags=int(r["carry_in"]) + int(r["bags_in"]), deposits=float(r["deposits"]))
               for r in read_csv("radical-daily.csv")]
    bounce = [dict(day=r["day"], city=r["city"], area="—", name=r["name"],
                   capacity=int(r["capacity"] or 0), peak=int(r["reservations_end"]), hours=0,
                   bags=int(r["reservations_end"]),
                   deposits=float(r["change"]) if r["change"] not in ("", None) else 0.0)
              for r in read_csv("bounce-daily.csv")]
    hours = collections.defaultdict(lambda: [0, 0])
    for r in read_csv("radical-occupancy.csv"):
        if r.get("booked"):
            h = hours[(r["city"], int(r["hour"]))]
            h[0] += int(r["booked"])
            h[1] += int(r["capacity"])
    payload = dict(
        built=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        bags_per_booking=BAGS_PER_BOOKING,
        sources=dict(
            radical=dict(label="Radical Storage", rows=radical,
                         columns=["Peak bags", "Fill", "Bags in", "Deposits"],
                         note="Bags booked, read every hour. Deposits are the rises of the hourly curve "
                              "divided by %g bags per booking: a floor, because luggage that arrives and "
                              "leaves inside the same hour is invisible." % BAGS_PER_BOOKING),
            bounce=dict(label="Bounce", rows=bounce,
                        columns=["Reservations", "Fill", "Reservations", "Change"],
                        note="Reservations and capacity as Bounce publishes them. The window the "
                             "reservation counter covers is not settled yet, so read the level and treat "
                             "the change as raw movement, not deposits."),
        ),
        hours=[dict(city=c, hour=h, booked=v[0], capacity=v[1]) for (c, h), v in sorted(hours.items())],
    )
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf8") as f:
        f.write(TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":"))))
    print(f"{len(radical)} Radical point-days and {len(bounce)} Bounce point-days "
          f"-> {os.path.join(DOCS, 'index.html')}")


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Luggage storage occupancy in Italy</title>
<style>
:root{--bg:#fff;--fg:#14171f;--mut:#667;--line:#e4e7ee;--bar:#2f6df6;--bar2:#cfdcfb;--card:#f7f8fb}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#11141a;--fg:#eef1f6;--mut:#99a;--line:#262b36;--bar:#6b9bff;--bar2:#26344f;--card:#171b23;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#11141a;--fg:#eef1f6;--mut:#99a;--line:#262b36;--bar:#6b9bff;--bar2:#26344f;--card:#171b23;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;padding:24px 16px 64px;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}p.sub{color:var(--mut);margin:0 0 20px}
.controls{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}select,input{padding:7px 10px;border:1px solid var(--line);
border-radius:8px;background:var(--card);color:var(--fg);font:inherit}
table{width:100%;border-collapse:collapse;margin-bottom:12px}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}th{cursor:pointer;color:var(--mut);font-weight:600;white-space:nowrap}
tr.parent{cursor:pointer}tr.child td:first-child{padding-left:26px;color:var(--mut)}tr.leaf td:first-child{padding-left:46px}
h2{font-size:16px;margin:24px 0 8px}.bar{height:8px;background:var(--bar2);border-radius:4px;overflow:hidden;min-width:60px}
.bar>i{display:block;height:100%;background:var(--bar)}.hrs{display:grid;grid-template-columns:repeat(24,1fr);gap:3px;align-items:end;height:120px}
.hrs>div{background:var(--bar);border-radius:3px 3px 0 0;min-height:2px}.hlab{display:grid;grid-template-columns:repeat(24,1fr);gap:3px;color:var(--mut);font-size:10px;text-align:center}
td{font-variant-numeric:tabular-nums}
@media (max-width:640px){body{padding:16px 12px 48px}table{font-size:13px}
th:nth-child(2),td:nth-child(2),th:nth-child(4),td:nth-child(4){display:none}
th,td{padding:6px 4px}tr.child td:first-child{padding-left:14px}tr.leaf td:first-child{padding-left:26px}}
</style></head><body><div class="wrap">
<h1>Luggage storage in Italy — how full the competition is</h1>
<p class="sub" id="sub"></p>
<div class="controls">
<select id="src"></select><select id="day"></select><select id="city"></select><input id="q" placeholder="filter by name or area">
</div>
<div id="hourblock"><h2>Bags present by hour of day</h2>
<div class="hrs" id="hrs"></div><div class="hlab" id="hlab"></div></div>
<h2 id="tabletitle">City → area → point</h2>
<table id="tbl"><thead><tr><th data-k="label">Name</th><th data-k="kind">Level</th><th data-k="points">Points</th>
<th data-k="capacity">Places</th><th data-k="peak" id="c1">Peak</th><th data-k="fill">Fill</th>
<th data-k="bags" id="c3">Bags in</th><th data-k="deposits" id="c4">Deposits</th></tr></thead><tbody></tbody></table>
<p class="sub" id="note"></p>
</div><script>
const D=__DATA__;
const src=document.getElementById('src'),day=document.getElementById('day'),city=document.getElementById('city'),q=document.getElementById('q');
src.innerHTML=Object.entries(D.sources).map(([k,s])=>`<option value="${k}">${s.label}</option>`).join('');
let sortKey='deposits',dir=-1,open=new Set();
const rows=()=>D.sources[src.value].rows;
function refill(){
 const rs=rows(),days=[...new Set(rs.map(r=>r.day))].sort(),cities=[...new Set(rs.map(r=>r.city))].sort();
 day.innerHTML='<option value="">all days</option>'+days.map(d=>`<option>${d}</option>`).join('');
 city.innerHTML='<option value="">all cities</option>'+cities.map(c=>`<option>${c}</option>`).join('');
 const s=D.sources[src.value];
 document.getElementById('c1').textContent=s.columns[0];document.getElementById('c3').textContent=s.columns[2];
 document.getElementById('c4').textContent=s.columns[3];document.getElementById('note').textContent=s.note;
 document.getElementById('hourblock').hidden=src.value!=='radical';
 document.getElementById('tabletitle').textContent=src.value==='radical'?'City → area → point':'City → point';
 document.getElementById('sub').textContent='Built '+D.built+' · '+rs.length+' point-days · public data from '+s.label;
 open=new Set();draw();}
const sel=()=>rows().filter(p=>(!day.value||p.day===day.value)&&(!city.value||p.city===city.value)
  &&(!q.value||(p.name+' '+p.area).toLowerCase().includes(q.value.toLowerCase())));
function group(rs,keys){const m=new Map();for(const r of rs){const k=keys.map(k=>r[k]).join(' / ');
  const a=m.get(k)||{label:keys.length>1?r[keys[keys.length-1]]:r[keys[0]],ids:new Set(),capacity:0,peak:0,bags:0,deposits:0,key:k};
  a.ids.add(r.city+r.area+r.name);a.capacity+=r.capacity;a.peak+=r.peak;a.bags+=r.bags;a.deposits+=r.deposits;m.set(k,a)}
  return [...m.values()].map(a=>({...a,points:a.ids.size,fill:a.capacity?100*a.peak/a.capacity:0}))}
function sortRows(r){return r.sort((a,b)=>(a[sortKey]>b[sortKey]?1:a[sortKey]<b[sortKey]?-1:0)*dir)}
function draw(){const rs=sel(),body=document.querySelector('#tbl tbody'),out=[],deep=src.value==='radical';
 for(const c of sortRows(group(rs,['city']))){out.push(row(c,'city','parent'));
  if(open.has(c.key)){
   if(deep){for(const a of sortRows(group(rs.filter(r=>r.city===c.key),['city','area'])))
    {out.push(row(a,'area','child'));if(open.has(a.key)){for(const p of sortRows(group(rs.filter(r=>r.city+' / '+r.area===a.key),['city','area','name'])))
      out.push(row(p,'point','leaf'))}}}
   else{for(const p of sortRows(group(rs.filter(r=>r.city===c.key),['city','name']))) out.push(row(p,'point','child'))}}}
 body.innerHTML=out.join('');
 [...body.querySelectorAll('tr.parent,tr.child')].forEach(tr=>tr.onclick=()=>{const k=tr.dataset.key;if(!k)return;open.has(k)?open.delete(k):open.add(k);draw()});
 if(src.value==='radical'){
  const hs={};for(const h of D.hours){if(city.value&&h.city!==city.value)continue;hs[h.hour]=hs[h.hour]||[0,0];hs[h.hour][0]+=h.booked;hs[h.hour][1]+=h.capacity}
  const mx=Math.max(1,...Object.values(hs).map(v=>v[0]));
  document.getElementById('hrs').innerHTML=[...Array(24).keys()].map(h=>`<div style="height:${100*(hs[h]?hs[h][0]:0)/mx}%" title="${h}:00 — ${hs[h]?hs[h][0]:0} bags"></div>`).join('');
  document.getElementById('hlab').innerHTML=[...Array(24).keys()].map(h=>`<div>${h%3?'':h}</div>`).join('');}}
function row(r,kind,cls){return `<tr class="${cls}" data-key="${kind==='point'?'':r.key}"><td>${r.label}</td><td>${kind}</td><td>${r.points}</td>
 <td>${r.capacity}</td><td>${r.peak}</td><td><div class="bar"><i style="width:${Math.min(100,r.fill).toFixed(0)}%"></i></div></td>
 <td>${r.bags}</td><td>${r.deposits.toFixed(1)}</td></tr>`}
document.querySelectorAll('#tbl th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;dir=(k===sortKey)?-dir:-1;sortKey=k;draw()});
[day,city,q].forEach(e=>e.oninput=draw);src.onchange=refill;refill();
</script></body></html>"""


if __name__ == "__main__":
    build()
