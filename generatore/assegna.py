"""Assegna ogni agente a piano, team, ruolo, superiore e motore secondo gerarchia.toml."""
from collections import Counter

_RESPONSABILI = ("direttore", "capo", "comanager")


def _piano_di(agente: dict, piani: dict, default: str) -> str:
    id_ = agente["id"]
    for p, cfg in piani.items():  # 1. elenco esplicito, capo o comanager
        if id_ in cfg.get("agenti", []) or id_ == cfg.get("capo"):
            return p
        if any(id_ == t.get("comanager") for t in cfg.get("team", {}).values()):
            return p
    for p, cfg in piani.items():  # 2. fonte
        if agente["fonte"] in cfg.get("fonti", []):
            return p
    migliore, lung = None, 0  # 3. prefisso più lungo fra tutti i piani
    for p, cfg in piani.items():
        for pref in cfg.get("prefissi", []):
            if id_.startswith(pref) and len(pref) > lung:
                migliore, lung = p, len(pref)
    return migliore or default  # 4. default


def _team_di(id_: str, cfg: dict) -> str | None:
    team = cfg.get("team", {})
    if id_ == cfg.get("capo"):
        return None
    for t, tc in team.items():
        if id_ == tc.get("comanager"):
            return t
    for t, tc in team.items():
        if any(parola in id_ for parola in tc.get("match", [])):
            return t
    return None


def costruisci(agenti: list[dict], gerarchia: dict, generato: str) -> dict:
    piani = gerarchia.get("piani", {})
    default = gerarchia.get("ufficio", {}).get("piano_default", "")
    motori = gerarchia.get("motori", {})
    comanager = {tc.get("comanager") for cfg in piani.values() for tc in cfg.get("team", {}).values()}

    risultato = []
    for a in agenti:
        id_ = a["id"]
        piano = _piano_di(a, piani, default)
        cfg = piani.get(piano, {})
        team = _team_di(id_, cfg)
        if id_ == "direttore":
            ruolo = "direttore"
        elif id_ == cfg.get("capo"):
            ruolo = "capo"
        elif id_ in comanager:
            ruolo = "comanager"
        else:
            ruolo = "operaio"
        if ruolo == "direttore":
            capo = None
        elif ruolo == "capo":
            capo = "direttore"
        elif ruolo == "operaio" and team:
            capo = cfg["team"][team].get("comanager")
        else:
            capo = cfg.get("capo")
        if id_ in motori:
            motore = motori[id_]
        elif ruolo in _RESPONSABILI:
            motore = "claude"
        else:
            motore = cfg.get("motore_operai", "gemini")
        risultato.append({
            "id": id_, "nome": a["nome"], "descrizione": a["descrizione"], "piano": piano,
            "team": team, "ruolo": ruolo, "capo": capo, "motore": motore, "fonte": a["fonte"],
            "prompt_path": f"prompts/{id_}.md",
        })

    elenco_piani = [
        {"id": p, "numero": cfg.get("numero", 0), "nome": cfg.get("nome", p), "emoji": cfg.get("emoji", ""),
         "colore": cfg.get("colore", ""), "capo": cfg.get("capo"), "team": list(cfg.get("team", {}))}
        for p, cfg in piani.items()
    ]
    elenco_piani.sort(key=lambda x: x["numero"], reverse=True)
    return {"generato": generato, "piani": elenco_piani, "agenti": risultato}


def valida(org: dict, gerarchia: dict) -> tuple[list[str], list[str]]:
    errori, avvisi = [], []
    conteggio = Counter(a["id"] for a in org["agenti"])
    ids = set(conteggio)
    errori += [f"id duplicato: {i}" for i, n in conteggio.items() if n > 1]
    piani = gerarchia.get("piani", {})
    default = gerarchia.get("ufficio", {}).get("piano_default")
    if default not in piani:
        errori.append(f"piano_default inesistente: {default}")
    for p, cfg in piani.items():
        if cfg.get("capo") not in ids:
            errori.append(f"capo inesistente nel piano {p}: {cfg.get('capo')}")
        for t, tc in cfg.get("team", {}).items():
            if tc.get("comanager") not in ids:
                errori.append(f"comanager inesistente nel team {p}.{t}: {tc.get('comanager')}")
        avvisi += [f"agente elencato in {p} inesistente: {i}" for i in cfg.get("agenti", []) if i not in ids]
    avvisi += [f"motore per agente inesistente: {i}" for i in gerarchia.get("motori", {}) if i not in ids]
    return errori, avvisi
