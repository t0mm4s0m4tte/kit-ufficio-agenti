"""Ordini di lavoro firmati (spec §5.6 e §9): creazione in 03 - Ordini di lavoro, stati, scadenza e firma HMAC."""
from datetime import datetime, timedelta
import hashlib
import hmac
from pathlib import Path
import re
import threading

from portineria.vault import frontmatter, pulisci_nome

CARTELLA_ORDINI = "03 - Ordini di lavoro"
STATI = ("piano", "approvato", "in-corso", "fatto", "pubblicato", "errore", "annullato")
STATI_APERTI = ("piano", "approvato", "in-corso")
TRANSIZIONI: dict[str, set[str]] = {
    "piano": {"approvato", "annullato"},
    "approvato": {"in-corso", "annullato", "errore"},
    "in-corso": {"fatto", "errore", "annullato"},
    "fatto": {"pubblicato"},
    "pubblicato": set(),
    "errore": set(),
    "annullato": set(),
}
SCADENZA_ORE = 24
_ID_RE = re.compile(r"^L-(\d{3,})$", re.IGNORECASE)
_LOCK = threading.Lock()


class StatoNonValido(ValueError):
    pass


class FirmaNonValida(ValueError):
    pass


def calcola_firma(chiave: str, ordine_id: str, destinazione: str, piano: str) -> str:
    if not chiave or not chiave.strip():
        raise ValueError("ORDINI_HMAC_KEY mancante")
    messaggio = f"{ordine_id.strip().upper()}\n{destinazione.strip()}\n{piano.strip()}".encode("utf-8")
    return hmac.new(chiave.encode("utf-8"), messaggio, hashlib.sha256).hexdigest()


def verifica_firma(ordine: dict, chiave: str) -> bool:
    firma = str(ordine.get("firma") or "").strip()
    if not chiave or not chiave.strip() or not firma:
        return False
    attesa = calcola_firma(
        chiave,
        str(ordine.get("id") or ""),
        str(ordine.get("destinazione") or ""),
        str(ordine.get("piano") or ""),
    )
    return hmac.compare_digest(firma, attesa)


def _leggi_frontmatter(testo: str) -> tuple[dict[str, str], str]:
    if not testo.startswith("---\n"):
        return {}, testo
    fine = testo.find("\n---\n", 4)
    if fine == -1:
        return {}, testo
    campi: dict[str, str] = {}
    for riga in testo[4:fine].splitlines():
        if ":" not in riga:
            continue
        k, _, v = riga.partition(":")
        val = v.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        campi[k.strip()] = val
    return campi, testo[fine + 5:]


def _nota_commessa(vault: Path, commessa: str) -> Path | None:
    if not commessa or not commessa.strip():
        return None
    cartella = Path(vault) / "01 - Commesse"
    pulito = pulisci_nome(commessa)
    if cartella.is_dir():
        for p in cartella.glob("*.md"):
            if p.stem.casefold() == pulito.casefold() and p.is_file():
                return p
    return None


def _destinazione_da_commessa(vault: Path, commessa: str) -> tuple[str, str]:
    if not commessa or not commessa.strip():
        return "cantiere", "cantiere"
    pulito = pulisci_nome(commessa)
    nota = _nota_commessa(vault, commessa)
    if nota is None:
        return "cantiere", pulito
    fm, _ = _leggi_frontmatter(nota.read_text(encoding="utf-8", errors="replace"))
    esecuzione = fm.get("esecuzione", "cantiere").strip().lower() or "cantiere"
    if esecuzione not in ("cantiere", "pc"):
        esecuzione = "cantiere"
    dest = (fm.get("cartella_pc") if esecuzione == "pc" else fm.get("repo")) or fm.get("destinazione") or pulito
    return esecuzione, dest.strip()


def url_produzione_commessa(vault: Path, commessa: str) -> str:
    """Legge l'eventuale URL di produzione dal frontmatter della nota commessa."""
    nota = _nota_commessa(vault, commessa)
    if nota is None:
        return ""
    fm, _ = _leggi_frontmatter(nota.read_text(encoding="utf-8", errors="replace"))
    for chiave in ("url_produzione", "produzione", "url", "dominio"):
        val = (fm.get(chiave) or "").strip()
        if not val:
            continue
        if val.startswith(("http://", "https://")):
            return val
        if "." in val and " " not in val:
            return f"https://{val}"
    return ""


