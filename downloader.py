import uuid
from dataclasses import dataclass
from pathlib import Path
import yt_dlp
import imageio_ffmpeg
from config import DOWNLOADS_DIR


@dataclass
class DownloadedVideo:
    job_id: str
    url: str
    file_path: Path
    title: str
    description: str
    duration: float
    platform: str


def detect_platform(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    if "instagram.com" in u:
        return "Instagram"
    if "facebook.com" in u or "fb.watch" in u or "fb.com" in u:
        return "Facebook"
    if "tiktok.com" in u:
        return "TikTok"
    return "Web"


class _SilentLogger:
    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def download_video(url: str, custom_job_id: str = "") -> DownloadedVideo:
    """Descarga un video de YouTube, Instagram, Facebook o TikTok usando yt-dlp."""
    job_id = custom_job_id or uuid.uuid4().hex[:8]
    output_template = str(DOWNLOADS_DIR / f"{job_id}.%(ext)s")
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    ydl_opts = {
        "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg_exe,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _SilentLogger(),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded_path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
        if not downloaded_path.exists():
            # Fallback por si la extensión final difiere
            matches = list(DOWNLOADS_DIR.glob(f"{job_id}.*"))
            if not matches:
                raise FileNotFoundError("No se encontró el archivo de video descargado.")
            downloaded_path = matches[0]

    return DownloadedVideo(
        job_id=job_id,
        url=url,
        file_path=downloaded_path,
        title=info.get("title") or "Video sin título",
        description=info.get("description") or "",
        duration=float(info.get("duration") or 0.0),
        platform=detect_platform(url),
    )
