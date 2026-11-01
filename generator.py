import asyncio
import subprocess
import sys
import time
from pathlib import Path
import requests
import edge_tts
import imageio_ffmpeg
from google import genai
from google.genai import types
from config import (
    OUTPUTS_DIR,
    GEMINI_API_KEY,
     TTS_PROVIDER,
    EDGE_TTS_VOICE,
    ELEVENLABS_API_KEY,
    ELEVENLABS_VOICE_ID,
)
from analyzer import VideoProposal


async def generate_voiceover(script: str, output_audio_path: Path) -> Path:
    """Genera el archivo de audio MP3 con ElevenLabs o Edge-TTS."""
    if TTS_PROVIDER == "elevenlabs" and ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": script,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        output_audio_path.write_bytes(resp.content)
        return output_audio_path

    communicate = edge_tts.Communicate(script, EDGE_TTS_VOICE)
    await communicate.save(str(output_audio_path))
    return output_audio_path


def _generate_ai_video_sync(visual_prompt: str, output_video_path: Path) -> tuple[bool, str]:
    """
    Intenta generar el video en 9:16 usando los modelos actuales del SDK google-genai:
    1. gemini-omni-1.1-flash (Interactions API)
    2. veo-3.1-generate-preview (Models API)
    Devuelve (éxito, nombre_del_motor).
    """
    if not GEMINI_API_KEY or GEMINI_API_KEY == "tu_gemini_api_key_aqui":
        return False, ""

    # Intento 1: Gemini Omni 1.1 Flash (Interactions API)
    try:
        client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=600_000),
        )
        interaction = client.interactions.create(
            model="gemini-omni-1.1-flash",
            input=[{"type": "text", "text": visual_prompt}],
            response_format={
                "type": "video",
                "delivery": "uri",
                "aspect_ratio": "9:16",
                "resolution": "720p",
            },
        )
        output_video = getattr(interaction, "output_video", None)
        if output_video and getattr(output_video, "uri", None):
            video_bytes = client.files.download(file=output_video.uri)
            output_video_path.write_bytes(video_bytes)
            if output_video_path.exists() and output_video_path.stat().st_size > 0:
                return True, "Gemini Omni 1.1 Flash (9:16)"
    except Exception as exc:
        print(f"[Generator] Info Omni 1.1 Flash: {exc}", file=sys.stderr)

    # Intento 2: Google Veo 3.1 (veo-3.1-generate-preview)
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        operation = client.models.generate_videos(
            model="veo-3.1-generate-preview",
            source=types.GenerateVideosSource(prompt=visual_prompt),
            config=types.GenerateVideosConfig(
                number_of_videos=1,
                duration_seconds=5,
                enhance_prompt=True,
                aspect_ratio="9:16",
            ),
        )
        while not operation.done:
            time.sleep(10)
            operation = client.operations.get(operation)

        if operation.response and operation.response.generated_videos:
            generated_video = operation.response.generated_videos[0].video
            video_bytes = client.files.download(file=generated_video)
            output_video_path.write_bytes(video_bytes)
            if output_video_path.exists() and output_video_path.stat().st_size > 0:
                return True, "Google Veo 3.1 (9:16)"
    except Exception as exc:
        print(f"[Generator] Info Veo 3.1: {exc}", file=sys.stderr)

    return False, ""


def _merge_video_and_audio(
    base_video_path: Path,
    audio_path: Path,
    final_output_path: Path,
    restyle_fallback: bool = False,
) -> Path:
    """Combina el video base con la nueva voz en off de forma ligera en memoria."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    if restyle_fallback:
        # Escala a 720x1280 (9:16 ligero en RAM) a 30fps con look cinematográfico
        vf_filter = (
            "fps=30,scale=720:1280:force_original_aspect_ratio=increase,"
            "crop=720:1280,eq=contrast=1.08:saturation=1.15,format=yuv420p"
        )
        cmd = [
            ffmpeg_exe,
            "-y",
            "-loglevel",
            "error",
            "-stream_loop",
            "-1",
            "-i",
            str(base_video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            vf_filter,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-threads",
            "2",
            "-crf",
            "24",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            str(final_output_path),
        ]
    else:
        cmd = [
            ffmpeg_exe,
            "-y",
            "-loglevel",
            "error",
            "-stream_loop",
            "-1",
            "-i",
            str(base_video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-threads",
            "2",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            str(final_output_path),
        ]

    proc = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg error ({proc.returncode}): {proc.stderr.strip()}")
    return final_output_path


async def generate_final_video(
    proposal: VideoProposal, source_video_path: Path
) -> tuple[Path, str]:
    """
    Ejecuta la generación completa tras la aprobación en Telegram o UI:
    1. Genera la voz en off con IA.
    2. Genera el video con Gemini Omni 1.1 Flash / Google Veo 3.1 (o aplica remix visual 9:16 si no hay cuota de video IA).
    3. Combina video y voz con FFmpeg y devuelve la ruta del MP4 final y el motor empleado.
    """
    job_id = proposal.job_id
    audio_path = OUTPUTS_DIR / f"{job_id}_voiceover.mp3"
    raw_ai_path = OUTPUTS_DIR / f"{job_id}_ai_raw.mp4"
    final_video_path = OUTPUTS_DIR / f"{job_id}_final.mp4"

    await generate_voiceover(proposal.voiceover_script, audio_path)

    ai_ok, ai_engine = await asyncio.to_thread(
        _generate_ai_video_sync, proposal.visual_prompt, raw_ai_path
    )

    if ai_ok:
        await asyncio.to_thread(
            _merge_video_and_audio,
            raw_ai_path,
            audio_path,
            final_video_path,
            False,
        )
        engine_used = f"{ai_engine} + Voz IA ({TTS_PROVIDER})"
    else:
        await asyncio.to_thread(
            _merge_video_and_audio,
            source_video_path,
            audio_path,
            final_video_path,
            True,
        )
        engine_used = f"Remix Visual 9:16 + Voz IA ({TTS_PROVIDER})"

    return final_video_path, engine_used