def _prossimo_id(cartella: Path) -> str:
    massimo = 0
    if cartella.is_dir():
        for p in cartella.glob("L-*.md"):
            m = _ID_RE.match(p.stem)
            if m:
                massimo = max(massimo, int(m.group(1)))
    return f"L-{massimo + 1:03d}"


def _salva_ordine(vault: Path, ordine: dict) -> Path:
    cartella = Path(vault) / CARTELLA_ORDINI
    cartella.mkdir(parents=True, exist_ok=True)
    fm = frontmatter({
        "tipo": "ordine",
        "id": ordine["id"],
        "titolo": ordine["titolo"],
        "agente": ordine["agente"],
        "commessa": ordine.get("commessa", ""),
        "esecuzione": ordine["esecuzione"],
        "destinazione": ordine["destinazione"],
        "stato": ordine["stato"],
        "creato": ordine["creato"],
        "firma": ordine.get("firma", ""),
    })
    corpo = f"{fm}# {ordine['id']} — {ordine['titolo']}\n\n## Piano\n\n{ordine['piano'].strip()}\n"
    esito = (ordine.get("esito") or "").strip()
    if esito:
        corpo += f"\n## Esito\n\n{esito}\n"
    percorso = cartella / f"{ordine['id']}.md"
    percorso.write_text(corpo, encoding="utf-8", newline="\n")
    return percorso


def _parse_ordine(percorso: Path) -> dict:
    testo = percorso.read_text(encoding="utf-8", errors="replace")
    fm, resto = _leggi_frontmatter(testo)
    piano, esito = "", ""
    if "\n## Piano\n" in resto:
        dopo_piano = resto.split("\n## Piano\n", 1)[1]
        if "\n## Esito\n" in dopo_piano:
            piano, esito = dopo_piano.split("\n## Esito\n", 1)
        else:
            piano = dopo_piano
    else:
        piano = resto
    return {
        "id": (fm.get("id") or percorso.stem).upper(),
        "titolo": fm.get("titolo") or percorso.stem,
        "agente": fm.get("agente") or "",
        "commessa": fm.get("commessa") or "",
        "esecuzione": fm.get("esecuzione") or "cantiere",
        "destinazione": fm.get("destinazione") or "cantiere",
        "stato": fm.get("stato") or "piano",
        "creato": fm.get("creato") or "",
        "firma": fm.get("firma") or "",
        "piano": piano.strip(),
        "esito": esito.strip(),
    }


def crea_ordine(
    vault: Path,
    agente: str,
    piano: str,
    titolo: str = "",
    commessa: str = "",
    esecuzione: str = "",
    destinazione: str = "",
    adesso: datetime | None = None,
) -> dict:
    if not isinstance(piano, str) or not piano.strip():
        raise ValueError("il testo del piano non può essere vuoto")
    if not isinstance(agente, str) or not agente.strip():
        raise ValueError("agente mancante")
    esec_comm, dest_comm = _destinazione_da_commessa(Path(vault), commessa)
    esec = (esecuzione.strip().lower() if esecuzione else esec_comm) or "cantiere"
    if esec not in ("cantiere", "pc"):
        raise ValueError(f"esecuzione non valida: {esec}")
    dest = (destinazione.strip() if destinazione else dest_comm) or "cantiere"
    titolo_pulito = (titolo.strip() if titolo else "") or piano.strip().splitlines()[0][:60]
    ora = (adesso or datetime.now()).isoformat(timespec="seconds")

    with _LOCK:
        cartella = Path(vault) / CARTELLA_ORDINI
        ordine_id = _prossimo_id(cartella)
        ordine = {
            "id": ordine_id,
            "titolo": titolo_pulito,
            "agente": agente.strip(),
            "commessa": commessa.strip() if commessa else "",
            "esecuzione": esec,
            "destinazione": dest,
            "stato": "piano",
            "creato": ora,
            "firma": "",
            "piano": piano.strip(),
            "esito": "",
        }
        _salva_ordine(Path(vault), ordine)
    return ordine


def carica_ordine(vault: Path, ordine_id: str) -> dict:
    codice = (ordine_id or "").strip().upper()
    if not _ID_RE.match(codice):
        raise KeyError(ordine_id)
    percorso = Path(vault) / CARTELLA_ORDINI / f"{codice}.md"
    if not percorso.is_file():
        raise KeyError(codice)
    return _parse_ordine(percorso)


