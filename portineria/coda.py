"""Coda dei lavori Claude: uno alla volta, a due livelli di priorità, con limite di attesa e controllo della RAM del Pi."""
from collections import deque
import logging
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)
PRIORITA = ("alta", "normale")


class CodaPiena(Exception):
    pass


def ram_libera_mb(meminfo: Path = Path("/proc/meminfo")) -> int:
    try:
        for riga in Path(meminfo).read_text(encoding="utf-8", errors="replace").splitlines():
            if riga.startswith("MemAvailable:"):
                return int(riga.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 10_000


class CodaClaude:
    def __init__(self, ram_mb: Callable[[], int] = ram_libera_mb, max_attesa: int = 10,
                 ram_minima: int = 300, attesa_ram_s: float = 10.0):
        self._ram_mb, self._max, self._minima, self._pausa = ram_mb, max_attesa, ram_minima, attesa_ram_s
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._alta: deque[tuple[str, Callable[[], str], Callable[[str | None, Exception | None], None]]] = deque()
        self._normale: deque[tuple[str, Callable[[], str], Callable[[str | None, Exception | None], None]]] = deque()
        self._in_corso: str | None = None
        self._attesa_ram = False
        self._thread = threading.Thread(target=self._worker, name="coda-claude", daemon=True)
        self._thread.start()

    def accoda(self, etichetta: str, lavoro: Callable[[], str],
               al_termine: Callable[[str | None, Exception | None], None],
               priorita: str = "alta") -> int:
        if priorita not in PRIORITA:
            raise ValueError(f"priorità non valida: {priorita}")
        # dal worker stesso (al_termine che delega) il lavoro in corso sta già finendo
        dal_worker = threading.current_thread() is self._thread
        with self._cond:
            tot_attesa = len(self._alta) + len(self._normale)
            occupato = tot_attesa > 0 or (self._in_corso is not None and not dal_worker)
            if occupato and tot_attesa >= self._max:
                raise CodaPiena()
            if priorita == "alta":
                indice = len(self._alta)
                self._alta.append((etichetta, lavoro, al_termine))
            else:
                indice = tot_attesa
                self._normale.append((etichetta, lavoro, al_termine))
            posizione = indice + (1 if self._in_corso and not dal_worker else 0)
            self._cond.notify()
        return posizione

    def stato(self) -> dict:
        with self._lock:
            in_attesa = [e for e, _, _ in self._alta] + [e for e, _, _ in self._normale]
            return {"in_corso": self._in_corso, "in_attesa": in_attesa, "attesa_ram": self._attesa_ram}

    def _worker(self) -> None:
        while True:
            with self._cond:
                while not self._alta and not self._normale:
                    self._cond.wait()
                etichetta, lavoro, al_termine = (self._alta if self._alta else self._normale).popleft()
                self._in_corso = etichetta
            while self._ram_mb() < self._minima:
                with self._lock:
                    self._attesa_ram = True
                time.sleep(self._pausa)
            with self._lock:
                self._attesa_ram = False
            risultato, errore = None, None
            try:
                risultato = lavoro()
            except Exception as e:  # noqa: BLE001 — passato ad al_termine
                errore = e
            try:
                al_termine(risultato, errore)
            except Exception:  # noqa: BLE001
                log.exception("errore in al_termine di %s", etichetta)
            finally:
                with self._lock:
                    self._in_corso = None
