import asyncio
import math
import subprocess
from pathlib import Path
from typing import List, Tuple

import imageio_ffmpeg
from PIL import Image, ImageEnhance, ImageOps

from analyzer import VideoProposal
from audio_assets import ensure_default_audio_assets, get_phonk_track, get_sfx_files
from config import (
    FRAMES_DIR,
    IMPACT_HOLD_SECONDS,
    ORIGINAL_AUDIO_VOLUME,
    OUTPUTS_DIR,
    PHONK_VOLUME,
    SFX_VOLUME,
    STORYBOARDS_DIR,
)
from art_generator import extract_frame_at_timestamp, generate_manga_panel

FPS = 30
WIDTH = 720
HEIGHT = 1280


def _probe_video_duration(video_path: Path) -> float:
    """Obtiene la duración real del video usando ffprobe/ffmpeg."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg_exe, "-i", str(video_path)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    text = proc.stderr or ""
    for line in text.splitlines():
        if "Duration:" in line:
            try:
                dur_part = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = dur_part.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
            except Exception:
                pass
    return 10.0


def _has_audio_stream(video_path: Path) -> bool:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg_exe, "-i", str(video_path)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return "Audio:" in (proc.stderr or "")


def _render_manga_impact_clip(
    panel_image_path: Path,
    output_clip_path: Path,
    hold_seconds: float = IMPACT_HOLD_SECONDS,
) -> Path:
    """
    Crea un clip de video 9:16 (720x1280 @ 30fps) a partir de la imagen Manga/Baki con:
    - 2 frames iniciales de Impact Flash (negativo / destello anime de choque)
    - Zoom Punch agresivo (1.03x -> 1.18x)
    - Camera Shake sísmico amortiguado (vibración X/Y de golpe en los primeros 0.45s)
    - Pista de audio silenciosa base (para concatenación limpia con los segmentos del video)
    """
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    base_img = Image.open(panel_image_path).convert("RGB").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    inverted_img = ImageOps.invert(base_img)
    flash_img = ImageEnhance.Brightness(base_img).enhance(2.4)

    total_frames = max(15, int(round(hold_seconds * FPS)))

    cmd = [
        ffmpeg_exe,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{WIDTH}x{HEIGHT}",
        "-pix_fmt",
        "rgb24",
        "-r",
        str(FPS),
        "-i",
        "-",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-t",
        f"{hold_seconds:.3f}",
        "-shortest",
        str(output_clip_path),
    ]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None

    for idx in range(total_frames):
        t = idx / FPS
        if idx == 0:
            frame = flash_img
        elif idx == 1:
            frame = inverted_img
        else:
            # Zoom Punch rápido al inicio + avance lento dramático
            zoom = 1.04 + 0.14 * (1.0 - math.exp(-t * 8.5)) + 0.03 * (t / max(0.5, hold_seconds))
            # Sacudida de cámara (Camera Shake) fuerte en el impacto que se amortigua
            shake_amp = 26.0 * math.exp(-t * 6.2)
            dx = int(round(shake_amp * math.sin(idx * 2.45)))
            dy = int(round(shake_amp * math.cos(idx * 3.15)))

            crop_w = int(WIDTH / zoom)
            crop_h = int(HEIGHT / zoom)
            max_ox = max(1, (WIDTH - crop_w) // 2)
            max_oy = max(1, (HEIGHT - crop_h) // 2)

            cx = WIDTH // 2 + max(-max_ox, min(max_ox, dx))
            cy = HEIGHT // 2 + max(-max_oy, min(max_oy, dy))

            left = max(0, min(WIDTH - crop_w, cx - crop_w // 2))
            top = max(0, min(HEIGHT - crop_h, cy - crop_h // 2))

            cropped = base_img.crop((left, top, left + crop_w, top + crop_h))
            frame = cropped.resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR)

        proc.stdin.write(frame.tobytes())

    proc.stdin.close()
    err_out = proc.stderr.read().decode("utf-8", errors="ignore") if proc.stderr else ""
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Error renderizando clip de impacto: {err_out}")

    return output_clip_path


def _extract_video_segment_9_16(
    source_video_path: Path,
    start_sec: float,
    end_sec: float,
    output_seg_path: Path,
    has_audio: bool,
) -> Path:
    """Extrae un corte del video original normalizado a 720x1280 (9:16), 30fps y audio 44.1kHz."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    seg_dur = max(0.15, end_sec - start_sec)
    vf = (
        f"fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},eq=contrast=1.06:saturation=1.08,format=yuv420p"
    )

    if has_audio:
        cmd = [
            ffmpeg_exe,
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{start_sec:.3f}",
            "-t",
            f"{seg_dur:.3f}",
            "-i",
            str(source_video_path),
            "-vf",
            vf,
            "-af",
            "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "22",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(output_seg_path),
        ]
    else:
        cmd = [
            ffmpeg_exe,
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{start_sec:.3f}",
            "-t",
            f"{seg_dur:.3f}",
            "-i",
            str(source_video_path),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "22",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            str(output_seg_path),
        ]

    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Error cortando segmento ({start_sec}s-{end_sec}s): {proc.stderr}")
    return output_seg_path


