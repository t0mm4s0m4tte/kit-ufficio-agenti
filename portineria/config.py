"""Configurazione della Portineria letta da ~/ufficio/.env."""
from dataclasses import dataclass
from pathlib import Path, PurePath

OBBLIGATORIE = ("TELEGRAM_TOKEN", "TELEGRAM_USER_ID", "GEMINI_API_KEY")


@dataclass
class Config:
    telegram_token: str
    telegram_user_id: int
    gemini_api_key: str
    gemini_model: str
    claude_bin: str
    base: PurePath
    claude_token: str = ""  # passato solo al processo claude, mai agli altri
    omniroute_url: str = ""  # vuoto = niente OmniRoute, solo Gemini diretto
    omniroute_key: str = ""
    omniroute_model: str = "auto"
    web_host: str = ""  # IP Tailscale del Pi; vuoto = edificio spento
    web_porta: int = 8090
    ordini_hmac_key: str = ""  # chiave per firmare gli ordini approvati, solo nel .env
    esecutore_url: str = ""  # es. http://<IP_DEL_PC>:8771 per eseguire Claude sul PC quando è acceso


    @property
    def vault(self) -> PurePath:
        return self.base / "vault"

    @property
    def stato(self) -> PurePath:
        return self.base / "stato"

    @property
    def app(self) -> PurePath:
        return self.base / "app"

    @property
    def env_path(self) -> PurePath:
        return self.base / ".env"


def _leggi_env(path: Path) -> dict[str, str]:
    valori = {}
    for riga in path.read_text(encoding="utf-8", errors="replace").splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#") or "=" not in riga:
            continue
        chiave, _, valore = riga.partition("=")
        valore = valore.strip()
        if len(valore) >= 2 and valore[0] == valore[-1] and valore[0] in "\"'":
            valore = valore[1:-1]
        valori[chiave.strip()] = valore
    return valori


def carica_config(env_path: Path) -> Config:
    v = _leggi_env(env_path)
    for chiave in OBBLIGATORIE:
        if not v.get(chiave):
            raise ValueError(f"manca {chiave} in {env_path}")
    try:
        web_porta = int(v.get("WEB_PORT") or 8090)
    except ValueError:
        raise ValueError(f"WEB_PORT deve essere un numero, trovato {v['WEB_PORT']!r}") from None
    if not 1 <= web_porta <= 65535:
        raise ValueError("WEB_PORT deve essere fra 1 e 65535")
    return Config(
        telegram_token=v["TELEGRAM_TOKEN"],
        telegram_user_id=int(v["TELEGRAM_USER_ID"]),
        gemini_api_key=v["GEMINI_API_KEY"],
        gemini_model=v.get("GEMINI_MODEL") or "gemini-4-hydrogen",
        claude_bin=v.get("CLAUDE_BIN") or "claude",
        base=Path(v.get("UFFICIO_DIR") or Path.home() / "ufficio"),
        claude_token=v.get("CLAUDE_CODE_OAUTH_TOKEN", ""),
        omniroute_url=v.get("OMNIROUTE_URL", ""),
        omniroute_key=v.get("OMNIROUTE_KEY", ""),
        omniroute_model=v.get("OMNIROUTE_MODEL") or "auto",
        web_host=v.get("WEB_HOST", ""),
        web_porta=web_porta,
        ordini_hmac_key=v.get("ORDINI_HMAC_KEY", ""),
        esecutore_url=v.get("ESECUTORE_URL", ""),
    )
