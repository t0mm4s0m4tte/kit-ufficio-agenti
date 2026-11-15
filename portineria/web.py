"""Server web dell'edificio: pagina statica + API JSON, di default solo in locale (127.0.0.1)."""
import json
import logging
import threading
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from portineria.coda import ram_libera_mb
from portineria.edificio import comando_claude, pianta, stanze
from portineria.motori import verifica_firma_richiesta
from portineria.ordini import crea_ordine
from portineria.presenza import TTL_DEFAULT

log = logging.getLogger(__name__)

CHAT_WEB = 0
MAX_CORPO = 20_000
MAX_TESTO = 4000
TIPI = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".mp4": "video/mp4",
    ".glb": "model/gltf-binary",
}


def _parse_range(header: str, totale: int) -> tuple[int, int] | None:
    if totale <= 0 or not header.startswith("bytes="):
        return None
    spec = header.removeprefix("bytes=").split(",", 1)[0].strip()
    if "-" not in spec:
        return None
    inizio_str, fine_str = spec.split("-", 1)
    if not inizio_str:
        if not fine_str.isdigit():
            return None
        coda = int(fine_str)
        if coda <= 0:
            return None
        return max(0, totale - coda), totale - 1
    if not inizio_str.isdigit():
        return None
    inizio = int(inizio_str)
    if inizio >= totale:
        return None
    if not fine_str:
        return inizio, totale - 1
    if not fine_str.isdigit():
        return None
    fine = min(int(fine_str), totale - 1)
    return (inizio, fine) if fine >= inizio else None


class AvvisiWeb:
    def __init__(self):
        self._righe: deque = deque(maxlen=20)
        self._lock = threading.Lock()

    def aggiungi(self, testo: str) -> None:
        with self._lock:
            self._righe.append({"t": datetime.now().isoformat(timespec="seconds"), "testo": testo})

    def ultimi(self, n: int = 10) -> list[dict]:
        with self._lock:
            return list(self._righe)[-n:] if n > 0 else []


