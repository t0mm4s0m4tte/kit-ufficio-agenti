"""python -m portineria — il processo dell'ufficio: portineria Telegram ed edificio 3D."""
import functools
import logging
import os
import sys
import threading
import time
from pathlib import Path

from portineria.chat import Cronologia
from portineria.coda import CodaClaude
from portineria.config import carica_config
from portineria.gruppi import Gruppi
from portineria.motori import chiedi_claude, chiedi_rapido
from portineria.organigramma import Organigramma
from portineria.vault import prepara_vault
from portineria.web import CHAT_WEB, AvvisiWeb, avvia_web
from portineria.telegram import Telegram, autorizzato, estrai, leggi_offset, salva_offset
from portineria.ufficio import Motori, Ufficio

log = logging.getLogger("portineria")


def elabora(msg: dict | None, user_id: int, tg, ufficio, gruppi) -> None:
    if msg is None:
        return
    if not autorizzato(msg, user_id, gruppi):
        log.warning("non autorizzato user=%s chat=%s", msg.get("user_id"), msg.get("chat_id"))
        return
    if msg["testo"] is None:
        if not msg.get("contenuto", True):
            return  # messaggio di servizio: nessuna risposta
        tg.invia(msg["chat_id"], "Per ora capisco solo messaggi di testo.", msg["thread_id"])
        return
    if msg["chat_tipo"] in ("group", "supergroup"):
        ufficio.gestisci_gruppo(msg["chat_id"], msg["thread_id"], msg["testo"], gruppi)
    else:
        ufficio.gestisci(msg["chat_id"], msg["testo"], msg["thread_id"])


def avvia_edificio(avvia, host: str, porta: int, attesa: float = 30, dormi=time.sleep) -> threading.Thread:
    """Accende l'edificio in un thread a parte, cosi' Telegram parte subito. Dopo un riavvio del Pi
    l'IP Tailscale puo' non esserci ancora (OSError): si ritenta ogni `attesa` s. Gli altri errori
    (porta impossibile, organigramma rotto) non si risolvono da soli: log e basta."""
    def giro():
        tentativi = 0
        while True:
            try:
                avvia()
            except OSError:
                tentativi += 1
                if tentativi == 1:
                    log.exception("Edificio non avviato su %s:%d, ritento ogni %d s", host, porta, attesa)
                else:
                    log.debug("Edificio ancora non avviato su %s:%d (tentativo %d)", host, porta, tentativi)
                dormi(attesa)
                continue
            except Exception:  # noqa: BLE001 — l'edificio resta spento, Telegram no
                log.exception("Edificio non avviato su %s:%d, non ritento", host, porta)
                return
            log.info("Edificio su http://%s:%d", host, porta)
            return

    t = threading.Thread(target=giro, name="edificio", daemon=True)
    t.start()
    return t


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = carica_config(Path(os.environ.get("UFFICIO_ENV") or Path.home() / "ufficio" / ".env"))
    stato = Path(cfg.stato)
    (stato / "chat").mkdir(parents=True, exist_ok=True)
    prepara_vault(Path(cfg.vault))
    org = Organigramma.carica(Path(cfg.app) / "organigramma.json")
    tg = Telegram(cfg.telegram_token)
    motori = Motori(functools.partial(chiedi_rapido, cfg=cfg), functools.partial(chiedi_claude, cfg=cfg))
    coda = CodaClaude()
    avvisi = AvvisiWeb()

    def invia(chat_id, testo, thread_id=None):
        if chat_id == CHAT_WEB:
            avvisi.aggiungi(testo)
        else:
            tg.invia(chat_id, testo, thread_id)

    ufficio = Ufficio(org, Cronologia(stato / "chat"), coda, motori, invia, cfg)
    if cfg.web_host:
        avvia_edificio(functools.partial(avvia_web, cfg.web_host, cfg.web_porta, ufficio,
                                         Path(cfg.app) / "edificio", avvisi), cfg.web_host, cfg.web_porta)
    else:
        log.info("Edificio spento: WEB_HOST vuoto")
    if not cfg.telegram_token:
        log.info("Telegram spento: TELEGRAM_TOKEN vuoto (l'edificio resta attivo)")
        threading.Event().wait()
    gruppi = Gruppi(stato / "gruppi.json")
    percorso_offset = stato / "offset.txt"
    offset = leggi_offset(percorso_offset)
    log.info("Portineria avviata: %d agenti", len(org.tutti()))

    while True:
        try:
            aggiornamenti = tg.aggiornamenti(offset)
        except Exception as e:  # noqa: BLE001 — rete o Telegram giù: riprova
            log.error("getUpdates fallito: %s", e)
            time.sleep(5)
            continue
        for u in aggiornamenti:
            offset = u["update_id"] + 1
            salva_offset(percorso_offset, offset)  # prima di elaborare: niente doppioni dopo un riavvio
            try:
                elabora(estrai(u), cfg.telegram_user_id, tg, ufficio, gruppi)
            except Exception:  # noqa: BLE001
                log.exception("errore sull'update %s", u.get("update_id"))


if __name__ == "__main__":
    main()
