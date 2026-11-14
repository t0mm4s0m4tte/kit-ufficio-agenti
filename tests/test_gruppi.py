import json
import unittest

from portineria.gruppi import Gruppi, membri_stanza, responsabile
from portineria.organigramma import Organigramma
from tests.test_router import PIANI, agente
from tests.test_ufficio import BaseUfficio

AGENTI_G = [
    agente("direttore", "Direttore", "direzione", "direttore", motore="claude"),
    agente("marketing-growth-hacker", "Growth Hacker", "marketing", "capo", motore="claude"),
    agente("marketing-seo-specialist", "SEO Specialist", "marketing", "comanager", team="seo", motore="claude"),
    agente("marketing-blog", "Blog", "marketing"),
    agente("sales-deal-strategist", "Deal Strategist", "vendite", "capo", motore="claude"),
] + [agente(f"seo-{i}", f"Seo {i}", "marketing", team="seo") for i in range(1, 6)]


class TestGruppi(BaseUfficio):
    def setUp(self):
        super().setUp()
        self.org = Organigramma({"piani": PIANI, "agenti": AGENTI_G})
        for a in AGENTI_G:
            (self.cfg.app / "prompts" / f"{a['id']}.md").write_text("P", encoding="utf-8")
        self.uff = self.crea_ufficio(self.coda)
        self.g = Gruppi(self.cfg.base / "gruppi.json")

    def test_collega_e_stanza_con_fallback_al_gruppo(self):
        g = Gruppi(self.cfg.base / "g2.json")
        g.collega(-100, None, "marketing", None)
        g.collega(-100, 7, "marketing", "seo")
        self.assertEqual(g.stanza(-100, 7)["team"], "seo")
        self.assertEqual(g.stanza(-100, 9)["piano"], "marketing")  # argomento non collegato → gruppo
        self.assertIsNone(g.stanza(-200, None))
        self.assertEqual(Gruppi(self.cfg.base / "g2.json").stanza(-100, 7)["team"], "seo")  # persistito
        self.assertTrue(g.scollega(-100, 7))
        self.assertFalse(g.scollega(-100, 7))
        self.assertEqual(g.stanza(-100, 7)["team"], None)

    def test_file_corrotto_vuoto(self):
        p = self.cfg.base / "rotto.json"
        p.write_text("{rotto", encoding="utf-8")
        self.assertIsNone(Gruppi(p).stanza(-1, None))

    def test_responsabile_e_membri(self):
        self.assertEqual(responsabile(self.org, {"piano": "direzione", "team": None}), "direttore")
        self.assertEqual(responsabile(self.org, {"piano": "marketing", "team": "seo"}), "marketing-seo-specialist")
        self.assertEqual(responsabile(self.org, {"piano": "marketing", "team": None}), "marketing-growth-hacker")
        self.assertEqual([a["id"] for a in membri_stanza(self.org, {"piano": "direzione", "team": None})],
                         ["marketing-growth-hacker", "sales-deal-strategist"])
        self.assertNotIn("marketing-seo-specialist",
                         [a["id"] for a in membri_stanza(self.org, {"piano": "marketing", "team": "seo"})])

    def test_collega_da_chat(self):
        self.uff.gestisci_gruppo(-100, 7, "/collega marketing seo", self.g)
        self.assertEqual(self.inviati[-1],
                         (-100, "Collegato: 📣 Marketing & Social · team seo. Qui risponde SEO Specialist.", 7))
        self.assertEqual(self.g.stanza(-100, 7)["team"], "seo")
        dati = json.loads((self.cfg.base / "gruppi.json").read_text(encoding="utf-8"))
        self.assertEqual(dati, [{"chat_id": -100, "thread_id": 7, "piano": "marketing", "team": "seo"}])
        self.uff.gestisci_gruppo(-100, 7, "/scollega", self.g)
        self.assertEqual(self.testi()[-1], "Scollegato.")
        self.uff.gestisci_gruppo(-100, 7, "/scollega", self.g)
        self.assertEqual(self.testi()[-1], "Questa chat non era collegata.")

    def test_collega_piano_sconosciuto(self):
        self.uff.gestisci_gruppo(-100, None, "/collega cucina", self.g)
        self.assertTrue(self.testi()[-1].startswith("Piano o team sconosciuto. Piani: direzione, marketing"))
        self.uff.gestisci_gruppo(-100, None, "/collega marketing cina", self.g)
        self.assertTrue(self.testi()[-1].startswith("Piano o team sconosciuto."))
        self.assertIsNone(self.g.stanza(-100, None))

    def test_chat_non_collegata_silenzio(self):
        self.uff.gestisci_gruppo(-300, None, "ciao a tutti", self.g)
        self.uff.gestisci_gruppo(-300, None, "/aiuto", self.g)
        self.assertEqual(self.inviati, [])

    def test_riunione_massimo_quattro_chiamate(self):
        self.g.collega(-100, 7, "marketing", "seo")
        coinvolti = [{"agente": f"seo-{i}", "brief": f"b{i}"} for i in range(1, 6)]
        self.risposte_claude = [json.dumps({"risposta": "Ci pensiamo noi.", "coinvolgi": coinvolti})]
        self.uff.gestisci_gruppo(-100, 7, "serve una strategia SEO", self.g)
        self.attendi_coda()
        self.assertEqual(len(self.chiamate_claude) + len(self.chiamate_gemini), 4)
        self.assertIn("MEMBRI DELLA STANZA:\nseo-1 | marketing |", self.chiamate_claude[0][2])
        self.assertIn("Bash", self.chiamate_claude[0][2])
        self.assertIn('"coinvolgi"', self.chiamate_claude[0][2])
        self.assertEqual(self.chiamate_gemini[0][2], "b1\n\nMessaggio dell'operatore nella stanza: serve una strategia SEO")
        self.assertEqual(len(self.inviati), 4)
        self.assertTrue(all(c == -100 and th == 7 for c, _, th in self.inviati))
        self.assertTrue(self.testi()[0].startswith("📣 SEO Specialist · comanager Marketing & Social\n\nCi pensiamo noi."))
        self.assertTrue(self.testi()[1].startswith("📣 Seo 1 · operaio"))

    def test_coinvolto_fuori_stanza_scartato(self):
        self.g.collega(-100, None, "marketing", None)
        self.risposte_claude = [json.dumps({"risposta": "Faccio io.", "coinvolgi": [
            {"agente": "sales-deal-strategist", "brief": "x"}]})]
        self.uff.gestisci_gruppo(-100, None, "idea?", self.g)
        self.attendi_coda()
        self.assertEqual(len(self.chiamate_claude) + len(self.chiamate_gemini), 1)
        self.assertEqual(self.testi(), ["📣 Growth Hacker · capo Marketing & Social\n\nFaccio io."])

    def test_at_nome_e_comandi_in_gruppo(self):
        self.g.collega(-100, 7, "marketing", None)
        self.uff.gestisci_gruppo(-100, 7, "@blog ciao", self.g)
        self.assertEqual(self.inviati[-1][0::2], (-100, 7))
        self.uff.gestisci_gruppo(-100, 7, "/chi seo 1", self.g)
        self.assertIn("@seo-1", self.testi()[-1])


if __name__ == "__main__":
    unittest.main()
