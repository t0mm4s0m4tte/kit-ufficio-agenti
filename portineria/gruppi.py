"""Gruppi e argomenti Telegram collegati ai piani e ai team dell'ufficio."""
import json
import logging
import threading
from pathlib import Path

from portineria.organigramma import Organigramma

log = logging.getLogger(__name__)


class Gruppi:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._stanze: dict[tuple, dict] = {}
        try:
            for s in json.loads(self.path.read_text(encoding="utf-8")):
                self._stanze[(s["chat_id"], s["thread_id"])] = s
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            log.error("gruppi.json illeggibile, parto senza gruppi collegati")

    def _salva(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(self._stanze.values()), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def collega(self, chat_id: int, thread_id: int | None, piano: str, team: str | None) -> None:
        with self._lock:
            self._stanze[(chat_id, thread_id)] = {"chat_id": chat_id, "thread_id": thread_id, "piano": piano, "team": team}
            self._salva()

    def scollega(self, chat_id: int, thread_id: int | None) -> bool:
        with self._lock:
            if self._stanze.pop((chat_id, thread_id), None) is None:
                return False
            self._salva()
            return True

    def stanza(self, chat_id: int, thread_id: int | None) -> dict | None:
        with self._lock:
            return self._stanze.get((chat_id, thread_id)) or self._stanze.get((chat_id, None))


def responsabile(org: Organigramma, stanza: dict) -> str:
    if stanza["piano"] == "direzione":
        return "direttore"
    if stanza.get("team"):
        for a in org.tutti():
            if a["piano"] == stanza["piano"] and a.get("team") == stanza["team"] and a["ruolo"] == "comanager":
                return a["id"]
    return org.piano(stanza["piano"])["capo"]


def membri_stanza(org: Organigramma, stanza: dict) -> list[dict]:
    if stanza["piano"] == "direzione":
        return org.capi()
    return org.membri(stanza["piano"], stanza.get("team"))
