"""All settings come from environment variables (loaded from .env in development)."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/recite")
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_ttl_minutes: int = int(os.getenv("JWT_TTL_MINUTES", "720"))
    whisper_cli: str = os.getenv("WHISPER_CLI", "whisper-cli")
    # Accurate model: every final decision. Fast model: only to follow along while a phrase is being said.
    whisper_model: Path = _path(os.getenv("WHISPER_MODEL", "models/ggml-tarbiyah-small-q8_0.bin"))
    whisper_fast_model: Path = _path(os.getenv("WHISPER_FAST_MODEL", "models/ggml-tarteel-base-q8_0.bin"))
    whisper_threads: int = int(os.getenv("WHISPER_THREADS") or min(8, os.cpu_count() or 4))
    storage_dir: Path = _path(os.getenv("STORAGE_DIR", "storage"))
    revision_cron: str = os.getenv("REVISION_CRON", "0 5 * * *")
    timezone: str = os.getenv("TZ", "UTC") or "UTC"
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
    max_audio_seconds: int = int(os.getenv("MAX_AUDIO_SECONDS", "180"))
    max_ayahs_per_attempt: int = int(os.getenv("MAX_AYAHS_PER_ATTEMPT", "20"))
    demo_email: str = os.getenv("DEMO_EMAIL", "demo@recite.test")
    demo_password: str = os.getenv("DEMO_PASSWORD", "")

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def reports_dir(self) -> Path:
        return self.storage_dir / "reports"


settings = Settings()
