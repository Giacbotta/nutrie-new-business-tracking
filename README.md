# Rilevamenti padel di Nutrie

Due script che girano su GitHub Actions invece che sul PC di Giacomo, preparati il 22/09/2026.

- `rilevamento_playtomic.py`: occupazione dei campi da padel della zona di Mogliano Veneto, dalle pagine pubbliche di Playtomic. Bacheca **S-16**, fino al 09/10/2026.
- `bandi_comunali.py`: bandi nuovi su impianti sportivi e aree comunali in 10 comuni della zona. Bacheca **E-90**.

Gli originali e la documentazione stanno nel vault, in `Nutrie Brain/workspace/padel/` e nelle note `output-m3-padel-mappa-e-occupazione-2026` e `output-m3-padel-bandi-comunali-2026`. Queste copie differiscono solo in una riga: i dati si scrivono in `dati/`.

## Quando gira

Orari in `.github/workflows/rilevamenti.yml`, in UTC:

- Playtomic `rileva`: ogni 2 ore dalle 7 alle 23 ora italiana d'estate.
- Playtomic `anticipo`: ogni giorno alle 21 ora italiana d'estate.
- `bandi`: ogni lunedì alle 9 ora italiana d'estate.

Dal 25/10 (ora solare) gli stessi orari cadono un'ora prima.

Ogni esecuzione salva i CSV in `dati/` con un commit. Si lancia anche a mano da **Actions > rilevamenti > Run workflow**.

## Costo

Zero. Repository privato: GitHub Free include 2.000 minuti al mese di Actions, e qui se ne usano circa 400. Nessun token, nessun servizio a pagamento, nessun dato personale nelle richieste.

## Per fermarlo

Actions > rilevamenti > "..." > **Disable workflow**. Il rilevamento Playtomic va fermato dopo il 09/10/2026.
