import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from portineria.chat import Cronologia, registra_inbox


class TestChat(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_ultimi_n_in_ordine(self):
        c = Cronologia(self.dir / "chat")
        for i in range(12):
            c.aggiungi("celia", "utente", f"m{i}")
        self.assertEqual([m["testo"] for m in c.ultimi("celia")], [f"m{i}" for i in range(2, 12)])
        self.assertEqual(c.ultimi("celia", 1)[0]["ruolo"], "utente")

    def test_riga_corrotta_saltata(self):
        (self.dir / "celia.jsonl").write_text(
            '{"t": "x", "ruolo": "utente", "testo": "a"}\n{rotto\n{"t": "y", "ruolo": "agente", "testo": "b"}\n',
            encoding="utf-8")
        self.assertEqual([m["testo"] for m in Cronologia(self.dir).ultimi("celia")], ["a", "b"])

    def test_agente_senza_storia(self):
        self.assertEqual(Cronologia(self.dir).ultimi("nessuno"), [])

    def test_inbox_crea_e_appende(self):
        p = registra_inbox(self.dir, datetime(2026, 11, 10, 9, 5), "celia", "gemini", "D1", "R1")
        registra_inbox(self.dir, datetime(2026, 11, 10, 9, 7), "celia", "gemini", "D2", "R2")
        testo = p.read_text(encoding="utf-8")
        self.assertEqual(p, self.dir / "00 - Inbox" / "2026-11-10.md")
        self.assertTrue(testo.startswith("---\ntipo: inbox\ndata: 2026-11-10\n---"))
        self.assertIn("## 09:05 · celia (gemini)\n\n**Domanda:** D1\n\n**Risposta:** R1\n", testo)
        self.assertEqual(testo.count("**Domanda:**"), 2)
        self.assertEqual(testo.count("tipo: inbox"), 1)


if __name__ == "__main__":
    unittest.main()