def avvia_web(host: str, porta: int, ufficio, statici: Path, avvisi: AvvisiWeb) -> ThreadingHTTPServer:
    radice = Path(statici).resolve()
    piani = pianta(ufficio.org)["piani"]

    class Gestore(BaseHTTPRequestHandler):
        timeout = 30  # una connessione mezza aperta non tiene occupato un thread per sempre

        def log_message(self, formato, *args):
            log.debug("%s " + formato, self.address_string(), *args)

        def _rispondi(self, stato: int, dati, tipo: str = "application/json; charset=utf-8",
                      extra_headers: dict[str, str] | None = None) -> None:
            corpo = dati if isinstance(dati, bytes) else json.dumps(dati, ensure_ascii=False).encode("utf-8")
            self.send_response(stato)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            if self.path.startswith("/api/"):
                self.send_header("Cache-Control", "no-store")
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(corpo)

        def _errore(self, stato: int, testo: str) -> None:
            self._rispondi(stato, {"errore": testo})

        def _origine(self) -> str:
            return f"{host}:{self.server.server_address[1]}"

        def _host_ok(self) -> bool:
            if self.headers.get("Host") == self._origine():
                return True
            self._errore(403, "host non consentito")
            return False

        def do_GET(self):
            if not self._host_ok():
                return
            url = urlsplit(self.path)
            if url.path == "/api/edificio":
                self._rispondi(200, {"piani": piani, "stanze": stanze(Path(ufficio.cfg.vault), host)})
            elif url.path == "/api/stato":
                flussi = ufficio.flussi_attivi() if hasattr(ufficio, "flussi_attivi") else []
                eventi = ufficio.eventi_piani() if hasattr(ufficio, "eventi_piani") else {}
                tel = getattr(ufficio, "telemetria", None)
                statistiche = tel.sintesi() if tel and hasattr(tel, "sintesi") else {}
                self._rispondi(200, {**ufficio.attivi(), "ram_mb": ram_libera_mb(), "flussi": flussi, "eventi": eventi, "statistiche": statistiche})
            elif url.path.startswith("/api/agente/"):
                self._agente(unquote(url.path.removeprefix("/api/agente/")))
            elif url.path == "/api/cerca":
                q = parse_qs(url.query).get("q", [""])[0].strip()
                trovati = ufficio.org.cerca(q, 20) if q else []
                self._rispondi(200, {"risultati": [{"id": a["id"], "nome": a["nome"], "piano": a["piano"]}
                                                   for a in trovati]})
            elif url.path.startswith("/api/"):
                self._errore(404, "non trovato")
            else:
                self._statico(unquote(url.path))

        def _agente(self, agente_id: str) -> None:
            a = ufficio.org.get(agente_id)
            if a is None:
                return self._errore(404, "agente sconosciuto")
            campi = ("id", "nome", "ruolo", "piano", "team", "capo", "descrizione", "motore")
            self._rispondi(200, {**{k: a.get(k) for k in campi}, "comando": comando_claude(a),
                                 "chat": ufficio.cron.ultimi(agente_id, 20), "avvisi": avvisi.ultimi()})

        def _statico(self, percorso: str) -> None:
            # il percorso e' gia' decodificato: si risolve e deve restare dentro la cartella statici
            try:
                if "\0" in percorso:
                    raise ValueError
                base = radice
                rel = percorso.lstrip("/") or "index.html"
                f = (base / rel).resolve()
                valido = f.is_relative_to(base) and f.suffix in TIPI and f.is_file()
            except (OSError, ValueError):
                valido = False
            if not valido:
                return self._errore(404, "non trovato")
            dati = f.read_bytes()
            tipo = TIPI[f.suffix]
            intervallo = self.headers.get("Range")
            if intervallo:
                span = _parse_range(intervallo, len(dati))
                if span is None:
                    return self._rispondi(416, b"", "text/plain; charset=utf-8",
                                          {"Content-Range": f"bytes */{len(dati)}"})
                inizio, fine = span
                return self._rispondi(206, dati[inizio:fine + 1], tipo, {
                    "Accept-Ranges": "bytes",
                    "Content-Range": f"bytes {inizio}-{fine}/{len(dati)}",
                })
            extra = {"Accept-Ranges": "bytes"} if f.suffix == ".mp4" else None
            self._rispondi(200, dati, tipo, extra)

        def do_POST(self):
            if not self._host_ok():
                return
            origine = self.headers.get("Origin")
            if not (self.headers.get("Content-Type") or "").startswith("application/json") \
                    or (origine is not None and origine != f"http://{self._origine()}"):
                return self._errore(403, "richiesta non consentita")
            try:
                lunghezza = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                lunghezza = -1
            if lunghezza > MAX_CORPO:
                return self._errore(413, "richiesta troppo grande")
            raw = self.rfile.read(max(lunghezza, 0))
            if self.path == "/api/telemetria-esterna":
                return self._telemetria_esterna(raw)
            try:
                dati = json.loads(raw.decode("utf-8"))
            except ValueError:  # include UnicodeDecodeError
                dati = None
            if not isinstance(dati, dict) or not isinstance(dati.get("agente"), str):
                return self._errore(400, "JSON non valido")
            agente = dati["agente"]
            if self.path == "/api/chiedi":
                testo = dati.get("testo")
                if not isinstance(testo, str) or not testo.strip() or len(testo) > MAX_TESTO:
                    return self._errore(400, f"il testo deve avere da 1 a {MAX_TESTO} caratteri")
                if ufficio.org.get(agente) is None:
                    return self._errore(404, "agente sconosciuto")
                # l'ora si prende prima: gli avvisi della coda nati durante la chiamata hanno t >= questa
                t = datetime.now().isoformat(timespec="seconds")
                if agente == "direttore":  # come su Telegram: risponde o passa all'agente giusto
                    ufficio.direttore(testo, CHAT_WEB, None)
                else:
                    ufficio.chiedi(agente, testo, CHAT_WEB, None, al_termine=lambda r: None)
                self._rispondi(202, {"ok": True, "t": t})
            elif self.path == "/api/motore":
                motore = dati.get("motore")
                if not isinstance(motore, str):
                    return self._errore(400, "motore non valido")
                try:
                    ufficio.imposta_motore(agente, motore)
                except ValueError:
                    return self._errore(400, "motore non valido")
                except KeyError:
                    return self._errore(404, "agente sconosciuto")
                self._rispondi(200, {"ok": True})
            elif self.path == "/api/ordine":
                testo = dati.get("testo")
                titolo = dati.get("titolo", "")
                commessa = dati.get("commessa", "")
                if not isinstance(testo, str) or not testo.strip() or len(testo) > MAX_TESTO:
                    return self._errore(400, f"il testo deve avere da 1 a {MAX_TESTO} caratteri")
                if not isinstance(titolo, str) or len(titolo) > 200 or not isinstance(commessa, str) or len(commessa) > 200:
                    return self._errore(400, "titolo o commessa non validi")
                if ufficio.org.get(agente) is None:
                    return self._errore(404, "agente sconosciuto")
                ordine = crea_ordine(
                    Path(ufficio.cfg.vault),
                    agente=agente,
                    piano=testo,
                    titolo=titolo,
                    commessa=commessa,
                )
                self._rispondi(200, {"ok": True, "id": ordine["id"], "stato": ordine["stato"]})
            else:
                self._errore(404, "non trovato")

        def _telemetria_esterna(self, raw: bytes) -> None:
            chiave = getattr(getattr(ufficio, "cfg", None), "ordini_hmac_key", "")
            ts = self.headers.get("X-Timestamp") or ""
            firma_req = self.headers.get("X-Firma") or ""
            if not verifica_firma_richiesta(chiave, ts, raw, firma_req):
                return self._errore(403, "firma non valida")
            try:
                dati = json.loads(raw.decode("utf-8"))
            except ValueError:
                dati = None
            if not isinstance(dati, dict) or not isinstance(dati.get("azione"), str):
                return self._errore(400, "JSON non valido")
            if not hasattr(ufficio, "presenza_esterna"):
                return self._errore(501, "telemetria esterna non supportata")
            for campo in ("agente", "da_agente", "tipo", "commessa", "nota", "motore", "sorgente"):
                if campo in dati and dati[campo] is not None and not isinstance(dati[campo], str):
                    return self._errore(400, f"campo {campo} non valido")
            ttl = dati.get("ttl", TTL_DEFAULT)
            reg_tel = dati.get("registra_telemetria", True)
            if not isinstance(reg_tel, bool):
                return self._errore(400, "campo registra_telemetria non valido")
            try:
                esito = ufficio.presenza_esterna(
                    dati["azione"],
                    agente_id=dati.get("agente") or "",
                    da_agente=dati.get("da_agente") or "",
                    tipo=dati.get("tipo") or "esterna",
                    commessa=dati.get("commessa") or "",
                    nota=dati.get("nota") or "",
                    motore=dati.get("motore") or "gemini",
                    sorgente=dati.get("sorgente") or "antigravity",
                    ttl=ttl,
                    registra_telemetria=reg_tel,
                )
            except KeyError:
                return self._errore(404, "agente sconosciuto")
            except ValueError as exc:
                return self._errore(400, str(exc))
            self._rispondi(200, {"ok": True, **esito})

    server = ThreadingHTTPServer((host, porta), Gestore)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _demo() -> None:
    """Ufficio finto per lavorare sulla pagina dal PC: nessun AI, risposte di prova dopo 4 s, una alla volta.
    Una seconda domanda mentre la prima lavora va in coda; "errore" nel testo simula un errore del motore;
    al Direttore "passa" nel testo fa rispondere con una delega."""
    import tempfile
    import time
    from portineria.chat import Cronologia
    from portineria.organigramma import Organigramma

    repo = Path(__file__).resolve().parent.parent
    tmp = Path(tempfile.mkdtemp(prefix="edificio-demo-"))
    (tmp / "vault" / "01 - Commesse").mkdir(parents=True)
    for nome in ("Sito", "Gioco"):
        (tmp / "vault" / "01 - Commesse" / f"{nome}.md").write_text(f"# {nome}\n", encoding="utf-8")
    avvisi = AvvisiWeb()

    class UfficioDemo:
        org = Organigramma.carica(repo / "organigramma.json")
        cron = Cronologia(tmp / "cron")
        cfg = type("Cfg", (), {"vault": tmp / "vault"})()

        def __init__(self):
            self._stato = threading.Lock()
            self._turno = threading.Lock()  # un lavoro alla volta, come la coda di Claude
            self._lavorando: set = set()
            self._in_coda: list = []
            self._flussi: list = []
            self._eventi: dict = {}

        def attivi(self):
            with self._stato:
                return {"lavorando": sorted(self._lavorando), "in_coda": list(self._in_coda), "attesa_ram": False}

        def flussi_attivi(self):
            with self._stato:
                return list(self._flussi)

        def eventi_piani(self):
            with self._stato:
                return {k: dict(v) for k, v in self._eventi.items()}

        def imposta_motore(self, agente_id, motore):
            if motore not in ("claude", "gemini"):
                raise ValueError(motore)
            agente = self.org.get(agente_id)
            if agente is None:
                raise KeyError(agente_id)
            agente["motore"] = motore

        def chiedi(self, agente_id, testo, chat_id, thread_id, al_termine=None, risposta="risposta di prova", **kw):
            # come l'Ufficio vero: le due righe in cronologia solo alla risposta, coda ed errori come avvisi
            with self._stato:
                self._in_coda.append(agente_id)
                posizione = len(self._in_coda) - (0 if self._lavorando else 1)
            if posizione > 0:
                avvisi.aggiungi(f"⏳ In coda, posizione {posizione}.")

            def lavora():
                agente = self.org.get(agente_id)
                flusso = kw.get("flusso")
                with self._turno:
                    with self._stato:
                        self._in_coda.remove(agente_id)
                        self._lavorando.add(agente_id)
                        if flusso:
                            self._flussi.append(flusso)
                        if agente:
                            self._eventi[agente["piano"]] = {
                                "testo": f"{agente['nome']}: al lavoro",
                                "commessa": "Sito" if "sito" in testo.lower() else "",
                                "agente": agente_id,
                                "t": datetime.now().isoformat(timespec="seconds"),
                            }
                    time.sleep(4)
                    if "errore" in testo.lower():
                        avvisi.aggiungi(f"Errore di {agente['nome']}: errore di prova")
                    else:
                        self.cron.aggiungi(agente_id, "utente", testo)
                        self.cron.aggiungi(agente_id, "agente", risposta)
                    with self._stato:
                        self._lavorando.discard(agente_id)
                        if flusso in self._flussi:
                            self._flussi.remove(flusso)
                        if agente:
                            self._eventi[agente["piano"]] = {
                                "testo": f"{agente['nome']}: completato",
                                "commessa": "Sito" if "sito" in testo.lower() else "",
                                "agente": agente_id,
                                "t": datetime.now().isoformat(timespec="seconds"),
                            }
            threading.Thread(target=lavora, daemon=True).start()

        def direttore(self, testo, chat_id, thread_id):
            # il Direttore vero risponde con una riga JSON: la pagina la deve mostrare in chiaro
            delega = "passa" in testo.lower()
            uscita = {"agente": "revisore-codice", "brief": "Dai un'occhiata al codice."} \
                if delega else {"risposta": "Risposta di prova del Direttore."}
            flusso = {
                "da_agente": "direttore",
                "da_piano": "direzione",
                "a_agente": "revisore-codice",
                "a_piano": "engineering",
                "tipo": "delega",
                "commessa": "Sito" if "sito" in testo.lower() else "",
            } if delega else None
            self.chiedi("direttore", testo, chat_id, thread_id, risposta=json.dumps(uscita, ensure_ascii=False),
                        flusso=flusso)

    logging.basicConfig(level=logging.INFO)
    avvia_web("127.0.0.1", 8090, UfficioDemo(), repo / "edificio", avvisi)
    print("Demo su http://127.0.0.1:8090/ (Ctrl+C per fermare)", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    import sys
    if "--demo" not in sys.argv:
        sys.exit("uso: python -m portineria.web --demo")
    _demo()
