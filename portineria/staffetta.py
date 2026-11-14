"""Staffetta autore -> revisore: scelta di un revisore coerente e prompt di controllo rapido."""
from portineria.organigramma import Organigramma

_PAROLE_SICUREZZA = (
    "sicurezza", "security", "auth", "token", "password", "permess",
    "hmac", "chiav", "segret", "vulnerab", "login", "bypass",
)
REVISORI_ENGINEERING = (
    "testing-reality-checker",
    "engineering-code-reviewer",
    "ecc-code-reviewer",
    "security-architect",
)
REVISORI_SICUREZZA = (
    "security-architect",
    "security-cloud-security-architect",
    "testing-reality-checker",
    "engineering-code-reviewer",
    "ecc-code-reviewer",
)


def scegli_revisore(
    org: Organigramma,
    autore: dict,
    testo: str = "",
    revisore_esplicito: str | None = None,
) -> dict | None:
    """Sceglie un revisore coerente e diverso dall'autore per la staffetta QA."""
    if revisore_esplicito:
        cand = org.get(revisore_esplicito.strip())
        if cand is not None and cand["id"] != autore["id"]:
            return cand

    if autore.get("piano") == "engineering":
        basso = (testo or "").casefold()
        ordine = REVISORI_SICUREZZA if any(p in basso for p in _PAROLE_SICUREZZA) else REVISORI_ENGINEERING
        for cid in ordine:
            cand = org.get(cid)
            if cand is not None and cand["id"] != autore["id"]:
                return cand

    piano = org.piano(autore.get("piano", ""))
    if piano:
        capo_id = piano.get("capo")
        if capo_id and capo_id != autore["id"]:
            capo = org.get(capo_id)
            if capo is not None:
                return capo

    if autore["id"] != "direttore":
        return org.get("direttore")
    return None


def prompt_revisione(autore: dict, piano_nome: str, richiesta: str, bozza: str) -> str:
    return (
        "Controllo rapido QA (staffetta autore -> revisore).\n"
        f"Richiesta originale dell'operatore:\n{richiesta}\n\n"
        f"Bozza prodotta da {autore['nome']} ({piano_nome}):\n{bozza}\n\n"
        "Verifica accuratezza, completezza ed eventuali rischi o errori. "
        "Restituisci direttamente la versione finale migliorata e pronta per l'operatore in italiano."
    )
