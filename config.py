import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "workspace"
DOWNLOADS_DIR = WORK_DIR / "downloads"
OUTPUTS_DIR = WORK_DIR / "outputs"

DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_TELEGRAM_USER_ID = os.getenv("ALLOWED_TELEGRAM_USER_ID", "")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_ANALYSIS_MODEL = os.getenv("GEMINI_ANALYSIS_MODEL", "gemini-3.8-flash")
VEO_MODEL = os.getenv("VEO_MODEL", "gemini-omni-1.1-flash")

TTS_PROVIDER = os.getenv("TTS_PROVIDER", "edge").lower()
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "es-MX-DaliaNeural")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")

DEFAULT_CONTENT_STYLE = os.getenv(
    "DEFAULT_CONTENT_STYLE",
    "Video corto dinámico (Reel/Short/TikTok) en español, con un gancho fuerte en los primeros 3 segundos, "
    "tono profesional pero cercano, manteniendo la estructura ganadora del video original pero recreado "
    "con visuales cinematográficos generados por IA.",
)
