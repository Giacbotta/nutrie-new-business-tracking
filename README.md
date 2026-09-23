# Nutrie new business tracking

Scripts that run on GitHub Actions and track public data about new business opportunities for Nutrie S.r.l. (self-service luggage storage in Venice and Pisa). Set up in September 2026. They read only public data: public Playtomic availability, municipal notice boards, public property listings.

| Script | What it tracks | Board item | Runs |
|---|---|---|---|
| `playtomic_occupancy.py` | Occupancy of the padel courts around Mogliano Veneto, from public Playtomic pages | S-16 | every 2 hours, until 09/10/2026 |
| `municipal_tenders.py` | New tenders on sports facilities and municipal land in 10 municipalities of the area | E-90 | Monday |
| `laundromats_for_sale.py` | Laundromats for sale in the provinces of Padova, Treviso and Venezia, on Subito, immobiliare.it and Trovit | E-91 | Monday, with retries until every portal answers |
| `radical_occupancy.py` | How full the Radical Storage luggage points are in 86 Italian province capitals, and how many deposits they take per day | E-93 | every hour |
| `bounce_occupancy.py` | The same for Bounce: 3.381 points in 92 Italian cities, with capacity and reservation counts | E-93 | every hour |
| `stow_occupancy.py` | Free lockers per shop and size at Stow Your Bags, the one real locker operator among the four, read from its booking form | E-93 | every hour |
| `competitors_census.py` | Weekly census of Stasher in Italy: points, declared capacity, prices | E-93 | Monday |
| `dashboard.py` | Builds `docs/index.html` from all four sources | E-93 | after every hourly run |

"Board item" refers to Nutrie's internal task board.

## Data

Everything is written to `data/` and committed by each run:

- `playtomic-occupancy.csv`, `playtomic-lead.csv`: raw Playtomic readings; `playtomic-summary.csv` is the summary.
- `tenders-seen.csv`: every tender seen; `tenders-new.md`: what each run found.
- `laundromats.csv`: every laundromat listing seen, active or gone; `laundromats-new.md`: what changed in each run.
- `radical-cities.csv`, `radical-points.csv`: the 86 Italian cities Radical Storage covers and their 1.386 points, with capacity, reviews and price.
- `radical-occupancy.csv`: one row per point per hour, with the bags already booked in that hour.
- `radical-daily.csv`, `radical-daily-area.csv`, `radical-daily-city.csv`: deposits per day, by point, by neighbourhood and by city.

### How the Radical numbers are built

Radical's booking page answers whether N more bags still fit in a time window. The script narrows N
down until it flips, so capacity minus that number is the luggage already booked. The answer is the
**peak** of the window, not a total, so deposits per day are rebuilt from the hourly curve: every
hour the script reads the hour about to start, and the rises of that curve are luggage arriving.

Two things to keep in mind when reading the numbers:

- **It is a floor.** A bag dropped off and picked up inside the same hour is invisible, and bags are
  turned into deposits with a fixed 2 bags per booking (`BAGS_PER_BOOKING`), an assumption still to
  be calibrated against Nutrie's own Radical bookings.
- **It is Radical only.** Walk-in customers, other marketplaces and storages outside Radical are not
  in these numbers. It measures online demand, not the whole demand of a city.

`radicalstorage.com/robots.txt` disallows the `/v3/` and `/v4/` paths this script reads. It is kept
deliberately light for that reason: one hour-slot per point per run, each search starting from the
previous reading, four threads with a pause before each request, and a hard ceiling per run.

### Bounce

Bounce answers a whole city in one GraphQL request (`stores(citySlug:)` on graphql.usebounce.com),
so a national round costs about 120 requests against Radical's 2.800, and it covers 3.381 points in
92 cities against Radical's 1.386 in 86. What it gives per point is different, though: a capacity
figure the site labels "Current availability", and a `reservationCount`. **What window that counter
covers is not settled**: it is far too small to be a lifetime total, and Bounce's own site never
uses the field. Until a few days of history say how it behaves, read the level and treat its change
as raw movement, not as deposits. `bounce.com/robots.txt` disallows only `/packages/location`,
`*.md` and `llms.txt`, none of which this script touches.

### Stow Your Bags

The only one of the four that is a real locker operator rather than a marketplace, so the closest
comparison to Nutrie. Its public pages carry no availability, but its booking form does:
`stow_occupancy.py` walks the form the way a visitor does (pick the day, pick the time, go to step
two) and reads `lockersAvailability`, which gives the lockers still bookable per size. 33 Italian
shops, four requests each, about 130 per round.

Capacity is not published, so the yardstick is the most lockers ever seen free for that shop and
size: the fill figure only gets more accurate as the series grows.

`www.stowyourbags.com` allows robots, but `customer.stowyourbags.com`, where the booking form
lives, disallows them. Giacomo asked for the reading anyway on 23/09/2026, knowing that, so it is
kept to one slot per shop every two hours, one shop at a time.

### Stasher

Small in Italy (5 points in Venice, 1 in Ferrara) and it publishes no availability: the capacity on
its city pages does not move with the dates asked. It gets a weekly census of points, declared
capacity and prices, nothing more.

### Neighbourhoods

Bounce and Radical both publish a position for every point. Radical names its own zone in the point
URL; Bounce does not, so `bounce_occupancy.py areas` gives each point a neighbourhood once and
caches it in `data/bounce-areas.csv`: first from a Radical point within 500 m, then from
OpenStreetMap's Nominatim at one request per second for the rest. Only new points are looked up.

### Dashboard

`docs/index.html`, rebuilt after every run: city → neighbourhood → single point, with the bags-by-hour
profile and the estimated deposits. Open the file from the repository, or enable GitHub Pages on
`docs/` to have it at a URL.

The Google Sheet «Nutrie new business tracking» reads the CSV files with `IMPORTDATA`, one tab per file.

Listing titles, tender titles and the keyword lists stay in Italian: they come from Italian sources.

## Notifications

When the laundromat check finds new listings, listings that are gone or back online, it opens an issue that mentions the repository owner. GitHub sends it by email. It also opens one when a portal has been unreachable for every attempt of the week.

## When it runs

Times are in `.github/workflows/tracking.yml`, in UTC:

- `playtomic`: every 2 hours from 07:00 to 23:00 Italian summer time.
- `playtomic-lead`: every day at 21:00 Italian summer time.
- `tenders`: every Monday at 09:00 Italian summer time.
- `laundromats`: every Monday at 09:31 Italian summer time, then again at 13:31 and 19:31 the same day, Tuesday at 09:31 and 19:31, and Wednesday at 09:31. immobiliare.it sometimes blocks automated reads for a whole day, so the week is a cycle of six attempts: the first one that reads every portal writes `data/laundromats-state.json` and all the later attempts of that week stop immediately. If all six are blocked, the last one sends an email naming the portal that was never read. `laundromats-now` forces a round, ignoring the state.
- `occupancy` (Radical, Bounce and Stow Your Bags, then the dashboard): every hour, day and night. At night Radical and most Stow shops are shut and report nothing, while Bounce keeps counting the luggage left overnight.
- `radical-census`: every Monday at 05:06 Italian summer time.

From 25/10 (winter time) the same runs fall one hour earlier. Any command can be started by hand from **Actions > tracking > Run workflow**, typing its name in the "command" field.

## Cost

Zero. Public repository: GitHub Actions minutes are free. No tokens, no paid services, no personal data in the requests. `curl_cffi`, used for immobiliare.it and Trovit, is free.

## To stop it

Actions > tracking > "..." > **Disable workflow**.
