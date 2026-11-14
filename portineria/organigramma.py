"""Organigramma in memoria: ricerca e risoluzione degli agenti."""
import json
from pathlib import Path


class Organigramma:
    def __init__(self, dati: dict):
        self._piani = {p["id"]: p for p in dati.get("piani", [])}
        self._agenti = list(dati.get("agenti", []))
        self._per_id = {a["id"]: a for a in self._agenti}

    @classmethod
    def carica(cls, path: Path) -> "Organigramma":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def get(self, id: str) -> dict | None:
        return self._per_id.get(id)

    def piano(self, id_piano: str) -> dict | None:
        return self._piani.get(id_piano)

    def piani(self) -> list[dict]:
        return sorted(self._piani.values(), key=lambda p: -p["numero"])

    def tutti(self) -> list[dict]:
        return list(self._agenti)

    def membri(self, piano: str, team: str | None = None) -> list[dict]:
        if team:
            return [a for a in self._agenti
                    if a["piano"] == piano and a.get("team") == team and a["ruolo"] != "comanager"]
        return [a for a in self._agenti if a["piano"] == piano and a["ruolo"] not in ("capo", "direttore")]

    def capi(self) -> list[dict]:
        return [a for a in self._agenti if a["ruolo"] == "capo"]

    def cerca(self, parola: str, limite: int = 20) -> list[dict]:
        p = parola.casefold()
        trovati = [a for a in self._agenti
                   if p in a["id"].casefold() or p in a["nome"].casefold() or p in a.get("descrizione", "").casefold()]
        return trovati[:limite]

    def risolvi(self, token: str) -> list[dict]:
        t = token.casefold()
        livelli = (
            lambda a: a["id"].casefold() == t,
            lambda a: a["nome"].casefold() == t,
            lambda a: t in a["id"].casefold() or t in a["nome"].casefold(),
        )
        for criterio in livelli:
            trovati = [a for a in self._agenti if criterio(a)]
            if trovati:
                return trovati
        return []

    def elenco_compatto(self, agenti: list[dict]) -> str:
        return "\n".join(f"{a['id']} | {a['piano']} | {a.get('descrizione', '')[:90]}" for a in agenti)
