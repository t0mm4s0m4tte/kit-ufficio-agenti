import json
from pathlib import Path
import tempfile
import unittest

from portineria.chat import Cronologia
from portineria.coda import CodaClaude
from portineria.config import Config
from portineria.motori import ErroreMotore
from portineria.organigramma import Organigramma
from portineria.staffetta import scegli_revisore
from portineria.ufficio import Motori, Ufficio
from tests.test_coda import aspetta
from tests.test_router import PIANI, agente

PIANI_ST = PIANI + [
    {"id": "engineering", "numero": 6, "nome": "Engineering", "emoji": "💻", "colore": "",
     "capo": "engineering-software-architect", "team": []},
]

AGENTI_ST = [
    agente("direttore", "Direttore", "direzione", "direttore", motore="claude"),
    agente("marketing-growth-hacker", "Growth Hacker", "marketing", "capo", motore="claude"),
    agente("marketing-copy", "Copywriter", "marketing", descrizione="Bozze landing e newsletter"),
    agente("sales-deal-strategist", "Deal Strategist", "vendite", "capo", motore="claude"),
    agente("celia-cold-email", "Celia", "vendite", descrizione="Cold email specialist"),
    agente("engineering-software-architect", "Software Architect", "engineering", "capo", motore="claude"),
    agente("engineering-backend-architect", "Backend Architect", "engineering", motore="claude"),
    agente("testing-reality-checker", "Reality Checker", "engineering", motore="gemini",
           descrizione="QA e verifica finale"),
    agente("security-architect", "Security Architect", "engineering", motore="claude",
           descrizione="Sicurezza, permessi, autenticazione"),
]


