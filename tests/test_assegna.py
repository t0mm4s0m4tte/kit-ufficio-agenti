import copy
import json
import tomllib
import unittest
from pathlib import Path

from generatore.assegna import costruisci, valida


def ag(id_, fonte="agency"):
    return {"id": id_, "nome": id_.title(), "descrizione": "d", "fonte": fonte, "prompt": "p"}


AGENTI = [ag("direttore", "ufficio"), ag("mkt-capo"), ag("mkt-seo"), ag("spec-capo"),
          ag("marketing-blog"), ag("sebo-seo", "ai-specialists"), ag("boh")]

GERARCHIA = {
    "ufficio": {"piano_default": "specialisti"},
    "motori": {},
    "piani": {
        "direzione": {"numero": 10, "nome": "Direzione", "emoji": "🏛", "colore": "#000",
                      "capo": "direttore", "motore_operai": "claude"},
        "marketing": {"numero": 8, "nome": "Marketing", "emoji": "📣", "colore": "#111",
                      "capo": "mkt-capo", "motore_operai": "gemini", "prefissi": ["marketing-"],
                      "fonti": ["ai-specialists"],
                      "team": {"seo": {"comanager": "mkt-seo", "match": ["seo"]}}},
        "specialisti": {"numero": 2, "nome": "Specialisti", "emoji": "🔬", "colore": "#222",
                        "capo": "spec-capo", "motore_operai": "gemini"},
    },
}


class TestAssegna(unittest.TestCase):
    def test_regole_di_assegnazione(self):
        org = costruisci(AGENTI, GERARCHIA, "2026-11-10T12:00:00")
        a = {x["id"]: x for x in org["agenti"]}
        self.assertEqual(a["marketing-blog"]["piano"], "marketing")      # prefisso
        self.assertEqual(a["sebo-seo"]["piano"], "marketing")            # fonte
        self.assertEqual(a["sebo-seo"]["team"], "seo")
        self.assertEqual(a["sebo-seo"]["capo"], "mkt-seo")
        self.assertEqual(a["boh"]["piano"], "specialisti")               # default
        self.assertEqual(a["boh"]["capo"], "spec-capo")
        self.assertEqual(a["mkt-capo"]["ruolo"], "capo")
        self.assertEqual(a["mkt-capo"]["capo"], "direttore")
        self.assertEqual(a["mkt-seo"]["ruolo"], "comanager")
        self.assertEqual(a["mkt-seo"]["capo"], "mkt-capo")
        self.assertEqual(a["mkt-seo"]["motore"], "claude")
        self.assertEqual(a["marketing-blog"]["motore"], "gemini")        # motore_operai
        self.assertEqual(a["marketing-blog"]["prompt_path"], "prompts/marketing-blog.md")
        self.assertNotIn("prompt", a["marketing-blog"])
        self.assertIsNone(a["direttore"]["capo"])
        self.assertEqual(a["direttore"]["ruolo"], "direttore")
        self.assertEqual(org["generato"], "2026-11-10T12:00:00")
        self.assertEqual([p["id"] for p in org["piani"]], ["direzione", "marketing", "specialisti"])
        self.assertEqual(org["piani"][1]["team"], ["seo"])

    def test_elenco_esplicito_vince_sul_prefisso(self):
        g = copy.deepcopy(GERARCHIA)
        g["piani"]["direzione"]["agenti"] = ["marketing-blog"]
        a = {x["id"]: x for x in costruisci(AGENTI, g, "t")["agenti"]}
        self.assertEqual(a["marketing-blog"]["piano"], "direzione")
        self.assertEqual(a["marketing-blog"]["capo"], "direttore")

    def test_prefisso_piu_lungo_vince(self):
        g = copy.deepcopy(GERARCHIA)
        g["piani"]["specialisti"]["prefissi"] = ["marketing-bl"]
        a = {x["id"]: x for x in costruisci(AGENTI, g, "t")["agenti"]}
        self.assertEqual(a["marketing-blog"]["piano"], "specialisti")

    def test_override_motore(self):
        g = copy.deepcopy(GERARCHIA)
        g["motori"] = {"marketing-blog": "claude"}
        a = {x["id"]: x for x in costruisci(AGENTI, g, "t")["agenti"]}
        self.assertEqual(a["marketing-blog"]["motore"], "claude")

    def test_valida_ok(self):
        self.assertEqual(valida(costruisci(AGENTI, GERARCHIA, "t"), GERARCHIA), ([], []))

    def test_valida_capo_mancante(self):
        org = costruisci([a for a in AGENTI if a["id"] != "mkt-capo"], GERARCHIA, "t")
        errori, _ = valida(org, GERARCHIA)
        self.assertTrue(any("mkt-capo" in e for e in errori))

    def test_valida_default_inesistente(self):
        g = copy.deepcopy(GERARCHIA)
        g["ufficio"]["piano_default"] = "cucina"
        errori, _ = valida(costruisci(AGENTI, GERARCHIA, "t"), g)
        self.assertTrue(any("cucina" in e for e in errori))

    def test_valida_avviso_id_elencato_inesistente(self):
        g = copy.deepcopy(GERARCHIA)
        g["piani"]["marketing"]["agenti"] = ["fantasma"]
        g["motori"] = {"spettro": "claude"}
        errori, avvisi = valida(costruisci(AGENTI, g, "t"), g)
        self.assertEqual(errori, [])
        self.assertTrue(any("fantasma" in a for a in avvisi))
        self.assertTrue(any("spettro" in a for a in avvisi))