def _assemble_aura_edit_sync(
    proposal: VideoProposal, source_video_path: Path
) -> Tuple[Path, str]:
    """
    Paso 5 del Pipeline: Ensamblado Automático (FFmpeg)
      1. Corta el video original en los timestamps de clímax.
      2. Inserta las imágenes Manga/Baki con Zoom Punch + Camera Shake + Impact Flash.
      3. Monta la pista de música Phonk + SFX de golpe sincronizados al milisegundo exacto de cada corte.
      4. Exporta el video final en vertical (9:16) .mp4 listo para entregar en Telegram y UI.
    """
    ensure_default_audio_assets()
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    job_id = proposal.job_id

    total_duration = _probe_video_duration(source_video_path)
    has_audio = _has_audio_stream(source_video_path)

    moments = sorted(
        proposal.impact_moments or [],
        key=lambda m: float(m.get("timestamp_sec", 0.0) or 0.0),
    )

    # Si no hubiera momentos cargados, crear uno a mitad del video
    if not moments:
        mid = round(total_duration * 0.5, 2)
        f_path = FRAMES_DIR / f"{job_id}_p1_frame.jpg"
        p_path = STORYBOARDS_DIR / f"{job_id}_p1_manga.jpg"
        extract_frame_at_timestamp(source_video_path, mid, f_path)
        _, eng = generate_manga_panel(f_path, proposal.visual_prompt or "Baki manga impact", p_path)
        moments = [
            {
                "index": 1,
                "timestamp_sec": mid,
                "storyboard_image_path": str(p_path),
                "sfx_type": "hit",
                "art_engine": eng,
            }
        ]

    segment_files: List[Path] = []
    impact_Insertion_times_ms: List[Tuple[int, str]] = []

    cursor_sec = 0.0
    accumulated_timeline_sec = 0.0
    hold_sec = IMPACT_HOLD_SECONDS

    for i, m in enumerate(moments, start=1):
        ts = float(m.get("timestamp_sec", 1.0))
        ts = max(cursor_sec + 0.25, min(ts, total_duration - 0.2))
        if ts <= cursor_sec:
            continue

        # 1. Segmento del video original desde cursor_sec hasta el timestamp de clímax
        vid_seg = OUTPUTS_DIR / f"{job_id}_seg_{i}_vid.mp4"
        _extract_video_segment_9_16(source_video_path, cursor_sec, ts, vid_seg, has_audio)
        segment_files.append(vid_seg)
        accumulated_timeline_sec += (ts - cursor_sec)

        # 2. Registrar el milisegundo exacto donde estalla el panel Manga para el SFX de golpe
        impact_start_ms = max(0, int(round(accumulated_timeline_sec * 1000)) - 35)
        impact_Insertion_times_ms.append((impact_start_ms, m.get("sfx_type", "hit")))

        # 3. Verificar que exista el panel Manga (regenerarlo si fue borrado)
        panel_path = Path(m.get("storyboard_image_path", ""))
        if not panel_path.exists():
            f_path = FRAMES_DIR / f"{job_id}_p{i}_frame.jpg"
            panel_path = STORYBOARDS_DIR / f"{job_id}_p{i}_manga.jpg"
            extract_frame_at_timestamp(source_video_path, ts, f_path)
            generate_manga_panel(f_path, m.get("manga_prompt", ""), panel_path)

        # 4. Renderizar el clip de impacto con Flash + Zoom Punch + Camera Shake
        imp_clip = OUTPUTS_DIR / f"{job_id}_seg_{i}_impact.mp4"
        _render_manga_impact_clip(panel_path, imp_clip, hold_sec)
        segment_files.append(imp_clip)
        accumulated_timeline_sec += hold_sec

        cursor_sec = ts

    # 5. Segmento final del video después del último impacto
    if cursor_sec < total_duration - 0.2:
        tail_seg = OUTPUTS_DIR / f"{job_id}_seg_tail.mp4"
        _extract_video_segment_9_16(source_video_path, cursor_sec, total_duration, tail_seg, has_audio)
        segment_files.append(tail_seg)
        accumulated_timeline_sec += (total_duration - cursor_sec)

    # 6. Concatenar todos los segmentos de video + impacto
    concat_list_file = OUTPUTS_DIR / f"{job_id}_concat.txt"
    concat_lines = [f"file '{p.resolve().as_posix()}'" for p in segment_files]
    concat_list_file.write_text("\n".join(concat_lines), encoding="utf-8")

    concat_video_path = OUTPUTS_DIR / f"{job_id}_concat_raw.mp4"
    cmd_concat = [
        ffmpeg_exe,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_file),
        "-c",
        "copy",
        str(concat_video_path),
    ]
    proc_c = subprocess.run(cmd_concat, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc_c.returncode != 0:
        raise RuntimeError(f"Error concatenando timeline: {proc_c.stderr}")

    # 7. Montaje de Audio Phonk + SFX de golpe sincronizados en cada corte
    phonk_track = get_phonk_track()
    sfx_files = get_sfx_files()
    hit_sfx = next((p for p in sfx_files if "hit" in p.name.lower()), sfx_files[0])
    slash_sfx = next((p for p in sfx_files if "slash" in p.name.lower()), hit_sfx)

    final_video_path = OUTPUTS_DIR / f"{job_id}_final.mp4"

    inputs_args = [
        "-i",
        str(concat_video_path),
        "-stream_loop",
        "-1",
        "-i",
        str(phonk_track),
    ]

    filter_parts = [
        f"[0:a]volume={ORIGINAL_AUDIO_VOLUME:.2f}[orig_a]",
        f"[1:a]volume={PHONK_VOLUME:.2f},afade=t=in:st=0:d=0.4[phonk_a]",
    ]
    mix_labels = ["[orig_a]", "[phonk_a]"]

    for idx, (delay_ms, sfx_kind) in enumerate(impact_Insertion_times_ms):
        sfx_Input_idx = idx + 2
        chosen_sfx = slash_sfx if sfx_kind == "slash" else hit_sfx
        inputs_args.extend(["-i", str(chosen_sfx)])
        lbl = f"[sfx_{idx}]"
        filter_parts.append(
            f"[{sfx_Input_idx}:a]volume={SFX_VOLUME:.2f},adelay={delay_ms}|{delay_ms}{lbl}"
        )
        mix_labels.append(lbl)

    num_audio_streams = len(mix_labels)
    filter_parts.append(
        f"{''.join(mix_labels)}amix=inputs={num_audio_streams}:duration=first:dropout_transition=0,"
        "alimiter=limit=0.95[final_a]"
    )
    filter_complex = ";".join(filter_parts)

    cmd_mix = [
        ffmpeg_exe,
        "-y",
        "-loglevel",
        "error",
        *inputs_args,
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v:0",
        "-map",
        "[final_a]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(final_video_path),
    ]

    proc_m = subprocess.run(cmd_mix, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc_m.returncode != 0:
        raise RuntimeError(f"Error mezclando audio Phonk + SFX: {proc_m.stderr}")

    # Limpieza de archivos temporales de segmentos
    for temp_p in [*segment_files, concat_list_file, concat_video_path]:
        try:
            temp_p.unlink(missing_ok=True)
        except Exception:
            pass

    art_engine = moments[0].get("art_engine", "Manga Engine")
    engine_summary = f"{art_engine} + {len(moments)} Cortes Zoom/Shake + Phonk/SFX (9:16)"
    return final_video_path, engine_summary


async def generate_final_video(
    proposal: VideoProposal, source_video_path: Path
) -> tuple[Path, str]:
    """Punto de entrada asíncrono para el Paso 5 (Ensamblado Automático)."""
    return await asyncio.to_thread(_assemble_aura_edit_sync, proposal, source_video_path)
