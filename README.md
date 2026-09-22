# Nutrie new business tracking

Scripts that run on GitHub Actions and track public data about new business opportunities for Nutrie S.r.l. (self-service luggage storage in Venice and Pisa). Set up in September 2026. They read only public data: public Playtomic availability, municipal notice boards, public property listings.

| Script | What it tracks | Board item | Runs |
|---|---|---|---|
| `playtomic_occupancy.py` | Occupancy of the padel courts around Mogliano Veneto, from public Playtomic pages | S-16 | every 2 hours, until 09/10/2026 |
| `municipal_tenders.py` | New tenders on sports facilities and municipal land in 10 municipalities of the area | E-90 | Monday |
| `laundromats_for_sale.py` | Laundromats for sale in the provinces of Padova, Treviso and Venezia, on Subito, immobiliare.it and Trovit | E-91 | Monday |

"Board item" refers to Nutrie's internal task board.

## Data

Everything is written to `data/` and committed by each run:

- `playtomic-occupancy.csv`, `playtomic-lead.csv`: raw Playtomic readings; `playtomic-summary.csv` is the summary.
- `tenders-seen.csv`: every tender seen; `tenders-new.md`: what each run found.
- `laundromats.csv`: every laundromat listing seen, active or gone; `laundromats-new.md`: what changed in each run.

The Google Sheet «Nutrie new business tracking» reads the CSV files with `IMPORTDATA`, one tab per file.

Listing titles, tender titles and the keyword lists stay in Italian: they come from Italian sources.

## Notifications

When the laundromat check finds new listings, listings that are gone or back online, it opens an issue that mentions the repository owner. GitHub sends it by email.

## When it runs

Times are in `.github/workflows/tracking.yml`, in UTC:

- `playtomic`: every 2 hours from 07:00 to 23:00 Italian summer time.
- `playtomic-lead`: every day at 21:00 Italian summer time.
- `tenders`: every Monday at 09:00 Italian summer time.
- `laundromats`: every Monday at 09:31 Italian summer time.

From 25/10 (winter time) the same runs fall one hour earlier. Any command can be started by hand from **Actions > tracking > Run workflow**, typing its name in the "command" field.

## Cost

Zero. Public repository: GitHub Actions minutes are free. No tokens, no paid services, no personal data in the requests. `curl_cffi`, used for immobiliare.it and Trovit, is free.

## To stop it

Actions > tracking > "..." > **Disable workflow**.
