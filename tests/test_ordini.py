from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from portineria.chat import Cronologia
from portineria.coda import CodaClaude
from portineria.config import Config, carica_config
from portineria.gruppi import Gruppi
from portineria.organigramma import Organigramma
from portineria.ordini import (
    FirmaNonValida,
    StatoNonValido,
    annulla_ordine,
    approva_ordine,
    avvia_ordine,
    calcola_firma,
    cambia_stato,
    carica_ordine,
    crea_ordine,
    elenca_ordini,
    pubblica_ordine,
    scadi_ordini,
    url_produzione_commessa,
    verifica_firma,
)
from portineria.ufficio import Motori, Ufficio
from tests.test_coda import aspetta
from tests.test_ufficio import AGENTI_UFF, PIANI_UFF


class TestOrdini(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.chiave = "segreto-hmac-di-test-32-caratteri"
        self.ora = datetime(2026, 11, 11, 10, 0, 0)

    def tearDown(self):
        self._tmp.cleanup()

    def test_creazione_progressiva_e_lettura(self):
        o1 = crea_ordine(self.vault, "capo-sviluppo", "Crea index.html",
                         titolo="Landing X", commessa="Sito", destinazione="utente/sito", adesso=self.ora)
        o2 = crea_ordine(self.vault, "celia-cold-email", "Aggiorna copy", adesso=self.ora)
        self.assertEqual(o1["id"], "L-001")
        self.assertEqual(o2["id"], "L-002")
        self.assertEqual(o1["stato"], "piano")
        self.assertEqual(o1["firma"], "")
        self.assertTrue((self.vault / "03 - Ordini di lavoro" / "L-001.md").is_file())

        letto = carica_ordine(self.vault, "l-001")
        self.assertEqual(letto["id"], "L-001")
        self.assertEqual(letto["titolo"], "Landing X")
        self.assertEqual(letto["destinazione"], "utente/sito")
        self.assertEqual(letto["piano"], "Crea index.html")
        self.assertEqual(letto["stato"], "piano")

    def test_destinazione_da_nota_commessa(self):
        commesse = self.vault / "01 - Commesse"
        commesse.mkdir(parents=True)
        (commesse / "Landing.md").write_text(
            "---\nesecuzione: cantiere\nrepo: utente/landing-page\n---\n# Landing\n",
            encoding="utf-8",
        )
        o = crea_ordine(self.vault, "capo-sviluppo", "Fai hero section",
                        commessa="Landing", adesso=self.ora)
        self.assertEqual(o["esecuzione"], "cantiere")
        self.assertEqual(o["destinazione"], "utente/landing-page")

    def test_firma_hmac_valida_e_rifiuto_manomissioni(self):
        o = crea_ordine(self.vault, "capo-sviluppo", "Piano originale",
                        destinazione="utente/repo", adesso=self.ora)
        self.assertFalse(verifica_firma(o, self.chiave))

        approvato = approva_ordine(self.vault, "L-001", self.chiave)
        self.assertEqual(approvato["stato"], "approvato")
        self.assertTrue(verifica_firma(approvato, self.chiave))
        self.assertEqual(
            approvato["firma"],
            calcola_firma(self.chiave, "L-001", "utente/repo", "Piano originale"),
        )

        # Manomissione del piano sul file nel vault (es. da parte di un agente o archivista)
        percorso = self.vault / "03 - Ordini di lavoro" / "L-001.md"
        testo_file = percorso.read_text(encoding="utf-8")
        percorso.write_text(testo_file.replace("Piano originale", "Piano manomesso rm -rf"), encoding="utf-8")

        manomesso = carica_ordine(self.vault, "L-001")
        self.assertFalse(verifica_firma(manomesso, self.chiave))
        with self.assertRaises(FirmaNonValida):
            avvia_ordine(self.vault, "L-001", self.chiave)
        # L'ordine con firma non valida viene marcato come errore
        self.assertEqual(carica_ordine(self.vault, "L-001")["stato"], "errore")

    def test_transizioni_stato_ammesse_e_vietate(self):
        crea_ordine(self.vault, "celia-cold-email", "Piano A", destinazione="utente/a", adesso=self.ora)
        # Non si può avviare o completare direttamente da 'piano'
        with self.assertRaises(StatoNonValido):
            cambia_stato(self.vault, "L-001", "in-corso")
        with self.assertRaises(StatoNonValido):
            cambia_stato(self.vault, "L-001", "fatto")

        approva_ordine(self.vault, "L-001", self.chiave)
        in_corso = avvia_ordine(self.vault, "L-001", self.chiave)
        self.assertEqual(in_corso["stato"], "in-corso")

        fatto = cambia_stato(self.vault, "L-001", "fatto", esito="Deploy completato")
        self.assertEqual(fatto["stato"], "fatto")
        self.assertEqual(fatto["esito"], "Deploy completato")
        # Anche dopo aver aggiunto l'esito, la firma sul piano resta integra
        self.assertTrue(verifica_firma(fatto, self.chiave))

        # Da 'fatto' non si può più annullare o approvare, ma si può pubblicare
        with self.assertRaises(StatoNonValido):
            annulla_ordine(self.vault, "L-001")
        with self.assertRaises(StatoNonValido):
            approva_ordine(self.vault, "L-001", self.chiave)

        pub = pubblica_ordine(self.vault, "L-001", nota="Pubblicato su main — https://sito.it")
        self.assertEqual(pub["stato"], "pubblicato")
        self.assertIn("Deploy completato", pub["esito"])
        self.assertIn("Pubblicato su main — https://sito.it", pub["esito"])
        self.assertTrue(verifica_firma(pub, self.chiave))
        with self.assertRaises(StatoNonValido):
            pubblica_ordine(self.vault, "L-001")

    def test_url_produzione_commessa(self):
        commesse = self.vault / "01 - Commesse"
        commesse.mkdir(parents=True)
        (commesse / "Landing.md").write_text(
            "---\nesecuzione: cantiere\nrepo: utente/landing\nurl: https://landing.utente.dev\n---\n# Landing\n",
            encoding="utf-8",
        )
        self.assertEqual(url_produzione_commessa(self.vault, "Landing"), "https://landing.utente.dev")
        self.assertEqual(url_produzione_commessa(self.vault, "Assente"), "")

    def test_scadenza_dopo_24_ore(self):
        crea_ordine(self.vault, "celia-cold-email", "Piano vecchio", adesso=self.ora)
        crea_ordine(self.vault, "celia-cold-email", "Piano recente", adesso=self.ora + timedelta(hours=10))
        crea_ordine(self.vault, "celia-cold-email", "Piano approvato", adesso=self.ora)
        approva_ordine(self.vault, "L-003", self.chiave)

        dopo_25h = self.ora + timedelta(hours=25)
        scaduti = scadi_ordini(self.vault, adesso=dopo_25h)
        self.assertEqual([o["id"] for o in scaduti], ["L-001"])
        self.assertEqual(carica_ordine(self.vault, "L-001")["stato"], "annullato")
        self.assertEqual(carica_ordine(self.vault, "L-002")["stato"], "piano")
        self.assertEqual(carica_ordine(self.vault, "L-003")["stato"], "approvato")

    def test_config_e_env_esempio_contengono_ordini_hmac_key(self):
        env_esempio = (Path(__file__).resolve().parent.parent / "deploy" / "env.esempio").read_text(encoding="utf-8")
        self.assertIn("ORDINI_HMAC_KEY=", env_esempio)

        env = Path(self._tmp.name) / ".env"
        env.write_text(
            "TELEGRAM_TOKEN=t\nTELEGRAM_USER_ID=5\nGEMINI_API_KEY=g\nORDINI_HMAC_KEY=chiave123\n",
            encoding="utf-8",
        )
        cfg = carica_config(env)
        self.assertEqual(cfg.ordini_hmac_key, "chiave123")


class TestComandiOrdiniUfficio(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.ora = datetime(2026, 11, 11, 12, 0, 0)
        self.cfg = Config("t", 5, "k", "gemini-2.5-flash", "claude", base, ordini_hmac_key="segreto-hmac")
        (base / "app" / "prompts").mkdir(parents=True)
        (base / "stato").mkdir(parents=True, exist_ok=True)
        for a in AGENTI_UFF:
            (base / "app" / "prompts" / f"{a['id']}.md").write_text(f"PROMPT {a['id']}", encoding="utf-8")
        self.org = Organigramma({"piani": PIANI_UFF, "agenti": AGENTI_UFF})
        self.cron = Cronologia(base / "stato" / "chat")
        self.inviati = []
        self.coda = CodaClaude()
        self.uff = Ufficio(
            self.org,
            self.cron,
            self.coda,
            Motori(lambda *a: "ok", lambda *a: "ok"),
            lambda c, t, th: self.inviati.append((c, t, th)),
            self.cfg,
            adesso=lambda: self.ora,
            in_parallelo=lambda f, *a: f(*a),
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_ordini_approva_annulla_da_utente_autorizzato(self):
        self.uff.gestisci(5, "/ordini")
        self.assertEqual(self.inviati[-1][1], "Nessun ordine di lavoro aperto.")

        crea_ordine(self.cfg.vault, "celia-cold-email", "Scrivi pagina",
                    titolo="Landing", destinazione="utente/landing", adesso=self.ora)
        self.uff.gestisci(5, "/ordini")
        self.assertIn("L-001", self.inviati[-1][1])
        self.assertIn("piano", self.inviati[-1][1])

        self.uff.gestisci(5, "/approva L-001")
        self.assertIn("L-001", self.inviati[-1][1])
        self.assertIn("approvato", self.inviati[-1][1].lower())
        ordine = carica_ordine(self.cfg.vault, "L-001")
        self.assertEqual(ordine["stato"], "approvato")
        self.assertTrue(verifica_firma(ordine, "segreto-hmac"))

        self.uff.gestisci(5, "/annulla L-001")
        self.assertIn("annullato", self.inviati[-1][1].lower())
        self.assertEqual(carica_ordine(self.cfg.vault, "L-001")["stato"], "annullato")
        self.assertEqual(elenca_ordini(self.cfg.vault, solo_aperti=True), [])

    def test_comando_ordine_da_telegram_solo_privato(self):
        sito = self.cfg.vault / "progetti" / "landing-x"
        sito.mkdir(parents=True)
        self.uff.gestisci(99, "/ordine progetti/landing-x | Esegui npm test")
        self.assertIn("riservato", self.inviati[-1][1].lower())
        self.assertEqual(elenca_ordini(self.cfg.vault), [])

        self.uff.gestisci(5, "/ordine progetti/landing-x | Esegui npm test e build")
        self.assertIn("L-001", self.inviati[-1][1])
        self.assertIn("/approva L-001", self.inviati[-1][1])
        o = carica_ordine(self.cfg.vault, "L-001")
        self.assertEqual(o["esecuzione"], "pc")
        self.assertEqual(o["destinazione"], "progetti/landing-x")
        self.assertEqual(o["piano"], "Esegui npm test e build")

        self.uff.gestisci(5, "/ordine @capo-sviluppo utente/repo | Aggiorna hero")
        o2 = carica_ordine(self.cfg.vault, "L-002")
        self.assertEqual(o2["esecuzione"], "cantiere")
        self.assertEqual(o2["agente"], "capo-sviluppo")
        self.assertEqual(o2["destinazione"], "utente/repo")

    def test_comandi_ordini_vietati_fuori_da_chat_privata_utente(self):
        crea_ordine(self.cfg.vault, "celia-cold-email", "Scrivi pagina",
                    titolo="Landing", destinazione="utente/landing", adesso=self.ora)

        # Chat ID diverso da TELEGRAM_USER_ID (es. CHAT_WEB=0 o altro utente=99)
        for cid in (0, 99, -100123):
            self.uff.gestisci(cid, "/approva L-001")
            self.uff.gestisci(cid, "/annulla L-001")
            self.uff.gestisci(cid, "/ordini")
        self.assertEqual(carica_ordine(self.cfg.vault, "L-001")["stato"], "piano")

        # Da un gruppo Telegram collegato (anche se l'utente ha passato il filtro)
        gruppi = Gruppi(self.cfg.stato / "gruppi.json")
        gruppi.collega(-100555, None, "direzione", None)
        self.uff.gestisci_gruppo(-100555, None, "/approva L-001", gruppi)
        self.uff.gestisci_gruppo(-100555, None, "/annulla L-001", gruppi)
        self.uff.gestisci_gruppo(-100555, None, "/ordini", gruppi)
        self.assertEqual(carica_ordine(self.cfg.vault, "L-001")["stato"], "piano")

    def test_approva_rifiuta_ordine_scaduto(self):
        crea_ordine(self.cfg.vault, "celia-cold-email", "Scrivi pagina", adesso=self.ora)
        self.ora = self.ora + timedelta(hours=25)
        self.uff.gestisci(5, "/approva L-001")
        self.assertEqual(carica_ordine(self.cfg.vault, "L-001")["stato"], "annullato")
        self.assertIn("scaduto", self.inviati[-1][1].lower())



if __name__ == "__main__":
    unittest.main()
