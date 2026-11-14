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
from portineria.cantiere import (
    TIMEOUT_CANTIERE,
    TimeoutCantiere,
    accoda_cantiere,
    argomenti_cantiere,
    branch_ordine,
    esegui_cantiere,
    percorso_repo,
    prompt_cantiere,
    verifica_branch,
)
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
        o1 = crea_ordine(self.vault, "engineering-software-architect", "Crea index.html",
                         titolo="Landing X", commessa="Sito", destinazione="tommy/sito", adesso=self.ora)
        o2 = crea_ordine(self.vault, "celia-cold-email", "Aggiorna copy", adesso=self.ora)
        self.assertEqual(o1["id"], "L-001")
        self.assertEqual(o2["id"], "L-002")
        self.assertEqual(o1["stato"], "piano")
        self.assertEqual(o1["firma"], "")
        self.assertTrue((self.vault / "03 - Ordini di lavoro" / "L-001.md").is_file())

        letto = carica_ordine(self.vault, "l-001")
        self.assertEqual(letto["id"], "L-001")
        self.assertEqual(letto["titolo"], "Landing X")
        self.assertEqual(letto["destinazione"], "tommy/sito")
        self.assertEqual(letto["piano"], "Crea index.html")
        self.assertEqual(letto["stato"], "piano")

    def test_destinazione_da_nota_commessa(self):
        commesse = self.vault / "01 - Commesse"
        commesse.mkdir(parents=True)
        (commesse / "Landing.md").write_text(
            "---\nesecuzione: cantiere\nrepo: tommy/landing-page\n---\n# Landing\n",
            encoding="utf-8",
        )
        o = crea_ordine(self.vault, "engineering-software-architect", "Fai hero section",
                        commessa="Landing", adesso=self.ora)
        self.assertEqual(o["esecuzione"], "cantiere")
        self.assertEqual(o["destinazione"], "tommy/landing-page")

    def test_firma_hmac_valida_e_rifiuto_manomissioni(self):
        o = crea_ordine(self.vault, "engineering-software-architect", "Piano originale",
                        destinazione="tommy/repo", adesso=self.ora)
        self.assertFalse(verifica_firma(o, self.chiave))

        approvato = approva_ordine(self.vault, "L-001", self.chiave)
        self.assertEqual(approvato["stato"], "approvato")
        self.assertTrue(verifica_firma(approvato, self.chiave))
        self.assertEqual(
            approvato["firma"],
            calcola_firma(self.chiave, "L-001", "tommy/repo", "Piano originale"),
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
        crea_ordine(self.vault, "celia-cold-email", "Piano A", destinazione="tommy/a", adesso=self.ora)
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
            "---\nesecuzione: cantiere\nrepo: tommy/landing\nurl: https://landing.tommy.dev\n---\n# Landing\n",
            encoding="utf-8",
        )
        self.assertEqual(url_produzione_commessa(self.vault, "Landing"), "https://landing.tommy.dev")
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
                    titolo="Landing", destinazione="tommy/landing", adesso=self.ora)
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
        sito = self.cfg.vault / "my-office" / "landing-x"
        sito.mkdir(parents=True)
        self.uff.gestisci(99, "/ordine my-office/landing-x | Esegui npm test")
        self.assertIn("riservato", self.inviati[-1][1].lower())
        self.assertEqual(elenca_ordini(self.cfg.vault), [])

        self.uff.gestisci(5, "/ordine my-office/landing-x | Esegui npm test e build")
        self.assertIn("L-001", self.inviati[-1][1])
        self.assertIn("/approva L-001", self.inviati[-1][1])
        o = carica_ordine(self.cfg.vault, "L-001")
        self.assertEqual(o["esecuzione"], "pc")
        self.assertEqual(o["destinazione"], "my-office/landing-x")
        self.assertEqual(o["piano"], "Esegui npm test e build")

        self.uff.gestisci(5, "/ordine @engineering-software-architect tommy/repo | Aggiorna hero")
        o2 = carica_ordine(self.cfg.vault, "L-002")
        self.assertEqual(o2["esecuzione"], "cantiere")
        self.assertEqual(o2["agente"], "engineering-software-architect")
        self.assertEqual(o2["destinazione"], "tommy/repo")

    def test_comandi_ordini_vietati_fuori_da_chat_privata_utente(self):
        crea_ordine(self.cfg.vault, "celia-cold-email", "Scrivi pagina",
                    titolo="Landing", destinazione="tommy/landing", adesso=self.ora)

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

    def test_approva_avvia_cantiere_quando_collegato(self):
        import subprocess
        from portineria.__main__ import collega_cantiere

        collega_cantiere(
            self.cfg,
            self.coda,
            self.uff,
            run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="Branch pronto", stderr=""),
        )
        crea_ordine(self.cfg.vault, "engineering-software-architect", "Landing",
                    destinazione="tommy/sito", adesso=self.ora)
        self.uff.gestisci(5, "/approva L-001")
        aspetta(lambda: carica_ordine(self.cfg.vault, "L-001")["stato"] == "fatto")
        self.assertTrue(any("ufficio/L-001" in msg for _, msg, _ in self.inviati))

    def test_approva_pc_non_avvia_cantiere_e_coda_piena_non_fallisce(self):
        from portineria.coda import CodaPiena

        chiamate = []
        self.uff.avvia_cantiere = lambda oid: chiamate.append(oid) or 0
        crea_ordine(self.cfg.vault, "engineering-software-architect", "Sul PC",
                    esecuzione="pc", destinazione="03-projects/app", adesso=self.ora)
        self.uff.gestisci(5, "/approva L-001")
        self.assertEqual(chiamate, [])
        self.assertEqual(carica_ordine(self.cfg.vault, "L-001")["stato"], "approvato")

        def coda_piena(_oid):
            raise CodaPiena()

        self.uff.avvia_cantiere = coda_piena
        crea_ordine(self.cfg.vault, "engineering-software-architect", "Sul Cantiere",
                    destinazione="tommy/sito", adesso=self.ora)
        self.uff.gestisci(5, "/approva L-002")
        self.assertEqual(carica_ordine(self.cfg.vault, "L-002")["stato"], "approvato")
        self.assertIn("coda", self.inviati[-1][1].lower())

    def test_pubblica_ordine_solo_da_fatto_e_branch_valido(self):
        import json
        from unittest.mock import patch

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

        self.uff.cfg.github_token = "ghp_pubblica"
        crea_ordine(self.cfg.vault, "engineering-software-architect", "Landing",
                    destinazione="tommy/sito", adesso=self.ora)

        # Prima di essere 'fatto' viene rifiutato senza chiamare GitHub
        with patch("urllib.request.urlopen") as m_open:
            self.uff.gestisci(5, "/pubblica L-001")
            m_open.assert_not_called()
        self.assertIn("fatto", self.inviati[-1][1].lower())

        # Approvato + avviato + completato su un branch errato / senza branch ufficio/L-001
        approva_ordine(self.cfg.vault, "L-001", "segreto-hmac")
        avvia_ordine(self.cfg.vault, "L-001", "segreto-hmac")
        cambia_stato(self.cfg.vault, "L-001", "fatto", esito="Completato senza branch")
        with patch("urllib.request.urlopen") as m_open:
            self.uff.gestisci(5, "/pubblica L-001")
            m_open.assert_not_called()
        self.assertIn("ufficio/L-001", self.inviati[-1][1])

        # Ordine L-002 fatto su ufficio/L-002 -> merge su main
        crea_ordine(self.cfg.vault, "engineering-software-architect", "Landing v2",
                    destinazione="tommy/sito", adesso=self.ora)
        approva_ordine(self.cfg.vault, "L-002", "segreto-hmac")
        avvia_ordine(self.cfg.vault, "L-002", "segreto-hmac")
        cambia_stato(self.cfg.vault, "L-002", "fatto", esito="Branch `ufficio/L-002`\nTutto pronto")

        chiamate_gh = []

        def finto_urlopen(req, timeout=15):
            chiamate_gh.append((req.get_method(), req.full_url, req.data))
            if req.full_url == "https://api.github.com/repos/tommy/sito/merges":
                return _Resp(201, {"sha": "abc1234567"})
            if req.full_url == "https://api.github.com/repos/tommy/sito":
                return _Resp(200, {"homepage": "https://sito-prod.vercel.app"})
            raise AssertionError(req.full_url)

        with patch("urllib.request.urlopen", side_effect=finto_urlopen):
            self.uff.gestisci(5, "/pubblica L-002")

        self.assertEqual(chiamate_gh[0][0], "POST")
        self.assertEqual(chiamate_gh[0][1], "https://api.github.com/repos/tommy/sito/merges")
        self.assertEqual(json.loads(chiamate_gh[0][2].decode("utf-8")), {
            "base": "main",
            "head": "ufficio/L-002",
            "commit_message": "Merge ufficio/L-002",
        })
        finale = carica_ordine(self.cfg.vault, "L-002")
        self.assertEqual(finale["stato"], "pubblicato")
        self.assertIn("https://sito-prod.vercel.app", finale["esito"])
        self.assertIn("https://sito-prod.vercel.app", self.inviati[-1][1])
        self.assertIn("pubblicato", self.inviati[-1][1].lower())

    def test_pubblica_vietato_fuori_da_chat_privata_utente(self):
        from unittest.mock import patch

        self.uff.cfg.github_token = "ghp_pubblica"
        crea_ordine(self.cfg.vault, "engineering-software-architect", "Landing",
                    destinazione="tommy/sito", adesso=self.ora)
        approva_ordine(self.cfg.vault, "L-001", "segreto-hmac")
        avvia_ordine(self.cfg.vault, "L-001", "segreto-hmac")
        cambia_stato(self.cfg.vault, "L-001", "fatto", esito="Branch `ufficio/L-001`")

        gruppi = Gruppi(self.cfg.stato / "gruppi.json")
        gruppi.collega(-100555, None, "direzione", None)
        with patch("urllib.request.urlopen") as m_open:
            self.uff.gestisci(99, "/pubblica L-001")
            self.uff.gestisci(5, "/pubblica L-001", thread_id=42)
            self.uff.gestisci_gruppo(-100555, None, "/pubblica L-001", gruppi)
            m_open.assert_not_called()
        self.assertEqual(carica_ordine(self.cfg.vault, "L-001")["stato"], "fatto")

    def test_pubblica_conflitto_github_non_cambia_stato(self):
        import io
        import json
        import urllib.error
        from unittest.mock import patch

        self.uff.cfg.github_token = "ghp_pubblica"
        crea_ordine(self.cfg.vault, "engineering-software-architect", "Landing",
                    destinazione="tommy/sito", adesso=self.ora)
        approva_ordine(self.cfg.vault, "L-001", "segreto-hmac")
        avvia_ordine(self.cfg.vault, "L-001", "segreto-hmac")
        cambia_stato(self.cfg.vault, "L-001", "fatto", esito="Branch `ufficio/L-001`")

        def finto_conflitto(req, timeout=15):
            raise urllib.error.HTTPError(
                req.full_url, 409, "Conflict", {}, io.BytesIO(json.dumps({"message": "Merge conflict"}).encode("utf-8"))
            )

        with patch("urllib.request.urlopen", side_effect=finto_conflitto):
            self.uff.gestisci(5, "/pubblica L-001")

        self.assertEqual(carica_ordine(self.cfg.vault, "L-001")["stato"], "fatto")
        self.assertIn("Merge conflict", self.inviati[-1][1])


