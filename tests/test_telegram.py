import tempfile
import unittest
from pathlib import Path

from portineria.telegram import Telegram, a_html, autorizzato, estrai, leggi_offset, salva_offset, spezza


class GruppiFinti:
    def __init__(self, collegati):
        self.collegati = collegati

    def stanza(self, chat_id, thread_id):
        if (chat_id, thread_id) in self.collegati or (chat_id, None) in self.collegati:
            return {"piano": "marketing", "team": None}
        return None


class TestTelegram(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_spezza_su_a_capo(self):
        self.assertEqual(spezza("a" * 3000 + "\n" + "b" * 3000), ["a" * 3000, "b" * 3000])

    def test_spezza_senza_a_capo(self):
        self.assertEqual([len(p) for p in spezza("x" * 9000)], [4000, 4000, 1000])

    def test_spezza_corto_e_vuoto(self):
        self.assertEqual(spezza("ciao"), ["ciao"])
        self.assertEqual(spezza(""), [])

    def test_estrai_foto_senza_testo(self):  # Review Focus 1
        m = estrai({"update_id": 7, "message": {"from": {"id": 5}, "chat": {"id": 5, "type": "private"}, "photo": [{}]}})
        self.assertIsNone(m["testo"])
        self.assertEqual((m["update_id"], m["user_id"], m["chat_tipo"]), (7, 5, "private"))

    def test_estrai_thread(self):
        m = estrai({"update_id": 8, "message": {"from": {"id": 5}, "chat": {"id": -100, "type": "supergroup"},
                                                "message_thread_id": 3, "is_topic_message": True, "text": "ciao"}})
        self.assertEqual((m["chat_id"], m["thread_id"], m["chat_tipo"], m["testo"]), (-100, 3, "supergroup", "ciao"))

    def test_estrai_risposta_fuori_dai_forum_senza_thread(self):
        m = estrai({"update_id": 8, "message": {"from": {"id": 5}, "chat": {"id": -100, "type": "supergroup"},
                                                "message_thread_id": 44, "text": "ciao"}})
        self.assertIsNone(m["thread_id"])

    def test_estrai_senza_message(self):
        self.assertIsNone(estrai({"update_id": 9, "edited_message": {}}))

    def test_offset_persistito(self):  # Review Focus 5
        p = self.dir / "offset.txt"
        self.assertEqual(leggi_offset(p), 0)
        salva_offset(p, 42)
        self.assertEqual(leggi_offset(p), 42)
        p.write_text("rotto", encoding="utf-8")
        self.assertEqual(leggi_offset(p), 0)

    def test_autorizzazione(self):
        g = GruppiFinti({(-100, 3)})
        privato = {"user_id": 5, "chat_tipo": "private", "chat_id": 5, "thread_id": None, "testo": "x"}
        self.assertTrue(autorizzato(privato, 5, g))
        self.assertFalse(autorizzato({**privato, "user_id": 6}, 5, g))
        gruppo = {"user_id": 5, "chat_tipo": "supergroup", "chat_id": -200, "thread_id": None, "testo": "x"}
        self.assertFalse(autorizzato(gruppo, 5, g))
        self.assertTrue(autorizzato({**gruppo, "testo": "/collega marketing"}, 5, g))
        self.assertFalse(autorizzato({**gruppo, "user_id": 6, "testo": "/collega marketing"}, 5, g))
        self.assertTrue(autorizzato({**gruppo, "chat_id": -100, "thread_id": 3}, 5, g))
        self.assertFalse(autorizzato({**gruppo, "testo": None}, 5, g))

    def test_invia_usa_thread_e_spezza(self):
        chiamate = []

        def post(url, headers, corpo, timeout):
            chiamate.append((url, corpo))
            return 200, {"ok": True}

        Telegram("TOK", post=post).invia(-100, "x" * 5000, 3)
        self.assertEqual(len(chiamate), 2)
        self.assertTrue(all(u.endswith("/botTOK/sendMessage") for u, _ in chiamate))
        self.assertTrue(all(c["message_thread_id"] == 3 and c["chat_id"] == -100 for _, c in chiamate))

    def test_html_inline(self):
        self.assertEqual(a_html("**ciao** e `x_y` <b> & [GH](https://g.it/a?b=1&c=2)"),
                         '<b>ciao</b> e <code>x_y</code> &lt;b&gt; &amp; <a href="https://g.it/a?b=1&amp;c=2">GH</a>')

    def test_html_titoli_e_blocchi_di_codice(self):
        self.assertEqual(a_html("## Candidate\nok"), "<b>Candidate</b>\nok")
        self.assertEqual(a_html("```\na < b\n```"), "<pre>a &lt; b</pre>")
        self.assertEqual(a_html("```\naperto"), "<pre>aperto</pre>")

    def test_html_tabella_diventa_elenco(self):
        tab = "| # | Opzione | Costo |\n|---|---|---|\n| 1 | **labelr** | Gratis |\n| 2 | Inbox Zero | Self-host |"
        self.assertEqual(a_html(tab), "• <b>1</b>\n   Opzione: <b>labelr</b>\n   Costo: Gratis\n"
                                      "• <b>2</b>\n   Opzione: Inbox Zero\n   Costo: Self-host")

    def test_html_non_tocca_underscore_e_asterischi_singoli(self):
        self.assertEqual(a_html("agente_id * 2 e _x_"), "agente_id * 2 e _x_")

    def test_invia_html_con_anteprima_link(self):
        chiamate = []

        def post(url, headers, corpo, timeout):
            chiamate.append(corpo)
            return 200, {"ok": True}

        Telegram("TOK", post=post).invia(5, "**ciao**")
        self.assertEqual((chiamate[0]["text"], chiamate[0]["parse_mode"]), ("<b>ciao</b>", "HTML"))
        self.assertTrue(chiamate[0]["link_preview_options"]["prefer_large_media"])

    def test_invia_html_rifiutato_ripiega_su_testo_semplice(self):
        chiamate = []

        def post(url, headers, corpo, timeout):
            chiamate.append(corpo)
            return (400, {"description": "can't parse entities"}) if "parse_mode" in corpo else (200, {"ok": True})

        with self.assertLogs(level="WARNING"):
            Telegram("TOK", post=post).invia(5, "**ciao**")
        self.assertEqual(chiamate[1], {"chat_id": 5, "text": "**ciao**"})

    def test_invia_errore_di_rete_ignorato(self):
        def post(*a, **k):
            raise OSError("rete giù")

        with self.assertLogs(level="ERROR"):
            Telegram("TOK", post=post).invia(5, "ciao")

    def test_aggiornamenti(self):
        visto = {}

        def post(url, headers, corpo, timeout):
            visto.update(url=url, corpo=corpo, timeout=timeout)
            return 200, {"ok": True, "result": [{"update_id": 1}]}

        self.assertEqual(Telegram("TOK", post=post).aggiornamenti(10), [{"update_id": 1}])
        self.assertTrue(visto["url"].endswith("/getUpdates"))
        self.assertEqual(visto["corpo"], {"offset": 10, "timeout": 50, "allowed_updates": ["message"]})
        self.assertEqual(visto["timeout"], 60)


class TgFinto:
    def __init__(self):
        self.inviati = []

    def invia(self, chat_id, testo, thread_id=None):
        self.inviati.append((chat_id, testo, thread_id))


class UfficioFinto:
    def __init__(self):
        self.privati, self.gruppo = [], []

    def gestisci(self, chat_id, testo, thread_id=None):
        self.privati.append((chat_id, testo, thread_id))

    def gestisci_gruppo(self, chat_id, thread_id, testo, gruppi):
        self.gruppo.append((chat_id, thread_id, testo))


class TestElabora(unittest.TestCase):
    def setUp(self):
        from portineria.__main__ import elabora
        self.elabora = elabora
        self.tg, self.uff, self.g = TgFinto(), UfficioFinto(), GruppiFinti(set())
        self.msg = {"update_id": 1, "user_id": 5, "chat_id": 5, "chat_tipo": "private", "thread_id": None, "testo": "ciao"}

    def test_testo_privato_va_all_ufficio(self):
        self.elabora(self.msg, 5, self.tg, self.uff, self.g)
        self.assertEqual(self.uff.privati, [(5, "ciao", None)])

    def test_messaggio_di_servizio_ignorato(self):
        # argomento creato, messaggio fissato, membro aggiunto: niente testo e niente contenuto
        self.elabora({**self.msg, "testo": None, "contenuto": False}, 5, self.tg, self.uff, self.g)
        self.assertEqual((self.tg.inviati, self.uff.privati), ([], []))

    def test_estrai_segna_il_contenuto(self):
        foto = estrai({"update_id": 7, "message": {"from": {"id": 5}, "chat": {"id": 5, "type": "private"}, "photo": [{}]}})
        servizio = estrai({"update_id": 8, "message": {"from": {"id": 5}, "chat": {"id": 5, "type": "private"},
                                                       "forum_topic_created": {"name": "SEO"}}})
        self.assertEqual((foto["contenuto"], servizio["contenuto"]), (True, False))

    def test_foto_risponde_solo_testo(self):  # Review Focus 1
        self.elabora({**self.msg, "testo": None, "contenuto": True}, 5, self.tg, self.uff, self.g)
        self.assertEqual(self.tg.inviati, [(5, "Per ora capisco solo messaggi di testo.", None)])
        self.assertEqual(self.uff.privati, [])

    def test_non_autorizzato_silenzio_e_log(self):
        with self.assertLogs(level="WARNING") as cm:
            self.elabora({**self.msg, "user_id": 6}, 5, self.tg, self.uff, self.g)
        self.assertIn("non autorizzato user=6 chat=5", cm.output[0])
        self.assertEqual((self.tg.inviati, self.uff.privati), ([], []))

    def test_gruppo_va_a_gestisci_gruppo(self):
        g = GruppiFinti({(-100, 3)})
        self.elabora({**self.msg, "chat_id": -100, "chat_tipo": "supergroup", "thread_id": 3}, 5, self.tg, self.uff, g)
        self.assertEqual(self.uff.gruppo, [(-100, 3, "ciao")])
        self.assertEqual(self.uff.privati, [])

    def test_update_senza_messaggio(self):
        self.elabora(None, 5, self.tg, self.uff, self.g)
        self.assertEqual((self.tg.inviati, self.uff.privati), ([], []))


if __name__ == "__main__":
    unittest.main()
