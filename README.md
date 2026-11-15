# Kit Ufficio Agenti

Un piccolo "ufficio" di agenti AI che puoi far girare su un Raspberry Pi o su un PC:

- una **portineria Telegram** (facoltativa): scrivi al bot e il Direttore smista la richiesta all'agente giusto;
- **ordini di lavoro firmati con HMAC**: un ordine nasce in bozza, tu lo approvi, e solo allora riceve una firma verificabile;
- un **edificio 3D** nel browser (Three.js): un piano per reparto, un omino per agente.

Solo libreria standard di Python: nessun `pip install`.

> **Cosa NON è.** Progetto personale, pubblicato così com'è. Nessuna assistenza garantita, nessuna garanzia di sicurezza o di risultato, nessuna promessa di compatibilità. Leggi il codice prima di usarlo con chiavi vere.

## Stato di verifica

| Cosa | Stato |
|---|---|
| Test automatici (`python -m unittest discover -s tests`) | eseguiti su Windows 11 con Python 3.11 |
| Installazione da zero seguendo questo README | misurata dall'autore prima della pubblicazione; il risultato sta nelle note di rilascio |
| Raspberry Pi con systemd (`deploy/ufficio.service`) | **non verificato**: scritto leggendo il codice |
| Telegram reale | **non verificato** in questa versione |
| Agenti con motore Claude o Gemini | **non verificato**: servono le tue chiavi |

## Requisiti

- Python **3.11 o più recente** (usa `tomllib`). Controlla: `python --version` (Linux/Pi: `python3 --version`).
- Un browser per l'edificio 3D.
- Facoltativi: un bot Telegram (token da @BotFather), una chiave Gemini, la CLI di Claude.

## Installazione

I comandi sono scritti per Linux/Raspberry Pi (`python3`, `curl`) e, dove cambiano, per PowerShell su Windows.
**Su Windows usa `python` al posto di `python3` e `curl.exe` al posto di `curl`** in tutti i comandi (in PowerShell `curl` è un alias che non accetta `-s`).
Le cartelle sono quelle di default: tutto sta in `~/ufficio` (Windows: `$HOME\ufficio`), con il codice in `~/ufficio/app`.

1. **Scarica il kit** in `~/ufficio/app`:
   ```bash
   git clone <URL_DEL_REPOSITORY> ~/ufficio/app
   ```
   PowerShell: `git clone <URL_DEL_REPOSITORY> "$HOME\ufficio\app"`.
   Senza git: scarica lo zip del repository e scompattalo in quella cartella.

2. **Crea il file di configurazione** a partire dal modello:
   ```bash
   cp ~/ufficio/app/deploy/env.esempio ~/ufficio/.env
   ```
   PowerShell: `Copy-Item "$HOME\ufficio\app\deploy\env.esempio" "$HOME\ufficio\.env"`.

3. **Genera la chiave HMAC.** Questo comando la scrive nel `.env` **senza mostrarla** (così non resta nello storico del terminale):
   ```bash
   cd ~/ufficio/app && python3 -m portineria.chiave
   ```
   PowerShell: `cd "$HOME\ufficio\app"; python -m portineria.chiave`.
   Non incollare mai la chiave in chat, issue o log. Per cambiarla dopo: `python3 -m portineria.chiave --forza`
   (gli ordini già firmati non saranno più verificabili).

4. **Lancia i test** (devono passare tutti):
   ```bash
   python3 -m unittest discover -s tests
   ```

5. **Avvia la portineria**:
   ```bash
   python3 -m portineria
   ```
   Senza `TELEGRAM_TOKEN` nel `.env` Telegram resta spento e il log lo dice; l'edificio parte lo stesso su
   `http://127.0.0.1:8090` (valori `WEB_HOST` e `WEB_PORT` del `.env`).

6. **Verifica.** Con la portineria in esecuzione, da un altro terminale:
   ```bash
   curl -s http://127.0.0.1:8090/api/stato
   curl -s http://127.0.0.1:8090/api/edificio
   ```
   Entrambi devono rispondere con JSON. Apri `http://127.0.0.1:8090` nel browser: vedi 3 piani (Direzione, Sviluppo, Contenuti) e 5 agenti.

## Gli ordini firmati

Un ordine è un file di testo in `~/ufficio/vault/03 - Ordini di lavoro/` (es. `L-001.md`).

