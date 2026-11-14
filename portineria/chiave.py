"""python -m portineria.chiave [percorso .env] [--forza]

Scrive una chiave HMAC nuova (ORDINI_HMAC_KEY) nel file .env senza mostrarla a video:
così non finisce nello storico del terminale né in log condivisi.
"""
import argparse
import os
import re
import secrets
import sys
from pathlib import Path

RIGA = re.compile(r"^ORDINI_HMAC_KEY\s*=(.*)$")


def _e_segnaposto(valore: str) -> bool:
    v = valore.strip().strip("\"'")
    return v == "" or (v.startswith("<") and v.endswith(">"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="portineria.chiave")
    ap.add_argument("env", nargs="?", type=Path, default=Path.home() / "ufficio" / ".env")
    ap.add_argument("--forza", action="store_true",
                    help="sostituisce una chiave già presente (gli ordini firmati prima non saranno più verificabili)")
    args = ap.parse_args(argv)

    righe = args.env.read_text(encoding="utf-8").splitlines() if args.env.exists() else []
    indice = next((i for i, r in enumerate(righe) if RIGA.match(r)), None)
    if indice is not None and not args.forza and not _e_segnaposto(RIGA.match(righe[indice]).group(1)):
        print(f"Una chiave c'è già in {args.env}: non la tocco. Usa --forza per sostituirla.")
        return 1
    nuova = f"ORDINI_HMAC_KEY={secrets.token_hex(32)}"
    if indice is None:
        righe.append(nuova)
    else:
        righe[indice] = nuova
    args.env.parent.mkdir(parents=True, exist_ok=True)
    args.env.write_text("\n".join(righe) + "\n", encoding="utf-8", newline="\n")
    if os.name == "posix":
        args.env.chmod(0o600)
    print(f"Chiave scritta in {args.env} (non mostrata).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
