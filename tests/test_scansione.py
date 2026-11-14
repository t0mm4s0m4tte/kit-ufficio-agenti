"""Scansione di segreti e dati personali su una cartella.

Uso sulla copia:   KIT_DIR=<cartella> python -m unittest tests.test_scansione -v
Senza KIT_DIR i test sulla cartella reale sono saltati; i test del
rilevatore girano sempre. Il rapporto non contiene MAI i valori trovati:
solo file, riga e tipo.
"""
import os
import re
import tempfile
import unittest
from pathlib import Path

# I termini sono composti a pezzi perché questo file non deve contenere,
# a sua volta, i residui che cerca.
_NOME = "tom" + "my"
_COGNOME = "matte" + "ucci"
_ACCOUNT = "t0mm4s" + "0m4tte"
_SEGNAPOSTO = re.compile(r"^(<[A-Za-z0-9_ -]+>|)$")

REGOLE = {
    "esadecimale-lungo": re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{40,}(?![0-9A-Za-z])"),
    "token-telegram": re.compile(r"(?<!\d)\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"),
    "base64-lungo": re.compile(r"(?<![A-Za-z0-9+/])(?=[A-Za-z0-9+/]*\d)(?=[A-Za-z0-9+/]*[a-z])"
                               r"(?=[A-Za-z0-9+/]*[A-Z])[A-Za-z0-9+/]{44,}={0,2}(?![A-Za-z0-9+/=])"),
    "ip-privato": re.compile(r"(?<![\d.])(?:192\.168|10\.\d{1,3}|100\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))"
                             r"\.\d{1,3}(?:\.\d{1,3})?(?![\d.])"),
    "percorso-personale": re.compile(r"/home/" + _NOME + r"|Users[\\/]+" + _NOME, re.I),
    "nome-personale": re.compile(_NOME + "|" + _COGNOME + "|" + _ACCOUNT, re.I),
}
ASSEGNAZIONE = re.compile(
    r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD))[ \t]*=[ \t]*(\S*)", re.M)
# I minificati di terzi non hanno senso da scansionare per base64.
SALTA_BASE64 = ("vendor/", ".min.js")
SALTA_DIR = {".git", "__pycache__"}


def _testo(percorso: Path):
    dati = percorso.read_bytes()
    if b"\0" in dati:
        return None  # binario
    try:
        return dati.decode("utf-8")
    except UnicodeDecodeError:
        return dati.decode("latin-1")


def scansiona(radice, eccezioni=None):
    """Ritorna (residui, binari). residui = [(file, riga, tipo)]; binari = [file].

    eccezioni: {(file_relativo, tipo): motivo}. Il motivo è obbligatorio.
    """
    radice, eccezioni = Path(radice), eccezioni or {}
    residui, binari = [], []
    for p in sorted(radice.rglob("*")):
        if not p.is_file() or SALTA_DIR & set(p.relative_to(radice).parts):
            continue
        rel = p.relative_to(radice).as_posix()
        testo = _testo(p)
        if testo is None:
            binari.append(rel)
            continue
        trovati = set()
        for n, riga in enumerate(testo.splitlines(), 1):
            for tipo, rx in REGOLE.items():
                if tipo == "base64-lungo" and any(s in rel for s in SALTA_BASE64):
                    continue
                if rx.search(riga):
                    trovati.add((n, tipo))
        for m in ASSEGNAZIONE.finditer(testo):
            if not _SEGNAPOSTO.match(m.group(2)):
                trovati.add((testo.count("\n", 0, m.start()) + 1, "segreto-assegnato"))
        for n, tipo in sorted(trovati):
            if (rel, tipo) not in eccezioni:
                residui.append((rel, n, tipo))
    return residui, binari


# Eccezioni ammesse, una per voce, ciascuna con motivo scritto e approvato.
ECCEZIONI = {}


class TestRilevatore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _scrivi(self, nome, testo):
        p = Path(self.tmp.name) / nome
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(testo, encoding="utf-8")

    def tipi(self, eccezioni=None):
        return {t for _, _, t in scansiona(self.tmp.name, eccezioni)[0]}

    def test_pulito(self):
        self._scrivi("a.env", "ORDINI_HMAC_KEY=<CHIAVE_HMAC_DA_GENERARE>\nWEB_HOST=\n")
        self.assertEqual(scansiona(self.tmp.name), ([], []))

    def test_chiave_hmac_non_segnaposto(self):
        self._scrivi("a.env", "ORDINI_HMAC_KEY=" + "ab" * 32 + "\n")
        self.assertLessEqual({"segreto-assegnato", "esadecimale-lungo"}, self.tipi())

    def test_token_telegram(self):
        self._scrivi("a.txt", "t = 123456789:" + "A" * 35)
        self.assertIn("token-telegram", self.tipi())

    def test_ip_privati(self):
        for ip in ("192.168." "1.5", "10.0." "0.7", "100.64." "1.2", "172.20." "3.4"):
            with self.subTest(ip=ip):
                self._scrivi("a.txt", "host " + ip)
                self.assertIn("ip-privato", self.tipi())
        self._scrivi("a.txt", "versione 8.8.8.8 e 1.2.3.4")
        self.assertNotIn("ip-privato", self.tipi())

    def test_percorso_e_nome_personale(self):
        self._scrivi("a.txt", "/home/" + _NOME + "/x e " + _COGNOME.capitalize())
        self.assertLessEqual({"percorso-personale", "nome-personale"}, self.tipi())

    def test_base64_lungo_ma_non_nei_minificati(self):
        blob = "Zq9" * 20
        self._scrivi("a.txt", "k=" + blob)
        self.assertIn("base64-lungo", self.tipi())
        os.remove(Path(self.tmp.name) / "a.txt")
        self._scrivi("vendor/x.min.js", "k=" + blob)
        self.assertEqual(self.tipi(), set())

    def test_binari_elencati_non_scansionati(self):
        (Path(self.tmp.name) / "i.png").write_bytes(b"\x89PNG\0\0")
        self.assertEqual(scansiona(self.tmp.name), ([], ["i.png"]))

    def test_eccezione_esplicita(self):
        self._scrivi("a.txt", "host 192.168." "1.5")
        self.assertEqual(self.tipi({("a.txt", "ip-privato"): "esempio di documentazione"}), set())

    def test_il_rapporto_non_contiene_valori(self):
        self._scrivi("a.env", "ORDINI_HMAC_KEY=" + "cd" * 32 + "\n")
        self.assertNotIn("cdcd", repr(scansiona(self.tmp.name)))


@unittest.skipUnless(os.environ.get("KIT_DIR"), "imposta KIT_DIR per scansionare una cartella")
class TestCartella(unittest.TestCase):
    def test_nessun_residuo(self):
        residui, binari = scansiona(os.environ["KIT_DIR"], ECCEZIONI)
        per_tipo = {}
        for _, _, t in residui:
            per_tipo[t] = per_tipo.get(t, 0) + 1
        print(f"\nresidui: {len(residui)} {per_tipo} | binari non scansionati: {len(binari)}")
        for f, n, t in residui[:40]:
            print(f"  {f}:{n} {t}")
        self.assertEqual(residui, [])


if __name__ == "__main__":
    unittest.main()
