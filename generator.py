import asyncio
import subprocess
from pathlib import Path
from typing import List, Tuple

import imageio_ffmpeg
from PIL import Image

from analyzer import VideoProposal
from art_generator import extract_frame_at_timestamp, generate_manga_panel
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

FPS = 30
WIDTH = 720
HEIGHT = 1280


def _probe_video_duration(video_path: Path) -> float:
    """Obtiene la duración real del video usando ffmpeg."""
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
    Renderiza el panel Manga en un Corte Seco (Hard Cut) de 0.6s - 1.0s (por defecto 0.8s):
    - Sin destellos de color ni alteraciones sobre el video real.
    - Micro-zoom brusco del 100% al 108% de golpe al entrar el impacto.
    - Screen Shake rápido (temblor seco de 5 cuadros iniciales) y luego cuadro fijo al 108%.
    """
    hold_seconds = max(0.6, min(1.0, float(hold_seconds or 0.8)))
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    base_img = (
        Image.open(panel_image_path)
        .convert("RGB")
        .resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    )

    total_frames = max(18, int(round(hold_seconds * FPS)))

    # Patrón exacto de 5 cuadros de Screen Shake seco al entrar el golpe (dx, dy en píxeles) + salto brusco 100% -> 108%
    shake_offsets = [
        (0, 0, 1.00),     # Cuadro 0: Entrada al 100%
        (-18, 14, 1.08),  # Cuadro 1: Golpe brusco al 108% + sacudida
        (16, -12, 1.08),  # Cuadro 2
        (-12, -10, 1.08), # Cuadro 3
        (9, 8, 1.08),     # Cuadro 4
        (-5, 4, 1.08),    # Cuadro 5
    ]

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
        "19",
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

    # Pre-calcular el cuadro fijo al 108% para los cuadros posteriores al temblor (frames 6..N)
    steady_zoom = 1.08
    steady_w = int(WIDTH / steady_zoom)
    steady_h = int(HEIGHT / steady_zoom)
    steady_left = (WIDTH - steady_w) // 2
    steady_top = (HEIGHT - steady_h) // 2
    steady_frame_bytes = (
        base_img.crop((steady_left, steady_top, steady_left + steady_w, steady_top + steady_h))
        .resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        .tobytes()
    )

    for idx in range(total_frames):
        if idx < len(shake_offsets):
            dx, dy, zoom = shake_offsets[idx]
            if zoom <= 1.001 and dx == 0 and dy == 0:
                proc.stdin.write(base_img.tobytes())
            else:
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
        else:
            proc.stdin.write(steady_frame_bytes)

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
    """
    Extrae el corte del video original en formato vertical 9:16 dejando la imagen del video real
    100% limpia (sin líneas de velocidad ni alteraciones de color).
    """
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    seg_dur = max(0.12, end_sec - start_sec)
    vf = (
        f"fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},format=yuv420p"
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
            "21",
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
            "21",
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
      - Deja el video original completamente limpio.
      - En cut_timestamp aplica un Corte Seco (Hard Cut) a la imagen Manga durante freeze_duration (0.6s-1.0s).
      - Aplica Screen Shake rápido de 5 cuadros + Micro-Zoom brusco del 100% al 108%.
      - Dispara el sonido seco de explosión / Phonk Bass Drop distorsionado exactamente en el corte.
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
                "freeze_duration": IMPACT_HOLD_SECONDS,
                "storyboard_image_path": str(p_path),
                "sfx_type": "bass_drop",
                "art_engine": eng,
            }
        ]

    segment_files: List[Path] = []
    impact_insertion_times_ms: List[int] = []

    cursor_sec = 0.0
    accumulated_timeline_sec = 0.0

    for i, m in enumerate(moments, start=1):
        ts = float(m.get("timestamp_sec", 1.0))
        ts = max(cursor_sec + 0.20, min(ts, total_duration - 0.15))
        if ts <= cursor_sec:
            continue

        freeze_sec = max(0.6, min(1.0, float(m.get("freeze_duration", IMPACT_HOLD_SECONDS) or 0.8)))

        # 1. Segmento limpio del video original hasta cut_timestamp
        vid_seg = OUTPUTS_DIR / f"{job_id}_seg_{i}_vid.mp4"
        _extract_video_segment_9_16(source_video_path, cursor_sec, ts, vid_seg, has_audio)
        segment_files.append(vid_seg)
        accumulated_timeline_sec += (ts - cursor_sec)

        # 2. Milisegundo exacto del Hard Cut para el Phonk Bass Drop
        impact_start_ms = max(0, int(round(accumulated_timeline_sec * 1000)))
        impact_insertion_times_ms.append(impact_start_ms)

        # 3. Verificar panel Manga
        panel_path = Path(m.get("storyboard_image_path", ""))
        if not panel_path.exists():
            f_path = FRAMES_DIR / f"{job_id}_p{i}_frame.jpg"
            panel_path = STORYBOARDS_DIR / f"{job_id}_p{i}_manga.jpg"
            extract_frame_at_timestamp(source_video_path, ts, f_path)
            generate_manga_panel(f_path, m.get("manga_prompt", ""), panel_path)

        # 4. Clip del Hard Cut con Screen Shake (5 frames) + Micro-Zoom (100% -> 108%)
        imp_clip = OUTPUTS_DIR / f"{job_id}_seg_{i}_impact.mp4"
        _render_manga_impact_clip(panel_path, imp_clip, freeze_sec)
        segment_files.append(imp_clip)
        accumulated_timeline_sec += freeze_sec

        cursor_sec = ts

    # 5. Resto del video limpio después del corte
    if cursor_sec < total_duration - 0.15:
        tail_seg = OUTPUTS_DIR / f"{job_id}_seg_tail.mp4"
        _extract_video_segment_9_16(source_video_path, cursor_sec, total_duration, tail_seg, has_audio)
        segment_files.append(tail_seg)
        accumulated_timeline_sec += (total_duration - cursor_sec)

    # 6. Concatenar todos los segmentos
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

    # 7. Montaje de Audio: Video original + Phonk de fondo + Phonk Bass Drop en el Hard Cut
    phonk_track = get_phonk_track()
    sfx_files = get_sfx_files()
    bass_drop_sfx = next(
        (p for p in sfx_files if "bass_drop" in p.name.lower()),
        sfx_files[0],
    )

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
        f"[1:a]volume={PHONK_VOLUME:.2f},afade=t=in:st=0:d=0.3[phonk_a]",
    ]
    mix_labels = ["[orig_a]", "[phonk_a]"]

    for idx, delay_ms in enumerate(impact_insertion_times_ms):
        sfx_input_idx = idx + 2
        inputs_args.extend(["-i", str(bass_drop_sfx)])
        lbl = f"[sfx_{idx}]"
        filter_parts.append(
            f"[{sfx_input_idx}:a]volume={SFX_VOLUME:.2f},adelay={delay_ms}|{delay_ms}{lbl}"
        )
        mix_labels.append(lbl)

    num_audio_streams = len(mix_labels)
    filter_parts.append(
        f"{''.join(mix_labels)}amix=inputs={num_audio_streams}:duration=first:dropout_transition=0,"
        "alimiter=limit=0.96[final_a]"
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
        raise RuntimeError(f"Error mezclando audio Phonk + Bass Drop: {proc_m.stderr}")

    for temp_p in [*segment_files, concat_list_file, concat_video_path]:
        try:
            temp_p.unlink(missing_ok=True)
        except Exception:
            pass

    art_engine = moments[0].get("art_engine", "Seinen Manga Engine")
    freeze_val = moments[0].get("freeze_duration", IMPACT_HOLD_SECONDS)
    engine_summary = f"{art_engine} | Hard Cut ({freeze_val}s) + 108% Zoom/Shake + Phonk Bass Drop"
    return final_video_path, engine_summary


async def generate_final_video(
    proposal: VideoProposal, source_video_path: Path
) -> tuple[Path, str]:
    """Punto de entrada asíncrono para el Paso 5 (Ensamblado Automático)."""
    return await asyncio.to_thread(_assemble_aura_edit_sync, proposal, source_video_path)
