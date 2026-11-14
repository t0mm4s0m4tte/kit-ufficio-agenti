"""Logica dell'ufficio: chi risponde, con quale motore, e cosa si scrive a Tommy."""
import logging
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath
from typing import Callable

from portineria import ares, biblioteca, calendario, cantiere, github, greta, ordini, posta, riunioni, staffetta, vercel, wol
from portineria.chat import Cronologia, registra_inbox
from portineria.coda import CodaClaude, CodaPiena, ram_libera_mb
from portineria.gruppi import membri_stanza, responsabile
from portineria.motori import ErroreMotore, LimiteClaude, QuotaGeminiEsaurita, TimeoutAgente
from portineria.organigramma import Organigramma
from portineria.presenza import RegistroPresenzeEsterni, TTL_DEFAULT
from portineria.router import instrada
from portineria.telemetria import Telemetria
from portineria.vault import leggi_json, primo_json, scrivi_json

log = logging.getLogger(__name__)
COMANDI_PRIVATI_TOMMY = (
    "ordini", "ordine", "approva", "annulla", "pubblica", "pc", "vercel",
    "briefing", "agenda", "todo", "fatto", "mail"
)

AIUTO = """Comandi dell'ufficio:
/aiuto — questo elenco
/chi <parola> — cerca un agente per nome, id o descrizione
/stato — coda di Claude, RAM libera e ultimi errori
/briefing — punto della situazione di oggi preparato da Greta (agenda + to-do + ordini + mail)
/mail — riepilogo delle ultime email nella casella Principale di Gmail
/todo [testo] — mostra i promemoria aperti o ne aggiunge uno nei tuoi appunti
/fatto <numero> — spunta un promemoria come completato
/riunione <commessa> <tema> — riunione cross-piano (proposte in sequenza + sintesi)
/rivedi [@agente] <richiesta> — risposta con controllo rapido di un revisore
/regola <piano> <testo> — aggiunge una regola o preferenza al Playbook del piano
/riferimento [<url> [perché ti piace]] — salva un sito di ispirazione per il frontend (senza argomenti: l'elenco); lo vedono Design, Engineering e Marketing
/ordini — ordini di lavoro aperti
/ordine [@agente] <destinazione> | <piano> — crea un ordine di lavoro
/approva <id> — approva e firma un ordine di lavoro
/annulla <id> — annulla un ordine di lavoro
/pubblica <id> — pubblica in produzione (merge su main)
/vercel [cartella] — mette online su Vercel una cartella del vault con index.html o un progetto Next.js con package.json (senza argomento: l'index.html più recente) e ti manda il link
/pc accendi|stato|spegni|resta — gestisce il PC remoto via Wake-on-LAN
/gemini — ripete con Gemini l'ultima richiesta fermata dal limite di Claude
/cerca <parole> — cerca nel vault (senza AI)
/archivia — fa lavorare subito gli archivisti Notturno e Fonti
@segretaria <messaggio> — parla con Greta (agenda, promemoria, to-do, link Google Calendar)
@bibliotecario cosa sappiamo su X — risposta dalle note del vault
@nome <messaggio> — parla direttamente con un agente
Testo libero — lo legge il Direttore, che risponde o passa all'agente giusto
Nei gruppi: /collega <piano> [team], /scollega"""


CAPACITA_UFFICIO = """COSA PUÒ FARE L'UFFICIO (non promettere altro):
- Gli agenti con motore Claude hanno accesso al vault e alla shell (Bash + subagent): possono eseguire npm install, test, build e controlli reali prima di rispondere (quando il PC è acceso girano direttamente sul PC; per lavoro con codice, comandi, test o build delega sempre a un agente Claude, es. engineering-software-architect).
- Gli agenti con motore Gemini non hanno accesso a file o shell: usali per testi, idee e analisi, mai per verificare codice o lanciare comandi.
- Per mettere online una cartella del vault Tommy scrive /vercel <cartella> (va bene un index.html o un progetto Next.js con package.json, es. my-office/landing-v2): il link arriva in chat. Non proporre Tailscale, server locali o Netlify.
- Per lavori asincroni su repository/progetti o quando serve approvazione esplicita: Tommy può usare /ordine o approvare un ordine di lavoro (/ordini, /approva <id>) per Cantiere o PC.
- Per un lavoro che coinvolge più agenti insieme usa "riunione"; in privato "coinvolgi" passa a un solo agente."""


ISTRUZIONE_RIUNIONE = """

Sei il responsabile di questa stanza di lavoro su Telegram. Rispondi a Tommy in italiano.
Uscita obbligatoria: una sola riga JSON, nient'altro:
{"risposta": "<testo per Tommy>", "coinvolgi": [{"agente": "<id dai MEMBRI DELLA STANZA>", "brief": "<istruzioni>"}]}
Coinvolgi al massimo 3 membri, solo se servono davvero; altrimenti "coinvolgi": []."""


MOTORI_VALIDI = {"claude", "gemini"}


@dataclass
class Motori:
    gemini: Callable[[str, list[dict], str], str]
    claude: Callable[[PurePath, list[dict], str], str]


def interpreta_responsabile(uscita: str, ammessi: set[str]) -> dict:
    obj = primo_json(uscita)
    if obj is not None:
        risposta = obj.get("risposta") if isinstance(obj.get("risposta"), str) else None
        grezzi = obj.get("coinvolgi") if isinstance(obj.get("coinvolgi"), list) else []
        if "agente" in obj:
            grezzi = [{"agente": obj.get("agente"), "brief": obj.get("brief", "")}]
        coinvolgi = [{"agente": c["agente"], "brief": str(c.get("brief", ""))}
                     for c in grezzi if isinstance(c, dict) and c.get("agente") in ammessi][:3]
        if risposta or coinvolgi:
            return {"risposta": risposta, "coinvolgi": coinvolgi}
    return {"risposta": uscita, "coinvolgi": []}


def firma(org: Organigramma, agente: dict) -> str:
    if agente["id"] == "direttore":
        return "🏛 Direttore\n\n"
    piano = org.piano(agente["piano"]) or {"emoji": "", "nome": agente["piano"]}
    return f"{piano['emoji']} {agente['nome']} · {agente['ruolo']} {piano['nome']}\n\n"


