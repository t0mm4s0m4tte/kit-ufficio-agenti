import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from portineria.organigramma import Organigramma
from portineria.telemetria import Telemetria

DATI_ORG = {
    "piani": [
        {"id": "direzione", "numero": 10, "nome": "Direzione Generale"},
        {"id": "vendite", "numero": 2, "nome": "Vendite"},
        {"id": "ingegneria", "numero": 7, "nome": "Ingegneria"},
    ],
    "agenti": [
        {"id": "direttore", "nome": "Direttore", "piano": "direzione", "ruolo": "direttore", "motore": "claude"},
        {"id": "celia", "nome": "Celia", "piano": "vendite", "ruolo": "specialista", "motore": "gemini"},
        {"id": "architetto", "nome": "Architetto", "piano": "ingegneria", "ruolo": "capo", "motore": "claude"},
        {"id": "dormiente", "nome": "Dormiente", "piano": "ingegneria", "ruolo": "specialista", "motore": "claude"},
    ],
}


class TestTelemetria(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "stato" / "telemetria.json"
        self.org = Organigramma(DATI_ORG)
        self.tel = Telemetria(self.path, self.org)

    def tearDown(self):
        self.tmp.cleanup()

    def test_iniziale_vuoto(self):
        self.assertEqual(self.tel.agenti_mai_usati(), 4)
        sintesi = self.tel.sintesi()
        self.assertEqual(sintesi["totale_claude"], 0)
        self.assertEqual(sintesi["totale_gemini"], 0)
        self.assertEqual(sintesi["totale_errori"], 0)
        self.assertEqual(sintesi["mai_usati"], 4)
        self.assertEqual(sintesi["totale_agenti"], 4)
        self.assertIn("mai usati", self.tel.riepilogo_testo())

    def test_registra_chiamata_e_persistenza(self):
        t0 = datetime(2026, 11, 11, 10, 0, 0)
        self.tel.registra("celia", "gemini", errore=False, adesso=t0)
        self.tel.registra("celia", "gemini", errore=True, adesso=t0)
        self.tel.registra("architetto", "claude", errore=False, adesso=t0)

        # Verifica stato in memoria
        c_celia = self.tel.stato_agente("celia")
        self.assertEqual(c_celia["gemini"], 2)
        self.assertEqual(c_celia["errori"], 1)
        self.assertEqual(c_celia["ultima_attivita"], "2026-11-11T10:00:00")

        p_vendite = self.tel.stato_piano("vendite")
        self.assertEqual(p_vendite["gemini"], 2)
        self.assertEqual(p_vendite["errori"], 1)

        # Verifica persistenza su file
        self.assertTrue(self.path.exists())
        dati_disco = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("celia", dati_disco["agenti"])
        self.assertEqual(dati_disco["agenti"]["celia"]["gemini"], 2)
        # Nessun segreto o testo chat
        for val in dati_disco["agenti"].values():
            self.assertNotIn("testo", val)
            self.assertNotIn("chat", val)

        # Ricaricamento da nuovo oggetto
        tel2 = Telemetria(self.path, self.org)
        self.assertEqual(tel2.stato_agente("celia")["gemini"], 2)
        self.assertEqual(tel2.stato_agente("architetto")["claude"], 1)

    def test_top_agenti_e_piani(self):
        t = datetime(2026, 11, 11, 12, 0, 0)
        for _ in range(5):
            self.tel.registra("architetto", "claude", adesso=t)
        for _ in range(3):
            self.tel.registra("celia", "gemini", adesso=t)
        self.tel.registra("direttore", "claude", adesso=t)

        top_ag = self.tel.top_agenti(2)
        self.assertEqual(len(top_ag), 2)
        self.assertEqual(top_ag[0]["id"], "architetto")
        self.assertEqual(top_ag[0]["totale"], 5)
        self.assertEqual(top_ag[1]["id"], "celia")
        self.assertEqual(top_ag[1]["totale"], 3)

        top_pi = self.tel.top_piani(5)
        self.assertEqual(top_pi[0]["id"], "ingegneria")
        self.assertEqual(top_pi[0]["nome"], "Ingegneria")
        self.assertEqual(top_pi[0]["totale"], 5)

        self.assertEqual(self.tel.agenti_mai_usati(), 1)  # solo 'dormiente'

        testo = self.tel.riepilogo_testo()
        self.assertIn("Top agenti:", testo)
        self.assertIn("@architetto (5)", testo)
        self.assertIn("Top piani:", testo)
        self.assertIn("Ingegneria (5)", testo)
        self.assertIn("Agenti mai usati: 1 su 4", testo)

    def test_thread_safety(self):
        def worker(nome, motore):
            for _ in range(20):
                self.tel.registra(nome, motore)

        threads = [
            threading.Thread(target=worker, args=("celia", "gemini")),
            threading.Thread(target=worker, args=("architetto", "claude")),
            threading.Thread(target=worker, args=("celia", "claude")),
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        self.assertEqual(self.tel.stato_agente("celia")["gemini"], 20)
        self.assertEqual(self.tel.stato_agente("celia")["claude"], 20)
        self.assertEqual(self.tel.stato_agente("architetto")["claude"], 20)
        sintesi = self.tel.sintesi()
        self.assertEqual(sintesi["totale_gemini"], 20)
        self.assertEqual(sintesi["totale_claude"], 40)
