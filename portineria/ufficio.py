"""Logica dell'ufficio: chi risponde, con quale motore, e cosa si scrive all'operatore."""
import logging
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath
from typing import Callable

from portineria import ordini, staffetta
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
COMANDI_PRIVATI = ("ordini", "ordine", "approva", "annulla")

AIUTO = """Comandi dell'ufficio:
/aiuto — questo elenco
/chi <parola> — cerca un agente per nome, id o descrizione
/stato — coda di Claude, RAM libera e ultimi errori
/rivedi [@agente] <richiesta> — risposta con controllo rapido di un revisore
/ordini — ordini di lavoro aperti
/ordine [@agente] <destinazione> | <piano> — crea un ordine di lavoro
/approva <id> — approva e firma un ordine di lavoro
/annulla <id> — annulla un ordine di lavoro
/gemini — ripete con Gemini l'ultima richiesta fermata dal limite di Claude
@nome <messaggio> — parla direttamente con un agente
Testo libero — lo legge il Direttore, che risponde o passa all'agente giusto
Nei gruppi: /collega <piano> [team], /scollega"""


CAPACITA_UFFICIO = """COSA PUÒ FARE L'UFFICIO (non promettere altro):
- Gli agenti con motore Claude hanno accesso alla shell (Bash + subagent) e possono eseguire comandi e controlli reali prima di rispondere.
- Gli agenti con motore Gemini non hanno accesso a file o shell: usali per testi, idee e analisi, mai per verificare codice o lanciare comandi.
- Per un lavoro che richiede approvazione esplicita l'operatore usa /ordine, poi /ordini e /approva <id>: l'ordine viene firmato con HMAC.
- In privato "coinvolgi" passa la richiesta a un solo agente."""


ISTRUZIONE_RIUNIONE = """

Sei il responsabile di questa stanza di lavoro su Telegram. Rispondi all'operatore in italiano.
Uscita obbligatoria: una sola riga JSON, nient'altro:
{"risposta": "<testo per l'operatore>", "coinvolgi": [{"agente": "<id dai MEMBRI DELLA STANZA>", "brief": "<istruzioni>"}]}
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
                 contesto: Callable[[str], str] | None = None,
                 telemetria: Telemetria | None = None):
        self.org, self.cron, self.coda, self.motori, self.invia, self.cfg = org, cronologia, coda, motori, invia, cfg
        self.adesso = adesso
        self.in_parallelo = in_parallelo or ThreadPoolExecutor(4).submit
        self.contesto_vault = contesto
        if telemetria is not None:
            self.telemetria = telemetria
        else:
            p_stato = getattr(self.cfg, "stato", None)
            path_tel = (Path(p_stato) / "telemetria.json") if p_stato else None
            self.telemetria = Telemetria(path_tel, self.org)
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
        elif comando == "rivedi":
            self._comando_rivedi(argomenti, chat_id, thread_id)
            return
        elif comando == "gemini":
            ripeti = self._da_ripetere.pop(chat_id, None)
            if ripeti is None:
                testo = "Niente da ripetere."
            else:
                agente_id, testo_agente, th, al_termine, registra = ripeti
                self.chiedi(agente_id, testo_agente, chat_id, th, al_termine=al_termine,
                            forza_motore="gemini", registra=registra)
                return
        elif comando in COMANDI_PRIVATI:
            if chat_id != self.cfg.telegram_user_id or thread_id is not None:
                testo = "Comando riservato all'operatore nella chat privata."
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
            return f"Ordine {o['id']} approvato e firmato ({o['esecuzione']}: {o['destinazione']})."
        try:
            o = ordini.annulla_ordine(vault, codice, motivo="annullato dall'operatore")
        except KeyError:
            return f"Ordine {codice} non trovato."
        except ordini.StatoNonValido as e:
            return f"Impossibile annullare {codice}: {e}"
        return f"Ordine {o['id']} annullato."

    def _crea_ordine_da_chat(self, vault: Path, argomenti: str) -> str:
        t = (argomenti or "").strip()
        agente_id = "capo-sviluppo" if self.org.get("capo-sviluppo") else "direttore"
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
        if (vault / dest_norm).is_dir() or ":" in dest or "\\" in dest:
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
            f"Scrivi /approva {o['id']} per approvarlo e firmarlo."
        )

    def _stato(self) -> str:
        s = self.coda.stato()
        righe = [f"Claude in corso: {s['in_corso'] or 'nessuno'}", f"In attesa: {len(s['in_attesa'])}"]
        if s["attesa_ram"]:
            righe.append("In attesa di RAM")
        righe.append(f"RAM libera: {ram_libera_mb()} MB")
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
                scelto["brief"] + "\n\nRichiesta originale dell'operatore: " + testo,
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
        if r.tipo == "comando" and r.comando in COMANDI_PRIVATI:
            self.invia(chat_id, "Comando riservato all'operatore nella chat privata.", thread_id)
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
                self.chiedi(c["agente"], c["brief"] + "\n\nMessaggio dell'operatore nella stanza: " + testo,
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
        commessa_eff = commessa

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
            except Exception as e:  # noqa: BLE001 — ogni errore va all'operatore
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
        """Aggiunge il contesto del vault al testo mandato al motore."""
        blocchi = []
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
