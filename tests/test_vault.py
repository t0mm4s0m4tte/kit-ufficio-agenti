import tempfile
import unittest
from unittest import mock
from pathlib import Path

from portineria.vault import CARTELLE, appendi, frontmatter, nome_libero, prepara_vault, scrivi_nuova, sposta


class TestVault(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.v = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_prepara_vault_idempotente(self):
        prepara_vault(self.v)
        (self.v / ".stignore").write_text("mio", encoding="utf-8")
        prepara_vault(self.v)
        for c in CARTELLE:
            self.assertTrue((self.v / c).is_dir(), c)
        self.assertEqual((self.v / ".stignore").read_text(encoding="utf-8"), "mio")

    def test_stignore_creato(self):
        prepara_vault(self.v)
        self.assertIn(".obsidian/workspace*", (self.v / ".stignore").read_text(encoding="utf-8"))

    def test_nome_gia_preso_riceve_suffisso(self):  # Review Focus 3
        (self.v / "Idea.md").write_text("di Tommy", encoding="utf-8")
        p = scrivi_nuova(self.v, "Idea", "nuova")
        self.assertEqual(p.name, "Idea (2).md")
        self.assertEqual(p.read_text(encoding="utf-8"), "nuova")
        self.assertEqual((self.v / "Idea.md").read_text(encoding="utf-8"), "di Tommy")
        self.assertEqual(scrivi_nuova(self.v, "Idea", "terza").name, "Idea (3).md")

    def test_nome_che_cambia_solo_maiuscole_riceve_suffisso(self):
        # sul Pi (ext4) "foo.md" e "Foo.md" sono due file: Syncthing su Windows andrebbe in conflitto
        (self.v / "Foo.md").write_text("di Tommy", encoding="utf-8")
        nomi = {"Foo.md"}
        with mock.patch.object(Path, "exists", lambda self: self.name in nomi):
            self.assertEqual(nome_libero(self.v, "foo").name, "foo (2).md")

    def test_nome_senza_caratteri_di_controllo_e_corto_in_byte(self):
        self.assertEqual(nome_libero(self.v, "a\x00b\tc").name, "a b c.md")
        self.assertLessEqual(len(nome_libero(self.v, "è" * 300).name.encode("utf-8")), 200)

    def test_nome_ripulito(self):
        self.assertEqual(nome_libero(self.v, 'a/b: c?').name, "a b c.md")
        self.assertEqual(nome_libero(self.v, "[[Link#x]]").name, "Link x.md")
        self.assertEqual(len(nome_libero(self.v, "a" * 300).stem), 100)
        self.assertEqual(nome_libero(self.v, "///").name, "senza titolo.md")

    def test_appendi_crea_con_intestazione_poi_aggiunge(self):
        p = self.v / "sotto" / "x.md"
        appendi(p, "uno", "---\ntipo: diario\n---\n")
        appendi(p, "due")
        self.assertEqual(p.read_text(encoding="utf-8"), "---\ntipo: diario\n---\n\nuno\n\ndue\n")

    def test_sposta_non_sovrascrive(self):
        (self.v / "a").mkdir()
        (self.v / "b").mkdir()
        (self.v / "a" / "x.md").write_text("primo", encoding="utf-8")
        (self.v / "b" / "x.md").write_text("secondo", encoding="utf-8")
        dest = self.v / "cestino"
        p1 = sposta(self.v / "a" / "x.md", dest)
        p2 = sposta(self.v / "b" / "x.md", dest)
        self.assertEqual((p1.name, p2.name), ("x.md", "x (2).md"))
        self.assertEqual((p1.read_text(encoding="utf-8"), p2.read_text(encoding="utf-8")), ("primo", "secondo"))
        self.assertFalse((self.v / "a" / "x.md").exists())

    def test_frontmatter(self):
        self.assertEqual(frontmatter({"tipo": "diario", "data": "2026-11-11"}), "---\ntipo: diario\ndata: 2026-11-11\n---\n")
        self.assertEqual(frontmatter({"t": "a\nb"}), "---\nt: a b\n---\n")


if __name__ == "__main__":
    unittest.main()
