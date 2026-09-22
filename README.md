# Rilevamenti padel di Nutrie

Due script che girano su GitHub Actions, preparati il 22/09/2026. Leggono solo dati pubblici (disponibilità pubbliche di Playtomic, albi pretori dei comuni).

- `rilevamento_playtomic.py`: occupazione dei campi da padel della zona di Mogliano Veneto, dalle pagine pubbliche di Playtomic. Bacheca **S-16**, fino al 09/10/2026.
- `bandi_comunali.py`: bandi nuovi su impianti sportivi e aree comunali in 10 comuni della zona. Bacheca **E-90**.

I dati si scrivono in `dati/`. `dati/riepilogo.csv` è la sintesi letta dal foglio Google con `IMPORTDATA`.

## Quando gira

Orari in `.github/workflows/rilevamenti.yml`, in UTC:

- Playtomic `rileva`: ogni 2 ore dalle 7 alle 23 ora italiana d'estate.
- Playtomic `anticipo`: ogni giorno alle 21 ora italiana d'estate.
- `bandi`: ogni lunedì alle 9 ora italiana d'estate.

Dal 25/10 (ora solare) gli stessi orari cadono un'ora prima.

Ogni esecuzione salva i CSV in `dati/` con un commit. Si lancia anche a mano da **Actions > rilevamenti > Run workflow**.

## Costo

Zero. Repository pubblico: i minuti di GitHub Actions sono gratuiti. Nessun token, nessun servizio a pagamento, nessun dato personale nelle richieste.

## Per fermarlo

Actions > rilevamenti > "..." > **Disable workflow**. Il rilevamento Playtomic va fermato dopo il 09/10/2026.
