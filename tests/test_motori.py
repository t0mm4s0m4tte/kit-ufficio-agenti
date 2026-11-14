import subprocess
import unittest
from dataclasses import replace
from pathlib import PurePosixPath

from portineria.config import Config
from portineria.motori import (ErroreMotore, LimiteClaude, QuotaGeminiEsaurita, TimeoutAgente, argomenti_claude,
                               chiedi_claude, chiedi_gemini, chiedi_rapido, testo_claude)

CFG = Config("t", 5, "k", "gemini-2.5-flash", "claude", PurePosixPath("/home/tommy/ufficio"))


class TestMotori(unittest.TestCase):
    def test_gemini_costruisce_richiesta_e_legge_testo(self):
        visto = {}

        def post(url, headers, corpo, timeout):
            visto.update(url=url, headers=headers, corpo=corpo, timeout=timeout)
            return 200, {"candidates": [{"content": {"parts": [{"text": "Ciao "}, {"text": "Tommy"}]}}]}

        r = chiedi_gemini("SYS", [{"ruolo": "utente", "testo": "a"}, {"ruolo": "agente", "testo": "b"}], "c", CFG, post=post)
        self.assertEqual(r, "Ciao Tommy")
        self.assertIn("gemini-2.5-flash:generateContent", visto["url"])
        self.assertEqual(visto["headers"]["x-goog-api-key"], "k")
        self.assertEqual(visto["corpo"]["systemInstruction"], {"parts": [{"text": "SYS"}]})
        self.assertEqual([c["role"] for c in visto["corpo"]["contents"]], ["user", "model", "user"])
        self.assertEqual(visto["corpo"]["contents"][-1]["parts"][0]["text"], "c")
        self.assertEqual(visto["timeout"], 120)

    def test_gemini_429(self):
        with self.assertRaises(QuotaGeminiEsaurita):
            chiedi_gemini("S", [], "t", CFG, post=lambda *a, **k: (429, {}))

    def test_gemini_errore_http_e_senza_testo(self):
        with self.assertRaises(ErroreMotore) as ctx:
            chiedi_gemini("S", [], "t", CFG, post=lambda *a, **k: (500, {"error": {"message": "boom"}}))
        self.assertIn("boom", str(ctx.exception))
        with self.assertRaises(ErroreMotore):
            chiedi_gemini("S", [], "t", CFG, post=lambda *a, **k: (200, {"candidates": []}))

    def test_omniroute_formato_openai(self):
        visto = {}

        def post(url, headers, corpo, timeout):
            visto.update(url=url, headers=headers, corpo=corpo)
            return 200, {"choices": [{"message": {"content": "Ciao"}}]}

        cfg = replace(CFG, omniroute_url="http://pc:20128/v1/", omniroute_key="ok")
        r = chiedi_rapido("SYS", [{"ruolo": "utente", "testo": "a"}, {"ruolo": "agente", "testo": "b"}], "c", cfg,
                          post=post)
        self.assertEqual(r, "Ciao")
        self.assertEqual(visto["url"], "http://pc:20128/v1/chat/completions")
        self.assertEqual(visto["headers"]["Authorization"], "Bearer ok")
        self.assertEqual(visto["corpo"]["model"], "auto")
        self.assertEqual([m["role"] for m in visto["corpo"]["messages"]], ["system", "user", "assistant", "user"])

    def test_omniroute_giu_ripiega_su_gemini(self):
        urls = []

        def post(url, headers, corpo, timeout):
            urls.append(url)
            if "20128" in url:
                raise OSError("PC spento")
            return 200, {"candidates": [{"content": {"parts": [{"text": "da Gemini"}]}}]}

        cfg = replace(CFG, omniroute_url="http://pc:20128/v1", omniroute_key="ok")
        self.assertEqual(chiedi_rapido("S", [], "t", cfg, post=post), "da Gemini")
        self.assertEqual(len(urls), 2)
        # senza OmniRoute configurato va dritto a Gemini
        urls.clear()
        chiedi_rapido("S", [], "t", CFG, post=post)
        self.assertNotIn("20128", urls[0])

    def test_argomenti_claude_abilitano_bash_e_negano_segreti(self):
        a = argomenti_claude(PurePosixPath("/home/tommy/ufficio/app/prompts/x.md"), CFG)
        self.assertEqual(a[:5], ["claude", "-p", "--output-format", "text", "--append-system-prompt-file"])
        self.assertIn("--permission-mode", a)
        self.assertEqual(a[a.index("--permission-mode") + 1], "acceptEdits")
        negati = a[a.index("--disallowedTools") + 1:]
        self.assertNotIn("Bash", negati)
        for regola in ("Read(//home/tommy/ufficio/.env)", "Edit(//home/tommy/ufficio/.env)",
                       "Read(//home/tommy/.ssh/**)", "Read(//home/tommy/.claude/**)",
                       "Edit(//home/tommy/ufficio/app/**)", "Edit(//home/tommy/ufficio/stato/**)",
                       "Bash(*/home/tommy/ufficio/.env*)"):
            self.assertIn(regola, negati)
        # Claude Code 2.1.3xx rifiuta Write(percorso): Edit(percorso) copre tutte le scritture
        self.assertIn("Edit(//home/tommy/ufficio/vault/**)", a)
        self.assertFalse(any(x.startswith("Write(") for x in a))
        permessi = a[a.index("--allowedTools"):a.index("--disallowedTools")]
        self.assertIn("Bash", permessi)
        self.assertIn("Agent", permessi)
        # Grep/Glob senza percorso varrebbero su tutto il Pi: nel vault bastano le regole Read
        self.assertNotIn("Grep", permessi)
        self.assertNotIn("Glob", permessi)
        self.assertIn("Read(//proc/**)", negati)

    def test_claude_pc_worker_firmato_e_fallback_sul_pi(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "p.md"
            p.write_text("PROMPT AGENTE", encoding="utf-8")
            cfg = replace(CFG, claude_token="TOKCLAUDE", ordini_hmac_key="chiave-hmac",
                          esecutore_url="http://100.121.89.90:8771/")
            chiamate = []

            def post_ok(url, headers, corpo, timeout):
                chiamate.append((url, headers, corpo, timeout))
                return 200, {"ok": True, "risposta": "  eseguito su pc  "}

            r = chiedi_claude(p, [{"ruolo": "utente", "testo": "a"}], "test", cfg,
                              run=lambda *a, **k: self.fail("non doveva usare il Pi"), post=post_ok)
            self.assertEqual(r, "eseguito su pc")
            self.assertEqual(chiamate[0][0], "http://100.121.89.90:8771/chiedi")
            self.assertIn("X-Timestamp", chiamate[0][1])
            self.assertIn("X-Firma", chiamate[0][1])
            self.assertEqual(chiamate[0][2]["sistema"], "PROMPT AGENTE")
            self.assertEqual(chiamate[0][2]["vault_pi"], "/home/tommy/ufficio/vault")
            self.assertEqual(chiamate[0][2]["claude_token"], "TOKCLAUDE")

            # Se il PC è spento/irraggiungibile ripiega sul Pi locale
            def post_giu(url, headers, corpo, timeout):
                raise OSError("PC spento")

            usato_pi = []

            def run_pi(args, **k):
                usato_pi.append(args)
                return subprocess.CompletedProcess(args, 0, "ok dal pi", "")

            r2 = chiedi_claude(p, [], "ciao", cfg, run=run_pi, post=post_giu)
            self.assertEqual(r2, "ok dal pi")
            self.assertEqual(len(usato_pi), 1)

            # Limite Claude dal PC propaga LimiteClaude (per /gemini)
            def post_limite(url, headers, corpo, timeout):
                return 429, {"ok": False, "errore_tipo": "limite", "errore": "5-hour limit reached"}

            with self.assertRaises(LimiteClaude):
                chiedi_claude(p, [], "ciao", cfg, run=run_pi, post=post_limite)

    def test_segretaria_senza_strumenti_e_mai_sul_pc(self):
        # riceve l'agenda con testo di terzi (inviti): nessuno strumento, e nemmeno il PC che ne ha di più
        percorso = PurePosixPath("/home/tommy/ufficio/app/prompts/_segretaria.md")
        a = argomenti_claude(percorso, CFG)
        self.assertEqual(a[a.index("--tools"):], ["--tools", ""])
        for vietato in ("--allowedTools", "--permission-mode"):
            self.assertNotIn(vietato, a)
        self.assertNotIn("Bash", a)
        cfg = replace(CFG, ordini_hmac_key="k", esecutore_url="http://100.121.89.90:8771")
        viste = []

        def run(args, **k):
            viste.append(args)
            return subprocess.CompletedProcess(args, 0, "dal pi", "")

        r = chiedi_claude(percorso, [], "ciao", cfg, run=run,
                          post=lambda *a, **k: self.fail("la segretaria non va mandata al PC"))
        self.assertEqual((r, len(viste)), ("dal pi", 1))
        self.assertIn("--tools", viste[0])

    def test_segreti_negati_agli_altri_agenti(self):
        a = argomenti_claude(PurePosixPath("/home/tommy/ufficio/app/prompts/x.md"), CFG)
        negati = a[a.index("--disallowedTools") + 1:]
        for regola in ("Read(//home/tommy/ufficio/segreti/**)", "Edit(//home/tommy/ufficio/segreti/**)",
                       "Bash(*/home/tommy/ufficio/segreti*)"):
            self.assertIn(regola, negati)

    def test_claude_ambiente_ridotto(self):
        visto = {}

        def run(args, **k):
            visto.update(k)
            return subprocess.CompletedProcess(args, 0, "ok", "")

        cfg = Config("t", 5, "k", "gemini-2.5-flash", "claude", PurePosixPath("/home/tommy/ufficio"), "TOKCLAUDE")
        chiedi_claude(PurePosixPath("p.md"), [], "t", cfg, run=run)
        self.assertEqual(visto["env"]["CLAUDE_CODE_OAUTH_TOKEN"], "TOKCLAUDE")
        self.assertEqual(set(visto["env"]) - {"PATH", "HOME", "LANG", "CLAUDE_CODE_OAUTH_TOKEN"}, set())

    def test_claude_limite_messaggi_nuovi(self):
        for messaggio in (
            "5-hour limit reached ∙ resets 3pm",
            "You've hit your limit · resets 8pm",
            "You've hit your session limit · resets 3:10am (Europe/Rome)",
        ):
            def run(args, **k):
                return subprocess.CompletedProcess(args, 1, messaggio, "")

            with self.assertRaises(LimiteClaude):
                chiedi_claude(PurePosixPath("p.md"), [], "t", CFG, run=run)

    def test_claude_passa_il_testo_su_stdin(self):
        visto = {}

        def run(args, **k):
            visto.update(k)
            return subprocess.CompletedProcess(args, 0, " ok \n", "")

        self.assertEqual(chiedi_claude(PurePosixPath("p.md"), [], "ciao", CFG, run=run), "ok")
        self.assertEqual((visto["input"], visto["timeout"], visto["cwd"]), ("ciao", 300, "/home/tommy/ufficio/vault"))

    def test_claude_timeout(self):
        def run(*a, **k):
            raise subprocess.TimeoutExpired("claude", 300)

        with self.assertRaises(TimeoutAgente):
            chiedi_claude(PurePosixPath("p.md"), [], "t", CFG, run=run)

    def test_claude_limite(self):
        def run(args, **k):
            return subprocess.CompletedProcess(args, 1, "", "Claude usage limit reached. Resets 5pm")

        with self.assertRaises(LimiteClaude) as ctx:
            chiedi_claude(PurePosixPath("p.md"), [], "t", CFG, run=run)
        self.assertIn("Resets 5pm", str(ctx.exception))

    def test_claude_risposta_lunga_che_parla_di_rate_limit_non_e_un_limite(self):
        lunga = "Il rate limit di un'API si gestisce così. " * 20

        def run(args, **k):
            return subprocess.CompletedProcess(args, 0, lunga, "")

        self.assertEqual(chiedi_claude(PurePosixPath("p.md"), [], "t", CFG, run=run), lunga.strip())

    def test_claude_errore_ultime_righe(self):
        def run(args, **k):
            return subprocess.CompletedProcess(args, 2, "", "\n".join(f"r{i}" for i in range(8)))

        with self.assertRaises(ErroreMotore) as ctx:
            chiedi_claude(PurePosixPath("p.md"), [], "t", CFG, run=run)
        self.assertIn("r7", str(ctx.exception))
        self.assertNotIn("r2", str(ctx.exception))

    def test_testo_claude_con_storia(self):
        t = testo_claude([{"ruolo": "utente", "testo": "a"}, {"ruolo": "agente", "testo": "b"}], "c")
        self.assertEqual(t, "Conversazione precedente:\nTommy: a\nTu: b\n\nNuovo messaggio di Tommy:\nc")
        self.assertEqual(testo_claude([], "c"), "c")


if __name__ == "__main__":
    unittest.main()