class TestCantiere(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.cfg = Config("t", 5, "k", "gemini-2.5-flash", "claude", base,
                          claude_token="tok-oauth", ordini_hmac_key="segreto-cantiere")
        self.ora = datetime(2026, 11, 11, 14, 0, 0)

    def tearDown(self):
        self._tmp.cleanup()

    def test_comando_isolato_sudo_cantiere_e_blocchi(self):
        crea_ordine(self.cfg.vault, "engineering-software-architect", "Aggiorna hero",
                    destinazione="tommy/sito-vetrina", adesso=self.ora)
        ordine = approva_ordine(self.cfg.vault, "L-001", self.cfg.ordini_hmac_key)

        cmd = argomenti_cantiere(ordine)
        self.assertEqual(cmd[:5], ["sudo", "-n", "-u", "cantiere", "--preserve-env=CLAUDE_CODE_OAUTH_TOKEN"])
        self.assertIn("--permission-mode", cmd)
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "acceptEdits")

        disallowed = cmd[cmd.index("--disallowedTools") + 1:]
        for vietato in ("next build", "npm run build", "docker", "main", "/home/tommy"):
            self.assertTrue(any(vietato in regola for regola in disallowed), f"manca blocco per {vietato}")

        testo_prompt = prompt_cantiere(ordine)
        self.assertIn("ufficio/L-001", testo_prompt)
        self.assertIn("mai su main", testo_prompt.lower())

    def test_branch_dedicato_e_divieto_main(self):
        self.assertEqual(branch_ordine("L-042"), "ufficio/L-042")
        verifica_branch("ufficio/L-042")
        for invalido in ("main", "master", "ufficio/main", "feature/x", "../L-001"):
            with self.assertRaises(ValueError):
                verifica_branch(invalido)
        self.assertEqual(percorso_repo("tommy/sito").as_posix(), "/home/cantiere/repo/sito")
        with self.assertRaises(ValueError):
            percorso_repo("../../etc")

    def test_timeout_15_minuti_propone_pc(self):
        import subprocess
        self.assertEqual(TIMEOUT_CANTIERE, 900)
        crea_ordine(self.cfg.vault, "engineering-software-architect", "Aggiorna hero",
                    destinazione="tommy/sito", adesso=self.ora)
        ordine = approva_ordine(self.cfg.vault, "L-001", self.cfg.ordini_hmac_key)

        def finto_run(cmd, **kw):
            self.assertEqual(kw["timeout"], 900)
            raise subprocess.TimeoutExpired(cmd, 900)

        with self.assertRaises(TimeoutCantiere) as ctx:
            esegui_cantiere(ordine, self.cfg, run=finto_run)
        self.assertIn("PC", str(ctx.exception))

    def test_rifiuta_ordine_senza_firma_valida(self):
        ordine = crea_ordine(self.cfg.vault, "engineering-software-architect", "Aggiorna hero",
                             destinazione="tommy/sito", adesso=self.ora)
        chiamate = []
        with self.assertRaises(FirmaNonValida):
            esegui_cantiere(ordine, self.cfg, run=lambda *a, **kw: chiamate.append(1))
        self.assertEqual(chiamate, [])

    def test_accoda_cantiere_usa_priorita_normale_e_aggiorna_stato(self):
        import subprocess
        crea_ordine(self.cfg.vault, "engineering-software-architect", "Crea pagina contatti",
                    destinazione="tommy/sito", adesso=self.ora)
        approva_ordine(self.cfg.vault, "L-001", self.cfg.ordini_hmac_key)

        priorita_viste = []
        coda_vera = CodaClaude()
        accoda_orig = coda_vera.accoda

        def spia_accoda(etichetta, lavoro, al_termine, priorita="alta"):
            priorita_viste.append((etichetta, priorita))
            return accoda_orig(etichetta, lavoro, al_termine, priorita=priorita)

        coda_vera.accoda = spia_accoda
        messaggi = []

        def finto_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout="Commit fatto su ufficio/L-001", stderr="")

        accoda_cantiere(self.cfg.vault, "L-001", coda_vera, self.cfg, messaggi.append, run=finto_run)
        aspetta(lambda: len(messaggi) == 1)

        self.assertEqual(priorita_viste, [("cantiere:L-001", "normale")])
        finale = carica_ordine(self.cfg.vault, "L-001")
        self.assertEqual(finale["stato"], "fatto")
        self.assertIn("ufficio/L-001", finale["esito"])
        self.assertIn("ufficio/L-001", messaggi[0])

    def test_accoda_cantiere_avvia_polling_anteprima_se_github_configurato(self):
        import json
        import subprocess
        from unittest.mock import patch

        class _Resp:
            def __init__(self, dati):
                self.status = 200
                self._raw = json.dumps(dati).encode("utf-8")

            def read(self):
                return self._raw

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        cfg_gh = Config(
            "t", 5, "k", "gemini-3.8-flash", "claude", self.cfg.base,
            claude_token="tok-oauth", ordini_hmac_key="segreto-cantiere", github_token="ghp_test"
        )
        crea_ordine(cfg_gh.vault, "engineering-software-architect", "Crea landing",
                    destinazione="tommy/sito", adesso=self.ora)
        approva_ordine(cfg_gh.vault, "L-001", cfg_gh.ordini_hmac_key)

        coda_vera = CodaClaude()
        messaggi = []

        def finto_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout="Push completato su ufficio/L-001", stderr="")

        def finto_urlopen(req, timeout=15):
            if req.full_url.endswith("/commits/ufficio%2FL-001"):
                return _Resp({"sha": "abc777"})
            if req.full_url.endswith("/commits/abc777/status"):
                return _Resp({
                    "state": "success",
                    "statuses": [{"context": "Vercel", "state": "success", "target_url": "https://sito-l001.vercel.app"}],
                })
            raise AssertionError(f"URL inatteso: {req.full_url}")

        with patch("urllib.request.urlopen", side_effect=finto_urlopen):
            accoda_cantiere(cfg_gh.vault, "L-001", coda_vera, cfg_gh, messaggi.append, run=finto_run)
            aspetta(lambda: any("🚀 Anteprima pronta per L-001:" in m for m in messaggi), secondi=3.0)

        self.assertTrue(
            any(m == "🚀 Anteprima pronta per L-001: https://sito-l001.vercel.app" for m in messaggi),
            messaggi,
        )


if __name__ == "__main__":
    unittest.main()
