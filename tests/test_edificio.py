import os
import tempfile
import unittest
from pathlib import Path

from portineria.edificio import COLONNE, PASSO, comando_claude, pianta, stanze
from portineria.organigramma import Organigramma


class TestEdificio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.org = Organigramma.carica(Path(__file__).parent.parent / "organigramma.json")

    def test_ogni_agente_compare_una_volta(self):
        p = pianta(self.org)
        ids = [a["id"] for piano in p["piani"] for a in piano["agenti"]]
        self.assertEqual(sorted(ids), sorted(a["id"] for a in self.org.tutti()))

    def test_niente_due_omini_nello_stesso_posto(self):
        for piano in pianta(self.org)["piani"]:
            posti = [(a["x"], a["z"]) for a in piano["agenti"]]
            self.assertEqual(len(posti), len(set(posti)), piano["id"])

    def test_tutti_dentro_il_piano(self):
        for piano in pianta(self.org)["piani"]:
            for a in piano["agenti"]:
                self.assertTrue(-PASSO * 2 <= a["x"] < piano["larghezza"] and -PASSO * 2 <= a["z"] < piano["profondita"], a["id"])

    def test_capo_nell_angolo_e_piu_grande(self):
        piano = next(p for p in pianta(self.org)["piani"] if p["id"] == "sviluppo")
        capo = next(a for a in piano["agenti"] if a["ruolo"] == "capo")
        self.assertEqual((capo["x"], capo["z"], capo["scala"]), (-4.0, -4.0, 1.5))

    def test_direttore_scala(self):
        direttore = next(a for p in pianta(self.org)["piani"] for a in p["agenti"] if a["id"] == "direttore")
        self.assertEqual((direttore["x"], direttore["z"], direttore["scala"]), (-4.0, -4.0, 1.8))

    def test_piani_dall_alto(self):
        self.assertEqual([p["numero"] for p in pianta(self.org)["piani"]], [10, 2, 1])


    def test_agente_su_piano_inesistente_da_errore(self):
        org = Organigramma({
            "piani": [{"id": "uno", "numero": 1, "nome": "Uno", "emoji": "x", "colore": "#fff", "capo": None, "team": []}],
            "agenti": [{"id": "a1", "nome": "A", "ruolo": "operaio", "piano": "uno", "team": None},
                       {"id": "a2", "nome": "B", "ruolo": "operaio", "piano": "tipo", "team": None}]})
        with self.assertRaises(ValueError) as c:
            pianta(org)
        self.assertIn("a2", str(c.exception))
        self.assertIn("tipo", str(c.exception))

    def test_comando_claude_per_fonte(self):
        self.assertEqual(comando_claude({"fonte": "agency", "id": "x", "nome": "Code Reviewer"}), 'claude --agent "Code Reviewer"')
        self.assertEqual(comando_claude({"fonte": "ecc", "id": "ecc-a11y-architect", "nome": "a"}), "claude --agent ecc:a11y-architect")
        self.assertEqual(comando_claude({"fonte": "ai-specialists", "id": "celia-cold-email", "nome": "c"}), "/ai-specialists:celia-cold-email")
        self.assertEqual(comando_claude({"fonte": "founder", "id": "founder-board", "nome": "f"}), "/founder-board")
        self.assertIsNone(comando_claude({"fonte": "ufficio", "id": "direttore", "nome": "d"}))

    def test_stanze(self):
        with tempfile.TemporaryDirectory() as t:
            vault = Path(t)
            for rel in ("01 - Commesse/Shopify.md", "02 - Conoscenza/a.md", "99 - Fonti/x.md", ".obsidian/y.md"):
                f = vault / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("x", encoding="utf-8")
            s = stanze(vault, "198.51.100.2")
            self.assertEqual([x["id"] for x in s], ["commesse", "palestra", "finanza", "archivio"])
            self.assertEqual(s[0]["link"], [{"testo": "Shopify", "url": "obsidian://open?vault=Ufficio&file=01%20-%20Commesse/Shopify"}])
            self.assertEqual(s[1]["link"][0]["url"], "http://198.51.100.2:3100")
            self.assertEqual(s[2]["link"][0]["url"], "http://198.51.100.2:8080")
            testi = [l["testo"] for l in s[3]["link"]]
            self.assertNotIn("x", testi)
            self.assertNotIn("y", testi)
            self.assertIn("a", testi)

    def test_archivio_le_dieci_piu_recenti(self):
        with tempfile.TemporaryDirectory() as t:
            vault = Path(t)
            for i in range(12):
                f = vault / f"n{i:02}.md"
                f.write_text("x", encoding="utf-8")
                os.utime(f, (1000 + i, 1000 + i))
            testi = [l["testo"] for l in stanze(vault, "h")[3]["link"]]
            self.assertEqual(testi, [f"n{i:02}" for i in range(11, 1, -1)])


if __name__ == "__main__":
    unittest.main()
