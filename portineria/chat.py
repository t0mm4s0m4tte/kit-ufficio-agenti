"""Cronologia delle chat per agente (jsonl) e registro giornaliero nell'Inbox del vault."""
import json
import threading
from datetime import datetime
from pathlib import Path

_LOCK = threading.Lock()  # ponytail: un lock globale, i messaggi sono pochi


class Cronologia:
    def __init__(self, cartella: Path):
        self.cartella = Path(cartella)

    def aggiungi(self, agente_id: str, ruolo: str, testo: str) -> None:
        riga = json.dumps({"t": datetime.now().isoformat(timespec="seconds"), "ruolo": ruolo, "testo": testo},
                          ensure_ascii=False)
        with _LOCK:
            self.cartella.mkdir(parents=True, exist_ok=True)
            with open(self.cartella / f"{agente_id}.jsonl", "a", encoding="utf-8", newline="\n") as f:
                f.write(riga + "\n")

    def ultimi(self, agente_id: str, n: int = 10) -> list[dict]:
        p = self.cartella / f"{agente_id}.jsonl"
        if not p.exists():
            return []
        messaggi = []
        for riga in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                messaggi.append(json.loads(riga))
            except ValueError:
                continue
        return messaggi[-n:] if n > 0 else []


def registra_inbox(vault: Path, quando: datetime, agente_id: str, motore: str, domanda: str, risposta: str) -> Path:
    giorno = quando.strftime("%Y-%m-%d")
    p = Path(vault) / "00 - Inbox" / f"{giorno}.md"
    blocco = (f"\n## {quando:%H:%M} · {agente_id} ({motore})\n\n"
              f"**Domanda:** {domanda}\n\n**Risposta:** {risposta}\n")
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        nuovo = not p.exists()
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            if nuovo:
                f.write(f"---\ntipo: inbox\ndata: {giorno}\n---\n")
            f.write(blocco)
    return p