def elenca_ordini(vault: Path, solo_aperti: bool = False) -> list[dict]:
    cartella = Path(vault) / CARTELLA_ORDINI
    if not cartella.is_dir():
        return []
    ordini = []
    for p in sorted(cartella.glob("L-*.md")):
        if _ID_RE.match(p.stem):
            o = _parse_ordine(p)
            if not solo_aperti or o["stato"] in STATI_APERTI:
                ordini.append(o)
    return ordini


def approva_ordine(vault: Path, ordine_id: str, chiave: str) -> dict:
    with _LOCK:
        ordine = carica_ordine(vault, ordine_id)
        if "approvato" not in TRANSIZIONI.get(ordine["stato"], set()):
            raise StatoNonValido(f"transizione non ammessa: {ordine['stato']} -> approvato")
        ordine["firma"] = calcola_firma(chiave, ordine["id"], ordine["destinazione"], ordine["piano"])
        ordine["stato"] = "approvato"
        _salva_ordine(Path(vault), ordine)
        return ordine


def avvia_ordine(vault: Path, ordine_id: str, chiave: str) -> dict:
    with _LOCK:
        ordine = carica_ordine(vault, ordine_id)
        if "in-corso" not in TRANSIZIONI.get(ordine["stato"], set()):
            raise StatoNonValido(f"transizione non ammessa: {ordine['stato']} -> in-corso")
        if not verifica_firma(ordine, chiave):
            ordine["stato"] = "errore"
            ordine["esito"] = "firma non valida"
            _salva_ordine(Path(vault), ordine)
            raise FirmaNonValida("firma non valida")
        ordine["stato"] = "in-corso"
        _salva_ordine(Path(vault), ordine)
        return ordine


def pubblica_ordine(vault: Path, ordine_id: str, nota: str = "") -> dict:
    with _LOCK:
        ordine = carica_ordine(vault, ordine_id)
        if "pubblicato" not in TRANSIZIONI.get(ordine["stato"], set()):
            raise StatoNonValido(f"transizione non ammessa: {ordine['stato']} -> pubblicato")
        ordine["stato"] = "pubblicato"
        if nota and nota.strip():
            vecchio = (ordine.get("esito") or "").strip()
            ordine["esito"] = f"{vecchio}\n{nota.strip()}".strip() if vecchio else nota.strip()
        _salva_ordine(Path(vault), ordine)
        return ordine


def cambia_stato(vault: Path, ordine_id: str, nuovo_stato: str, esito: str = "", chiave: str = "") -> dict:
    if nuovo_stato == "approvato":
        return approva_ordine(vault, ordine_id, chiave)
    if nuovo_stato == "in-corso" and chiave:
        return avvia_ordine(vault, ordine_id, chiave)
    if nuovo_stato == "pubblicato":
        return pubblica_ordine(vault, ordine_id, nota=esito)
    with _LOCK:
        ordine = carica_ordine(vault, ordine_id)
        ammesse = TRANSIZIONI.get(ordine["stato"], set())
        if nuovo_stato not in ammesse:
            raise StatoNonValido(f"transizione non ammessa: {ordine['stato']} -> {nuovo_stato}")
        ordine["stato"] = nuovo_stato
        if esito:
            ordine["esito"] = esito.strip()
        _salva_ordine(Path(vault), ordine)
        return ordine


def annulla_ordine(vault: Path, ordine_id: str, motivo: str = "") -> dict:
    return cambia_stato(vault, ordine_id, "annullato", esito=motivo)


def scadi_ordini(vault: Path, adesso: datetime | None = None) -> list[dict]:
    ora = adesso or datetime.now()
    limite = timedelta(hours=SCADENZA_ORE)
    scaduti = []
    with _LOCK:
        for ordine in elenca_ordini(vault, solo_aperti=True):
            if ordine["stato"] != "piano" or not ordine["creato"]:
                continue
            try:
                creato = datetime.fromisoformat(ordine["creato"])
            except ValueError:
                continue
            if ora - creato >= limite:
                ordine["stato"] = "annullato"
                ordine["esito"] = "scaduto dopo 24 ore senza approvazione"
                _salva_ordine(Path(vault), ordine)
                scaduti.append(ordine)
    return scaduti
