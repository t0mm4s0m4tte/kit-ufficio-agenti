"""python -m generatore [--home PATH] [--out DIR] — scrive organigramma.json e prompts/."""
import argparse
import json
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from generatore.antigravity import allinea_antigravity
from generatore.assegna import costruisci, valida
from generatore.fonti import raccogli

# Agenti dell'ufficio stesso: prompt scritto a mano in prompts/_<id>.md, mai generato né rimosso.
SINTETICI = [
    {"id": "direttore", "nome": "Direttore", "descrizione": "Dirige l'ufficio e smista il lavoro ai reparti."},
    {"id": "archivista-notturno", "nome": "Archivista Notturno",
     "descrizione": "Ogni notte trasforma i log della giornata in Diario, Commesse e note di Conoscenza."},
    {"id": "archivista-fonti", "nome": "Archivista Fonti",
     "descrizione": "Riassume le memorie di Claude e Antigravity e le collega alle commesse."},
    {"id": "bibliotecario", "nome": "Bibliotecario",
     "descrizione": "Risponde a 'cosa sappiamo su X' con le note del vault, citandole."},
    {"id": "capo-archivista", "nome": "Capo Archivista",
     "descrizione": "Ogni domenica controlla link rotti, doppioni e conflitti del vault e scrive un report."},
    {"id": "segretaria", "nome": "Greta",
     "descrizione": "Segretaria personale di Tommy: agenda, promemoria, memoria su di lui e smistamento delle richieste."},
    {"id": "coach-ares", "nome": "Coach ARES",
     "descrizione": "Capo della Palestra: orchestratore dei workout, readiness light e telemetria ARES."},
    {"id": "coach-andrea", "nome": "Coach Andrea",
     "descrizione": "Coach Palestra: tensione meccanica, spremi-muscolo, esecuzione maniacale e intensità IFBB."},
    {"id": "coach-jordan", "nome": "Coach Jordan",
     "descrizione": "Coach Palestra: heavy progressive overload, logbook rigido, Top Set e nutrizione peri-workout."},
    {"id": "coach-ludovico-lemme", "nome": "Coach Ludovico Lemme",
     "descrizione": "Coach Palestra: evidence-based, sistema KRATOS a 3 rotazioni, SFR e protocolli ADE."},
    {"id": "coach-gianz", "nome": "Coach Gianz",
     "descrizione": "Coach Palestra: propedeutica, transizione meccanica, programmazione Taurus e basi solide."},
]
SINTETICI = [{**s, "fonte": "ufficio", "prompt": ""} for s in SINTETICI]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="generatore")
    ap.add_argument("--home", type=Path, default=Path.home())
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument(
        "--antigravity",
        action="store_true",
        help="Allinea solo GEMINI.md e AGENTS.md all'organigramma esistente senza rigenerare i prompt",
    )
    args = ap.parse_args(argv)

    if args.antigravity:
        allinea_antigravity(args.out)
        print("Antigravity allineato con successo (GEMINI.md e AGENTS.md).")
        return 0

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
    allinea_antigravity(args.out)
    print(f"{len(org['agenti'])} agenti, {len(org['piani'])} piani")
    return 0


if __name__ == "__main__":
    sys.exit(main())
