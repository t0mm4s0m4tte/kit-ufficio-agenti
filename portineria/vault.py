"""Vault Ufficio: cartelle, frontmatter e scritture che non cancellano né sovrascrivono mai."""
import json
import re
import shutil
import threading
from pathlib import Path

CARTELLE = ["00 - Inbox", "01 - Commesse", "02 - Conoscenza", "03 - Ordini di lavoro", "04 - Diario",
            "90 - Indici", "99 - Fonti", "99 - Fonti/claude", "99 - Fonti/antigravity",
            "99 - Fonti/import-iniziale", "99 - Fonti/_elaborati", "99 - Fonti/_cestino"]
STIGNORE = ".obsidian/workspace*\n.trash\n*.tmp\n"
_VIETATI = re.compile(r'[\\/:*?"<>|\[\]#^\x00-\x1f\x7f]')
MAX_BYTE_NOME = 190  # ext4 accetta 255 byte: con " (n).md" si resta sotto i 200
_LOCK = threading.Lock()  # ponytail: un lock globale per scelta del nome + scrittura


def prepara_vault(vault: Path) -> None:
    vault = Path(vault)
    for c in CARTELLE:
        (vault / c).mkdir(parents=True, exist_ok=True)
    stignore = vault / ".stignore"
    if not stignore.exists():
        stignore.write_text(STIGNORE, encoding="utf-8", newline="\n")


def frontmatter(campi: dict[str, str]) -> str:
    righe = [f"{k}: {' '.join(str(v).splitlines())}" for k, v in campi.items()]
    return "---\n" + "".join(r + "\n" for r in righe) + "---\n"


def pulisci_nome(nome: str) -> str:
    if nome.lower().endswith(".md"):
        nome = nome[:-3]
    nome = " ".join(_VIETATI.sub(" ", nome).split())[:100]
    while len(nome.encode("utf-8")) > MAX_BYTE_NOME:
        nome = nome[:-1]
    return nome.strip() or "senza titolo"


def nome_libero(cartella: Path, nome: str) -> Path:
    base = pulisci_nome(nome)
    # confronto senza maiuscole: sul Pi "foo.md" e "Foo.md" convivono, su Windows no (conflitto Syncthing)
    cartella = Path(cartella)
    presi = {q.name.casefold() for q in cartella.iterdir()} if cartella.is_dir() else set()
    candidato, n = f"{base}.md", 2
    while candidato.casefold() in presi or (cartella / candidato).exists():
        candidato, n = f"{base} ({n}).md", n + 1
    return cartella / candidato


def scrivi_nuova(cartella: Path, nome: str, testo: str) -> Path:
    with _LOCK:
        Path(cartella).mkdir(parents=True, exist_ok=True)
        p = nome_libero(cartella, nome)
        p.write_text(testo, encoding="utf-8", newline="\n")
    return p


def appendi(percorso: Path, testo: str, intestazione: str = "") -> None:
    p = Path(percorso)
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        nuovo = not p.exists()
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            if nuovo:
                f.write(intestazione)
            f.write("\n" + testo.rstrip("\n") + "\n")


def sposta(file: Path, cartella: Path) -> Path:
    with _LOCK:
        Path(cartella).mkdir(parents=True, exist_ok=True)
        dest = nome_libero(cartella, Path(file).name)
        shutil.move(str(file), str(dest))
    return dest


def primo_json(testo: str) -> dict | None:
    """Il primo oggetto JSON nel testo (anche dentro un blocco ```)."""
    dec = json.JSONDecoder()
    i = testo.find("{")
    while i != -1:
        try:
            obj, _ = dec.raw_decode(testo, i)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = testo.find("{", i + 1)
    return None


def leggi_json(p: Path) -> dict:
    try:
        dati = json.loads(Path(p).read_text(encoding="utf-8"))
        return dati if isinstance(dati, dict) else {}
    except (OSError, ValueError):
        return {}


def scrivi_json(p: Path, dati: dict) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    tmp.replace(p)
