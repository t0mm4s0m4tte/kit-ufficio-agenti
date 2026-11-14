"""python -m generatore [--home PATH] [--out DIR] — scrive organigramma.json e prompts/."""
import argparse
import json
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from generatore.assegna import costruisci, valida
from generatore.fonti import raccogli

# Agenti scritti a mano in prompts/_<id>.md: il generatore non li rigenera né li rimuove.
SINTETICI = [
    {"id": "direttore", "nome": "Direttore", "descrizione": "Dirige l'ufficio e smista il lavoro ai reparti."},
    {"id": "capo-sviluppo", "nome": "Capo Sviluppo", "descrizione": "Scompone le richieste tecniche in passi verificabili."},
    {"id": "sviluppatore", "nome": "Sviluppatore", "descrizione": "Scrive codice semplice e testato."},
    {"id": "capo-contenuti", "nome": "Capo Contenuti", "descrizione": "Definisce tono e struttura dei testi."},
    {"id": "redattore", "nome": "Redattore", "descrizione": "Scrive testi chiari e onesti."},
]
SINTETICI = [{**s, "fonte": "ufficio", "prompt": ""} for s in SINTETICI]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="generatore")
    ap.add_argument("--home", type=Path, default=Path.home())
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)

    gerarchia = tomllib.loads((args.out / "gerarchia.toml").read_text(encoding="utf-8"))
    agenti, avvisi = raccogli(args.home)
    sintetici = {s["id"] for s in SINTETICI}
    agenti = SINTETICI + [a for a in agenti if a["id"] not in sintetici]
    org = costruisci(agenti, gerarchia, datetime.now().isoformat(timespec="seconds"))
    for a in org["agenti"]:
        if a["id"] in sintetici:
            a["prompt_path"] = f"prompts/_{a['id']}.md"
    errori, avvisi_val = valida(org, gerarchia)
    for avv in avvisi + avvisi_val:
        print("avviso:", avv, file=sys.stderr)
    if errori:
        for e in errori:
            print("ERRORE:", e, file=sys.stderr)
        return 1

    prompts = args.out / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    scritti = set()
    for a in agenti:
        if a["id"] in sintetici:
            continue
        (prompts / f"{a['id']}.md").write_text(a["prompt"], encoding="utf-8", newline="\n")
        scritti.add(f"{a['id']}.md")
    for f in prompts.glob("*.md"):
        if not f.name.startswith("_") and f.name not in scritti:
            f.unlink()
    (args.out / "organigramma.json").write_text(
        json.dumps(org, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(org['agenti'])} agenti, {len(org['piani'])} piani")
    return 0


if __name__ == "__main__":
    sys.exit(main())
