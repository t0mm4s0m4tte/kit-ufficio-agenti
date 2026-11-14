"""Orologio degli archivisti: lancia i giri all'ora giusta e recupera quelli saltati a Pi spento."""
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)
Dovuto = Callable[[datetime, "datetime | None"], bool]


def quotidiano(ora: int, minuto: int = 0) -> Dovuto:
    def dovuto(adesso, ultimo):
        scadenza = adesso.replace(hour=ora, minute=minuto, second=0, microsecond=0)
        if scadenza > adesso:
            scadenza -= timedelta(days=1)
        return ultimo is None or ultimo < scadenza
    return dovuto


def ogni(ore: int) -> Dovuto:
    return lambda adesso, ultimo: ultimo is None or adesso >= ultimo + timedelta(hours=ore)


def settimanale(giorno: int, ora: int) -> Dovuto:
    def dovuto(adesso, ultimo):
        scadenza = adesso.replace(hour=ora, minute=0, second=0, microsecond=0)
        scadenza -= timedelta(days=(adesso.weekday() - giorno) % 7)
        if scadenza > adesso:
            scadenza -= timedelta(days=7)
        return ultimo is None or ultimo < scadenza
    return dovuto


@dataclass
class Lavoro:
    nome: str
    dovuto: Dovuto
    esegui: Callable[[], None]


class Orologio:
    def __init__(self, stato: Path, lavori: list[Lavoro], adesso=datetime.now,
                 esito: Callable[[str, str], None] = lambda n, e: None):
        self.stato, self.adesso, self.esito = Path(stato), adesso, esito
        self.lavori = {l.nome: l for l in lavori}
        self._lock = threading.Lock()  # un giro alla volta (orologio e /archivia)

    def _leggi(self) -> dict:
        try:
            dati = json.loads(self.stato.read_text(encoding="utf-8"))
            return dati if isinstance(dati, dict) else {}
        except (OSError, ValueError):
            return {}

    def _segna(self, nome: str, quando: datetime) -> None:
        dati = self._leggi()
        dati[nome] = quando.isoformat(timespec="seconds")
        self.stato.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.stato.with_suffix(".tmp")
        tmp.write_text(json.dumps(dati), encoding="utf-8")
        tmp.replace(self.stato)

    def _esegui(self, lavoro: Lavoro, quando: datetime) -> None:
        self._segna(lavoro.nome, quando)  # prima: un crash a metà non lo fa ripartire in loop
        try:
            lavoro.esegui()
        except Exception as e:  # noqa: BLE001 — un giro rotto non ferma gli altri
            log.exception("giro %s fallito", lavoro.nome)
            self.esito(lavoro.nome, f"errore: {e}")
        else:
            self.esito(lavoro.nome, "ok")

    def giro(self) -> list[str]:
        with self._lock:
            adesso, stato, fatti = self.adesso(), self._leggi(), []
            for nome, lavoro in self.lavori.items():
                try:
                    ultimo = datetime.fromisoformat(stato[nome]) if nome in stato else None
                except (TypeError, ValueError):
                    ultimo = None
                if lavoro.dovuto(adesso, ultimo):
                    self._esegui(lavoro, adesso)
                    fatti.append(nome)
            return fatti

    def esegui(self, nome: str) -> None:
        with self._lock:
            self._esegui(self.lavori[nome], self.adesso())

    def avvia(self, ogni_s: float = 60.0) -> None:
        def ciclo():
            while True:
                try:
                    self.giro()
                except Exception:  # noqa: BLE001
                    log.exception("orologio")
                time.sleep(ogni_s)
        threading.Thread(target=ciclo, name="orologio", daemon=True).start()