class Ufficio:
    def __init__(self, org: Organigramma, cronologia: Cronologia, coda: CodaClaude, motori: Motori,
                 invia: Callable[[int, str, int | None], None], cfg, adesso=datetime.now, in_parallelo=None,
                 contesto: Callable[[str], str] | None = None, archivia: Callable[[], None] | None = None,
                 telemetria: Telemetria | None = None, avvia_cantiere: Callable[[str], int] | None = None):
        self.org, self.cron, self.coda, self.motori, self.invia, self.cfg = org, cronologia, coda, motori, invia, cfg
        self.adesso = adesso
        self.in_parallelo = in_parallelo or ThreadPoolExecutor(4).submit
        self.contesto_vault = contesto
        self.archivia = archivia
        self.avvia_cantiere = avvia_cantiere
        if telemetria is not None:
            self.telemetria = telemetria
        else:
            p_stato = getattr(self.cfg, "stato", None)
            path_tel = (Path(p_stato) / "telemetria.json") if p_stato else None
            self.telemetria = Telemetria(path_tel, self.org)
        self.stato_archivisti: dict[str, str] = {}
        self.errori: deque = deque(maxlen=5)
        self._da_ripetere: dict[int, tuple] = {}
        self._gemini_attivi: dict[str, int] = {}
        self._flussi: dict[int, dict] = {}
        self._prossimo_flusso = 0
        self._eventi_piano: dict[str, dict] = {}
        self._lock_attivi = threading.Lock()
        self.esterni = RegistroPresenzeEsterni(self.org)
        for agente_id, motore in leggi_json(self._file_motori()).items():
            if isinstance(motore, str) and motore in MOTORI_VALIDI and self.org.get(agente_id) is not None:
                self.org.get(agente_id)["motore"] = motore

    def flussi_attivi(self) -> list[dict]:
        with self._lock_attivi:
            interni = [dict(v) for v in self._flussi.values()]
        return interni + self.esterni.flussi()

    def eventi_piani(self) -> dict[str, dict]:
        with self._lock_attivi:
            return {k: dict(v) for k, v in self._eventi_piano.items()}

    def _apri_flusso(self, da_agente_id: str | None, a_agente_id: str, tipo: str, commessa: str = "") -> int | None:
        if not da_agente_id or da_agente_id == a_agente_id:
            return None
        da = self.org.get(da_agente_id)
        a = self.org.get(a_agente_id)
        if da is None or a is None:
            return None
        with self._lock_attivi:
            self._prossimo_flusso += 1
            token = self._prossimo_flusso
            self._flussi[token] = {
                "da_agente": da["id"],
                "da_piano": da["piano"],
                "a_agente": a["id"],
                "a_piano": a["piano"],
                "tipo": tipo,
                "commessa": commessa,
            }
            return token

    def _chiudi_flusso(self, token: int | None) -> None:
        if token is None:
            return
        with self._lock_attivi:
            self._flussi.pop(token, None)

    def _commessa_del_testo(self, testo: str) -> str:
        try:
            nota = biblioteca.commessa_citata(Path(self.cfg.vault), testo)
            return nota.stem if nota is not None else ""
        except OSError:
            return ""

    def _registra_evento(self, agente: dict, testo: str, commessa: str = "") -> None:
        with self._lock_attivi:
            self._eventi_piano[agente["piano"]] = {
                "testo": " ".join(testo.split())[:120],
                "commessa": commessa,
                "agente": agente["id"],
                "t": self.adesso().isoformat(timespec="seconds"),
            }

    def _file_motori(self) -> Path:
        return Path(self.cfg.stato) / "motori.json"

    def imposta_motore(self, agente_id: str, motore: str) -> None:
        if motore not in MOTORI_VALIDI:
            raise ValueError(f"motore non valido: {motore}")
        agente = self.org.get(agente_id)
        if agente is None:
            raise KeyError(agente_id)
        with self._lock_attivi:  # ponytail: stesso lock per attivi e file, basta per 4 thread
            agente["motore"] = motore
            salvati = leggi_json(self._file_motori())
            salvati[agente_id] = motore
            scrivi_json(self._file_motori(), salvati)

    def attivi(self) -> dict:
        s = self.coda.stato()
        with self._lock_attivi:
            lavorando = {i for i, n in self._gemini_attivi.items() if n > 0}
        if s["in_corso"]:
            lavorando.add(s["in_corso"])
        est_lav, est_coda = self.esterni.attivi()
        lavorando.update(est_lav)
        in_coda = list(s["in_attesa"])
        for eid in est_coda:
            if eid not in in_coda:
                in_coda.append(eid)
        return {"lavorando": sorted(i for i in lavorando if self.org.get(i) is not None),
                "in_coda": [i for i in in_coda if i not in lavorando and self.org.get(i) is not None],
                "attesa_ram": s["attesa_ram"]}

    def presenza_esterna(
        self,
        azione: str,
        agente_id: str = "",
        *,
        da_agente: str = "",
        tipo: str = "esterna",
        commessa: str = "",
        nota: str = "",
        motore: str = "gemini",
        sorgente: str = "antigravity",
        ttl: int = TTL_DEFAULT,
        registra_telemetria: bool = True,
    ) -> dict:
        esito = self.esterni.aggiorna(
            azione,
            agente_id=agente_id,
            da_agente=da_agente,
            tipo=tipo,
            commessa=commessa,
            sorgente=sorgente,
            ttl=ttl,
        )
        if esito["azione"] == "pulisci":
            return esito

        ag = self.org.get(esito["agente"])
        comm = " ".join((commessa or "").split())[:80]
        nota_pulita = " ".join((nota or "").split())[:100]
        if ag is not None:
            etichette = {"avvia": "al lavoro", "coda": "in coda", "errore": "errore", "fine": "completato"}
            msg = nota_pulita or etichette.get(esito["azione"], esito["azione"])
            self._registra_evento(ag, f"{ag['nome']}: {msg}", commessa=comm)

        if esito["azione"] in ("fine", "errore") and registra_telemetria and self.telemetria:
            motore_eff = motore if motore in MOTORI_VALIDI else "gemini"
            self.telemetria.registra(
                esito["agente"],
                motore_eff,
                errore=(esito["azione"] == "errore"),
                adesso=self.adesso(),
            )
        return esito

    # ---- ingresso dei messaggi privati -------------------------------------------------
    def gestisci(self, chat_id: int, testo: str, thread_id: int | None = None) -> None:
        r = instrada(testo, self.org)
        if r.tipo == "comando":
            self._comando(r.comando, r.argomenti, chat_id, thread_id)
        elif r.tipo == "ambiguo":
            righe = [f"@{i} — {self.org.get(i)['nome']}" for i in r.candidati]
            self.invia(chat_id, "Chi intendi?\n" + "\n".join(righe), thread_id)
        elif r.tipo == "sconosciuto":
            self.invia(chat_id, f"Non trovo «{r.testo}». Prova /chi <parola>.", thread_id)
        elif r.tipo == "agente":
            if not r.testo:
                self.invia(chat_id, f"Scrivi il messaggio dopo @{r.agente_id}.", thread_id)
            else:
                self.chiedi(r.agente_id, r.testo, chat_id, thread_id)
        else:
            self.direttore(r.testo, chat_id, thread_id)

    def _comando(self, comando: str, argomenti: str, chat_id: int, thread_id: int | None) -> None:
        if comando in ("aiuto", "start", "help"):
            testo = AIUTO
        elif comando == "chi":
            trovati = self.org.cerca(argomenti, 20) if argomenti else []
            testo = "\n".join(f"@{a['id']} — {a['nome']} ({self._nome_piano(a)})" for a in trovati) \
                or ("Nessun agente trovato." if argomenti else "Scrivi /chi <parola>.")
        elif comando == "stato":
            testo = self._stato()
        elif comando == "cerca":
            if not argomenti:
                testo = "Scrivi /cerca <parole>."
            else:
                trovati = biblioteca.cerca(Path(self.cfg.vault), argomenti)
                testo = "\n".join(f"📄 {r['percorso']} ({r['punteggio']})" for r in trovati) \
                    or "Niente nel vault su questo."
        elif comando == "archivia":
            if self.archivia is None:
                testo = "Archivisti non attivi."
            else:
                threading.Thread(target=self._protetto(self.archivia), name="archivia", daemon=True).start()
                testo = "Archivisti al lavoro: Notturno e Fonti. Il risultato lo trovi nel Diario."
        elif comando == "riunione":
            commessa, tema, menzioni = riunioni.analizza_argomenti_riunione(Path(self.cfg.vault), argomenti)
            if not commessa or not tema:
                testo = "Uso: /riunione <commessa> <tema> [@agente ...]"
            else:
                riunioni.avvia_riunione(
                    self,
                    chat_id,
                    thread_id,
                    commessa,
                    tema,
                    candidati=menzioni or None,
                    budget=riunioni.MAX_CHIAMATE,
                    firma_fn=firma,
                )
                return
        elif comando == "rivedi":
            self._comando_rivedi(argomenti, chat_id, thread_id)
            return
        elif comando == "regola":
            testo = self._comando_regola(argomenti)
        elif comando == "riferimento":
            testo = self._comando_riferimento(argomenti)
        elif comando == "gemini":
            ripeti = self._da_ripetere.pop(chat_id, None)
            if ripeti is None:
                testo = "Niente da ripetere."
            else:
                agente_id, testo_agente, th, al_termine, registra = ripeti
                self.chiedi(agente_id, testo_agente, chat_id, th, al_termine=al_termine,
                            forza_motore="gemini", registra=registra)
                return
        elif comando in COMANDI_PRIVATI_TOMMY:
            if chat_id != self.cfg.telegram_user_id or thread_id is not None:
                testo = "Comando riservato a Tommy nella chat privata."
            elif comando == "pc":
                testo = self._comando_pc(argomenti)
            elif comando == "vercel":
                self._comando_vercel(argomenti, chat_id, thread_id)
                return
            elif comando in ("briefing", "agenda"):
                self._comando_briefing(chat_id, thread_id)
                return
            elif comando == "mail":
                self._comando_mail(chat_id, thread_id)
                return
            elif comando == "todo":
                testo = self._comando_todo(argomenti)
            elif comando == "fatto":
                testo = self._comando_fatto(argomenti)
            else:
                testo = self._comando_ordine(comando, argomenti)
        else:
            testo = "Comando sconosciuto, scrivi /aiuto"
        self.invia(chat_id, testo, thread_id)

    def _comando_rivedi(self, argomenti: str, chat_id: int, thread_id: int | None) -> None:
        t = (argomenti or "").strip()
        if not t:
            self.invia(chat_id, "Uso: /rivedi [@agente] <richiesta>", thread_id)
            return
        if t.startswith("@"):
            r = instrada(t, self.org)
            if r.tipo == "ambiguo":
                righe = [f"@{i} — {self.org.get(i)['nome']}" for i in r.candidati]
                self.invia(chat_id, "Chi intendi?\n" + "\n".join(righe), thread_id)
            elif r.tipo == "sconosciuto":
                self.invia(chat_id, f"Non trovo «{r.testo}». Prova /chi <parola>.", thread_id)
            elif r.tipo == "agente" and not r.testo:
                self.invia(chat_id, f"Scrivi il messaggio dopo @{r.agente_id}.", thread_id)
            elif r.tipo == "agente":
                self.chiedi(r.agente_id, r.testo, chat_id, thread_id, revisione=True)
            return
        self.direttore(t, chat_id, thread_id, forza_revisione=True)

    def _comando_regola(self, argomenti: str) -> str:
        t = (argomenti or "").strip()
        if not t:
            return "Uso: /regola <piano> <testo>"
        piano_scelto = None
        testo_regola = ""
        # Controlla se inizia col nome o l'id esatto di un piano
        for p in sorted(self.org.piani(), key=lambda x: len(x["nome"]), reverse=True):
            for etichetta in (p["nome"], p["id"]):
                if t.casefold().startswith(etichetta.casefold() + " "):
                    piano_scelto = p
                    testo_regola = t[len(etichetta):].strip()
                    break
            if piano_scelto is not None:
                break
        if piano_scelto is None:
            parti = t.split(None, 1)
            if len(parti) < 2:
                return "Uso: /regola <piano> <testo>"
            piano_scelto = biblioteca.risolvi_piano_playbook(self.org, parti[0])
            testo_regola = parti[1].strip()
            if piano_scelto is None:
                ids = ", ".join(p["id"] for p in self.org.piani())
                return f"Piano sconosciuto: {parti[0]}. Piani disponibili: {ids}"
        if not testo_regola:
            return "Uso: /regola <piano> <testo>"
        try:
            percorso = biblioteca.aggiungi_regola_playbook(
                Path(self.cfg.vault), piano_scelto, testo_regola, org=self.org
            )
        except ValueError as e:
            return f"Impossibile salvare la regola: {e}"
        return f"📏 Regola aggiunta a {percorso.stem}: {testo_regola}"

    def _comando_riferimento(self, argomenti: str) -> str:
        vault = Path(self.cfg.vault)
        t = (argomenti or "").strip()
        if not t:
            righe = biblioteca.righe_riferimenti(vault)
            return ("🔗 Riferimenti frontend:\n" + "\n".join(righe)) if righe else \
                "Nessun riferimento salvato. Uso: /riferimento <url> [perché ti piace]"
        url, _, nota = t.partition(" ")
        try:
            if biblioteca.aggiungi_riferimento(vault, url, nota):
                return f"🔗 Riferimento salvato: {url}"
            return f"Era già nell'elenco: {url}"
        except ValueError:
            return "Uso: /riferimento <url> [perché ti piace] (l'indirizzo deve iniziare con https://)"

    def _comando_ordine(self, comando: str, argomenti: str) -> str:
        vault = Path(self.cfg.vault)
        scaduti = {o["id"] for o in ordini.scadi_ordini(vault, self.adesso())}
        if comando == "ordini":
            aperti = ordini.elenca_ordini(vault, solo_aperti=True)
            if not aperti:
                return "Nessun ordine di lavoro aperto."
            return "\n".join(
                f"{o['id']} [{o['stato']}] — {o['titolo']} (@{o['agente']}, {o['esecuzione']})"
                for o in aperti
            )
        if comando == "ordine":
            return self._crea_ordine_da_chat(vault, argomenti)
        codice = argomenti.strip().upper()
        if not codice:
            return f"Scrivi /{comando} <id>."
        if comando == "approva":
            if codice in scaduti:
                return f"Ordine {codice} scaduto (più di 24 ore senza approvazione) e annullato."
            try:
                o = ordini.approva_ordine(vault, codice, self.cfg.ordini_hmac_key)
            except KeyError:
                return f"Ordine {codice} non trovato."
            except (ordini.StatoNonValido, ValueError) as e:
                return f"Impossibile approvare {codice}: {e}"
            if o["esecuzione"] == "cantiere" and self.avvia_cantiere is not None:
                try:
                    posizione = self.avvia_cantiere(o["id"])
                except CodaPiena:
                    return (f"Ordine {o['id']} approvato e firmato ({o['destinazione']}), "
                            "ma la coda Claude è piena: riprova tra poco.")
                stato = "avviato" if posizione == 0 else f"in coda alla posizione {posizione}"
                return f"Ordine {o['id']} approvato, firmato e {stato} sul Cantiere ({o['destinazione']})."
            if o["esecuzione"] == "pc" and getattr(self.cfg, "pc_mac", "").strip():
                try:
                    wol.accendi_pc(
                        vault,
                        self.cfg,
                        invia=lambda msg: self.invia(self.cfg.telegram_user_id, msg, None),
                        adesso=self.adesso(),
                        in_parallelo=self.in_parallelo,
                    )
                    return f"Ordine {o['id']} approvato e firmato (pc: {o['destinazione']}). Wake-on-LAN inviato al PC."
                except ValueError as e:
                    return f"Ordine {o['id']} approvato e firmato (pc: {o['destinazione']}), ma WoL non inviato: {e}"
            return f"Ordine {o['id']} approvato e firmato ({o['esecuzione']}: {o['destinazione']})."
        if comando == "pubblica":
            return self._pubblica_ordine(vault, codice)
        try:
            o = ordini.annulla_ordine(vault, codice, motivo="annullato da Tommy")
        except KeyError:
            return f"Ordine {codice} non trovato."
        except ordini.StatoNonValido as e:
            return f"Impossibile annullare {codice}: {e}"
        return f"Ordine {o['id']} annullato."

    def _crea_ordine_da_chat(self, vault: Path, argomenti: str) -> str:
        t = (argomenti or "").strip()
        agente_id = "engineering-software-architect" if self.org.get("engineering-software-architect") else "direttore"
        if t.startswith("@"):
            tok, _, resto = t[1:].partition(" ")
            trovati = self.org.risolvi(tok)
            if not trovati:
                return f"Agente «{tok}» non trovato."
            if len(trovati) > 1:
                return "Chi intendi? " + ", ".join(f"@{a['id']}" for a in trovati[:5])
            agente_id = trovati[0]["id"]
            t = resto.strip()
        if "|" not in t:
            return "Uso: /ordine [@agente] <destinazione> | <piano>"
        dest, _, piano = t.partition("|")
        dest, piano = dest.strip(), piano.strip()
        if not dest or not piano:
            return "Uso: /ordine [@agente] <destinazione> | <piano>"
        dest_norm = dest.replace("\\", "/")
        if (vault / dest_norm).is_dir() or ":" in dest or "\\" in dest or dest_norm.startswith(("my-office/", "03-projects/")):
            esecuzione = "pc"
        elif "/" in dest_norm and " " not in dest_norm and not dest_norm.startswith("."):
            esecuzione = "cantiere"
        else:
            esecuzione = "pc"
        o = ordini.crea_ordine(
            vault,
            agente=agente_id,
            piano=piano,
            esecuzione=esecuzione,
            destinazione=dest,
            adesso=self.adesso(),
        )
        return (
            f"📝 Ordine {o['id']} creato ({o['esecuzione']}: {o['destinazione']}, @{o['agente']}). "
            f"Scrivi /approva {o['id']} per eseguirlo."
        )

    def _pubblica_ordine(self, vault: Path, codice: str) -> str:
        try:
            o = ordini.carica_ordine(vault, codice)
        except KeyError:
            return f"Ordine {codice} non trovato."
        if o["stato"] != "fatto":
            return f"Impossibile pubblicare {o['id']}: l'ordine è in stato '{o['stato']}' (richiesto 'fatto')."
        branch = cantiere.branch_ordine(o["id"])
        if o["esecuzione"] != "cantiere" or branch.casefold() not in (o.get("esito") or "").casefold():
            return f"Impossibile pubblicare {o['id']}: non risulta completato sul branch `{branch}`."
        try:
            repo = github.valida_repo(o.get("destinazione") or "")
        except ValueError:
            return (f"Impossibile pubblicare {o['id']}: repository "
                    f"'{o.get('destinazione', '')}' non valido (atteso owner/repo).")
        token_gh = getattr(self.cfg, "github_token", "")
        if not token_gh:
            return f"Impossibile pubblicare {o['id']}: GITHUB_TOKEN mancante nella configurazione."
        try:
            merge_res = github.unisci_branch(
                repo, head=branch, base="main", token=token_gh, messaggio=f"Merge {branch}"
            )
        except github.ErroreGitHub as e:
            return f"Errore durante la pubblicazione di {o['id']} su {repo}: {e}"

        link_prod = (
            ordini.url_produzione_commessa(vault, o.get("commessa", ""))
            or github.url_produzione_repo(repo, token=token_gh)
        )
        sha_merge = str(merge_res.get("sha") or "").strip()
        dettaglio_sha = f" (commit `{sha_merge[:7]}`)" if sha_merge else ""
        ordini.pubblica_ordine(vault, o["id"], nota=f"Pubblicato su `main`{dettaglio_sha} — {link_prod}")
        return f"✅ Ordine {o['id']} pubblicato su `main` ({repo}){dettaglio_sha}.\n🌐 Produzione: {link_prod}"

    def _comando_vercel(self, argomenti: str, chat_id: int, thread_id: int | None) -> None:
        token = getattr(self.cfg, "vercel_token", "")
        if not token:
            self.invia(chat_id, "VERCEL_TOKEN mancante in ~/ufficio/.env "
                                "(crealo su vercel.com/account/tokens e riavvia l'ufficio).", thread_id)
            return
        vault = Path(self.cfg.vault)
        try:
            cartella = vercel.risolvi_cartella(vault, argomenti)
        except vercel.ErroreVercel as e:
            self.invia(chat_id, f"Impossibile pubblicare: {e}", thread_id)
            return
        nome = vercel.slug(cartella.relative_to(vault.resolve()).as_posix())
        attesa = " Se è un progetto Next.js Vercel lo compila: ci vuole un paio di minuti." if vercel._e_next(cartella) else ""
        self.invia(chat_id, f"⏳ Pubblico «{cartella.name}» su Vercel come {nome}…{attesa}", thread_id)

        def lavoro() -> None:
            try:
                url = vercel.pubblica_cartella(cartella, nome, token, getattr(self.cfg, "vercel_team_id", ""))
            except vercel.ErroreVercel as e:
                self.invia(chat_id, f"Errore Vercel: {e}", thread_id)
                return
            self.invia(chat_id, f"🚀 Online: {url}\nVisibile a chiunque abbia il link. "
                                f"Per aggiornarla rilancia /vercel: l'indirizzo resta lo stesso.", thread_id)

        self.in_parallelo(self._protetto(lavoro))  # la chiamata di rete non blocca il ciclo di Telegram

    def _comando_briefing(self, chat_id: int, thread_id: int | None) -> None:
        ordini_aperti = ordini.elenca_ordini(Path(self.cfg.vault))
        info_ordini = ""
        if ordini_aperti:
            info_ordini = "\n\nOrdini di lavoro aperti nell'ufficio:\n" + "\n".join(
                f"- #{o['id']} ({o.get('destinatario', 'sconosciuto')}): {o.get('titolo', '')}"
                for o in ordini_aperti[:5]
            )
        prompt = (
            "Fai un briefing esecutivo della giornata per Tommy (oggi e domani).\n"
            "Evidenzia gli orari dei prossimi impegni, i to-do aperti e le priorità.\n"
            "Stile di Greta: amichevole, secca, calda, zero fuffa, evidenzia prima ciò che scade prima."
            + info_ordini
        )
        self.chiedi("segretaria", prompt, chat_id, thread_id)

    def _comando_mail(self, chat_id: int, thread_id: int | None) -> None:
        percorso = self.cfg.base / "segreti" / "gmail.txt"
        if posta.leggi_credenziali(percorso) is None:
            self.invia(
                chat_id,
                "Gmail non ancora collegata: crea ~/ufficio/segreti/gmail.txt (chmod 600) con:\n"
                "EMAIL=tua@gmail.com\nAPP_PASSWORD=xxxx xxxx xxxx xxxx",
                thread_id,
            )
            return
        prompt = (
            "Controlla le ultime email nella casella Principale di Tommy.\n"
            "Elenca prima le non lette o urgenti (mittente, oggetto e cosa serve), poi eventuali altre rilevanti. "
            "Stile: secco e chiaro, zero fuffa."
        )
        self.chiedi("segretaria", prompt, chat_id, thread_id)

    def _comando_todo(self, argomenti: str) -> str:
        t = (argomenti or "").strip()
        v = Path(self.cfg.vault)
        if not t:
            todos = greta.elenca_todo(v, solo_aperti=True)
            if not todos:
                return "📋 Nessun promemoria aperto nei tuoi appunti.\n(Scrivi /todo <testo> per aggiungerne uno)"
            elenco = "\n".join(f"[{item['indice']}] {item['testo']}" for item in todos)
            return f"📋 To-Do e Promemoria di Tommy:\n{elenco}\n\n(Usa /fatto <numero> per spuntare o /todo <testo> per aggiungere)"
        ok = greta.aggiungi_todo(v, t, oggi=self.adesso().date())
        return f"✅ Segnato nei tuoi appunti: {t}" if ok else "Errore nel salvataggio del promemoria."

    def _comando_fatto(self, argomenti: str) -> str:
        t = (argomenti or "").strip()
        if not t:
            return "Uso: /fatto <numero o parola>"
        v = Path(self.cfg.vault)
        arg = int(t) if t.isdigit() else t
        ok = greta.spunta_todo(v, arg)
        return f"✅ Promemoria spuntato: {t}" if ok else f"Non ho trovato il promemoria «{t}»."

    def _comando_pc(self, argomenti: str) -> str:
        azione = (argomenti or "").strip().lower()
        vault = Path(self.cfg.vault)
        if azione == "accendi":
            try:
                wol.accendi_pc(
                    vault,
                    self.cfg,
                    invia=lambda msg: self.invia(self.cfg.telegram_user_id, msg, None),
                    adesso=self.adesso(),
                    in_parallelo=self.in_parallelo,
                )
            except ValueError as e:
                return f"Impossibile accendere il PC: {e}"
            return "⚡ Magic packet Wake-on-LAN inviato al PC e handshake firmato salvato."
        if azione == "stato":
            ip = getattr(self.cfg, "pc_ip", "100.121.89.90")
            online = wol.pc_raggiungibile(ip)
            ordini_pc = [
                o for o in ordini.elenca_ordini(vault, solo_aperti=True)
                if o["esecuzione"] == "pc" and o["stato"] in ("approvato", "in-corso")
            ]
            stato_str = f"🖥 PC online su Tailscale ({ip})." if online else f"💤 PC spento o non raggiungibile su Tailscale ({ip})."
            return f"{stato_str} Ordini PC in coda/corso: {len(ordini_pc)}."
        if azione == "spegni":
            try:
                wol.scrivi_comando_pc(vault, "spegni", self.cfg.ordini_hmac_key, adesso=self.adesso())
            except ValueError as e:
                return f"Impossibile inviare comando di spegnimento: {e}"
            return "🛑 Richiesta di spegnimento inviata al PC (preavviso 5 minuti). Scrivi /pc resta per annullarlo."
        if azione == "iberna":
            try:
                wol.scrivi_comando_pc(vault, "iberna", self.cfg.ordini_hmac_key, adesso=self.adesso())
            except ValueError as e:
                return f"Impossibile inviare comando di ibernazione: {e}"
            return "🌙 Richiesta di ibernazione inviata al PC."
        if azione == "resta":
            try:
                wol.scrivi_comando_pc(vault, "resta", self.cfg.ordini_hmac_key, adesso=self.adesso())
            except ValueError as e:
                return f"Impossibile annullare lo spegnimento: {e}"
            return "✅ Spegnimento automatico annullato: il PC resterà acceso."
        return "Uso: /pc accendi | stato | spegni | iberna | resta"

    def _stato(self) -> str:
        s = self.coda.stato()
        righe = [f"Claude in corso: {s['in_corso'] or 'nessuno'}", f"In attesa: {len(s['in_attesa'])}"]
        if s["attesa_ram"]:
            righe.append("In attesa di RAM")
        righe.append(f"RAM libera: {ram_libera_mb()} MB")
        if self.stato_archivisti:
            righe.append("Archivisti:")
            righe += [f"{nome}: {esito}" for nome, esito in list(self.stato_archivisti.items())]
        errori = list(self.errori)
        righe.append("Ultimi errori:" if errori else "Nessun errore recente.")
        righe += [f"{quando} {msg}" for quando, msg in errori]
        if self.telemetria:
            riepilogo = self.telemetria.riepilogo_testo()
            if riepilogo:
                righe.append(riepilogo)
        return "\n".join(righe)

    def _nome_piano(self, agente: dict) -> str:
        return (self.org.piano(agente["piano"]) or {}).get("nome", agente["piano"])

    # ---- Direttore ---------------------------------------------------------------------
    def direttore(self, testo: str, chat_id: int, thread_id: int | None, forza_revisione: bool = False) -> None:
        """Il Direttore risponde o passa a un agente; lo chiamano Telegram e la chat web (CHAT_WEB)."""
        altri = [a for a in self.org.tutti() if a["id"] != "direttore"]
        ammessi = {a["id"] for a in altri}
        messaggio = testo + "\n\n" + CAPACITA_UFFICIO + "\n\nAGENTI DISPONIBILI:\n" + self.org.elenco_compatto(altri)

        def dopo(uscita: str) -> None:
            obj = primo_json(uscita)
            if isinstance(obj, dict) and isinstance(obj.get("riunione"), dict):
                dati_r = obj["riunione"]
                commessa = str(dati_r.get("commessa") or "").strip() or self._commessa_del_testo(testo) or "Generale"
                tema = str(dati_r.get("tema") or "").strip() or testo
                part = dati_r.get("partecipanti")
                candidati = [str(p) for p in part if isinstance(p, str)] if isinstance(part, list) else None
                riunioni.avvia_riunione(
                    self,
                    chat_id,
                    thread_id,
                    commessa,
                    tema,
                    candidati=candidati,
                    budget=riunioni.MAX_CHIAMATE - 1,
                    firma_fn=firma,
                )
                return
            r = interpreta_responsabile(uscita, ammessi)
            if not r["coinvolgi"]:
                self.invia(chat_id, firma(self.org, self.org.get("direttore")) + (r["risposta"] or uscita), thread_id)
                return
            scelto = r["coinvolgi"][0]  # in privato: Direttore + un solo esecutore
            dest = self.org.get(scelto["agente"])
            rev_dir: bool | str = False
            if isinstance(obj, dict):
                if isinstance(obj.get("revisore"), str) and obj["revisore"].strip():
                    rev_dir = obj["revisore"].strip()
                elif obj.get("revisione") is True:
                    rev_dir = True
            self.invia(chat_id, f"🏛 Direttore\n\nPasso a {dest['nome']} ({self._nome_piano(dest)}).", thread_id)
            self.chiedi(
                dest["id"],
                scelto["brief"] + "\n\nRichiesta originale di Tommy: " + testo,
                chat_id,
                thread_id,
                flusso_da="direttore",
                flusso_tipo="delega",
                revisione=rev_dir or forza_revisione,
            )

        self.chiedi("direttore", messaggio, chat_id, thread_id, al_termine=dopo, registra=testo)

    # ---- gruppi e argomenti ---------------------------------------------------------------
    def gestisci_gruppo(self, chat_id: int, thread_id: int | None, testo: str, gruppi) -> None:
        r = instrada(testo, self.org)
        if r.tipo == "comando" and r.comando == "collega":
            self._collega(chat_id, thread_id, r.argomenti, gruppi)
            return
        if r.tipo == "comando" and r.comando == "scollega":
            esito = "Scollegato." if gruppi.scollega(chat_id, thread_id) else "Questa chat non era collegata."
            self.invia(chat_id, esito, thread_id)
            return
        stanza = gruppi.stanza(chat_id, thread_id)
        if stanza is None:
            return  # chat non collegata: silenzio
        if r.tipo == "comando" and r.comando in COMANDI_PRIVATI_TOMMY:
            self.invia(chat_id, "Comando riservato a Tommy nella chat privata.", thread_id)
            return
        if r.tipo != "direttore":
            self.gestisci(chat_id, testo, thread_id)  # @nome e comandi: come in privato
            return
        self._riunione(stanza, r.testo, chat_id, thread_id)

    def _collega(self, chat_id: int, thread_id: int | None, argomenti: str, gruppi) -> None:
        parti = argomenti.split()
        piano = self.org.piano(parti[0]) if parti else None
        team = parti[1] if len(parti) > 1 else None
        if piano is None or (team and team not in piano.get("team", [])):
            ids = ", ".join(p["id"] for p in self.org.piani())
            self.invia(chat_id, f"Piano o team sconosciuto. Piani: {ids}", thread_id)
            return
        gruppi.collega(chat_id, thread_id, piano["id"], team)
        resp = self.org.get(responsabile(self.org, {"piano": piano["id"], "team": team}))
        nome_team = f" · team {team}" if team else ""
        self.invia(chat_id, f"Collegato: {piano['emoji']} {piano['nome']}{nome_team}. "
                            f"Qui risponde {resp['nome'] if resp else '?'}.", thread_id)

    def _riunione(self, stanza: dict, testo: str, chat_id: int, thread_id: int | None) -> None:
        resp_id = responsabile(self.org, stanza)
        membri = [m for m in membri_stanza(self.org, stanza) if m["id"] != resp_id]
        ammessi = {m["id"] for m in membri}
        messaggio = (testo + "\n\n" + CAPACITA_UFFICIO + "\n\nMEMBRI DELLA STANZA:\n"
                     + self.org.elenco_compatto(membri) + ISTRUZIONE_RIUNIONE)

        def dopo(uscita: str) -> None:
            r = interpreta_responsabile(uscita, ammessi)
            if r["risposta"]:
                self.invia(chat_id, firma(self.org, self.org.get(resp_id)) + r["risposta"], thread_id)
            for c in r["coinvolgi"]:  # al massimo 3: totale ≤ 4 chiamate
                self.chiedi(c["agente"], c["brief"] + "\n\nMessaggio di Tommy nella stanza: " + testo,
                            chat_id, thread_id, flusso_da=resp_id, flusso_tipo="riunione")

        self.chiedi(resp_id, messaggio, chat_id, thread_id, al_termine=dopo, registra=testo)

    # ---- chiamata di un agente -----------------------------------------------------------
    def chiedi(self, agente_id: str, testo: str, chat_id: int, thread_id: int | None,
               al_termine: Callable[[str], None] | None = None, forza_motore: str | None = None,
               registra: str | None = None, al_errore: Callable[[Exception], None] | None = None,
               aggiungi_contesto: bool = True, flusso_da: str | None = None,
               flusso_tipo: str = "delega", commessa: str = "", revisione: bool | str = False) -> None:
        """`registra` è il testo salvato in cronologia e Inbox (default: `testo`)."""
        agente = self.org.get(agente_id)
        prompt = self.cfg.app / agente["prompt_path"]
        storia = self.cron.ultimi(agente_id)
        da_registrare = testo if registra is None else registra
        commessa_eff = commessa or self._commessa_del_testo(da_registrare)

        # Se l'agente chiamato è il Coach ARES, arricchisce il contesto con i dati live di ARES
        if agente_id == "coach-ares":
            ares_dati = ares.recupera_stato_ares(base_url=getattr(self.cfg, "ares_url", "http://127.0.0.1:3100"))
            info_ares = ares.sintetizza_contesto_ares(ares_dati)
            testo = f"{testo}\n\n{info_ares}"

        # La segretaria riceve l'agenda dai link iCal segreti (sola lettura), gli appunti dal vault e la posta Principale
        if agente_id == "segretaria":
            agenda = calendario.agenda_testo(self.cfg.base / "segreti" / "calendari.txt")
            if agenda:
                testo = f"{testo}\n\n{agenda}"
            blocco_appunti = greta.blocco_appunti_greta(Path(self.cfg.vault))
            if blocco_appunti:
                testo = f"{testo}\n\n{blocco_appunti}"
            blocco_posta = posta.posta_testo(
                self.cfg.base / "segreti" / "gmail.txt",
                stato_monitor=self.cfg.stato / "monitor_leveraged.json",
            )
            if blocco_posta:
                testo = f"{testo}\n\n{blocco_posta}"

        if revisione:
            esplicito = revisione if isinstance(revisione, str) else None
            revisore = staffetta.scegli_revisore(self.org, agente, da_registrare, revisore_esplicito=esplicito)
            if revisore is not None:
                al_termine_finale = al_termine

                def dopo_autore(bozza: str) -> None:
                    def consegna_finale(finale: str) -> None:
                        if al_termine_finale:
                            al_termine_finale(finale)
                        else:
                            intestazione = f"{firma(self.org, agente).rstrip()}\n🔎 Revisione: {revisore['nome']}\n\n"
                            self.invia(chat_id, intestazione + finale, thread_id)

                    def fallback_bozza(err: Exception) -> None:
                        if al_termine_finale:
                            al_termine_finale(bozza)
                        else:
                            self.invia(
                                chat_id,
                                f"{firma(self.org, agente)}{bozza}\n\n_(Revisione automatica non disponibile: {err})_",
                                thread_id,
                            )

                    prompt_rev = staffetta.prompt_revisione(agente, self._nome_piano(agente), da_registrare, bozza)
                    self.chiedi(
                        revisore["id"],
                        prompt_rev,
                        chat_id,
                        thread_id,
                        al_termine=consegna_finale,
                        al_errore=fallback_bozza,
                        registra=f"[Revisione {agente['nome']}] {da_registrare}",
                        aggiungi_contesto=False,
                        flusso_da=agente["id"],
                        flusso_tipo="staffetta",
                        commessa=commessa_eff,
                        revisione=False,
                    )

                al_termine = dopo_autore

        flusso_token = self._apri_flusso(flusso_da, agente_id, flusso_tipo, commessa=commessa_eff)
        self._registra_evento(agente, f"{agente['nome']}: al lavoro", commessa=commessa_eff)
        contesto = {
            "agente": agente,
            "testo": testo,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "al_termine": al_termine,
            "da_registrare": da_registrare,
            "al_errore": al_errore,
            "aggiungi_contesto": aggiungi_contesto,
            "flusso_token": flusso_token,
            "commessa": commessa_eff,
            "motore": (forza_motore or agente["motore"]),
        }

        if (forza_motore or agente["motore"]) != "gemini":
            self._accoda_claude(contesto, prompt, storia)
            return

        def lavoro_gemini():
            with self._lock_attivi:
                self._gemini_attivi[agente_id] = self._gemini_attivi.get(agente_id, 0) + 1
            try:
                sistema = Path(prompt).read_text(encoding="utf-8", errors="replace")
                msg = self._testo_motore(testo, da_registrare, agente) if aggiungi_contesto else testo
                risposta = self.motori.gemini(sistema, storia, msg)
            except QuotaGeminiEsaurita:
                contesto["motore"] = "claude"
                self._accoda_claude(contesto, prompt, storia)
                return
            except Exception as e:  # noqa: BLE001 — ogni errore va a Tommy
                self._errore(e, contesto)
                return
            finally:
                with self._lock_attivi:
                    self._gemini_attivi[agente_id] -= 1
            self._fatto(risposta, "gemini", contesto)

        self.in_parallelo(self._protetto(lavoro_gemini))

    def _accoda_claude(self, contesto: dict, prompt: PurePath, storia: list[dict]) -> None:
        agente = contesto["agente"]
        testo = contesto["testo"]
        chat_id = contesto["chat_id"]
        thread_id = contesto["thread_id"]
        da_registrare = contesto["da_registrare"]
        aggiungi_contesto = contesto["aggiungi_contesto"]

        def al_termine(risposta, errore):
            if errore is not None:
                self._errore(errore, contesto)
            else:
                self._fatto(risposta, "claude", contesto)

        def lavoro():  # il contesto si calcola nel worker, mai nel ciclo di Telegram
            msg = self._testo_motore(testo, da_registrare, agente) if aggiungi_contesto else testo
            return self.motori.claude(prompt, storia, msg)

        try:
            posizione = self.coda.accoda(agente["id"], lavoro, al_termine)
        except CodaPiena as e:
            if self.telemetria:
                self.telemetria.registra(agente["id"], "claude", errore=True, adesso=self.adesso())
            self._chiudi_flusso(contesto["flusso_token"])
            self._registra_evento(agente, f"{agente['nome']}: coda piena", commessa=contesto["commessa"])
            self.invia(chat_id, "Coda piena, riprova tra poco.", thread_id)
            if contesto["al_errore"] is not None:
                contesto["al_errore"](e)
            return
        if posizione > 0:
            self.invia(chat_id, f"⏳ In coda, posizione {posizione}.", thread_id)

    def _testo_motore(self, testo: str, da_registrare: str, agente: dict | None = None) -> str:
        """Aggiunge il contesto del vault e il Playbook del reparto (spec §5.3) al testo mandato al motore."""
        blocchi = []
        if agente is not None:
            try:
                piano_obj = self.org.piano(agente.get("piano", "")) or agente.get("piano")
                pb = biblioteca.estratto_playbook(
                    Path(self.cfg.vault),
                    piano_obj,
                    org=self.org,
                    max_car=biblioteca.MAX_CAR_PLAYBOOK,
                )
                if pb:
                    blocchi.append(f"PLAYBOOK DI REPARTO ({self._nome_piano(agente)}):\n{pb}")
                if agente.get("piano") in biblioteca.PIANI_RIFERIMENTI:
                    rif = biblioteca.estratto_riferimenti(Path(self.cfg.vault))
                    if rif:
                        blocchi.append(rif)
            except Exception:  # noqa: BLE001
                log.exception("playbook di reparto non disponibile")
        if self.contesto_vault is not None:
            try:
                extra = self.contesto_vault(da_registrare)
                if extra:
                    blocchi.append(extra)
            except Exception:  # noqa: BLE001 — senza contesto l'agente risponde lo stesso
                log.exception("contesto del vault non disponibile")
        unito = "\n\n".join(blocchi)
        return f"{testo}\n\nCONTESTO DAL VAULT:\n{unito}" if unito else testo

    def _fatto(self, risposta: str, motore: str, contesto: dict) -> None:
        agente = contesto["agente"]
        chat_id = contesto["chat_id"]
        thread_id = contesto["thread_id"]
        al_termine = contesto["al_termine"]
        da_registrare = contesto["da_registrare"]
        if self.telemetria:
            self.telemetria.registra(agente["id"], motore, errore=False, adesso=self.adesso())
        self._chiudi_flusso(contesto["flusso_token"])
        self._registra_evento(agente, f"{agente['nome']}: completato", commessa=contesto["commessa"])
        self.cron.aggiungi(agente["id"], "utente", da_registrare)
        self.cron.aggiungi(agente["id"], "agente", risposta)
        try:
            registra_inbox(Path(self.cfg.vault), self.adesso(), agente["id"], motore, da_registrare, risposta)
        except OSError:
            log.exception("Inbox non scrivibile")
        if al_termine:
            al_termine(risposta)
        else:
            self.invia(chat_id, firma(self.org, agente) + risposta, thread_id)

    def _errore(self, e: Exception, contesto: dict) -> None:
        agente = contesto["agente"]
        testo = contesto["testo"]
        chat_id = contesto["chat_id"]
        thread_id = contesto["thread_id"]
        al_termine = contesto["al_termine"]
        da_registrare = contesto["da_registrare"]
        if self.telemetria:
            self.telemetria.registra(agente["id"], contesto.get("motore", "claude"), errore=True, adesso=self.adesso())
        self._chiudi_flusso(contesto["flusso_token"])
        self._registra_evento(agente, f"{agente['nome']}: errore", commessa=contesto["commessa"])
        if isinstance(e, LimiteClaude):
            self._da_ripetere[chat_id] = (agente["id"], testo, thread_id, al_termine, da_registrare)
            msg = f"Limite di Claude raggiunto: {e}. Scrivi /gemini per far rispondere Gemini."
        elif isinstance(e, TimeoutAgente):
            msg = f"{agente['nome']} ci ha messo più di 5 minuti, lavoro interrotto."
        else:
            if not isinstance(e, ErroreMotore):
                log.exception("errore inatteso di %s", agente["id"], exc_info=e)
            msg = f"Errore di {agente['nome']}: {e}"
        log.warning("errore di %s: %s", agente["id"], msg)
        self.errori.append((self.adesso().strftime("%H:%M"), msg))
        self.invia(chat_id, msg, thread_id)
        if contesto["al_errore"] is not None:
            contesto["al_errore"](e)

    @staticmethod
    def _protetto(f: Callable[[], None]) -> Callable[[], None]:
        def g():
            try:
                f()
            except Exception:  # noqa: BLE001 — un thread del pool non deve morire in silenzio
                log.exception("errore nel lavoro Gemini")
        return g
