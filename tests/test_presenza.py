import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from portineria.chat import Cronologia
from portineria.coda import CodaClaude
from portineria.config import Config
from portineria.motori import firma_richiesta_esecutore, verifica_firma_richiesta
from portineria.organigramma import Organigramma
from portineria.presenza import (
    TTL_MAX,
    TTL_MIN,
    RegistroPresenzeEsterni,
    invia_presenza,
    main as presenza_main,
    risolvi_config_presenza,
)
from portineria.ufficio import Motori, Ufficio
from tests.test_router import PIANI, agente

PIANI_PRES = PIANI + [
    {"id": "design", "numero": 5, "nome": "Design & Prodotto", "emoji": "🎨", "colore": "#a855f7",
     "capo": "product-manager", "team": []},
    {"id": "engineering", "numero": 6, "nome": "Engineering", "emoji": "💻", "colore": "#3b82f6",
     "capo": "capo-sviluppo", "team": []},
]
AGENTI_PRES = [
    agente("direttore", "Direttore", "direzione", "direttore", motore="claude"),
    agente("design-ui-designer", "UI Designer", "design", "operaio", motore="claude"),
    agente("capo-sviluppo", "Software Architect", "engineering", "capo", motore="claude"),
]


class TestRegistroPresenzeEsterni(unittest.TestCase):
    def setUp(self):
        self.ora = [1000.0]
        self.org = Organigramma({"piani": PIANI_PRES, "agenti": AGENTI_PRES})
        self.reg = RegistroPresenzeEsterni(self.org, monotonic=lambda: self.ora[0])

    def test_avvia_coda_fine_e_flussi(self):
        self.reg.aggiorna(
            "avvia",
            agente_id="design-ui-designer",
            da_agente="direttore",
            tipo="riunione",
            commessa="Landing Pages",
            sorgente="antigravity",
            ttl=120,
        )
        lav, coda = self.reg.attivi()
        self.assertEqual(lav, ["design-ui-designer"])
        self.assertEqual(coda, [])
        self.assertEqual(
            self.reg.flussi(),
            [{
                "da_agente": "direttore",
                "da_piano": "direzione",
                "a_agente": "design-ui-designer",
                "a_piano": "design",
                "tipo": "riunione",
                "commessa": "Landing Pages",
            }],
        )

        # Passa in coda: esce da lavorando
        self.reg.aggiorna("coda", agente_id="design-ui-designer", sorgente="antigravity")
        self.assertEqual(self.reg.attivi(), ([], ["design-ui-designer"]))

        # Torna al lavoro con alias "lavora" e poi finisce
        self.reg.aggiorna("lavora", agente_id="design-ui-designer", sorgente="antigravity")
        self.assertEqual(self.reg.attivi(), (["design-ui-designer"], []))
        self.reg.aggiorna("fine", agente_id="design-ui-designer", sorgente="antigravity")
        self.assertEqual(self.reg.attivi(), ([], []))
        self.assertEqual(self.reg.flussi(), [])

    def test_scadenza_automatica_ttl(self):
        self.reg.aggiorna("avvia", agente_id="design-ui-designer", da_agente="direttore", ttl=30)
        self.ora[0] += 29.0
        self.assertEqual(self.reg.attivi(), (["design-ui-designer"], []))
        self.assertEqual(len(self.reg.flussi()), 1)

        self.ora[0] += 2.0
        self.assertEqual(self.reg.attivi(), ([], []))
        self.assertEqual(self.reg.flussi(), [])

    def test_pulisci_per_sorgente_o_totale(self):
        self.reg.aggiorna("avvia", agente_id="design-ui-designer", da_agente="direttore", sorgente="antigravity")
        self.reg.aggiorna("avvia", agente_id="capo-sviluppo", sorgente="altro")

        self.reg.aggiorna("pulisci", sorgente="antigravity")
        self.assertEqual(self.reg.attivi(), (["capo-sviluppo"], []))
        self.assertEqual(self.reg.flussi(), [])

        self.reg.aggiorna("pulisci")
        self.assertEqual(self.reg.attivi(), ([], []))

    def test_validazione_input_e_limiti_ttl(self):
        with self.assertRaises(ValueError):
            self.reg.aggiorna("avvia", agente_id="")
        with self.assertRaises(KeyError):
            self.reg.aggiorna("avvia", agente_id="inesistente")
        with self.assertRaises(ValueError):
            self.reg.aggiorna("inventata", agente_id="design-ui-designer")
        with self.assertRaises(ValueError):
            self.reg.aggiorna("avvia", agente_id="design-ui-designer", ttl="cento")
        with self.assertRaises(ValueError):
            self.reg.aggiorna("avvia", agente_id="design-ui-designer", ttl=True)

        res_min = self.reg.aggiorna("avvia", agente_id="design-ui-designer", ttl=1)
        res_max = self.reg.aggiorna("avvia", agente_id="capo-sviluppo", ttl=99_999)
        self.assertEqual(res_min["ttl"], TTL_MIN)
        self.assertEqual(res_max["ttl"], TTL_MAX)


