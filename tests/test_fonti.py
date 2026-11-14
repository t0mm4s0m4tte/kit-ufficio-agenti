import tempfile
import unittest
from pathlib import Path

from generatore.fonti import raccogli


def scrivi(base: Path, rel: str, testo: str) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(testo, encoding="utf-8")


def home_finta(home: Path) -> None:
    scrivi(home, ".claude/agents/marketing-growth-hacker.md",
           "---\nname: Growth Hacker\ndescription: Growth.\n---\ncorpo")
    scrivi(home, ".claude/agents/rotto.md", "niente frontmatter")
    scrivi(home, ".claude/plugins/synced/abc/ai-specialists/skills/celia-cold-email/SKILL.md",
           '---\nname: celia-cold-email\ndescription: "Celia, cold email specialist."\n---\nx')
    scrivi(home, ".claude/skills/founder-cfo/SKILL.md",
           "---\nname: founder-cfo\ndescription: >-\n  Your CFO.\n---\nx")
    (home / ".claude/plugins/cache/ecc/ecc/2.9.1").mkdir(parents=True)
    scrivi(home, ".claude/plugins/cache/ecc/ecc/2.10.0/agents/architect.md",
           "---\nname: architect\ndescription: Architetto.\n---\nx")


class TestFonti(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        home_finta(self.home)

    def tearDown(self):
        self._tmp.cleanup()

    def test_raccoglie_le_quattro_fonti(self):
        agenti, _ = raccogli(self.home)
        per_id = {a["id"]: a for a in agenti}
        self.assertEqual(per_id["marketing-growth-hacker"]["nome"], "Growth Hacker")
        self.assertEqual(per_id["celia-cold-email"]["nome"], "Celia")
        self.assertEqual(per_id["founder-cfo"]["nome"], "Founder Cfo")
        self.assertEqual(per_id["ecc-architect"]["fonte"], "ecc")
        self.assertIn("corpo", per_id["marketing-growth-hacker"]["prompt"])

    def test_usa_versione_ecc_piu_alta(self):
        agenti, _ = raccogli(self.home)
        self.assertIn("ecc-architect", {a["id"] for a in agenti})  # esiste solo in 2.10.0

    def test_file_rotto_saltato_con_avviso(self):  # Review Focus 4
        agenti, avvisi = raccogli(self.home)
        self.assertNotIn("rotto", {a["id"] for a in agenti})
        self.assertTrue(any("rotto.md" in a for a in avvisi))

    def test_duplicato_tiene_il_primo(self):
        scrivi(self.home, ".claude/plugins/synced/zz/ai-specialists/skills/celia-cold-email/SKILL.md",
               "---\ndescription: Altra, x.\n---\n")
        agenti, avvisi = raccogli(self.home)
        self.assertEqual([a["nome"] for a in agenti if a["id"] == "celia-cold-email"], ["Celia"])
        self.assertTrue(any(a.startswith("duplicato celia-cold-email") for a in avvisi))

    def test_home_vuota(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(raccogli(Path(d)), ([], []))


if __name__ == "__main__":
    unittest.main()
