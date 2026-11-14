import http.client
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from portineria.chat import Cronologia
from portineria.config import carica_config
from portineria.organigramma import Organigramma
from portineria.web import CHAT_WEB, AvvisiWeb, avvia_web

ORG = {"piani": [{"id": "direzione", "numero": 1, "nome": "Direzione", "emoji": "🏛", "colore": "#c9a227"}],
       "agenti": [{"id": "direttore", "nome": "Direttore", "descrizione": "d", "piano": "direzione", "team": None,
                   "ruolo": "direttore", "capo": None, "motore": "claude", "fonte": "ufficio",
                   "prompt_path": "prompts/_direttore.md"},
                  {"id": "celia", "nome": "Celia", "descrizione": "c", "piano": "direzione", "team": None,
                   "ruolo": "operaio", "capo": None, "motore": "gemini", "fonte": "ufficio",
                   "prompt_path": "prompts/celia.md"}]}


class UfficioFinto:
    def __init__(self, tmp: Path):
        self.org = Organigramma(ORG)
        self.cron = Cronologia(tmp / "cron")
        self.cfg = type("Cfg", (), {"vault": tmp / "vault"})()
        self.chiamate = []
        self.flussi = []
        self.eventi = {}
        self.invia = lambda chat_id, testo, thread_id=None: None

    def attivi(self):
        return {"lavorando": [], "in_coda": [], "attesa_ram": False}

    def flussi_attivi(self):
        return list(self.flussi)

    def eventi_piani(self):
        return dict(self.eventi)

    def imposta_motore(self, agente_id, motore):
        if motore not in ("claude", "gemini"):
            raise ValueError(motore)
        if self.org.get(agente_id) is None:
            raise KeyError(agente_id)

    def chiedi(self, agente_id, testo, chat_id, thread_id, al_termine=None, **kw):
        # come l'Ufficio vero: la cronologia si scrive solo alla risposta, intanto arriva l'avviso della coda
        self.chiamate.append(("chiedi", agente_id, testo, chat_id, thread_id))
        self.invia(chat_id, "⏳ In coda, posizione 1.", thread_id)

    def direttore(self, testo, chat_id, thread_id):
        self.chiamate.append(("direttore", testo, chat_id, thread_id))


