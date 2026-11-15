import contextlib
import io
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path

from portineria.chiave import main


class TestChiave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = Path(self.tmp.name) / ".env"

    def esegui(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            codice = main([str(self.env), *args])
        return codice, out.getvalue()

    def valore(self):
        m = re.search(r"^ORDINI_HMAC_KEY=(.*)$", self.env.read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else None

    def test_sostituisce_il_segnaposto_senza_stampare_la_chiave(self):
        self.env.write_text("WEB_PORT=8090\nORDINI_HMAC_KEY=<CHIAVE_HMAC_DA_GENERARE>\nALTRO=1\n", encoding="utf-8")
        codice, uscita = self.esegui()
        self.assertEqual(codice, 0)
        chiave = self.valore()
        self.assertRegex(chiave, r"^[0-9a-f]{64}$")
        self.assertNotIn(chiave, uscita)
        testo = self.env.read_text(encoding="utf-8")
        self.assertIn("WEB_PORT=8090", testo)
        self.assertIn("ALTRO=1", testo)
        self.assertEqual(testo.count("ORDINI_HMAC_KEY="), 1)

    def test_crea_il_file_se_manca(self):
        self.assertEqual(self.esegui()[0], 0)
        self.assertRegex(self.valore(), r"^[0-9a-f]{64}$")

    def test_non_sovrascrive_una_chiave_vera_senza_forza(self):
        self.env.write_text("ORDINI_HMAC_KEY=" + "ab" * 32 + "\n", encoding="utf-8")
        self.assertEqual(self.esegui()[0], 1)
        self.assertEqual(self.valore(), "ab" * 32)

    def test_forza_sostituisce_e_cambia_valore(self):
        self.env.write_text("ORDINI_HMAC_KEY=" + "ab" * 32 + "\n", encoding="utf-8")
        self.assertEqual(self.esegui("--forza")[0], 0)
        self.assertNotEqual(self.valore(), "ab" * 32)
        self.assertRegex(self.valore(), r"^[0-9a-f]{64}$")

    def test_ogni_chiamata_genera_una_chiave_diversa(self):
        self.esegui()
        prima = self.valore()
        self.esegui("--forza")
        self.assertNotEqual(prima, self.valore())


    @unittest.skipUnless(os.name == "posix", "permessi POSIX")
    def test_permessi_600_anche_se_il_file_esisteva_largo(self):
        self.env.write_text("ORDINI_HMAC_KEY=<CHIAVE_HMAC_DA_GENERARE>" + chr(10), encoding="utf-8")
        os.chmod(self.env, 0o644)
        self.assertEqual(self.esegui()[0], 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.env).st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "symlink POSIX")
    def test_non_segue_i_symlink(self):
        bersaglio = Path(self.tmp.name) / "altro.txt"
        bersaglio.write_text("intatto", encoding="utf-8")
        os.symlink(bersaglio, self.env)
        with self.assertRaises(OSError):
            self.esegui()
        self.assertEqual(bersaglio.read_text(encoding="utf-8"), "intatto")


if __name__ == "__main__":
    unittest.main()
