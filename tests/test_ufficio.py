import tempfile
import threading
import time
import unittest
from pathlib import Path

from portineria.chat import Cronologia
from portineria.coda import CodaClaude
from portineria.config import Config
from portineria.motori import LimiteClaude, QuotaGeminiEsaurita, TimeoutAgente
from portineria.organigramma import Organigramma
from portineria.ufficio import Motori, Ufficio, firma, interpreta_responsabile
from tests.test_router import PIANI, agente

PIANI_UFF = PIANI + [{"id": "engineering", "numero": 6, "nome": "Engineering", "emoji": "💻", "colore": "",
                      "capo": "engineering-software-architect", "team": []}]
AGENTI_UFF = [
    agente("direttore", "Direttore", "direzione", "direttore", motore="claude"),
    agente("celia-cold-email", "Celia", "vendite", descrizione="Cold email specialist"),
    agente("engineering-software-architect", "Software Architect", "engineering", "capo", motore="claude"),
]


def aspetta(condizione, secondi=3.0):
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        if condizione():
            return
        time.sleep(0.005)
    raise AssertionError("condizione mai vera")


class BaseUfficio(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.cfg = Config("t", 5, "k", "gemini-2.5-flash", "claude", base)
        (base / "app" / "prompts").mkdir(parents=True)
        for a in AGENTI_UFF:
            (base / "app" / "prompts" / f"{a['id']}.md").write_text(f"PROMPT {a['id']}", encoding="utf-8")
        self.org = Organigramma({"piani": PIANI_UFF, "agenti": AGENTI_UFF})
        self.cron = Cronologia(base / "stato" / "chat")
        self.inviati, self.chiamate_gemini, self.chiamate_claude = [], [], []
        self.risposte_gemini, self.risposte_claude = [], []
        self.coda = CodaClaude()
        self.uff = self.crea_ufficio(self.coda)

    def tearDown(self):
        self._tmp.cleanup()

    def crea_ufficio(self, coda):
        return Ufficio(self.org, self.cron, coda, Motori(self.gemini, self.claude),
                       lambda c, t, th: self.inviati.append((c, t, th)), self.cfg,
                       in_parallelo=lambda f, *a: f(*a))

    def _rispondi(self, coda_risposte, predefinita):
        r = coda_risposte.pop(0) if coda_risposte else predefinita
        if isinstance(r, Exception):
            raise r
        return r

    def gemini(self, sistema, storia, testo):
        self.chiamate_gemini.append((sistema, storia, testo))
        return self._rispondi(self.risposte_gemini, "risposta gemini")

    def claude(self, prompt_path, storia, testo):
        self.chiamate_claude.append((prompt_path, storia, testo))
        return self._rispondi(self.risposte_claude, "risposta claude")

    def attendi_coda(self):
        aspetta(lambda: self.coda.stato() == {"in_corso": None, "in_attesa": [], "attesa_ram": False})

    def testi(self):
        return [t for _, t, _ in self.inviati]


class TestUfficio(BaseUfficio):
    def test_agente_diretto_gemini(self):
        self.uff.gestisci(1, "@celia ciao")
        self.assertEqual(self.inviati[-1], (1, "🛒 Celia · operaio Vendite & E-commerce\n\nrisposta gemini", None))
        self.assertEqual(self.chiamate_gemini[0][0], "PROMPT celia-cold-email")
        self.assertEqual(len(self.cron.ultimi("celia-cold-email")), 2)
        inbox = list((self.cfg.vault / "00 - Inbox").glob("*.md"))
        self.assertEqual(len(inbox), 1)

    def test_storia_passata_al_motore(self):
        self.uff.gestisci(1, "@celia primo")
        self.uff.gestisci(1, "@celia secondo")
        self.assertEqual([m["testo"] for m in self.chiamate_gemini[1][1]], ["primo", "risposta gemini"])

    def test_agente_claude_riceve_il_percorso(self):
        self.uff.gestisci(1, "@engineering-software-architect progetta")
        self.attendi_coda()
        self.assertEqual(self.chiamate_claude[0][0], self.cfg.app / "prompts/engineering-software-architect.md")
        self.assertTrue(self.inviati[-1][1].startswith("💻 Software Architect · capo Engineering\n\n"))

    def test_direttore_delega(self):
        self.risposte_claude = ['{"agente": "celia-cold-email", "brief": "scrivi la mail"}']
        self.uff.gestisci(1, "mi serve una cold email")
        self.attendi_coda()
        testi = self.testi()
        self.assertEqual(testi[0], "🏛 Direttore\n\nPasso a Celia (Vendite & E-commerce).")
        self.assertEqual(len(testi), 2)
        self.assertEqual(self.chiamate_gemini[0][2], "scrivi la mail\n\nRichiesta originale di Tommy: mi serve una cold email")
        self.assertIn("AGENTI DISPONIBILI:\ncelia-cold-email | vendite |", self.chiamate_claude[0][2])
        self.assertNotIn("direttore |", self.chiamate_claude[0][2])
        # in cronologia resta il messaggio di Tommy, non l'elenco degli agenti
        self.assertEqual(self.cron.ultimi("direttore")[0]["testo"], "mi serve una cold email")

    def test_direttore_risponde_da_se(self):
        self.risposte_claude = ['{"risposta": "Tutto bene."}']
        self.uff.gestisci(1, "come va?")
        self.attendi_coda()
        self.assertEqual(self.testi(), ["🏛 Direttore\n\nTutto bene."])

    def test_direttore_dal_web(self):  # stesso smistamento di Telegram, chiamato da web.py con CHAT_WEB
        self.risposte_claude = ['{"risposta": "Tutto bene."}']
        self.uff.direttore("come va?", 0, None)
        self.attendi_coda()
        self.assertEqual(self.inviati, [(0, "🏛 Direttore\n\nTutto bene.", None)])

    def test_direttore_json_rotto_risponde_diretto(self):  # Review Focus 2
        self.risposte_claude = ["Certo, ecco cosa penso…"]
        self.uff.gestisci(1, "come va?")
        self.attendi_coda()
        self.assertEqual(self.inviati[-1][1], "🏛 Direttore\n\nCerto, ecco cosa penso…")
        self.assertEqual(len(self.chiamate_gemini), 0)

    def test_direttore_id_inesistente_risponde_diretto(self):  # Review Focus 2
        uscita = '{"agente": "fantasma", "brief": "x"}'
        self.risposte_claude = [uscita]
        self.uff.gestisci(1, "fai qualcosa")
        self.attendi_coda()
        self.assertEqual(len(self.chiamate_claude) + len(self.chiamate_gemini), 1)
        self.assertEqual(self.testi(), ["🏛 Direttore\n\n" + uscita])

    def test_in_coda_posizione(self):  # Review Focus 3
        blocco = threading.Event()
        self.coda.accoda("altro", blocco.wait, lambda r, e: None)
        aspetta(lambda: self.coda.stato()["in_corso"] == "altro")
        self.uff.gestisci(1, "@engineering-software-architect x")
        self.assertEqual(self.testi(), ["⏳ In coda, posizione 1."])
        blocco.set()
        self.attendi_coda()
        self.assertEqual(len(self.testi()), 2)

    def test_coda_piena(self):  # Review Focus 3
        coda = CodaClaude(max_attesa=0)
        uff = self.crea_ufficio(coda)
        blocco = threading.Event()
        coda.accoda("altro", blocco.wait, lambda r, e: None)
        aspetta(lambda: coda.stato()["in_corso"] == "altro")
        uff.gestisci(1, "@engineering-software-architect x")
        self.assertEqual(self.testi(), ["Coda piena, riprova tra poco."])
        blocco.set()

    def test_quota_gemini_passa_a_claude(self):
        self.risposte_gemini = [QuotaGeminiEsaurita("quota")]
        self.uff.gestisci(1, "@celia ciao")
        self.attendi_coda()
        self.assertEqual(self.chiamate_claude[0][2], "ciao")
        self.assertTrue(self.testi()[-1].endswith("risposta claude"))

    def test_limite_claude_e_comando_gemini(self):
        self.risposte_claude = [LimiteClaude("usage limit reached")]
        self.uff.gestisci(1, "@engineering-software-architect progetta")
        self.attendi_coda()
        self.assertIn("/gemini", self.testi()[-1])
        self.assertIn("usage limit reached", self.testi()[-1])
        self.uff.gestisci(1, "/gemini")
        self.assertEqual(self.chiamate_gemini[0][2], "progetta")
        self.assertTrue(self.testi()[-1].endswith("risposta gemini"))
        self.uff.gestisci(1, "/gemini")
        self.assertEqual(self.testi()[-1], "Niente da ripetere.")

    def test_timeout_e_stato(self):
        self.risposte_claude = [TimeoutAgente("t")]
        self.uff.gestisci(1, "@engineering-software-architect x")
        self.attendi_coda()
        self.assertEqual(self.testi()[-1], "Software Architect ci ha messo più di 5 minuti, lavoro interrotto.")
        self.uff.gestisci(1, "/stato")
        self.assertIn("RAM libera", self.testi()[-1])
        self.assertIn("5 minuti", self.testi()[-1])

    def test_comandi(self):
        self.uff.gestisci(1, "/aiuto")
        self.assertIn("/chi", self.testi()[-1])
        self.uff.gestisci(1, "/chi cold")
        self.assertEqual(self.testi()[-1], "@celia-cold-email — Celia (Vendite & E-commerce)")
        self.uff.gestisci(1, "/chi zzz")
        self.assertEqual(self.testi()[-1], "Nessun agente trovato.")
        self.uff.gestisci(1, "/boh")
        self.assertEqual(self.testi()[-1], "Comando sconosciuto, scrivi /aiuto")

    def test_ambiguo_e_sconosciuto(self):
        self.uff.gestisci(1, "@e ciao")
        self.assertTrue(self.testi()[-1].startswith("Chi intendi?\n@"))
        self.uff.gestisci(1, "@nessuno ciao")
        self.assertEqual(self.testi()[-1], "Non trovo «nessuno». Prova /chi <parola>.")

    def test_contesto_aggiunto_al_motore_ma_non_alla_cronologia(self):
        self.uff.contesto_vault = lambda t: "NOTA X" if "landing" in t else ""
        self.uff.gestisci(1, "@celia landing nuova")
        self.assertEqual(self.chiamate_gemini[-1][2], "landing nuova\n\nCONTESTO DAL VAULT:\nNOTA X")
        self.assertEqual(self.cron.ultimi("celia-cold-email")[0]["testo"], "landing nuova")

    def test_contesto_al_claude_calcolato_sul_messaggio_di_tommy(self):
        visti = []
        self.uff.contesto_vault = lambda t: visti.append(t) or "NOTA"
        self.risposte_claude = ['{"risposta": "ok"}']
        self.uff.gestisci(1, "come va la landing?")
        self.attendi_coda()
        self.assertEqual(visti, ["come va la landing?"])
        self.assertTrue(self.chiamate_claude[0][2].endswith("\n\nCONTESTO DAL VAULT:\nNOTA"))

    def test_contesto_vuoto_o_rotto_non_cambia_il_testo(self):
        self.uff.contesto_vault = lambda t: ""
        self.uff.gestisci(1, "@celia ciao")
        self.assertEqual(self.chiamate_gemini[-1][2], "ciao")

        def rotto(t):
            raise OSError("disco")

        self.uff.contesto_vault = rotto
        with self.assertLogs(level="ERROR"):
            self.uff.gestisci(1, "@celia ancora")
        self.assertEqual(self.chiamate_gemini[-1][2], "ancora")
        self.assertTrue(self.testi()[-1].endswith("risposta gemini"))

    def test_cerca(self):
        (self.cfg.vault / "02 - Conoscenza").mkdir(parents=True)
        (self.cfg.vault / "02 - Conoscenza" / "landing.md").write_text("landing landing", encoding="utf-8")
        self.uff.gestisci(1, "/cerca landing")
        self.assertEqual(self.testi()[-1], "📄 02 - Conoscenza/landing.md (2)")
        self.uff.gestisci(1, "/cerca zzzzz")
        self.assertEqual(self.testi()[-1], "Niente nel vault su questo.")
        self.uff.gestisci(1, "/cerca")
        self.assertEqual(self.testi()[-1], "Scrivi /cerca <parole>.")

    def test_archivia(self):
        self.uff.gestisci(1, "/archivia")
        self.assertEqual(self.testi()[-1], "Archivisti non attivi.")
        fatto = threading.Event()
        self.uff.archivia = fatto.set
        self.uff.gestisci(1, "/archivia")
        self.assertTrue(self.testi()[-1].startswith("Archivisti al lavoro"))
        self.assertTrue(fatto.wait(1))

    def test_stato_mostra_archivisti(self):
        self.uff.stato_archivisti["notturno"] = "2026-11-11 03:00 ok"
        self.uff.gestisci(1, "/stato")
        self.assertIn("Archivisti:\nnotturno: 2026-11-11 03:00 ok", self.testi()[-1])

    def test_firma(self):
        self.assertEqual(firma(self.org, self.org.get("direttore")), "🏛 Direttore\n\n")

    def test_interpreta_responsabile_max_tre_e_filtra(self):
        r = interpreta_responsabile('ok ```{"risposta": "va", "coinvolgi": [{"agente":"a","brief":"1"},{"agente":"x","brief":"2"},{"agente":"b","brief":"3"},{"agente":"c","brief":"4"},{"agente":"d","brief":"5"}]}```', {"a", "b", "c", "d"})
        self.assertEqual(r["risposta"], "va")
        self.assertEqual([c["agente"] for c in r["coinvolgi"]], ["a", "b", "c"])

    def test_interpreta_responsabile_testo_libero(self):
        self.assertEqual(interpreta_responsabile("ciao {non json}", {"a"}), {"risposta": "ciao {non json}", "coinvolgi": []})



class TestAttiviEMotore(BaseUfficio):
    def setUp(self):
        super().setUp()
        # copie: imposta_motore cambia i dict in memoria e non deve contaminare gli altri test
        self.org = Organigramma({"piani": PIANI_UFF, "agenti": [dict(a) for a in AGENTI_UFF]})
        self.uff = self.crea_ufficio(self.coda)

    def test_attivi_include_gemini_durante_la_chiamata(self):
        entra, esci = threading.Event(), threading.Event()

        def gemini(sistema, storia, testo):
            entra.set()
            esci.wait(2)
            return "ok"
        self.uff.motori.gemini = gemini
        t = threading.Thread(target=self.uff.chiedi, args=("celia-cold-email", "ciao", 1, None))
        t.start()
        self.assertTrue(entra.wait(2))
        self.assertEqual(self.uff.attivi()["lavorando"], ["celia-cold-email"])
        esci.set()
        t.join(3)
        aspetta(lambda: self.uff.attivi()["lavorando"] == [])

    def test_attivi_si_svuota_anche_se_gemini_fallisce(self):
        def rotto(*a):
            raise RuntimeError("giù")
        self.uff.motori.gemini = rotto
        self.uff.chiedi("celia-cold-email", "ciao", 1, None)
        aspetta(lambda: self.inviati)
        self.assertEqual(self.uff.attivi()["lavorando"], [])

    def test_attivi_esclude_etichette_che_non_sono_agenti(self):
        self.coda.stato = lambda: {"in_corso": "capo-archivista", "in_attesa": ["direttore"], "attesa_ram": False}
        self.assertEqual(self.uff.attivi(), {"lavorando": [], "in_coda": ["direttore"], "attesa_ram": False})

    def test_imposta_motore_salva_e_ricarica(self):
        self.uff.imposta_motore("celia-cold-email", "claude")
        self.assertEqual(self.org.get("celia-cold-email")["motore"], "claude")
        org2 = Organigramma({"piani": PIANI_UFF, "agenti": [dict(a) for a in AGENTI_UFF]})
        Ufficio(org2, self.cron, self.coda, Motori(self.gemini, self.claude), lambda *a: None, self.cfg)
        self.assertEqual(org2.get("celia-cold-email")["motore"], "claude")

    def test_imposta_motore_rifiuta_valori_sbagliati(self):
        with self.assertRaises(ValueError):
            self.uff.imposta_motore("celia-cold-email", "gpt")
        with self.assertRaises(KeyError):
            self.uff.imposta_motore("nessuno", "claude")

    def test_motori_json_con_valori_non_validi_viene_ignorato(self):
        f = self.cfg.stato / "motori.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{"celia-cold-email": ["x"], "direttore": "gemini"}', encoding="utf-8")
        org2 = Organigramma({"piani": PIANI_UFF, "agenti": [dict(a) for a in AGENTI_UFF]})
        Ufficio(org2, self.cron, self.coda, Motori(self.gemini, self.claude), lambda *a: None, self.cfg)
        self.assertEqual(org2.get("celia-cold-email")["motore"], "gemini")
        self.assertEqual(org2.get("direttore")["motore"], "gemini")

    def test_flussi_attivi_ed_eventi_piano_durante_delega(self):
        (self.cfg.vault / "01 - Commesse").mkdir(parents=True, exist_ok=True)
        (self.cfg.vault / "01 - Commesse" / "Shopify.md").write_text("# Shopify\n", encoding="utf-8")
        self.risposte_claude = ['{"agente": "celia-cold-email", "brief": "Scrivi per Shopify"}']
        entra, esci = threading.Event(), threading.Event()

        def gemini(sistema, storia, testo):
            entra.set()
            esci.wait(2)
            return "fatto"

        self.uff.motori.gemini = gemini
        t = threading.Thread(target=self.uff.gestisci, args=(1, "prepara mail per Shopify"))
        t.start()
        self.assertTrue(entra.wait(2))
        flussi = self.uff.flussi_attivi()
        self.assertEqual(len(flussi), 1)
        self.assertEqual((flussi[0]["da_agente"], flussi[0]["da_piano"]), ("direttore", "direzione"))
        self.assertEqual((flussi[0]["a_agente"], flussi[0]["a_piano"]), ("celia-cold-email", "vendite"))
        self.assertEqual(flussi[0]["tipo"], "delega")
        self.assertEqual(flussi[0]["commessa"], "Shopify")
        eventi = self.uff.eventi_piani()
        self.assertEqual(eventi["vendite"]["agente"], "celia-cold-email")
        self.assertEqual(eventi["vendite"]["commessa"], "Shopify")

        esci.set()
        t.join(3)
        self.attendi_coda()
        self.assertEqual(self.uff.flussi_attivi(), [])
        self.assertIn("completato", self.uff.eventi_piani()["vendite"]["testo"])

    def test_telemetria_e_riepilogo_stato(self):
        self.uff.gestisci(1, "@celia-cold-email ciao")
        self.assertTrue(self.testi()[-1].endswith("risposta gemini"))
        self.assertEqual(self.uff.telemetria.stato_agente("celia-cold-email")["gemini"], 1)
        self.assertEqual(self.uff.telemetria.stato_piano("vendite")["gemini"], 1)
        self.uff.gestisci(1, "/stato")
        self.assertIn("Attività:", self.testi()[-1])
        self.assertIn("@celia-cold-email", self.testi()[-1])
        self.assertIn("Vendite & E-commerce", self.testi()[-1])
        self.assertIn("Agenti mai usati", self.testi()[-1])

    def test_pubblica_con_mock_urllib_e_link_commessa(self):
        import json
        from unittest.mock import patch
        from portineria.ordini import approva_ordine, avvia_ordine, cambia_stato, carica_ordine, crea_ordine

        class _Resp:
            def __init__(self, status, dati):
                self.status = status
                self._raw = json.dumps(dati).encode("utf-8")

            def read(self):
                return self._raw

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        self.cfg.ordini_hmac_key = "hmac-ufficio"
        self.cfg.github_token = "ghp_ufficio"
        commesse = self.cfg.vault / "01 - Commesse"
        commesse.mkdir(parents=True, exist_ok=True)
        (commesse / "Shopify.md").write_text(
            "---\nesecuzione: cantiere\nrepo: tommy/shopify-landing\nurl: https://shopify.tommy.it\n---\n# Shopify\n",
            encoding="utf-8",
        )
        crea_ordine(self.cfg.vault, "engineering-software-architect", "Pubblica landing",
                    commessa="Shopify", adesso=self.uff.adesso())
        approva_ordine(self.cfg.vault, "L-001", self.cfg.ordini_hmac_key)
        avvia_ordine(self.cfg.vault, "L-001", self.cfg.ordini_hmac_key)
        cambia_stato(self.cfg.vault, "L-001", "fatto", esito="Branch `ufficio/L-001`")

        chiamate = []

        def finto_urlopen(req, timeout=15):
            chiamate.append((req.get_method(), req.full_url, json.loads(req.data.decode("utf-8"))))
            return _Resp(201, {"sha": "fedcba987"})

        with patch("urllib.request.urlopen", side_effect=finto_urlopen):
            self.uff.gestisci(1, "/pubblica L-001")
            self.assertIn("riservato", self.testi()[-1].lower())
            self.uff.gestisci(5, "/pubblica L-001")

        self.assertEqual(len(chiamate), 1)
        self.assertEqual(chiamate[0][0], "POST")
        self.assertEqual(chiamate[0][1], "https://api.github.com/repos/tommy/shopify-landing/merges")
        self.assertEqual(chiamate[0][2]["base"], "main")
        self.assertEqual(chiamate[0][2]["head"], "ufficio/L-001")
        self.assertEqual(chiamate[0][2]["commit_message"], "Merge ufficio/L-001")
        self.assertEqual(carica_ordine(self.cfg.vault, "L-001")["stato"], "pubblicato")
        self.assertIn("https://shopify.tommy.it", self.testi()[-1])

    def test_vercel_pubblica_la_cartella_del_vault(self):
        import json
        from unittest.mock import patch

        class _Resp:
            status = 200

            def read(self):
                return json.dumps({"url": "x-1.vercel.app", "alias": ["my-office-landing.vercel.app"]}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        sito = self.cfg.vault / "my-office" / "landing"
        sito.mkdir(parents=True)
        (sito / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")

        self.uff.gestisci(1, "/vercel")
        self.assertIn("riservato", self.testi()[-1].lower())
        self.uff.gestisci(5, "/vercel")
        self.assertIn("VERCEL_TOKEN mancante", self.testi()[-1])

        self.cfg.vercel_token = "vcl_test"
        richieste = []

        def finto_urlopen(req, timeout=0):
            richieste.append((req.full_url, json.loads(req.data.decode("utf-8"))))
            return _Resp()

        with patch("urllib.request.urlopen", side_effect=finto_urlopen):
            self.uff.gestisci(5, "/vercel")  # senza argomento: la landing più recente
            self.uff.gestisci(5, "/vercel ../fuori")
        self.assertEqual(len(richieste), 1)
        self.assertEqual(richieste[0][1]["name"], "my-office-landing")
        self.assertEqual(richieste[0][1]["files"], [{"file": "index.html", "data": "<h1>ok</h1>"}])
        self.assertIn("https://my-office-landing.vercel.app", self.testi()[-2])
        self.assertIn("dentro il vault", self.testi()[-1])
        self.assertNotIn("vcl_test", " ".join(self.testi()))


if __name__ == "__main__":
    unittest.main()
