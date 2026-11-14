"""Telegram Bot API via long polling, solo libreria standard."""
import html
import logging
import re
from pathlib import Path

from portineria.motori import _post_json

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{metodo}"
CONTENUTI = ("text", "photo", "voice", "audio", "video", "video_note", "animation", "sticker", "document",
             "contact", "location", "poll", "dice")


def spezza(testo: str, limite: int = 4000) -> list[str]:
    pezzi = []
    while len(testo) > limite:
        taglio = testo.rfind("\n", 0, limite + 1)
        if taglio > 0:
            pezzi.append(testo[:taglio])
            testo = testo[taglio + 1:]
        else:
            pezzi.append(testo[:limite])
            testo = testo[limite:]
    pezzi.append(testo)
    return [p for p in pezzi if p]


_CODICE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_GRASSETTO = re.compile(r"\*\*(.+?)\*\*")


def _inline(riga: str) -> str:
    parti = _CODICE.split(riga)  # posizioni dispari = contenuto tra apici inversi
    for i, p in enumerate(parti):
        p = html.escape(p, quote=False)
        if i % 2:
            parti[i] = f"<code>{p}</code>"
        else:
            p = _LINK.sub(lambda m: f'<a href="{m[2].replace(chr(34), "%22")}">{m[1]}</a>', p)
            parti[i] = _GRASSETTO.sub(r"<b>\1</b>", p)
    return "".join(parti)


def _celle(riga: str) -> list[str]:
    return [c.strip() for c in riga.strip().strip("|").split("|")]


def _tabella(righe: list[str]) -> list[str]:
    """Telegram non ha le tabelle: una voce per riga, colonne come «titolo: valore»."""
    intest = _celle(righe[0])
    out = []
    for r in righe[2:]:
        celle = _celle(r)
        out.append(f"• <b>{_inline(celle[0])}</b>" if celle[0] else "•")
        out += [f"   {_inline(h)}: {_inline(c)}" for h, c in zip(intest[1:], celle[1:]) if c]
    return out


def _e_tabella(righe: list[str], i: int) -> bool:
    return (righe[i].lstrip().startswith("|") and i + 1 < len(righe)
            and re.fullmatch(r"\s*\|?[\s:|-]*-[\s:|-]*\|?\s*", righe[i + 1]) is not None)


def a_html(testo: str) -> str:
    """Markdown degli agenti → HTML di Telegram. Conservativo: niente corsivo con _ o *."""
    righe, out, i = testo.split("\n"), [], 0
    while i < len(righe):
        r = righe[i]
        if r.lstrip().startswith("```"):
            codice, i = [], i + 1
            while i < len(righe) and not righe[i].lstrip().startswith("```"):
                codice.append(righe[i])
                i += 1
            out.append("<pre>" + html.escape("\n".join(codice), quote=False) + "</pre>")
            i += 1
        elif _e_tabella(righe, i):
            j = i + 2
            while j < len(righe) and righe[j].lstrip().startswith("|"):
                j += 1
            out += _tabella(righe[i:j])
            i = j
        else:
            m = re.match(r"#{1,6}\s+(.*)", r)
            out.append(f"<b>{_inline(m[1])}</b>" if m else _inline(r))
            i += 1
    return "\n".join(out)


def estrai(update: dict) -> dict | None:
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    return {
        "update_id": update.get("update_id"),
        "user_id": (msg.get("from") or {}).get("id"),
        "chat_id": (msg.get("chat") or {}).get("id"),
        "chat_tipo": (msg.get("chat") or {}).get("type"),
        # fuori dai forum message_thread_id indica solo un filo di risposte: non è un argomento
        "thread_id": msg.get("message_thread_id") if msg.get("is_topic_message") else None,
        "testo": msg.get("text"),
        # False = messaggio di servizio (argomento creato, fissato, membro aggiunto…)
        "contenuto": any(k in msg for k in CONTENUTI),
    }


class Telegram:
    def __init__(self, token: str, post=_post_json):
        self._token, self._post = token, post

    def _chiama(self, metodo: str, corpo: dict, timeout: float) -> dict:
        codice, dati = self._post(API.format(token=self._token, metodo=metodo),
                                  {"Content-Type": "application/json"}, corpo, timeout)
        if codice != 200 or not dati.get("ok"):
            raise RuntimeError(f"Telegram {metodo}: HTTP {codice} {dati.get('description', '')}".strip())
        return dati

    def aggiornamenti(self, offset: int, timeout: int = 50) -> list[dict]:
        corpo = {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]}
        return self._chiama("getUpdates", corpo, timeout + 10).get("result", [])

    def invia(self, chat_id: int, testo: str, thread_id: int | None = None) -> None:
        # 3500 e non 4000: l'HTML (escape di & < >) allunga il pezzo e Telegram taglia a 4096
        for pezzo in spezza(testo, 3500):
            base = {"chat_id": chat_id}
            if thread_id is not None:
                base["message_thread_id"] = thread_id
            corpo = {**base, "text": a_html(pezzo), "parse_mode": "HTML",
                     "link_preview_options": {"prefer_large_media": True}}
            try:
                try:
                    self._chiama("sendMessage", corpo, 30)
                except RuntimeError as e:  # Telegram rifiuta l'HTML: si ripiega sul testo semplice
                    log.warning("HTML rifiutato per %s, riprovo senza formattazione: %s", chat_id, e)
                    self._chiama("sendMessage", {**base, "text": pezzo}, 30)
            except Exception as e:  # noqa: BLE001 — mai far cadere il bot per un invio fallito
                log.error("invio a %s fallito: %s", chat_id, e)


def leggi_offset(path: Path) -> int:
    try:
        return int(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def salva_offset(path: Path, offset: int) -> None:
    Path(path).write_text(str(offset), encoding="utf-8")


def autorizzato(msg: dict, user_id: int, gruppi) -> bool:
    if msg.get("user_id") != user_id:
        return False
    if msg.get("chat_tipo") == "private":
        return True
    if gruppi.stanza(msg.get("chat_id"), msg.get("thread_id")) is not None:
        return True
    return (msg.get("testo") or "").startswith("/collega")
