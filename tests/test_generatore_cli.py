import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from generatore.__main__ import main
from tests.test_fonti import home_finta

GERARCHIA_MINIMA = """
[ufficio]
piano_default = "specialisti"

[motori]

[piani.direzione]
numero = 10
nome = "Direzione"
emoji = "🏛"
colore = "#000"
capo = "direttore"
motore_operai = "claude"

[piani.specialisti]
numero = 2
nome = "Specialisti"
emoji = "🔬"
colore = "#111"
capo = "{capo}"
motore_operai = "gemini"
"""


class TestGeneratoreCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.home = base / "home"
        self.out = base / "out"
        home_finta(self.home)
        (self.out / "prompts").mkdir(parents=True)
        (self.out / "prompts" / "_direttore.md").write_text("PROMPT DIRETTORE", encoding="utf-8")
        self.scrivi_gerarchia("celia-cold-email")

    def tearDown(self):
        self._tmp.cleanup()

    def scrivi_gerarchia(self, capo):
        (self.out / "gerarchia.toml").write_text(GERARCHIA_MINIMA.replace("{capo}", capo), encoding="utf-8")

    def esegui(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(["--home", str(self.home), "--out", str(self.out)])

    def test_scrive_organigramma_e_prompt(self):
        self.assertEqual(self.esegui(), 0)
        org = json.loads((self.out / "organigramma.json").read_text(encoding="utf-8"))
        per_id = {a["id"]: a for a in org["agenti"]}
        self.assertEqual(per_id["direttore"]["prompt_path"], "prompts/_direttore.md")
        self.assertEqual(per_id["direttore"]["ruolo"], "direttore")
        self.assertTrue((self.out / "prompts" / "celia-cold-email.md").exists())
        self.assertFalse((self.out / "prompts" / "direttore.md").exists())
        self.assertEqual((self.out / "prompts" / "_direttore.md").read_text(encoding="utf-8"), "PROMPT DIRETTORE")

    def test_archivisti_sintetici(self):
        (self.out / "prompts" / "_bibliotecario.md").write_text("MIO", encoding="utf-8")
        self.assertEqual(self.esegui(), 0)
        org = json.loads((self.out / "organigramma.json").read_text(encoding="utf-8"))
        per_id = {a["id"]: a for a in org["agenti"]}
        for i in ("archivista-notturno", "archivista-fonti", "bibliotecario", "capo-archivista"):
            self.assertEqual(per_id[i]["prompt_path"], f"prompts/_{i}.md")
            self.assertEqual(per_id[i]["fonte"], "ufficio")
        self.assertEqual(per_id["bibliotecario"]["nome"], "Bibliotecario")
        self.assertEqual((self.out / "prompts" / "_bibliotecario.md").read_text(encoding="utf-8"), "MIO")
        self.assertFalse((self.out / "prompts" / "bibliotecario.md").exists())

    def test_errori_bloccano_la_scrittura(self):
        self.scrivi_gerarchia("nessuno-cosi")
        self.assertEqual(self.esegui(), 1)
        self.assertFalse((self.out / "organigramma.json").exists())

    def test_rimuove_prompt_orfani(self):
        (self.out / "prompts" / "vecchio.md").write_text("x", encoding="utf-8")
        self.assertEqual(self.esegui(), 0)
        self.assertFalse((self.out / "prompts" / "vecchio.md").exists())
        self.assertTrue((self.out / "prompts" / "_direttore.md").exists())


if __name__ == "__main__":
    unittest.main()
