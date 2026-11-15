"""Motori degli agenti: Gemini Flash (REST) e Claude Code CLI (abbonamento)."""
import hashlib
import hmac
import json
import logging
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePath
from urllib.parse import urlsplit

from portineria.config import Config

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modello}:generateContent"
TIMEOUT_CLAUDE = 300
TIMEOUT_CLAUDE_PC = 600
log = logging.getLogger(__name__)


class ErroreMotore(Exception):
    pass


class QuotaGeminiEsaurita(ErroreMotore):
    pass


class LimiteClaude(ErroreMotore):
    pass


class TimeoutAgente(ErroreMotore):
    pass


class _EsecutoreNonRaggiungibile(ErroreMotore):
    pass


def _post_json(url: str, headers: dict, corpo: dict, timeout: float) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(corpo).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}


def chiedi_gemini(sistema: str, storia: list[dict], testo: str, cfg: Config, post=_post_json) -> str:
    contenuti = [{"role": "user" if m["ruolo"] == "utente" else "model", "parts": [{"text": m["testo"]}]}
                 for m in storia]
    contenuti.append({"role": "user", "parts": [{"text": testo}]})
    corpo = {"systemInstruction": {"parts": [{"text": sistema}]}, "contents": contenuti}
    headers = {"x-goog-api-key": cfg.gemini_api_key, "Content-Type": "application/json"}
    try:
        codice, dati = post(GEMINI_URL.format(modello=cfg.gemini_model), headers, corpo, 120)
    except OSError as e:
        raise ErroreMotore(f"Gemini non raggiungibile: {e}") from e
    if codice == 429:
        raise QuotaGeminiEsaurita("quota gratuita di Gemini esaurita")
    if codice != 200:
        msg = (dati.get("error") or {}).get("message", "") if isinstance(dati, dict) else ""
        raise ErroreMotore(f"Gemini HTTP {codice} {msg}".strip())
    try:
        parti = dati["candidates"][0]["content"]["parts"]
        risposta = "".join(p.get("text", "") for p in parti)
    except (KeyError, IndexError, TypeError):
        risposta = ""
    if not risposta.strip():
        raise ErroreMotore("Gemini non ha restituito testo")
    return risposta


