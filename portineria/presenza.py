"""Presenza e telemetria esterna per l'edificio 3D (es. agenti avviati dal PC o da Antigravity)."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from portineria.config import _leggi_env
from portineria.motori import firma_richiesta_esecutore

log = logging.getLogger(__name__)

TTL_DEFAULT = 180
TTL_MIN = 10
TTL_MAX = 900
AZIONI_VALIDE = ("avvia", "lavora", "coda", "fine", "errore", "pulisci")
TIPI_FLUSSO = ("delega", "riunione", "staffetta", "esterna")
_ENV_DEFAULT = Path.home() / ".ufficio" / "env"


def _pulisci_testo(val: str, max_len: int) -> str:
    return " ".join((val or "").split())[:max_len]


def _normalizza_ttl(ttl: int) -> int:
    if isinstance(ttl, bool) or not isinstance(ttl, int):
        raise ValueError("ttl deve essere un intero")
    return max(TTL_MIN, min(TTL_MAX, ttl))


class RegistroPresenzeEsterni:
    """Mantiene lo stato temporaneo (con scadenza TTL) degli agenti segnalati da orchestratori esterni."""

    def __init__(self, org, monotonic=time.monotonic):
        self._org = org
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._lavorando: dict[str, dict] = {}
        self._in_coda: dict[str, dict] = {}
        self._flussi: dict[tuple[str, str, str], dict] = {}

    def _pota(self, ora: float) -> None:
        self._lavorando = {k: v for k, v in self._lavorando.items() if v["scade_a"] > ora}
        self._in_coda = {k: v for k, v in self._in_coda.items() if v["scade_a"] > ora}
        self._flussi = {k: v for k, v in self._flussi.items() if v["scade_a"] > ora}

    def aggiorna(
        self,
        azione: str,
        agente_id: str = "",
        *,
        da_agente: str = "",
        tipo: str = "esterna",
        commessa: str = "",
        sorgente: str = "",
        ttl: int = TTL_DEFAULT,
    ) -> dict:
        azione_eff = (azione or "").strip().casefold()
        if azione_eff not in AZIONI_VALIDE:
            raise ValueError(f"azione non valida: {azione}")
        if azione_eff == "lavora":
            azione_eff = "avvia"

        sorg = _pulisci_testo(sorgente, 40)
        ora = self._monotonic()

        if azione_eff == "pulisci":
            with self._lock:
                self._pota(ora)
                if not sorg:
                    rimossi = len(self._lavorando) + len(self._in_coda) + len(self._flussi)
                    self._lavorando.clear()
                    self._in_coda.clear()
                    self._flussi.clear()
                    return {"azione": "pulisci", "rimossi": rimossi}
                prima = len(self._lavorando) + len(self._in_coda) + len(self._flussi)
                self._lavorando = {k: v for k, v in self._lavorando.items() if v["sorgente"] != sorg}
                self._in_coda = {k: v for k, v in self._in_coda.items() if v["sorgente"] != sorg}
                self._flussi = {k: v for k, v in self._flussi.items() if v["sorgente"] != sorg}
                dopo = len(self._lavorando) + len(self._in_coda) + len(self._flussi)
                return {"azione": "pulisci", "rimossi": prima - dopo}

        ag_id = (agente_id or "").strip()
        if not ag_id:
            raise ValueError("campo agente mancante")
        ag = self._org.get(ag_id)
        if ag is None:
            raise KeyError(agente_id)

        ttl_eff = _normalizza_ttl(ttl)
        scade_a = ora + ttl_eff
        commessa_eff = _pulisci_testo(commessa, 80)
        tipo_eff = tipo if tipo in TIPI_FLUSSO else "esterna"
        da_id = (da_agente or "").strip()
        da = self._org.get(da_id) if da_id and da_id != ag_id else None

        with self._lock:
            self._pota(ora)
            if azione_eff == "avvia":
                self._in_coda.pop(ag_id, None)
                self._lavorando[ag_id] = {"scade_a": scade_a, "sorgente": sorg}
                if da is not None:
                    self._flussi[(da["id"], ag["id"], sorg)] = {
                        "da_agente": da["id"],
                        "da_piano": da["piano"],
                        "a_agente": ag["id"],
                        "a_piano": ag["piano"],
                        "tipo": tipo_eff,
                        "commessa": commessa_eff,
                        "sorgente": sorg,
                        "scade_a": scade_a,
                    }
            elif azione_eff == "coda":
                self._lavorando.pop(ag_id, None)
                self._in_coda[ag_id] = {"scade_a": scade_a, "sorgente": sorg}
            else:
                self._lavorando.pop(ag_id, None)
                self._in_coda.pop(ag_id, None)
                self._flussi = {
                    k: v
                    for k, v in self._flussi.items()
                    if not (v["a_agente"] == ag_id and (not sorg or v["sorgente"] == sorg))
                }
        return {"azione": azione_eff, "agente": ag_id, "ttl": ttl_eff}

    def attivi(self) -> tuple[list[str], list[str]]:
        ora = self._monotonic()
        with self._lock:
            self._pota(ora)
            lav = sorted(self._lavorando)
            coda = sorted(k for k in self._in_coda if k not in self._lavorando)
            return lav, coda

    def flussi(self) -> list[dict]:
        ora = self._monotonic()
        with self._lock:
            self._pota(ora)
            return [
                {
                    "da_agente": f["da_agente"],
                    "da_piano": f["da_piano"],
                    "a_agente": f["a_agente"],
                    "a_piano": f["a_piano"],
                    "tipo": f["tipo"],
                    "commessa": f["commessa"],
                }
                for f in self._flussi.values()
            ]


def risolvi_config_presenza(
    env_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Legge EDIFICIO_URL (o WEB_HOST/WEB_PORT) e ORDINI_HMAC_KEY da env file + ambiente."""
    percorso = _ENV_DEFAULT if env_path is None else Path(env_path)
    da_file = _leggi_env(percorso) if percorso.is_file() else {}
    env = {**da_file, **(os.environ if environ is None else environ)}
    chiave = (env.get("ORDINI_HMAC_KEY") or "").strip()
    url = (env.get("EDIFICIO_URL") or "").strip().rstrip("/")
    if not url:
        host = (env.get("WEB_HOST") or "").strip()
        if host:
            porta = (env.get("WEB_PORT") or "8090").strip()
            url = f"http://{host}:{porta}"
    return url, chiave