ASSUNTI_GITHUB = (
    "3d-artist", "threejs-webgl", "blender-web-pipeline", "modern-web-design",
    "ui-ux-designer", "react-three-fiber", "quant-analyst", "trading-risk-manager",
    "crypto-trader", "shopify-developer", "nextjs-developer", "tailwind-expert",
    "postgres-pro", "python-pro", "c-pro", "vercel-deployment-specialist",
    "bash-pro", "video-editor", "social-media-clip-creator", "motion-director",
    "moc-agent",
)

ASSUNTI_3D = {
    # Piano 4 - gamedev (team blender)
    "blender-director": ("gamedev", "blender"),
    "blender-artist": ("gamedev", "blender"),
    "blender-modeler": ("gamedev", "blender"),
    "blender-script-agent": ("gamedev", "blender"),
    "blender-hardsurface-modeler": ("gamedev", "blender"),
    "blender-sculptor": ("gamedev", "blender"),
    "blender-retopology-uv": ("gamedev", "blender"),
    "blender-rigging-artist": ("gamedev", "blender"),
    "blender-facial-shapekeys": ("gamedev", "blender"),
    "blender-geometry-nodes": ("gamedev", "blender"),
    "blender-hair-cloth-artist": ("gamedev", "blender"),
    "blender-pbr-baker": ("gamedev", "blender"),
    "blender-asset-qa": ("gamedev", "blender"),
    "blender-visual-critic": ("gamedev", "blender"),
    "blender-game-asset-artist": ("gamedev", "blender"),
    "blender-wireframe-reconstructor": ("gamedev", "blender"),
    "blender-image-to-sculpt": ("gamedev", "blender"),
    "blender-studio-operator": ("gamedev", "blender"),
    # Piano 4 - gamedev (team art3d)
    "3d-art-director": ("gamedev", "art3d"),
    "3d-concept-planner": ("gamedev", "art3d"),
    "character-sculptor": ("gamedev", "art3d"),
    "character-artist": ("gamedev", "art3d"),
    "creature-artist": ("gamedev", "art3d"),
    "environment-artist": ("gamedev", "art3d"),
    "prop-artist": ("gamedev", "art3d"),
    "vehicle-artist": ("gamedev", "art3d"),
    "vegetation-artist": ("gamedev", "art3d"),
    "material-lighting-artist": ("gamedev", "art3d"),
    "3d-asset-optimizer": ("gamedev", "art3d"),
    "collision-proxy-engineer": ("gamedev", "art3d"),
    "rig-motion-retargeter": ("gamedev", "art3d"),
    "maya-artist": ("gamedev", "art3d"),
    "zbrush-sculptor": ("gamedev", "art3d"),
    "ai-3d-asset-generator": ("gamedev", "art3d"),
    "render-pipeline": ("gamedev", "art3d"),
    "asset-store-curator": ("gamedev", "art3d"),
    # Piano 4 - gamedev (team unity)
    "unity-technical-artist": ("gamedev", "unity"),
    "unity-level-designer": ("gamedev", "unity"),
    "unity-lighting-artist": ("gamedev", "unity"),
    "unity-rigging-skinning": ("gamedev", "unity"),
    "unity-terrain-specialist": ("gamedev", "unity"),
    "unity-vfx-animation": ("gamedev", "unity"),
    "unity-cinematic-director": ("gamedev", "unity"),
    # Piano 5 - design (team web3d)
    "3d-generalist-lookdev": ("design", "web3d"),
    "archviz-artist": ("design", "web3d"),
    "blender-product-animator": ("design", "web3d"),
    "3dviz-scene-designer": ("design", "web3d"),
    "img-to-threejs-modeler": ("design", "web3d"),
    "threejs-procedural-architect": ("design", "web3d"),
    "threejs-game-3d-director": ("design", "web3d"),
    # Piano 6 - engineering (team cad)
    "cad-automation": ("engineering", "cad"),
    "cad-hardware-engineer": ("engineering", "cad"),
    "cadquery-parametric-engineer": ("engineering", "cad"),
    "openscad-modeler": ("engineering", "cad"),
    "build123d-assembly-engineer": ("engineering", "cad"),
    "freecad-mcp-engineer": ("engineering", "cad"),
    "blender-print-engineer": ("engineering", "cad"),
    "fabricator-3d-printing": ("engineering", "cad"),
    # Piano 2 - specialisti
    "prompt-crafter": ("specialisti", None),
}


class TestOrganigrammaReale(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        radice = Path(__file__).resolve().parent.parent
        cls.radice = radice
        cls.org = json.loads((radice / "organigramma.json").read_text(encoding="utf-8"))
        cls.gerarchia = tomllib.loads((radice / "gerarchia.toml").read_text(encoding="utf-8"))

    def test_gerarchia_e_organigramma_validi(self):
        errori, avvisi = valida(self.org, self.gerarchia)
        self.assertEqual((errori, avvisi), ([], []))

    def test_nomi_unici_descrizioni_pulite_e_prompt_esistenti(self):
        nomi_visti = {}
        for a in self.org["agenti"]:
            chiave = a["nome"].casefold()
            self.assertNotIn(chiave, nomi_visti, f"nome duplicato: {a['nome']}")
            nomi_visti[chiave] = a["id"]
            desc = a["descrizione"].strip()
            self.assertTrue(desc, f"descrizione vuota: {a['id']}")
            self.assertNotIn("\n", desc, f"descrizione su più righe: {a['id']}")
            self.assertFalse(desc.startswith('"') and not desc.endswith('"'),
                             f"frontmatter troncato: {a['id']}")
            self.assertTrue((self.radice / a["prompt_path"]).is_file(),
                            f"prompt mancante: {a['prompt_path']}")


if __name__ == "__main__":
    unittest.main()