def chiedi_omniroute(sistema: str, storia: list[dict], testo: str, cfg: Config, post=_post_json) -> str:
    """OmniRoute sul PC: API compatibile OpenAI, ruota i livelli gratuiti di più provider."""
    messaggi = [{"role": "system", "content": sistema}]
    messaggi += [{"role": "user" if m["ruolo"] == "utente" else "assistant", "content": m["testo"]} for m in storia]
    messaggi.append({"role": "user", "content": testo})
    headers = {"Authorization": f"Bearer {cfg.omniroute_key}", "Content-Type": "application/json"}
    url = cfg.omniroute_url.rstrip("/") + "/chat/completions"
    try:
        codice, dati = post(url, headers, {"model": cfg.omniroute_model, "messages": messaggi}, 120)
    except OSError as e:
        raise ErroreMotore(f"OmniRoute non raggiungibile: {e}") from e
    if codice != 200:
        raise ErroreMotore(f"OmniRoute HTTP {codice}")
    try:
        risposta = dati["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        risposta = ""
    if not risposta.strip():
        raise ErroreMotore("OmniRoute non ha restituito testo")
    return risposta


def chiedi_rapido(sistema: str, storia: list[dict], testo: str, cfg: Config, post=_post_json) -> str:
    """Motore degli agenti "gemini": OmniRoute se configurato e acceso, altrimenti Gemini diretto."""
    if cfg.omniroute_url:
        try:
            return chiedi_omniroute(sistema, storia, testo, cfg, post)
        except ErroreMotore as e:
            log.warning("OmniRoute non disponibile, uso Gemini: %s", e)
    return chiedi_gemini(sistema, storia, testo, cfg, post)


# Agenti che ricevono testo scritto da altri (l'agenda dei calendari, con gli inviti): nessuno strumento e mai sul PC.
# Un invito costruito ad arte non deve poter arrivare a Bash, WebFetch o ai file.
SENZA_STRUMENTI: frozenset = frozenset()


def senza_strumenti(prompt_path: PurePath) -> bool:
    return PurePath(prompt_path).name in SENZA_STRUMENTI


def argomenti_claude(prompt_path: PurePath, cfg: Config) -> list[str]:
    if senza_strumenti(prompt_path):  # il prompt arriva da stdin: --tools "" non va seguito da un argomento
        return [cfg.claude_bin, "-p", "--output-format", "text", "--append-system-prompt-file", str(prompt_path),
                "--tools", ""]
    # "/" + percorso assoluto = forma "//home/<percorso>" delle regole di Claude Code.
    # Le regole Read valgono anche per Grep/Glob, le Edit anche per Write: niente Grep/Glob
    # senza percorso, che varrebbero su tutto il Pi.
    vault, base, home = cfg.vault.as_posix(), cfg.base.as_posix(), cfg.base.parent.as_posix()
    return [cfg.claude_bin, "-p", "--output-format", "text", "--append-system-prompt-file", str(prompt_path),
            "--permission-mode", "acceptEdits",
            "--allowedTools", "Bash", f"Read(/{vault}/**)", f"Edit(/{vault}/**)",
            "WebSearch", "WebFetch", "Agent",
            "--disallowedTools", "Read(//proc/**)",
            f"Read(/{base}/.env)", f"Edit(/{base}/.env)",
            f"Read(/{home}/.ssh/**)", f"Read(/{home}/.claude/**)", f"Edit(/{home}/.claude/**)",
            f"Edit(/{base}/app/**)", f"Edit(/{base}/stato/**)",
            f"Read(/{base}/segreti/**)", f"Edit(/{base}/segreti/**)",
            f"Bash(*{base}/.env*)", f"Bash(*{home}/.ssh*)", "Bash(*~/.ssh*)", f"Bash(*{base}/segreti*)"]


def testo_claude(storia: list[dict], testo: str) -> str:
    if not storia:
        return testo
    righe = [f"{'Operatore' if m['ruolo'] == 'utente' else 'Tu'}: {m['testo']}" for m in storia]
    return "Conversazione precedente:\n" + "\n".join(righe) + "\n\nNuovo messaggio di Operatore:\n" + testo


def e_limite_claude(returncode: int, stdout: str, stderr: str) -> bool:
    tutto = f"{stdout or ''}\n{stderr or ''}".casefold()
    frasi = ("usage limit", "rate limit", "limit reached", "hit your limit", "session limit")
    if not any(f in tutto for f in frasi):
        return False
    # ponytail: una risposta lunga e riuscita che *parla* di rate limit non è un limite
    return returncode != 0 or len((stdout or "").strip()) < 300


def _e_limite(r: subprocess.CompletedProcess) -> bool:
    return e_limite_claude(r.returncode, r.stdout or "", r.stderr or "")


FINESTRA_FIRMA_S = 120


def firma_richiesta_esecutore(chiave: str, ts: str, corpo: bytes) -> str:
    msg = f"{ts.strip()}\n".encode("utf-8") + corpo
    return hmac.new(chiave.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verifica_firma_richiesta(
    chiave: str,
    ts: str,
    corpo: bytes,
    firma: str,
    adesso: float | None = None,
    finestra_s: int = FINESTRA_FIRMA_S,
) -> bool:
    """Verifica firma HMAC-SHA256 e finestra temporale anti-replay delle richieste firmate."""
    if not (chiave and chiave.strip() and ts and firma):
        return False
    try:
        t_req = int(ts.strip())
    except ValueError:
        return False
    ora = time.time() if adesso is None else adesso
    if abs(ora - t_req) > finestra_s:
        return False
    attesa = firma_richiesta_esecutore(chiave, ts, corpo)
    return hmac.compare_digest(firma.strip(), attesa)


def _esecutore_raggiungibile(url: str, timeout: float = 2.0) -> bool:
    u = urlsplit(url)
    host = u.hostname
    if not host:
        return False
    porta = u.port or (443 if u.scheme == "https" else 80)
    try:
        with socket.create_connection((host, porta), timeout=timeout):
            return True
    except OSError:
        return False


def chiedi_claude_pc(prompt_path: PurePath, storia: list[dict], testo: str, cfg: Config, post=_post_json) -> str:
    """Invia una richiesta Claude a un worker HTTP remoto (ESECUTORE_URL, facoltativo) con firma HMAC."""
    if post is _post_json and not _esecutore_raggiungibile(cfg.esecutore_url):
        raise _EsecutoreNonRaggiungibile("PC non raggiungibile")
    sistema = Path(prompt_path).read_text(encoding="utf-8", errors="replace")
    corpo = {
        "sistema": sistema,
        "testo": testo_claude(storia, testo),
        "vault_pi": cfg.vault.as_posix(),
        "claude_token": cfg.claude_token,
    }
    raw = json.dumps(corpo).encode("utf-8")
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Timestamp": ts,
        "X-Firma": firma_richiesta_esecutore(cfg.ordini_hmac_key, ts, raw),
    }
    url = cfg.esecutore_url.rstrip("/") + "/chiedi"
    try:
        codice, dati = post(url, headers, corpo, TIMEOUT_CLAUDE_PC + 30)
    except OSError as e:
        raise _EsecutoreNonRaggiungibile(f"worker PC non raggiungibile: {e}") from e
    if codice in (502, 503):
        raise _EsecutoreNonRaggiungibile(f"worker PC HTTP {codice}")
    err_tipo = dati.get("errore_tipo") if isinstance(dati, dict) else ""
    err_msg = str(dati.get("errore") or f"HTTP {codice}") if isinstance(dati, dict) else f"HTTP {codice}"
    if codice == 429 or err_tipo == "limite" or (codice != 200 and e_limite_claude(1, "", err_msg)):
        raise LimiteClaude(err_msg)
    if codice == 504 or err_tipo == "timeout":
        raise TimeoutAgente(err_msg)
    if codice != 200 or not (isinstance(dati, dict) and dati.get("ok")):
        raise ErroreMotore(f"worker PC: {err_msg}")
    risposta = str(dati.get("risposta") or "").strip()
    if not risposta:
        raise ErroreMotore("worker PC non ha restituito testo")
    return risposta


def _ambiente(cfg: Config) -> dict[str, str]:
    """Solo ciò che serve a claude: niente token di Telegram o chiave Gemini."""
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
           "HOME": os.environ.get("HOME") or cfg.base.parent.as_posix(),
           "LANG": "C.UTF-8"}
    if cfg.claude_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = cfg.claude_token
    return env


def chiedi_claude(prompt_path: PurePath, storia: list[dict], testo: str, cfg: Config,
                  run=subprocess.run, post=_post_json) -> str:
    if cfg.esecutore_url and cfg.ordini_hmac_key and not senza_strumenti(prompt_path):
        try:
            return chiedi_claude_pc(prompt_path, storia, testo, cfg, post=post)
        except _EsecutoreNonRaggiungibile as e:
            log.warning("Esecutore PC non disponibile, uso Claude sul Pi: %s", e)
    try:
        r = run(argomenti_claude(prompt_path, cfg), input=testo_claude(storia, testo), cwd=str(cfg.vault),
                capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_CLAUDE, env=_ambiente(cfg))
    except subprocess.TimeoutExpired as e:
        raise TimeoutAgente("timeout") from e
    if _e_limite(r):
        raise LimiteClaude(((r.stdout or "") + " " + (r.stderr or "")).strip())
    if r.returncode != 0:
        coda = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-5:])
        raise ErroreMotore(f"claude è uscito con codice {r.returncode}: {coda}")
    return (r.stdout or "").strip()
