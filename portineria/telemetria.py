"""Telemetria dei piani e degli agenti: contatori di chiamate, errori e attività."""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from threading import Lock

log = logging.getLogger(__name__)


class Telemetria:
    def __init__(self, path: Path | str | None = None, org=None):
        self.path = Path(path) if path else None
        self.org = org
        self._lock = Lock()
        self._dati: dict = {
            "agenti": {},
            "piani": {},
            "totale": {"claude": 0, "gemini": 0, "errori": 0},
        }
        self._carica()

    def _carica(self) -> None:
        if not self.path:
            return
        try:
            if self.path.exists():
                contenuto = self.path.read_text(encoding="utf-8")
                if contenuto.strip():
                    caricati = json.loads(contenuto)
                    if isinstance(caricati, dict):
                        self._dati["agenti"] = caricati.get("agenti", {})
                        self._dati["piani"] = caricati.get("piani", {})
                        self._dati["totale"] = caricati.get("totale", {"claude": 0, "gemini": 0, "errori": 0})
        except Exception:
            log.exception("Impossibile caricare telemetria da %s", self.path)

    def _salva(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._dati, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            log.exception("Impossibile salvare telemetria su %s", self.path)

    def registra(self, agente_id: str, motore: str, errore: bool = False, adesso: datetime | None = None) -> None:
        ts = (adesso or datetime.now()).isoformat(timespec="seconds")
        chiave_motore = "claude" if motore == "claude" else "gemini"

        piano_id = "sconosciuto"
        if self.org:
            info = self.org.get(agente_id)
            if info and "piano" in info:
                piano_id = info["piano"]

        with self._lock:
            # Agente
            ag = self._dati["agenti"].setdefault(agente_id, {
                "claude": 0,
                "gemini": 0,
                "errori": 0,
                "ultima_attivita": None,
                "piano": piano_id,
            })
            if piano_id != "sconosciuto":
                ag["piano"] = piano_id
            ag[chiave_motore] = ag.get(chiave_motore, 0) + 1
            if errore:
                ag["errori"] = ag.get("errori", 0) + 1
            ag["ultima_attivita"] = ts

            # Piano
            pi = self._dati["piani"].setdefault(piano_id, {
                "claude": 0,
                "gemini": 0,
                "errori": 0,
                "ultima_attivita": None,
            })
            pi[chiave_motore] = pi.get(chiave_motore, 0) + 1
            if errore:
                pi["errori"] = pi.get("errori", 0) + 1
            pi["ultima_attivita"] = ts

            # Totale
            tot = self._dati["totale"]
            tot[chiave_motore] = tot.get(chiave_motore, 0) + 1
            if errore:
                tot["errori"] = tot.get("errori", 0) + 1

            self._salva()

    def stato_agente(self, agente_id: str) -> dict:
        with self._lock:
            val = self._dati["agenti"].get(agente_id)
            return dict(val) if val else {"claude": 0, "gemini": 0, "errori": 0, "ultima_attivita": None}

    def stato_piano(self, piano_id: str) -> dict:
        with self._lock:
            val = self._dati["piani"].get(piano_id)
            return dict(val) if val else {"claude": 0, "gemini": 0, "errori": 0, "ultima_attivita": None}

    def top_agenti(self, limite: int = 5) -> list[dict]:
        with self._lock:
            risultati = []
            for a_id, val in self._dati["agenti"].items():
                tot = val.get("claude", 0) + val.get("gemini", 0)
                risultati.append({
                    "id": a_id,
                    "totale": tot,
                    "claude": val.get("claude", 0),
                    "gemini": val.get("gemini", 0),
                    "errori": val.get("errori", 0),
                    "ultima_attivita": val.get("ultima_attivita"),
                })
        risultati.sort(key=lambda x: -x["totale"])
        return risultati[:limite]

    def top_piani(self, limite: int = 5) -> list[dict]:
        with self._lock:
            risultati = []
            for p_id, val in self._dati["piani"].items():
                tot = val.get("claude", 0) + val.get("gemini", 0)
                nome = p_id
                if self.org:
                    p_info = self.org.piano(p_id)
                    if p_info and "nome" in p_info:
                        nome = p_info["nome"]
                risultati.append({
                    "id": p_id,
                    "nome": nome,
                    "totale": tot,
                    "claude": val.get("claude", 0),
                    "gemini": val.get("gemini", 0),
                    "errori": val.get("errori", 0),
                    "ultima_attivita": val.get("ultima_attivita"),
                })
        risultati.sort(key=lambda x: -x["totale"])
        return risultati[:limite]

    def agenti_mai_usati(self) -> int:
        if not self.org:
            return 0
        tutti = self.org.tutti()
        with self._lock:
            usati = {
                a_id
                for a_id, val in self._dati["agenti"].items()
                if (val.get("claude", 0) + val.get("gemini", 0)) > 0
            }
        return sum(1 for a in tutti if a["id"] not in usati)

    def riepilogo_testo(self) -> str:
        top_ag = self.top_agenti(5)
        top_pi = self.top_piani(5)
        tot_agenti = len(self.org.tutti()) if self.org else 0
        mai_usati = self.agenti_mai_usati()

        agenti_attivi = [a for a in top_ag if a["totale"] > 0]
        piani_attivi = [p for p in top_pi if p["totale"] > 0]

        if not agenti_attivi and not piani_attivi:
            if tot_agenti > 0:
                return f"Attività:\nNessuna chiamata registrata. Agenti mai usati: {mai_usati} su {tot_agenti}."
            return "Attività:\nNessuna chiamata registrata."

        righe = ["Attività:"]
        if agenti_attivi:
            righe.append("Top agenti: " + ", ".join(f"@{a['id']} ({a['totale']})" for a in agenti_attivi))
        if piani_attivi:
            righe.append("Top piani: " + ", ".join(f"{p['nome']} ({p['totale']})" for p in piani_attivi))
        if tot_agenti > 0:
            righe.append(f"Agenti mai usati: {mai_usati} su {tot_agenti}")
        return "\n".join(righe)

    def sintesi(self) -> dict:
        with self._lock:
            tot = self._dati["totale"]
            c_claude = tot.get("claude", 0)
            c_gemini = tot.get("gemini", 0)
            c_err = tot.get("errori", 0)
        return {
            "totale_claude": c_claude,
            "totale_gemini": c_gemini,
            "totale_errori": c_err,
            "top_agenti": self.top_agenti(5),
            "top_piani": self.top_piani(5),
            "mai_usati": self.agenti_mai_usati(),
            "totale_agenti": len(self.org.tutti()) if self.org else 0,
        }
