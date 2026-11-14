"""Pianta dell'edificio 3D: posizioni degli omini per piano, stanze del piano terra, comando Claude Code."""
import urllib.parse
from pathlib import Path

from portineria.organigramma import Organigramma

PASSO = 2.0
COLONNE = 14


def _omino(a: dict, x: float, z: float, scala: float) -> dict:
    # niente motore: cambia con imposta_motore, la pagina lo legge da /api/agente
    return {"id": a["id"], "nome": a["nome"], "ruolo": a["ruolo"], "team": a.get("team"),
            "x": float(x), "z": float(z), "scala": scala}


def _piano(org: Organigramma, p: dict) -> dict:
    agenti = [a for a in org.tutti() if a["piano"] == p["id"]]
    omini, usati = [], set()

    def metti(a, x, z, scala):
        omini.append(_omino(a, x, z, scala))
        usati.add(a["id"])

    capo = next((a for a in agenti if a["id"] == p.get("capo")), None)
    direttore = next((a for a in agenti if a["ruolo"] == "direttore"), None)
    if direttore:
        metti(direttore, -PASSO * 2, -PASSO * 2, 1.8)
    if capo and capo["id"] not in usati:
        metti(capo, -PASSO * 2, 0 if direttore else -PASSO * 2, 1.5)

    # la segretaria personale siede nell'ufficio vetrato accanto al Direttore (x < 0: fuori dalla griglia)
    segretaria = next((a for a in agenti if a["id"] == "segretaria"), None)
    if segretaria and direttore and segretaria["id"] not in usati:
        metti(segretaria, -PASSO * 2, 0, 1.2)

    # gruppi: un gruppo per team (comanager per primo), poi gli agenti senza team
    gruppi = []
    for team in p.get("team", []):
        membri = [a for a in agenti if a.get("team") == team and a["id"] not in usati]
        membri.sort(key=lambda a: a["ruolo"] != "comanager")
        gruppi.append(membri)
    gruppi.append([a for a in agenti if a["id"] not in usati and a.get("team") not in p.get("team", [])])

    riga = 0
    for membri in gruppi:
        if not membri:
            continue
        if riga:
            riga += 1  # riga vuota fra i gruppi
        for i, a in enumerate(membri):
            scala = 1.2 if a["ruolo"] == "comanager" else 1.0
            metti(a, (i % COLONNE) * PASSO, (riga + i // COLONNE) * PASSO, scala)
        riga += (len(membri) + COLONNE - 1) // COLONNE
    return {"id": p["id"], "numero": p["numero"], "nome": p["nome"], "emoji": p["emoji"], "colore": p["colore"],
            "larghezza": COLONNE * PASSO, "profondita": max(riga, 1) * PASSO, "agenti": omini}


def pianta(org: Organigramma) -> dict:
    ids = {p["id"] for p in org.piani()}
    orfani = [a for a in org.tutti() if a["piano"] not in ids]
    if orfani:
        raise ValueError("agenti su piani inesistenti: " + ", ".join(f"{a['id']} (piano {a['piano']!r})" for a in orfani))
    return {"piani": [_piano(org, p) for p in org.piani()]}


def _url_nota(vault: Path, f: Path) -> str:
    rel = f.relative_to(vault).with_suffix("").as_posix()
    return "obsidian://open?vault=Ufficio&file=" + urllib.parse.quote(rel)


def stanze(vault: Path, host: str) -> list[dict]:
    vault = Path(vault)
    commesse = sorted((vault / "01 - Commesse").glob("*.md"), key=lambda f: f.name)
    recenti = sorted(
        (f for f in vault.rglob("*.md")
         if not any(part.startswith(".") for part in f.relative_to(vault).parts)
         and f.relative_to(vault).parts[0] != "99 - Fonti"),
        key=lambda f: f.stat().st_mtime, reverse=True)[:10]
    return [
        {"id": "commesse", "nome": "Sala Commesse",
         "link": [{"testo": f.stem, "url": _url_nota(vault, f)} for f in commesse if not f.name.startswith(".")]},
        {"id": "palestra", "nome": "Palestra", "link": [{"testo": "ARES", "url": f"http://{host}:3100"}]},
        {"id": "finanza", "nome": "Finanza", "link": [{"testo": "Spese", "url": f"http://{host}:8080"}]},
        {"id": "archivio", "nome": "Archivio", "link": [{"testo": f.stem, "url": _url_nota(vault, f)} for f in recenti]},
    ]


def comando_claude(agente: dict) -> str | None:
    fonte = agente["fonte"]
    if fonte == "agency":
        return f'claude --agent "{agente["nome"]}"'
    if fonte == "ecc":
        return "claude --agent ecc:" + agente["id"].removeprefix("ecc-")
    if fonte == "ai-specialists":
        return f"/ai-specialists:{agente['id']}"
    if fonte == "founder":
        return f"/{agente['id']}"
    return None
