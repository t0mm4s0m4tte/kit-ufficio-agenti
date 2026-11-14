"""Raccoglie gli agenti installati sul PC dalle quattro fonti."""
from pathlib import Path

from generatore.frontmatter import leggi_frontmatter


def _versione(nome: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(x) for x in nome.split("."))
    except ValueError:
        return None


def _cartella_ecc(home: Path) -> Path | None:
    base = home / ".claude/plugins/cache/ecc/ecc"
    if not base.is_dir():
        return None
    versioni = [(v, d) for d in base.iterdir() if d.is_dir() and (v := _versione(d.name))]
    return max(versioni)[1] if versioni else None


def _title(id_: str) -> str:
    return id_.replace("-", " ").title()


def _nome_specialist(descrizione: str, id_: str) -> str:
    prima, virgola, _ = descrizione.partition(",")
    return prima.strip() if virgola and len(prima.strip()) <= 20 else _title(id_)


def raccogli(home: Path) -> tuple[list[dict], list[str]]:
    ecc = _cartella_ecc(home)
    candidati = [("agency", p, p.stem) for p in sorted((home / ".claude/agents").glob("*.md"))]
    candidati += [("ai-specialists", p, p.parent.name) for p in sorted(home.glob(".claude/plugins/synced/*/ai-specialists/skills/*/SKILL.md"))]
    candidati += [("founder", p, p.parent.name) for p in sorted(home.glob(".claude/skills/founder-*/SKILL.md"))]
    if ecc:
        candidati += [("ecc", p, "ecc-" + p.stem) for p in sorted((ecc / "agents").glob("*.md"))]

    agenti, avvisi, visti = [], [], set()
    for fonte, path, id_ in candidati:
        testo = path.read_text(encoding="utf-8", errors="replace")
        campi, _ = leggi_frontmatter(testo)
        descrizione = campi.get("description", "")
        if fonte in ("agency", "ecc"):
            nome = campi.get("name", "")
        elif fonte == "ai-specialists":
            nome = _nome_specialist(descrizione, id_)
        else:
            nome = _title(id_)
        if not descrizione or not nome:
            avvisi.append(f"saltato {path}: frontmatter incompleto")
            continue
        if id_ in visti:
            avvisi.append(f"duplicato {id_}: {path}")
            continue
        visti.add(id_)
        agenti.append({"id": id_, "nome": nome, "descrizione": descrizione, "fonte": fonte, "prompt": testo})
    return agenti, avvisi
