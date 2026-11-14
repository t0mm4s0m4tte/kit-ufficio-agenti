"""Parser minimale del frontmatter YAML degli agenti (solo chiavi di primo livello)."""
import json


def _valore(grezzo: str) -> str:
    v = grezzo.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return str(json.loads(v)).strip()
        except ValueError:
            return v[1:-1].strip()
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'").strip()
    return v


def leggi_frontmatter(testo: str) -> tuple[dict[str, str], str]:
    norm = testo.replace("\r\n", "\n")
    righe = norm.split("\n")
    if not righe or righe[0].strip() != "---":
        return {}, testo
    try:
        fine = next(i for i in range(1, len(righe)) if righe[i].strip() == "---")
    except StopIteration:
        return {}, testo

    campi: dict[str, str] = {}
    blocco_chiave, blocco_tipo, blocco_righe = None, "", []

    def chiudi_blocco():
        if blocco_chiave is not None:
            sep = "\n" if blocco_tipo.startswith("|") else " "
            campi[blocco_chiave] = sep.join(r.strip() for r in blocco_righe).strip()

    for riga in righe[1:fine]:
        if riga[:1] in (" ", "\t"):
            if blocco_chiave is not None:
                blocco_righe.append(riga)
            continue  # righe indentate fuori da un blocco: ignorate
        chiudi_blocco()
        blocco_chiave, blocco_righe = None, []
        chiave, sep, resto = riga.partition(":")
        chiave, resto = chiave.strip(), resto.strip()
        if not sep or not chiave:
            continue
        if resto in (">", ">-", "|", "|-", ">+", "|+"):
            blocco_chiave, blocco_tipo = chiave, resto
        else:
            campi[chiave] = _valore(resto)
    chiudi_blocco()
    return campi, "\n".join(righe[fine + 1:])
