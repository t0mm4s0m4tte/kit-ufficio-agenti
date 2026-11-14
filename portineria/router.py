"""Decide a chi va un messaggio: comando, agente (@nome) o Direttore."""
from dataclasses import dataclass, field

from portineria.organigramma import Organigramma


@dataclass
class Instradamento:
    tipo: str  # comando | agente | ambiguo | sconosciuto | direttore
    agente_id: str | None = None
    testo: str = ""
    candidati: list[str] = field(default_factory=list)
    comando: str | None = None
    argomenti: str = ""


def instrada(testo: str, org: Organigramma) -> Instradamento:
    t = testo.strip()
    if t.startswith("/"):
        testa, _, resto = t.partition(" ")
        nome = testa[1:].split("@", 1)[0].lower()
        return Instradamento("comando", comando=nome, argomenti=resto.strip())
    if t.startswith("@") and len(t) > 1:
        token, _, resto = t[1:].partition(" ")
        trovati = org.risolvi(token)
        if len(trovati) == 1:
            return Instradamento("agente", agente_id=trovati[0]["id"], testo=resto.strip())
        if trovati:
            return Instradamento("ambiguo", candidati=[a["id"] for a in trovati[:8]])
        return Instradamento("sconosciuto", testo=token)
    return Instradamento("direttore", testo=t)
