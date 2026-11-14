import tempfile
import threading
import time
import unittest
from pathlib import Path

from portineria.coda import CodaClaude, CodaPiena, ram_libera_mb


def aspetta(condizione, secondi=3.0):
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        if condizione():
            return True
        time.sleep(0.005)
    raise AssertionError("condizione mai vera")


class TestCoda(unittest.TestCase):
    def test_uno_alla_volta_in_ordine(self):
        coda = CodaClaude()
        eventi, fatto = [], threading.Event()

        def lavoro(nome):
            def f():
                eventi.append(f"inizio {nome}")
                time.sleep(0.02)
                eventi.append(f"fine {nome}")
                return nome
            return f

        coda.accoda("A", lavoro("A"), lambda r, e: None)
        coda.accoda("B", lavoro("B"), lambda r, e: fatto.set())
        self.assertTrue(fatto.wait(3))
        self.assertEqual(eventi, ["inizio A", "fine A", "inizio B", "fine B"])

    def test_posizioni_e_coda_piena(self):  # Review Focus 3
        coda = CodaClaude(max_attesa=2)
        blocco = threading.Event()
        self.assertEqual(coda.accoda("A", lambda: blocco.wait() and "a", lambda r, e: None), 0)
        aspetta(lambda: coda.stato()["in_corso"] == "A")
        self.assertEqual(coda.accoda("B", lambda: "b", lambda r, e: None), 1)
        self.assertEqual(coda.accoda("C", lambda: "c", lambda r, e: None), 2)
        self.assertEqual(coda.stato()["in_attesa"], ["B", "C"])
        with self.assertRaises(CodaPiena):
            coda.accoda("D", lambda: "d", lambda r, e: None)
        blocco.set()
        aspetta(lambda: coda.stato() == {"in_corso": None, "in_attesa": [], "attesa_ram": False})

    def test_max_attesa_zero_lascia_partire_il_primo(self):
        coda = CodaClaude(max_attesa=0)
        blocco = threading.Event()
        self.assertEqual(coda.accoda("A", blocco.wait, lambda r, e: None), 0)
        aspetta(lambda: coda.stato()["in_corso"] == "A")
        with self.assertRaises(CodaPiena):
            coda.accoda("B", lambda: "b", lambda r, e: None)
        blocco.set()

    def test_attende_ram(self):
        valori = iter([100, 100, 500])
        letture = []

        def ram():
            v = next(valori)
            letture.append(v)
            return v

        coda = CodaClaude(ram_mb=ram, attesa_ram_s=0.05)
        partito, fatto = threading.Event(), threading.Event()
        coda.accoda("A", lambda: partito.set() or "ok", lambda r, e: fatto.set())
        aspetta(lambda: coda.stato()["attesa_ram"])
        self.assertFalse(partito.is_set())
        self.assertTrue(fatto.wait(3))
        self.assertEqual(letture, [100, 100, 500])
        self.assertFalse(coda.stato()["attesa_ram"])

    def test_eccezione_passata_ad_al_termine(self):
        coda = CodaClaude()
        visto, fatto = [], threading.Event()

        def lavoro():
            raise ValueError("no")

        coda.accoda("A", lavoro, lambda r, e: (visto.append((r, e)), fatto.set()))
        self.assertTrue(fatto.wait(3))
        self.assertIsNone(visto[0][0])
        self.assertIsInstance(visto[0][1], ValueError)

    def test_errore_in_al_termine_non_ferma_il_worker(self):
        coda = CodaClaude()
        fatto = threading.Event()

        def esplode(r, e):
            raise RuntimeError("al_termine rotto")

        with self.assertLogs(level="ERROR"):
            coda.accoda("A", lambda: "a", esplode)
            coda.accoda("B", lambda: "b", lambda r, e: fatto.set())
            self.assertTrue(fatto.wait(3))

    def test_accoda_da_al_termine_parte_subito(self):
        coda = CodaClaude()
        posizioni, fatto = [], threading.Event()
        coda.accoda("A", lambda: "a",
                    lambda r, e: posizioni.append(coda.accoda("B", lambda: "b", lambda r2, e2: fatto.set())))
        self.assertTrue(fatto.wait(3))
        self.assertEqual(posizioni, [0])

    def test_ram_libera_da_meminfo(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "meminfo"
            p.write_text("MemTotal:  949000 kB\nMemAvailable:   659456 kB\n", encoding="utf-8")
            self.assertEqual(ram_libera_mb(p), 644)
            self.assertEqual(ram_libera_mb(Path(d) / "assente"), 10_000)

    def test_priorita_alta_scavalca_normale_ma_preserva_fifo(self):
        coda = CodaClaude()
        blocco = threading.Event()
        ordine, finito = [], threading.Event()

        def registra(nome, ultimo=False):
            def f():
                ordine.append(nome)
                return nome
            def fine(r, e):
                if ultimo:
                    finito.set()
            return f, fine

        self.assertEqual(coda.accoda("in-corso", blocco.wait, lambda r, e: None), 0)
        aspetta(lambda: coda.stato()["in_corso"] == "in-corso")

        # Accodiamo due lavori a priorità normale e poi due ad alta priorità
        pos_n1 = coda.accoda("N1", *registra("N1"), priorita="normale")
        pos_n2 = coda.accoda("N2", *registra("N2", ultimo=True), priorita="normale")
        pos_a1 = coda.accoda("A1", *registra("A1"), priorita="alta")
        pos_a2 = coda.accoda("A2", *registra("A2"))  # default = alta

        self.assertEqual((pos_n1, pos_n2, pos_a1, pos_a2), (1, 2, 1, 2))
        self.assertEqual(coda.stato()["in_attesa"], ["A1", "A2", "N1", "N2"])

        blocco.set()
        self.assertTrue(finito.wait(3))
        self.assertEqual(ordine, ["A1", "A2", "N1", "N2"])

    def test_priorita_condivide_max_attesa(self):
        coda = CodaClaude(max_attesa=2)
        blocco = threading.Event()
        coda.accoda("A", blocco.wait, lambda r, e: None)
        aspetta(lambda: coda.stato()["in_corso"] == "A")
        coda.accoda("N1", lambda: "n1", lambda r, e: None, priorita="normale")
        coda.accoda("A1", lambda: "a1", lambda r, e: None, priorita="alta")
        with self.assertRaises(CodaPiena):
            coda.accoda("A2", lambda: "a2", lambda r, e: None, priorita="alta")
        with self.assertRaises(CodaPiena):
            coda.accoda("N2", lambda: "n2", lambda r, e: None, priorita="normale")
        blocco.set()
        aspetta(lambda: coda.stato()["in_corso"] is None and not coda.stato()["in_attesa"])

    def test_priorita_non_valida_rifiutata(self):
        coda = CodaClaude()
        with self.assertRaises(ValueError):
            coda.accoda("X", lambda: "x", lambda r, e: None, priorita="urgente")


if __name__ == "__main__":
    unittest.main()
