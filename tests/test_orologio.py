import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from portineria.orologio import Lavoro, Orologio, ogni, quotidiano, settimanale


class TestOrologio(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.stato = Path(self._tmp.name) / "orologio.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_quotidiano(self):
        d = quotidiano(3)
        self.assertTrue(d(datetime(2026, 11, 11, 3, 0), datetime(2026, 11, 10, 3, 0)))
        self.assertFalse(d(datetime(2026, 11, 11, 2, 59), datetime(2026, 11, 10, 3, 0)))
        self.assertFalse(d(datetime(2026, 11, 11, 9, 0), datetime(2026, 11, 11, 3, 0)))
        self.assertTrue(d(datetime(2026, 11, 11, 9, 0), None))

    def test_giro_saltato_parte_una_volta(self):  # Review Focus 2
        eseguiti = []
        orari = iter([datetime(2026, 11, 11, 10, 0), datetime(2026, 11, 11, 10, 1)])
        self.stato.write_text(json.dumps({"notturno": "2026-11-09T03:00:00"}), encoding="utf-8")
        o = Orologio(self.stato, [Lavoro("notturno", quotidiano(3), lambda: eseguiti.append(1))],
                     adesso=lambda: next(orari))
        self.assertEqual(o.giro(), ["notturno"])
        self.assertEqual(o.giro(), [])
        self.assertEqual(eseguiti, [1])

    def test_settimanale_domenica(self):
        d = settimanale(6, 4)  # 15 nov 2026 è domenica
        self.assertFalse(d(datetime(2026, 11, 15, 3, 59), datetime(2026, 11, 8, 4, 0)))
        self.assertTrue(d(datetime(2026, 11, 15, 4, 0), datetime(2026, 11, 8, 4, 0)))
        self.assertTrue(d(datetime(2026, 11, 17, 9, 0), datetime(2026, 11, 8, 4, 0)))  # recupero di martedì
        self.assertFalse(d(datetime(2026, 11, 17, 9, 0), datetime(2026, 11, 15, 4, 0)))

    def test_ogni_ora(self):
        d = ogni(1)
        self.assertFalse(d(datetime(2026, 11, 11, 10, 59), datetime(2026, 11, 11, 10, 0)))
        self.assertTrue(d(datetime(2026, 11, 11, 11, 0), datetime(2026, 11, 11, 10, 0)))

    def test_stato_salvato_prima_e_errore_non_ferma(self):
        esiti, eseguiti = [], []

        def rotto():
            stato = json.loads(self.stato.read_text(encoding="utf-8"))
            eseguiti.append(("a", "a" in stato))  # lo stato è già salvato quando il lavoro parte
            raise RuntimeError("Gemini giù")

        o = Orologio(self.stato, [Lavoro("a", ogni(1), rotto), Lavoro("b", ogni(1), lambda: eseguiti.append(("b", True)))],
                     adesso=lambda: datetime(2026, 11, 11, 10, 0), esito=lambda n, e: esiti.append((n, e)))
        with self.assertLogs(level="ERROR"):
            self.assertEqual(o.giro(), ["a", "b"])
        self.assertEqual(eseguiti, [("a", True), ("b", True)])
        self.assertEqual(esiti, [("a", "errore: Gemini giù"), ("b", "ok")])
        self.assertEqual(set(json.loads(self.stato.read_text(encoding="utf-8"))), {"a", "b"})

    def test_esegui_subito(self):
        esiti = []
        o = Orologio(self.stato, [Lavoro("a", ogni(1), lambda: None)], adesso=lambda: datetime(2026, 11, 11, 10, 0),
                     esito=lambda n, e: esiti.append((n, e)))
        o.esegui("a")
        self.assertEqual(esiti, [("a", "ok")])
        self.assertEqual(o.giro(), [])  # appena eseguito: non è più dovuto

    def test_stato_corrotto_riparte_da_zero(self):
        self.stato.write_text("{rotto", encoding="utf-8")
        o = Orologio(self.stato, [Lavoro("a", ogni(1), lambda: None)], adesso=lambda: datetime(2026, 11, 11, 10, 0))
        self.assertEqual(o.giro(), ["a"])


if __name__ == "__main__":
    unittest.main()
