"""python -m portineria — il processo 24/7 dell'ufficio sul Raspberry."""
import functools
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path

from portineria import archivisti, biblioteca, cantiere
from portineria.chat import Cronologia
from portineria.coda import CodaClaude
from portineria.config import carica_config
from portineria.gruppi import Gruppi
from portineria.motori import chiedi_claude, chiedi_rapido
from portineria.orologio import Lavoro, Orologio, ogni, quotidiano, settimanale
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


def archivisti_e_orologio(cfg, ufficio: Ufficio, coda: CodaClaude) -> Orologio:
    """I quattro archivisti (spec §6) sull'orologio interno: 03:00, ogni ora, domenica 04:00."""
    vault, stato, app = Path(cfg.vault), Path(cfg.stato), Path(cfg.app)
    gemini = functools.partial(chiedi_rapido, cfg=cfg)

    def prompt(id_: str) -> str:
        return (app / "prompts" / f"_{id_}.md").read_text(encoding="utf-8", errors="replace")

    def notturno():
        archivisti.controlla_esiti(
            archivisti.giro_notturno(vault, date.today(), gemini, prompt("archivista-notturno")))

    def fonti():
        archivisti.controlla_esiti(
            archivisti.giro_fonti(vault, stato / "fonti.json", gemini, prompt("archivista-fonti")))

    def capo():
        oggi = date.today()
        testo = archivisti.testo_capo(archivisti.controlli(vault), oggi)

        def fatto(report, errore):
            if errore is not None:
                esito("capo", f"errore: {errore}")
            else:
                archivisti.scrivi_report(vault, oggi, report)

        coda.accoda("capo-archivista",
                    lambda: chiedi_claude(app / "prompts" / "_capo-archivista.md", [], testo, cfg),
                    fatto, priorita="normale")

    def sentinella():
        from portineria.sentinella import giro_sentinella
        errori = [msg for _, msg in list(ufficio.errori)]
        giro_sentinella(
            vault=vault,
            invia_telegram=lambda msg: ufficio.invia(cfg.telegram_user_id, msg, None),
            errori_recenti=errori,
            gemini=gemini,
        )

    def greta_mattina():
        from portineria.greta import giro_greta_mattina
        giro_greta_mattina(
            vault=vault,
            percorso_segreti=Path(cfg.base) / "segreti" / "calendari.txt",
            invia_telegram=lambda msg: ufficio.invia(cfg.telegram_user_id, msg, None),
            motore_sintesi=gemini,
            percorso_gmail=Path(cfg.base) / "segreti" / "gmail.txt",
        )

    def greta_sera():
        from portineria.greta import giro_greta_sera
        giro_greta_sera(
            vault=vault,
            percorso_segreti=Path(cfg.base) / "segreti" / "calendari.txt",
            invia_telegram=lambda msg: ufficio.invia(cfg.telegram_user_id, msg, None),
            motore_sintesi=gemini,
            percorso_gmail=Path(cfg.base) / "segreti" / "gmail.txt",
        )

    def monitor_leveraged():
        from portineria.posta import controlla_mittente
        controlla_mittente(
            percorso_segreti=Path(cfg.base) / "segreti" / "gmail.txt",
            stato_path=stato / "monitor_leveraged.json",
            invia_telegram=lambda msg: ufficio.invia(cfg.telegram_user_id, msg, None),
        )

    def wol_clipping():
        if not getattr(cfg, "pc_mac", ""):
            return
        from portineria import wol
        if not wol.pc_raggiungibile(cfg):
            wol.invia_magic_packet(cfg.pc_mac, broadcast=getattr(cfg, "pc_broadcast", "255.255.255.255"))
            ufficio.invia(cfg.telegram_user_id, "⚡ Wake-on-LAN inviato al PC per autopilot clipping (08:55).", None)

    def esito(nome: str, risultato: str) -> None:
        adesso = datetime.now()
        ufficio.stato_archivisti[nome] = f"{adesso:%Y-%m-%d %H:%M} {risultato}"
        if risultato.startswith("errore"):
            ufficio.errori.append((f"{adesso:%H:%M}", f"archivista {nome}: {risultato}"))

    orologio = Orologio(stato / "orologio.json", [
        Lavoro("notturno", quotidiano(3), notturno),
        Lavoro("fonti", ogni(1), fonti),
        Lavoro("capo", settimanale(6, 4), capo),
        Lavoro("sentinella", quotidiano(8, 30), sentinella),
        Lavoro("greta_mattina", quotidiano(8, 0), greta_mattina),
        Lavoro("greta_sera", quotidiano(22, 30), greta_sera),
        Lavoro("monitor_leveraged", ogni(2), monitor_leveraged),
        Lavoro("wol_clipping", quotidiano(8, 55), wol_clipping),
    ], esito=esito)
    ufficio.archivia = lambda: (orologio.esegui("notturno"), orologio.esegui("fonti"))
    return orologio


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


def collega_cantiere(cfg, coda: CodaClaude, ufficio: Ufficio, run=subprocess.run) -> None:
    """Collega l'avvio automatico sul Cantiere degli ordini approvati con /approva."""
    ufficio.avvia_cantiere = lambda oid: cantiere.accoda_cantiere(
        Path(cfg.vault),
        oid,
        coda,
        cfg,
        lambda msg: ufficio.invia(cfg.telegram_user_id, msg, None),
        run=run,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = carica_config(Path(os.environ.get("UFFICIO_ENV", "/home/tommy/ufficio/.env")))
    stato = Path(cfg.stato)
    (stato / "chat").mkdir(parents=True, exist_ok=True)
    prepara_vault(Path(cfg.vault))
    org = Organigramma.carica(Path(cfg.app) / "organigramma.json")
    biblioteca.inizializza_playbook(Path(cfg.vault), org)
    tg = Telegram(cfg.telegram_token)
    motori = Motori(functools.partial(chiedi_rapido, cfg=cfg), functools.partial(chiedi_claude, cfg=cfg))
    coda = CodaClaude()
    avvisi = AvvisiWeb()

    def invia(chat_id, testo, thread_id=None):
        if chat_id == CHAT_WEB:
            avvisi.aggiungi(testo)
        else:
            tg.invia(chat_id, testo, thread_id)

    ufficio = Ufficio(org, Cronologia(stato / "chat"), coda, motori, invia, cfg,
                      contesto=functools.partial(biblioteca.contesto, Path(cfg.vault)))
    collega_cantiere(cfg, coda, ufficio)
    archivisti_e_orologio(cfg, ufficio, coda).avvia()
    if cfg.web_host:
        avvia_edificio(functools.partial(avvia_web, cfg.web_host, cfg.web_porta, ufficio,
                                         Path(cfg.app) / "edificio", avvisi), cfg.web_host, cfg.web_porta)
    else:
        log.info("Edificio spento: WEB_HOST vuoto")
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