class TestWeb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        (tmp / "statici").mkdir()
        (tmp / "statici" / "index.html").write_text("<h1>ciao</h1>", encoding="utf-8")
        (tmp / "statici" / "app.js").write_text("1", encoding="utf-8")
        (tmp / "segreto.txt").write_text("no", encoding="utf-8")
        (tmp / "vault" / "01 - Commesse").mkdir(parents=True)
        self.ufficio = UfficioFinto(tmp)
        self.avvisi = AvvisiWeb()
        self.ufficio.invia = lambda chat_id, testo, thread_id=None: self.avvisi.aggiungi(testo)  # come __main__
        self.server = avvia_web("127.0.0.1", 0, self.ufficio, tmp / "statici", self.avvisi)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.porta = self.server.server_address[1]

    def richiesta(self, metodo, percorso, corpo=None, header=None):
        h = {"Host": f"127.0.0.1:{self.porta}"}
        if metodo == "POST":
            h["Content-Type"] = "application/json"
        h.update(header or {})
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=5)
        c.putrequest(metodo, percorso, skip_host=True, skip_accept_encoding=True)
        dati = corpo.encode() if corpo is not None else None
        if dati is not None:
            h["Content-Length"] = str(len(dati))
        for k, v in h.items():
            c.putheader(k, v)
        c.endheaders(dati)
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), r.read())
        c.close()
        return out

    def json(self, metodo, percorso):
        return json.loads(self.richiesta(metodo, percorso)[2])

    def test_pagina_e_file_statici(self):
        self.assertEqual(self.richiesta("GET", "/")[0], 200)
        stato, header, _ = self.richiesta("GET", "/app.js")
        self.assertEqual((stato, header["Content-Type"]), (200, "text/javascript; charset=utf-8"))

    def test_file_statici_media_e_range(self):
        statici = Path(self.tmp.name) / "statici"
        (statici / "greta.webp").write_bytes(b"RIFFwebp")
        (statici / "greta.jpg").write_bytes(b"\xff\xd8\xff\xe0")
        (statici / "greta.mp4").write_bytes(b"0123456789abcdef")

        for nome, mime in (("greta.webp", "image/webp"), ("greta.jpg", "image/jpeg"), ("greta.mp4", "video/mp4")):
            stato, header, _ = self.richiesta("GET", f"/{nome}")
            self.assertEqual((stato, header["Content-Type"]), (200, mime))

        stato, header, corpo = self.richiesta("GET", "/greta.mp4", header={"Range": "bytes=0-3"})
        self.assertEqual(stato, 206)
        self.assertEqual(header.get("Accept-Ranges"), "bytes")
        self.assertEqual(header.get("Content-Range"), "bytes 0-3/16")
        self.assertEqual(corpo, b"0123")

        stato_suff, header_suff, corpo_suff = self.richiesta("GET", "/greta.mp4", header={"Range": "bytes=-4"})
        self.assertEqual(stato_suff, 206)
        self.assertEqual(header_suff.get("Content-Range"), "bytes 12-15/16")
        self.assertEqual(corpo_suff, b"cdef")

        stato_ko, header_ko, _ = self.richiesta("GET", "/greta.mp4", header={"Range": "bytes=99-120"})
        self.assertEqual(stato_ko, 416)
        self.assertEqual(header_ko.get("Content-Range"), "bytes */16")

    def test_percorsi_fuori_dalla_cartella_404(self):
        for p in ("/../segreto.txt", "/%2e%2e/segreto.txt", "/sub/../../segreto.txt", "/%2e%2e%2fsegreto.txt",
                  "/app.js%00.txt", "/..%5csegreto.txt"):
            self.assertEqual(self.richiesta("GET", p)[0], 404, p)

    def test_host_sbagliato_403(self):
        self.assertEqual(self.richiesta("GET", "/api/stato", header={"Host": "evil.example:8090"})[0], 403)

    def test_post_senza_json_403_e_nessuna_chiamata(self):
        stato = self.richiesta("POST", "/api/chiedi", '{"agente": "direttore", "testo": "ciao"}',
                               header={"Content-Type": "text/plain"})[0]
        self.assertEqual((stato, self.ufficio.chiamate), (403, []))

    def test_post_da_altra_origine_403(self):
        stato = self.richiesta("POST", "/api/chiedi", '{"agente": "direttore", "testo": "ciao"}',
                               header={"Origin": "http://evil.example"})[0]
        self.assertEqual((stato, self.ufficio.chiamate), (403, []))

    def test_chiedi_ok(self):
        stato = self.richiesta("POST", "/api/chiedi", '{"agente": "celia", "testo": "ciao"}')[0]
        self.assertEqual(stato, 202)
        self.assertEqual(self.ufficio.chiamate, [("chiedi", "celia", "ciao", CHAT_WEB, None)])

    def test_chiedi_avviso_della_coda_e_orario(self):
        stato, _, corpo = self.richiesta("POST", "/api/chiedi", '{"agente": "celia", "testo": "ciao"}')
        self.assertEqual(stato, 202)
        t = json.loads(corpo)["t"]
        datetime.fromisoformat(t)
        d = self.json("GET", "/api/agente/celia")
        self.assertEqual(d["chat"], [])  # il messaggio lo mostra la pagina come "in attesa"
        self.assertEqual(d["avvisi"][-1]["testo"], "⏳ In coda, posizione 1.")
        self.assertGreaterEqual(d["avvisi"][-1]["t"], t)

    def test_chiedi_al_direttore_passa_dallo_smistamento(self):
        stato = self.richiesta("POST", "/api/chiedi", '{"agente": "direttore", "testo": "ciao"}')[0]
        self.assertEqual(stato, 202)
        self.assertEqual(self.ufficio.chiamate, [("direttore", "ciao", CHAT_WEB, None)])

    def test_chiedi_input_sbagliati(self):
        casi = [('{"agente": "nessuno", "testo": "x"}', 404), ('{"agente": "direttore", "testo": ""}', 400),
                ('{"agente": "direttore", "testo": "' + "x" * 4001 + '"}', 400), ("non json", 400),
                ('[1]', 400), ('{"agente": 5, "testo": "x"}', 400),
                ('{"agente": "direttore", "testo": "' + "x" * 20001 + '"}', 413)]
        for corpo, atteso in casi:
            self.assertEqual(self.richiesta("POST", "/api/chiedi", corpo)[0], atteso, corpo[:40])
        self.assertEqual(self.ufficio.chiamate, [])

    def test_motore(self):
        self.assertEqual(self.richiesta("POST", "/api/motore", '{"agente": "direttore", "motore": "gpt"}')[0], 400)
        self.assertEqual(self.richiesta("POST", "/api/motore", '{"agente": "nessuno", "motore": "gemini"}')[0], 404)
        self.assertEqual(self.richiesta("POST", "/api/motore", '{"agente": "direttore", "motore": "gemini"}')[0], 200)

    def test_crea_ordine_sempre_in_stato_piano(self):
        from portineria.ordini import carica_ordine
        corpo = json.dumps({
            "agente": "celia",
            "titolo": "Nuova landing",
            "testo": "Crea la hero section",
            "commessa": "Shopify",
            "stato": "approvato",
            "firma": "tentativo-falso",
        })
        stato, _, risp = self.richiesta("POST", "/api/ordine", corpo)
        self.assertEqual(stato, 200)
        dati = json.loads(risp)
        self.assertEqual(dati, {"ok": True, "id": "L-001", "stato": "piano"})
        ordine = carica_ordine(self.ufficio.cfg.vault, "L-001")
        self.assertEqual(ordine["stato"], "piano")
        self.assertEqual(ordine["firma"], "")
        self.assertEqual(ordine["agente"], "celia")
        self.assertEqual(ordine["titolo"], "Nuova landing")
        self.assertEqual(ordine["piano"], "Crea la hero section")
        self.assertEqual(ordine["commessa"], "Shopify")

    def test_crea_ordine_input_sbagliati(self):
        casi = [
            ('{"agente": "nessuno", "testo": "x"}', 404),
            ('{"agente": "celia", "testo": ""}', 400),
            ('{"agente": "celia", "testo": "   "}', 400),
            ('{"agente": "celia", "testo": "' + "x" * 4001 + '"}', 400),
            ('{"agente": "celia", "testo": "ok", "titolo": 123}', 400),
            ('{"agente": "celia", "testo": "ok", "commessa": ["x"]}', 400),
            ('{"agente": "celia", "testo": "ok", "titolo": "' + "t" * 201 + '"}', 400),
        ]
        for corpo, atteso in casi:
            self.assertEqual(self.richiesta("POST", "/api/ordine", corpo)[0], atteso, corpo[:40])

    def test_edificio_stato_agente_cerca(self):
        self.assertIn("piani", self.json("GET", "/api/edificio"))
        self.assertEqual(
            set(self.json("GET", "/api/stato")),
            {"lavorando", "in_coda", "attesa_ram", "ram_mb", "flussi", "eventi", "statistiche"},
        )
        self.assertIsNone(self.json("GET", "/api/agente/direttore")["comando"])
        self.assertEqual(self.richiesta("GET", "/api/agente/nessuno")[0], 404)
        self.assertEqual(self.json("GET", "/api/cerca?q=")["risultati"], [])

    def test_stato_include_flussi_ed_eventi(self):
        self.ufficio.flussi = [{
            "da_agente": "direttore",
            "da_piano": "direzione",
            "a_agente": "celia",
            "a_piano": "vendite",
            "tipo": "delega",
            "commessa": "Shopify",
        }]
        self.ufficio.eventi = {
            "vendite": {"testo": "Celia: al lavoro", "commessa": "Shopify", "agente": "celia", "t": "2026-11-11T19:00:00"}
        }
        dati = self.json("GET", "/api/stato")
        self.assertEqual(dati["flussi"], self.ufficio.flussi)
        self.assertEqual(dati["eventi"], self.ufficio.eventi)

    def test_stato_include_statistiche(self):
        class TelemetriaFinta:
            def sintesi(self):
                return {"totale_claude": 7, "totale_gemini": 3, "totale_errori": 1, "top_agenti": []}

        self.ufficio.telemetria = TelemetriaFinta()
        dati = self.json("GET", "/api/stato")
        self.assertEqual(dati["statistiche"]["totale_claude"], 7)
        self.assertEqual(dati["statistiche"]["totale_gemini"], 3)
        self.assertEqual(dati["statistiche"]["totale_errori"], 1)

    def test_nessun_header_cors(self):
        _, header, _ = self.richiesta("GET", "/api/stato")
        self.assertFalse([k for k in header if k.lower().startswith("access-control")])
        self.assertEqual(header["Cache-Control"], "no-store")

    def test_avvisi_web(self):
        avvisi = AvvisiWeb()
        for i in range(25):
            avvisi.aggiungi(f"a{i}")
        self.assertEqual([x["testo"] for x in avvisi.ultimi(3)], ["a22", "a23", "a24"])
        # ISO completo come la chat: la pagina li confronta come stringhe anche a cavallo della mezzanotte
        self.assertEqual(datetime.fromisoformat(avvisi.ultimi(1)[0]["t"]).isoformat(), avvisi.ultimi(1)[0]["t"])

    def test_connessione_mezza_aperta_non_blocca_un_thread(self):
        self.assertEqual(self.server.RequestHandlerClass.timeout, 30)