def invia_presenza(
    azione: str,
    agente: str = "",
    *,
    da_agente: str = "",
    tipo: str = "esterna",
    commessa: str = "",
    nota: str = "",
    motore: str = "gemini",
    sorgente: str = "antigravity",
    ttl: int = TTL_DEFAULT,
    url: str | None = None,
    chiave: str | None = None,
    env_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    timeout: float = 3.0,
    urlopen=urllib.request.urlopen,
) -> bool:
    """Invia un ping firmato HMAC-SHA256 a POST /api/telemetria-esterna (fail-safe: ritorna False se offline)."""
    url_risolta, chiave_risolta = risolvi_config_presenza(env_path=env_path, environ=environ)
    base_url = (url if url is not None else url_risolta).strip().rstrip("/")
    chiave_eff = (chiave if chiave is not None else chiave_risolta).strip()
    if not base_url or not chiave_eff:
        return False

    payload: dict[str, object] = {
        "azione": azione,
        "sorgente": sorgente,
        "ttl": ttl,
    }
    if agente:
        payload["agente"] = agente
    if da_agente:
        payload["da_agente"] = da_agente
    if tipo:
        payload["tipo"] = tipo
    if commessa:
        payload["commessa"] = commessa
    if nota:
        payload["nota"] = nota
    if motore:
        payload["motore"] = motore

    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ts = str(int(time.time()))
    firma = firma_richiesta_esecutore(chiave_eff, ts, raw)
    req = urllib.request.Request(
        f"{base_url}/api/telemetria-esterna",
        data=raw,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": ts,
            "X-Firma": firma,
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        log.debug("Telemetria esterna verso %s non riuscita: %s", base_url, exc)
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="portineria.presenza", description="Aggiorna la presenza di agenti esterni nell'edificio 3D.")
    ap.add_argument("azione", choices=("avvia", "lavora", "coda", "fine", "errore", "pulisci"))
    ap.add_argument("agente", nargs="?", default="")
    ap.add_argument("--da", default="", dest="da_agente")
    ap.add_argument("--tipo", default="esterna", choices=TIPI_FLUSSO)
    ap.add_argument("--commessa", default="")
    ap.add_argument("--nota", default="")
    ap.add_argument("--motore", default="gemini", choices=("gemini", "claude"))
    ap.add_argument("--sorgente", default="antigravity")
    ap.add_argument("--ttl", type=int, default=TTL_DEFAULT)
    ap.add_argument("--env", type=Path, default=None)
    ap.add_argument("--url", default=None)
    ap.add_argument("--strict", action="store_true", help="Ritorna codice 1 se il server non è configurato o raggiungibile")
    args = ap.parse_args(argv)

    ok = invia_presenza(
        args.azione,
        args.agente,
        da_agente=args.da_agente,
        tipo=args.tipo,
        commessa=args.commessa,
        nota=args.nota,
        motore=args.motore,
        sorgente=args.sorgente,
        ttl=args.ttl,
        url=args.url,
        env_path=args.env,
    )
    if args.strict and not ok:
        print("Impossibile inviare la presenza all'edificio (controlla EDIFICIO_URL e ORDINI_HMAC_KEY).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