class TestUfficioPresenzaEsterna(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.cfg = Config("t", 5, "k", "gemini-3.8-flash", "claude", base, ordini_hmac_key="segreto")
        (base / "app" / "prompts").mkdir(parents=True)
        for a in AGENTI_PRES:
            (base / "app" / "prompts" / f"{a['id']}.md").write_text(f"PROMPT {a['id']}", encoding="utf-8")
        self.org = Organigramma({"piani": PIANI_PRES, "agenti": AGENTI_PRES})
        self.cron = Cronologia(base / "stato" / "chat")
        self.coda = CodaClaude()
        self.uff = Ufficio(
            self.org,
            self.cron,
            self.coda,
            Motori(lambda *_: "g", lambda *_: "c"),
            lambda *_: None,
            self.cfg,
            adesso=lambda: datetime(2026, 11, 12, 2, 0, 0),
            in_parallelo=lambda f, *a: f(*a),
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_ufficio_fonde_attivi_flussi_eventi_e_telemetria(self):
        self.uff.presenza_esterna(
            "avvia",
            agente_id="design-ui-designer",
            da_agente="direttore",
            tipo="riunione",
            commessa="Landing Pages",
            nota="Design system v2",
            sorgente="antigravity",
        )
        self.assertIn("design-ui-designer", self.uff.attivi()["lavorando"])
        self.assertEqual(len(self.uff.flussi_attivi()), 1)
        self.assertEqual(self.uff.eventi_piani()["design"]["testo"], "UI Designer: Design system v2")
        self.assertEqual(self.uff.eventi_piani()["design"]["commessa"], "Landing Pages")

        self.uff.presenza_esterna(
            "fine",
            agente_id="design-ui-designer",
            commessa="Landing Pages",
            motore="gemini",
            sorgente="antigravity",
        )
        self.assertNotIn("design-ui-designer", self.uff.attivi()["lavorando"])
        self.assertEqual(self.uff.flussi_attivi(), [])
        self.assertEqual(self.uff.eventi_piani()["design"]["testo"], "UI Designer: completato")
        self.assertEqual(self.uff.telemetria.stato_agente("design-ui-designer")["gemini"], 1)

        self.uff.presenza_esterna("errore", agente_id="capo-sviluppo", motore="claude")
        self.assertEqual(self.uff.eventi_piani()["engineering"]["testo"], "Software Architect: errore")
        self.assertEqual(self.uff.telemetria.stato_agente("capo-sviluppo")["errori"], 1)


class TestClientPresenza(unittest.TestCase):
    def test_risolvi_config_da_env_file_e_fallback_web_host(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / "env"
            env.write_text("ORDINI_HMAC_KEY=chiave-1\nWEB_HOST=198.51.100.2\nWEB_PORT=8090\n", encoding="utf-8")
            url, chiave = risolvi_config_presenza(env_path=env, environ={})
            self.assertEqual((url, chiave), ("http://198.51.100.2:8090", "chiave-1"))

            url2, chiave2 = risolvi_config_presenza(
                env_path=env,
                environ={"EDIFICIO_URL": "http://198.51.100.3:9090/", "ORDINI_HMAC_KEY": "override"},
            )
            self.assertEqual((url2, chiave2), ("http://198.51.100.3:9090", "override"))

    def test_invia_presenza_firma_richiesta_e_non_esplode_se_offline(self):
        catturate = []

        class RispFake:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ok":true}'

        def fake_open(req, timeout=0):
            catturate.append((req.full_url, dict(req.header_items()), req.data, timeout))
            return RispFake()

        ok = invia_presenza(
            "avvia",
            "design-ui-designer",
            da_agente="direttore",
            tipo="riunione",
            commessa="Landing Pages",
            nota="Design",
            url="http://198.51.100.2:8090",
            chiave="segreto-hmac",
            urlopen=fake_open,
        )
        self.assertTrue(ok)
        self.assertEqual(len(catturate), 1)
        url, hdrs, raw, _ = catturate[0]
        self.assertEqual(url, "http://198.51.100.2:8090/api/telemetria-esterna")
        hdrs_low = {k.lower(): v for k, v in hdrs.items()}
        self.assertTrue(verifica_firma_richiesta("segreto-hmac", hdrs_low["x-timestamp"], raw, hdrs_low["x-firma"]))
        corpo = json.loads(raw.decode("utf-8"))
        self.assertEqual(corpo["azione"], "avvia")
        self.assertEqual(corpo["agente"], "design-ui-designer")
        self.assertEqual(corpo["da_agente"], "direttore")

        # Senza config -> False senza eccezioni
        self.assertFalse(invia_presenza("avvia", "design-ui-designer", url="", chiave="", environ={}))

        # Se Pi irraggiungibile -> False senza eccezioni
        def offline(*_, **__):
            raise urllib.error.URLError("connessione rifiutata")

        self.assertFalse(
            invia_presenza("avvia", "design-ui-designer", url="http://198.51.100.3:8090", chiave="k", urlopen=offline)
        )

    def test_cli_presenza(self):
        with patch("portineria.presenza.invia_presenza", return_value=True) as mock_invia:
            rc = presenza_main([
                "avvia", "design-ui-designer", "--da", "direttore",
                "--tipo", "riunione", "--commessa", "Landing Pages", "--nota", "War Room",
            ])
        self.assertEqual(rc, 0)
        mock_invia.assert_called_once()
        args, kw = mock_invia.call_args
        self.assertEqual(args, ("avvia", "design-ui-designer"))
        self.assertEqual(kw["da_agente"], "direttore")
        self.assertEqual(kw["tipo"], "riunione")
        self.assertEqual(kw["commessa"], "Landing Pages")

        with patch("portineria.presenza.invia_presenza", return_value=False):
            buf = io.StringIO()
            with patch("sys.stderr", buf):
                rc_err = presenza_main(["pulisci", "--strict"])
            self.assertEqual(rc_err, 1)


if __name__ == "__main__":
    unittest.main()