class TestStaffetta(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.cfg = Config("t", 5, "k", "gemini-2.5-flash", "claude", self.base)
        (self.base / "app" / "prompts").mkdir(parents=True)
        for a in AGENTI_ST:
            (self.base / "app" / "prompts" / f"{a['id']}.md").write_text(f"PROMPT {a['id']}", encoding="utf-8")
        self.org = Organigramma({"piani": PIANI_ST, "agenti": AGENTI_ST})
        self.cron = Cronologia(self.base / "stato" / "chat")
        self.coda = CodaClaude()
        self.inviati = []
        self.chiamate = []
        self.risposte = {}
        self.uff = Ufficio(
            self.org,
            self.cron,
            self.coda,
            Motori(self._gemini, self._claude),
            lambda c, t, th: self.inviati.append((c, t, th)),
            self.cfg,
            in_parallelo=lambda f, *a: f(*a),
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _risposta(self, agente_id, testo):
        self.chiamate.append((agente_id, testo))
        coda = self.risposte.get(agente_id)
        if coda:
            val = coda.pop(0)
            if isinstance(val, Exception):
                raise val
            return val
        return f"Risposta {agente_id}"

    def _gemini(self, sistema, storia, testo):
        return self._risposta(sistema.removeprefix("PROMPT ").strip(), testo)

    def _claude(self, prompt_path, storia, testo):
        return self._risposta(Path(prompt_path).stem, testo)

    def _attendi(self):
        aspetta(lambda: self.coda.stato() == {"in_corso": None, "in_attesa": [], "attesa_ram": False})

    def test_scegli_revisore_coerente_per_piano_e_tema(self):
        backend = self.org.get("engineering-backend-architect")
        self.assertEqual(
            scegli_revisore(self.org, backend, "aggiungi endpoint ordini")["id"],
            "testing-reality-checker",
        )
        self.assertEqual(
            scegli_revisore(self.org, backend, "controlla sicurezza token HMAC e permessi")["id"],
            "security-architect",
        )
        # Se l'autore è già testing-reality-checker non rivede sé stesso
        rc = self.org.get("testing-reality-checker")
        self.assertNotEqual(scegli_revisore(self.org, rc, "verifica")["id"], rc["id"])

        # Per gli altri piani sceglie il capo del piano; se autore è il capo, sale al Direttore
        celia = self.org.get("celia-cold-email")
        self.assertEqual(scegli_revisore(self.org, celia, "bozza lancio")["id"], "sales-deal-strategist")
        capo_vendite = self.org.get("sales-deal-strategist")
        self.assertEqual(scegli_revisore(self.org, capo_vendite, "bozza")["id"], "direttore")

    def test_staffetta_via_chiedi_restituisce_solo_versione_finale_revisionata(self):
        self.risposte["engineering-backend-architect"] = ["Bozza endpoint /api/ordini"]
        self.risposte["testing-reality-checker"] = ["Versione finale verificata: /api/ordini OK"]

        self.uff.chiedi("engineering-backend-architect", "implementa /api/ordini", 5, None, revisione=True)
        self._attendi()

        self.assertEqual([cid for cid, _ in self.chiamate],
                         ["engineering-backend-architect", "testing-reality-checker"])
        self.assertIn("Bozza endpoint /api/ordini", self.chiamate[1][1])
        # A Tommy arriva SOLO la versione finale dopo la revisione (non la bozza intermedia)
        self.assertEqual(len(self.inviati), 1)
        self.assertIn("Versione finale verificata: /api/ordini OK", self.inviati[0][1])
        self.assertIn("Reality Checker", self.inviati[0][1])
        self.assertEqual(self.uff.flussi_attivi(), [])

    def test_direttore_puo_attivare_revisione_su_delega(self):
        self.risposte["direttore"] = [json.dumps({
            "agente": "celia-cold-email",
            "brief": "Scrivi sequenza lancio Q4",
            "revisione": True,
        })]
        self.risposte["celia-cold-email"] = ["Bozza sequenza Q4"]
        self.risposte["sales-deal-strategist"] = ["Sequenza Q4 approvata e rifinita"]

        self.uff.gestisci(5, "Prepara la campagna lancio importante per i clienti")
        self._attendi()

        self.assertEqual(
            [cid for cid, _ in self.chiamate],
            ["direttore", "celia-cold-email", "sales-deal-strategist"],
        )
        self.assertIn("Bozza sequenza Q4", self.chiamate[2][1])
        self.assertIn("Passo a Celia", self.inviati[0][1])
        self.assertIn("Sequenza Q4 approvata e rifinita", self.inviati[-1][1])
        self.assertEqual(len(self.inviati), 2)

    def test_comando_rivedi_con_menzione_e_fallback_se_revisore_fallisce(self):
        self.risposte["marketing-copy"] = ["Bozza landing page"]
        self.risposte["marketing-growth-hacker"] = [ErroreMotore("claude occupato")]

        self.uff.gestisci(5, "/rivedi @marketing-copy bozza landing")
        self._attendi()

        self.assertEqual([cid for cid, _ in self.chiamate], ["marketing-copy", "marketing-growth-hacker"])
        # Nonostante il fallimento del revisore, Tommy riceve la bozza con nota di fallback
        testo_totale = "\n".join(t for _, t, _ in self.inviati)
        self.assertIn("Bozza landing page", testo_totale)
        self.assertEqual(self.uff.flussi_attivi(), [])

    def test_comando_rivedi_senza_menzione_passa_al_direttore_con_revisione(self):
        self.risposte["direttore"] = [json.dumps({
            "agente": "engineering-backend-architect",
            "brief": "Controlla query auth",
        })]
        self.risposte["engineering-backend-architect"] = ["Bozza patch auth"]
        self.risposte["security-architect"] = ["Patch auth verificata contro bypass"]

        self.uff.gestisci(5, "/rivedi controlla sicurezza token auth")
        self._attendi()

        self.assertEqual(
            [cid for cid, _ in self.chiamate],
            ["direttore", "engineering-backend-architect", "security-architect"],
        )
        self.assertIn("Patch auth verificata contro bypass", self.inviati[-1][1])


if __name__ == "__main__":
    unittest.main()
