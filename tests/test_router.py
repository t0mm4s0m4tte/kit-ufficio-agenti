import json
import tempfile
import unittest
from pathlib import Path

from portineria.config import carica_config
from portineria.organigramma import Organigramma
from portineria.router import instrada

PIANI = [
    {"id": "direzione", "numero": 10, "nome": "Direzione", "emoji": "🏛", "colore": "", "capo": "direttore", "team": []},
    {"id": "marketing", "numero": 8, "nome": "Marketing & Social", "emoji": "📣", "colore": "", "capo": "marketing-growth-hacker", "team": ["seo"]},
    {"id": "vendite", "numero": 7, "nome": "Vendite & E-commerce", "emoji": "🛒", "colore": "", "capo": "sales-deal-strategist", "team": []},
]


def agente(id_, nome, piano, ruolo="operaio", team=None, motore="gemini", descrizione="desc"):
    return {"id": id_, "nome": nome, "descrizione": descrizione, "piano": piano, "team": team, "ruolo": ruolo,
            "capo": None, "motore": motore, "fonte": "agency", "prompt_path": f"prompts/{id_}.md"}


AGENTI = [
    agente("direttore", "Direttore", "direzione", "direttore", motore="claude"),
    agente("marketing-growth-hacker", "Growth Hacker", "marketing", "capo", motore="claude"),
    agente("marketing-carousel-growth-engine", "Carousel Growth Engine", "marketing", team="seo"),
    agente("personal-growth-mentor", "Personal Growth Mentor", "marketing"),
    agente("celia-cold-email", "Celia", "vendite", descrizione="Cold email specialist"),
]


def scrivi_organigramma(cartella: Path, agenti=AGENTI, piani=PIANI) -> Path:
    p = cartella / "organigramma.json"
    p.write_text(json.dumps({"generato": "t", "piani": piani, "agenti": agenti}), encoding="utf-8")
    return p


class TestRouter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.org = Organigramma.carica(scrivi_organigramma(self.dir))

    def tearDown(self):
        self._tmp.cleanup()

    def test_id_esatto(self):
        r = instrada("@celia-cold-email scrivi una mail", self.org)
        self.assertEqual((r.tipo, r.agente_id, r.testo), ("agente", "celia-cold-email", "scrivi una mail"))

    def test_nome_esatto_case_insensitive(self):
        self.assertEqual(instrada("@CELIA ciao", self.org).agente_id, "celia-cold-email")

    def test_sottostringa_univoca(self):
        self.assertEqual(instrada("@growth-hacker idea", self.org).agente_id, "marketing-growth-hacker")

    def test_agente_senza_testo(self):
        r = instrada("@celia", self.org)
        self.assertEqual((r.tipo, r.testo), ("agente", ""))

    def test_ambiguo(self):
        r = instrada("@growth idea", self.org)
        self.assertEqual(r.tipo, "ambiguo")
        self.assertEqual(len(r.candidati), 3)

    def test_sconosciuto(self):
        r = instrada("@nessuno ciao", self.org)
        self.assertEqual((r.tipo, r.testo), ("sconosciuto", "nessuno"))

    def test_comando_con_nome_bot(self):
        r = instrada("/chi@UfficioBot seo", self.org)
        self.assertEqual((r.tipo, r.comando, r.argomenti), ("comando", "chi", "seo"))

    def test_testo_libero_va_al_direttore(self):
        r = instrada("  mi serve un logo ", self.org)
        self.assertEqual((r.tipo, r.testo), ("direttore", "mi serve un logo"))

    def test_organigramma_query(self):
        self.assertEqual(self.org.get("celia-cold-email")["nome"], "Celia")
        self.assertIsNone(self.org.get("x"))
        self.assertEqual(self.org.piano("vendite")["nome"], "Vendite & E-commerce")
        self.assertEqual([a["id"] for a in self.org.capi()], ["marketing-growth-hacker"])
        self.assertEqual([a["id"] for a in self.org.membri("marketing")],
                         ["marketing-carousel-growth-engine", "personal-growth-mentor"])
        self.assertEqual([a["id"] for a in self.org.membri("marketing", "seo")], ["marketing-carousel-growth-engine"])
        self.assertEqual([a["id"] for a in self.org.cerca("COLD")], ["celia-cold-email"])
        self.assertEqual(self.org.elenco_compatto([self.org.get("celia-cold-email")]),
                         "celia-cold-email | vendite | Cold email specialist")

    def test_config_chiave_mancante(self):
        env = self.dir / ".env"
        env.write_text("TELEGRAM_USER_ID=5\nGEMINI_API_KEY=k\n", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            carica_config(env)
        self.assertIn("TELEGRAM_TOKEN", str(ctx.exception))

    def test_config_default_e_percorsi(self):
        env = self.dir / ".env"
        env.write_text('# commento\n\nTELEGRAM_TOKEN="abc"\nTELEGRAM_USER_ID=5\nGEMINI_API_KEY=k\n', encoding="utf-8")
        cfg = carica_config(env)
        self.assertEqual((cfg.telegram_token, cfg.telegram_user_id), ("abc", 5))
        self.assertEqual(cfg.gemini_model, "gemini-4-hydrogen")
        self.assertEqual(cfg.claude_bin, "claude")
        self.assertEqual(cfg.vault, Path.home() / "ufficio" / "vault")
        self.assertEqual(cfg.env_path, Path.home() / "ufficio" / ".env")
        self.assertEqual(cfg.claude_token, "")
        env.write_text("TELEGRAM_TOKEN=a\nTELEGRAM_USER_ID=5\nGEMINI_API_KEY=k\nCLAUDE_CODE_OAUTH_TOKEN=xyz\n",
                       encoding="utf-8")
        self.assertEqual(carica_config(env).claude_token, "xyz")


if __name__ == "__main__":
    unittest.main()