1. **Bozza**: su Telegram con `/ordine`, oppure senza Telegram con questo comando (dalla cartella `~/ufficio/app`, con la portineria avviata):
   ```bash
   python3 -c "import urllib.request, json; r = urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8090/api/ordine', data=json.dumps({'agente': 'redattore', 'testo': 'Scrivi tre righe di prova', 'titolo': 'Prova'}).encode(), headers={'Content-Type': 'application/json'})); print(r.status, r.read().decode())"
   ```
   Risponde `200 {"ok": true, "id": "L-001", "stato": "piano"}`: il primo ordine è `L-001`, i successivi `L-002`, ecc.
2. **Approvazione**: su Telegram `/approva L-001`; senza Telegram, sempre dalla cartella `~/ufficio/app`:
   ```bash
   python3 -c "from pathlib import Path; from portineria.config import carica_config; from portineria.ordini import approva_ordine, verifica_firma; c = carica_config(Path.home()/'ufficio'/'.env'); o = approva_ordine(c.vault, 'L-001', c.ordini_hmac_key); print(o['stato'], verifica_firma(o, c.ordini_hmac_key))"
   ```
   Stampa `approvato True` se la firma è valida. Non stampa la chiave.
3. **Firma**: HMAC-SHA256, con la tua `ORDINI_HMAC_KEY`, sul codice dell'ordine, la destinazione e il testo del piano. Chi esegue l'ordine ricalcola la firma e lo rifiuta se qualcosa è cambiato dopo l'approvazione.
4. Un ordine non approvato entro 24 ore scade e viene annullato.

Nel kit v1 non c'è nessun esecutore: gli ordini si creano, si approvano e si firmano, ma non partono lavori automatici.
La firma non sostituisce una revisione di sicurezza del tuo ambiente.

## Sicurezza: leggi prima di esporre l'edificio

L'edificio **non ha autenticazione**. Chiunque raggiunga la porta può scrivere agli agenti (`/api/chiedi`), cambiarne il motore
e creare ordini, e può leggere le chat. Gli agenti con motore Claude possono eseguire comandi di shell sulla macchina
che ospita il kit: una richiesta ostile o una prompt injection può quindi far eseguire comandi. Il default `WEB_HOST=127.0.0.1`
ascolta solo in locale. Non metterlo su Internet; su una LAN usalo solo se ti fidi di tutti i dispositivi collegati.

## Cosa contiene

- `portineria/`: bot, instradamento, ordini, server dell'edificio.
- `edificio/`: pagina 3D (include `three.module.min.js`, vedi `THIRD_PARTY.md`).
- `generatore/`: rigenera `organigramma.json` da `gerarchia.toml` (`python3 -m generatore`).
- `prompts/`: 5 prompt di esempio scritti per il kit. Aggiungi i tuoi agenti in `gerarchia.toml` e in `prompts/_<id>.md`.
- `deploy/`: modello `env.esempio` e unità systemd per il Pi (vedi `docs/installazione-pi.md`).
- `tests/`: suite automatica, solo libreria standard.

## Cosa NON c'è nella v1

Niente prompt di terzi o già pronti, niente moduli personali (segretaria, coach, posta, calendario, palestra),
niente esecutore remoto sul PC, niente Wake-on-LAN, niente pubblicazione su GitHub o Vercel, niente archivisti
automatici, niente riunioni cross-piano.

Restano nel codice alcuni ganci facoltativi, spenti di default: `ESECUTORE_URL` (worker remoto firmato che nel kit non c'è),
`OMNIROUTE_*` (router di modelli), la sorgente `antigravity` della telemetria esterna, i link `obsidian://` e il
generatore, che legge le definizioni di agenti installate nella tua cartella `~/.claude` se presente.
Se non li usi, ignorali.

## Controllo prima di condividere la tua copia

`tests/test_scansione.py` cerca chiavi, token, indirizzi privati e nomi personali in una cartella:
```bash
KIT_DIR=<CARTELLA_DA_CONTROLLARE> python3 -m unittest tests.test_scansione -v
```
PowerShell: `$env:KIT_DIR="<CARTELLA_DA_CONTROLLARE>"; python -m unittest tests.test_scansione -v`.
Il rapporto elenca file, riga e tipo, mai i valori trovati. Non sostituisce una revisione umana.

## Licenza

MIT: vedi il file `LICENSE`. Il codice di terzi incluso (three.js) ha la sua licenza, indicata in `THIRD_PARTY.md`.
