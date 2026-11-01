import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "workspace"
DOWNLOADS_DIR = WORK_DIR / "downloads"
FRAMES_DIR = WORK_DIR / "frames"
STORYBOARDS_DIR = WORK_DIR / "storyboards"
PROPOSALS_DIR = WORK_DIR / "proposals"
OUTPUTS_DIR = WORK_DIR / "outputs"

ASSETS_DIR = BASE_DIR / "assets"
AUDIO_PHONK_DIR = ASSETS_DIR / "audio" / "phonk"
AUDIO_SFX_DIR = ASSETS_DIR / "audio" / "sfx"

for _d in (
    DOWNLOADS_DIR,
    FRAMES_DIR,
    STORYBOARDS_DIR,
    PROPOSALS_DIR,
    OUTPUTS_DIR,
    AUDIO_PHONK_DIR,
    AUDIO_SFX_DIR,
):
    _d.mkdir(parents=True, exist_ok=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_TELEGRAM_USER_ID = os.getenv("ALLOWED_TELEGRAM_USER_ID", "")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_ANALYSIS_MODEL = os.getenv("GEMINI_ANALYSIS_MODEL", "gemini-2.5-flash")

# --- Generación de Arte Manga / Seinen / Baki (Local GPU RTX 3050 6GB + Fallbacks) ---
# Opciones de ART_PROVIDER: "auto" | "a1111" | "comfyui" | "gemini" | "flux_pollinations" | "manga_ink"
ART_PROVIDER = os.getenv("ART_PROVIDER", "auto").lower()
SD_WEBUI_URL = os.getenv("SD_WEBUI_URL", "http://127.0.0.1:7860").rstrip("/")
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
MANGA_LORA_TAG = os.getenv("MANGA_LORA_TAG", "<lora:baki_manga_style:0.85>")
IMG2IMG_DENOISING = float(os.getenv("IMG2IMG_DENOISING", "0.68"))

MANGA_NEGATIVE_PROMPT = os.getenv(
    "MANGA_NEGATIVE_PROMPT",
    "3D render, CGI, smooth skin, realistic photo, clean digital painting, colorful, underwear, soft lighting, bodybuilding pose, blurry, western comic",
)

# --- Parámetros de Edición de Impacto (FFmpeg) ---
MAX_IMPACT_MOMENTS = int(os.getenv("MAX_IMPACT_MOMENTS", "1"))
IMPACT_HOLD_SECONDS = float(os.getenv("IMPACT_HOLD_SECONDS", "0.8"))
PHONK_VOLUME = float(os.getenv("PHONK_VOLUME", "0.42"))
SFX_VOLUME = float(os.getenv("SFX_VOLUME", "1.0"))
ORIGINAL_AUDIO_VOLUME = float(os.getenv("ORIGINAL_AUDIO_VOLUME", "0.75"))

DEFAULT_CONTENT_STYLE = os.getenv(
    "DEFAULT_CONTENT_STYLE",
    "Gritty dark seinen manga battle panel, traditional Japanese ink drawing, rough black ink linework, "
    "aggressive brush strokes, heavy cross-hatching, extreme chiaroscuro, intense shadows. "
    "Style of Kentaro Miura (Berserk), Keisuke Itagaki (Baki the Grappler), and Yusuke Murata.",
)
