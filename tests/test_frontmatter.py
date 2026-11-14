import unittest

from generatore.frontmatter import leggi_frontmatter


class TestFrontmatter(unittest.TestCase):
    def test_valori_semplici(self):
        campi, corpo = leggi_frontmatter("---\nname: Growth Hacker\ncolor: green\n---\n# Titolo\ntesto")
        self.assertEqual(campi, {"name": "Growth Hacker", "color": "green"})
        self.assertEqual(corpo, "# Titolo\ntesto")

    def test_stringa_tra_virgolette_con_escape(self):
        campi, _ = leggi_frontmatter('---\ndescription: "Celia, cold email specialist.\\n"\n---\nx')
        self.assertEqual(campi["description"], "Celia, cold email specialist.")

    def test_virgolette_singole(self):
        campi, _ = leggi_frontmatter("---\nname: 'Celia'\n---\nx")
        self.assertEqual(campi["name"], "Celia")

    def test_blocco_piegato(self):
        t = "---\nname: founder-cfo\ndescription: >-\n  Your CFO. Builds the\n  unit economics.\nother: 1\n---\ncorpo"
        campi, _ = leggi_frontmatter(t)
        self.assertEqual(campi["description"], "Your CFO. Builds the unit economics.")
        self.assertEqual(campi["other"], "1")

    def test_blocco_letterale(self):
        campi, _ = leggi_frontmatter("---\nd: |\n  riga1\n  riga2\n---\nx")
        self.assertEqual(campi["d"], "riga1\nriga2")

    def test_senza_frontmatter(self):
        self.assertEqual(leggi_frontmatter("solo testo"), ({}, "solo testo"))

    def test_frontmatter_non_chiuso(self):
        self.assertEqual(leggi_frontmatter("---\nname: x\nsenza fine"), ({}, "---\nname: x\nsenza fine"))

    def test_crlf(self):
        self.assertEqual(leggi_frontmatter("---\r\nname: x\r\n---\r\ncorpo")[0], {"name": "x"})


if __name__ == "__main__":
    unittest.main()