class TestConfigWeb(unittest.TestCase):
    BASE = "TELEGRAM_TOKEN=t\nTELEGRAM_USER_ID=1\nGEMINI_API_KEY=g\n"

    def _carica(self, extra: str):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text(self.BASE + extra, encoding="utf-8")
            return carica_config(env)

    def test_valori_impostati(self):
        cfg = self._carica("WEB_HOST=100.113.116.92\nWEB_PORT=9000\n")
        self.assertEqual((cfg.web_host, cfg.web_porta), ("100.113.116.92", 9000))

    def test_default(self):
        cfg = self._carica("")
        self.assertEqual((cfg.web_host, cfg.web_porta), ("", 8090))

    def test_porta_non_numerica(self):
        with self.assertRaises(ValueError):
            self._carica("WEB_PORT=abc\n")

    def test_porta_fuori_intervallo(self):
        for porta in ("0", "65536", "99999", "-1"):
            with self.assertRaisesRegex(ValueError, "WEB_PORT deve essere fra 1 e 65535"):
                self._carica(f"WEB_PORT={porta}\n")
        self.assertEqual(self._carica("WEB_PORT=65535\n").web_porta, 65535)


class TestCartellaEdificio(unittest.TestCase):
    def test_edificio_pesa_meno_di_un_mega(self):
        cartella = Path(__file__).parent.parent / "edificio"
        self.assertTrue((cartella / "vendor" / "three.module.min.js").exists())
        self.assertLess(sum(f.stat().st_size for f in cartella.rglob("*") if f.is_file()), 1_000_000)

    def test_scheda_agente_ha_bottone_crea_ordine(self):
        app_js = (Path(__file__).parent.parent / "edificio" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Crea ordine di lavoro", app_js)
        self.assertIn("/api/ordine", app_js)

    def test_edificio_mostra_flussi_verticali_e_schermo_vivo(self):
        app_js = (Path(__file__).parent.parent / "edificio" / "app.js").read_text(encoding="utf-8")
        index_html = (Path(__file__).parent.parent / "edificio" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("cdn.", index_html.lower())
        self.assertIn("stato.flussi", app_js)
        self.assertIn("stato.eventi", app_js)
        self.assertIn("aggiornaFlussiEsterni", app_js)
        self.assertIn("aggiornaSchermo", app_js)
        self.assertIn("schermoTex.needsUpdate = true", app_js)

    def test_edificio_mostra_statistiche_telemetria(self):
        app_js = (Path(__file__).parent.parent / "edificio" / "app.js").read_text(encoding="utf-8")
        self.assertIn("stato.statistiche", app_js)
        self.assertIn("totale_claude", app_js)
        self.assertIn("mai_usati", app_js)

    def test_scheda_segretaria_ha_avatar_video_e_frame(self):
        cartella = Path(__file__).parent.parent / "edificio"
        app_js = (cartella / "app.js").read_text(encoding="utf-8")
        for nome in ("greta.mp4", "greta-portrait.webp", "greta-full.webp"):
            p = cartella / "assets" / nome
            self.assertTrue(p.is_file() and p.stat().st_size > 0, f"Manca asset web {p}")
        for token in ("schedaAvatarGreta", "assets/greta.mp4", "assets/greta-portrait.webp", "assets/greta-full.webp"):
            self.assertIn(token, app_js)

    def test_assets_master_greta_salvati(self):
        master = Path(__file__).parent.parent / "assets" / "greta"
        for nome in ("greta.mp4", "greta-portrait.png", "greta-full.png"):
            p = master / nome
            self.assertTrue(p.is_file() and p.stat().st_size > 0, f"Manca asset master {p}")


class TestEdificioInBackground(unittest.TestCase):
    def _gira(self, esiti):
        from portineria.__main__ import avvia_edificio
        chiamate, pause = [], []

        def avvia():
            chiamate.append(1)
            e = esiti.pop(0)
            if e is not None:
                raise e
        with self.assertLogs("portineria", level="DEBUG") as log:
            avvia_edificio(avvia, "100.1.2.3", 8090, dormi=pause.append).join(3)
        return len(chiamate), pause, [(r.levelname, r.getMessage(), r.exc_info is not None) for r in log.records]

    def test_ritenta_finche_l_ip_non_c_e(self):
        n, pause, log = self._gira([OSError("Cannot assign requested address"), OSError("idem"), None])
        self.assertEqual((n, pause), (3, [30, 30]))
        # il primo errore con l'eccezione, poi una riga DEBUG per tentativo, infine l'indirizzo
        self.assertEqual([(livello, eccezione) for livello, _, eccezione in log],
                         [("ERROR", True), ("DEBUG", False), ("INFO", False)])
        self.assertEqual(log[-1][1], "Edificio su http://100.1.2.3:8090")

    def test_errori_che_non_si_risolvono_da_soli_fermano_solo_l_edificio(self):
        for e in (OverflowError("porta"), ValueError("agenti su piani inesistenti")):
            n, pause, log = self._gira([e])
            self.assertEqual((n, pause), (1, []))
            self.assertEqual([(livello, eccezione) for livello, _, eccezione in log], [("ERROR", True)])


class TestTelemetriaEsternaWeb(unittest.TestCase):
    def setUp(self):
        import time
        from portineria.coda import CodaClaude
        from portineria.config import Config
        from portineria.motori import firma_richiesta_esecutore
        from portineria.ufficio import Motori, Ufficio

        self._firma = firma_richiesta_esecutore
        self._time = time
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        (base / "statici").mkdir()
        (base / "statici" / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
        (base / "vault").mkdir()
        (base / "app" / "prompts").mkdir(parents=True)
        for a in ORG["agenti"]:
            (base / "app" / "prompts" / f"{a['id']}.md").write_text("p", encoding="utf-8")
        self.cfg = Config("t", 5, "k", "gemini-3.8-flash", "claude", base, ordini_hmac_key="chiave-web-hmac")
        self.ufficio = Ufficio(
            Organigramma(ORG),
            Cronologia(base / "stato" / "chat"),
            CodaClaude(),
            Motori(lambda *_: "g", lambda *_: "c"),
            lambda *_: None,
            self.cfg,
            in_parallelo=lambda f, *a: f(*a),
        )
        self.server = avvia_web("127.0.0.1", 0, self.ufficio, base / "statici", AvvisiWeb())
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.porta = self.server.server_address[1]

    def _post(self, percorso, dati, firma=None, ts=None):
        corpo = json.dumps(dati, ensure_ascii=False).encode("utf-8")
        ts_eff = str(int(self._time.time())) if ts is None else ts
        firma_eff = self._firma(self.cfg.ordini_hmac_key, ts_eff, corpo) if firma is None else firma
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=5)
        h = {
            "Host": f"127.0.0.1:{self.porta}",
            "Content-Type": "application/json",
            "Content-Length": str(len(corpo)),
        }
        if ts_eff:
            h["X-Timestamp"] = ts_eff
        if firma_eff:
            h["X-Firma"] = firma_eff
        c.request("POST", percorso, body=corpo, headers=h)
        r = c.getresponse()
        body = json.loads(r.read())
        c.close()
        return r.status, body

    def _stato(self):
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=5)
        c.request("GET", "/api/stato", headers={"Host": f"127.0.0.1:{self.porta}"})
        r = c.getresponse()
        body = json.loads(r.read())
        c.close()
        return body

    def test_firma_mancante_o_errata_rifiutata_403(self):
        stato, _ = self._post("/api/telemetria-esterna", {"azione": "avvia", "agente": "celia"}, firma="")
        self.assertEqual(stato, 403)
        stato2, _ = self._post("/api/telemetria-esterna", {"azione": "avvia", "agente": "celia"}, firma="falsa")
        self.assertEqual(stato2, 403)
        stato3, _ = self._post(
            "/api/telemetria-esterna",
            {"azione": "avvia", "agente": "celia"},
            ts=str(int(self._time.time()) - 400),
        )
        self.assertEqual(stato3, 403)
        self.assertEqual(self._stato()["lavorando"], [])

        # Anche con chiave vuota sul server (fail closed)
        self.cfg.ordini_hmac_key = ""
        stato4, _ = self._post("/api/telemetria-esterna", {"azione": "avvia", "agente": "celia"}, firma="x")
        self.assertEqual(stato4, 403)

    def test_ciclo_avvia_fine_pulisci_su_stato_live(self):
        stato, _ = self._post(
            "/api/telemetria-esterna",
            {
                "azione": "avvia",
                "agente": "celia",
                "da_agente": "direttore",
                "tipo": "riunione",
                "commessa": "Landing",
                "nota": "Design v2",
                "sorgente": "antigravity",
            },
        )
        self.assertEqual(stato, 200)
        s = self._stato()
        self.assertIn("celia", s["lavorando"])
        self.assertEqual(len(s["flussi"]), 1)
        self.assertEqual(s["flussi"][0]["da_agente"], "direttore")
        self.assertEqual(s["flussi"][0]["a_agente"], "celia")
        self.assertEqual(s["eventi"]["direzione"]["testo"], "Celia: Design v2")

        stato_fine, _ = self._post(
            "/api/telemetria-esterna",
            {"azione": "fine", "agente": "celia", "motore": "gemini", "sorgente": "antigravity"},
        )
        self.assertEqual(stato_fine, 200)
        s2 = self._stato()
        self.assertEqual(s2["lavorando"], [])
        self.assertEqual(s2["flussi"], [])
        self.assertEqual(s2["statistiche"]["totale_gemini"], 1)

        # pulisci funziona anche senza campo agente
        self._post("/api/telemetria-esterna", {"azione": "avvia", "agente": "celia"})
        self.assertEqual(self._stato()["lavorando"], ["celia"])
        stato_pul, _ = self._post("/api/telemetria-esterna", {"azione": "pulisci"})
        self.assertEqual(stato_pul, 200)
        self.assertEqual(self._stato()["lavorando"], [])

    def test_errori_input_telemetria(self):
        self.assertEqual(self._post("/api/telemetria-esterna", {"azione": "avvia", "agente": "boh"})[0], 404)
        self.assertEqual(self._post("/api/telemetria-esterna", {"azione": "invalid", "agente": "celia"})[0], 400)
        self.assertEqual(self._post("/api/telemetria-esterna", {"azione": "avvia"})[0], 400)

