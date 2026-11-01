import json
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from art_generator import (
    build_seinen_master_prompt,
    extract_frame_at_timestamp,
    generate_manga_panel,
)
from config import (
    FRAMES_DIR,
    GEMINI_API_KEY,
    GEMINI_ANALYSIS_MODEL,
    IMPACT_HOLD_SECONDS,
    STORYBOARDS_DIR,
)
from downloader import DownloadedVideo

FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
]


def format_timestamp(sec: float) -> str:
    sec = max(0.0, float(sec))
    mins = int(sec // 60)
    rem = sec - (mins * 60)
    return f"{mins:02d}:{rem:05.2f}"


def parse_timestamp_to_seconds(val: Any) -> float:
    if isinstance(val, (int, float)):
        return max(0.0, float(val))
    s = str(val or "0").strip()
    if ":" in s:
        parts = s.split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60.0 + float(parts[1])
            if len(parts) == 3:
                return int(parts[0]) * 3600.0 + int(parts[1]) * 60.0 + float(parts[2])
        except Exception:
            return 0.0
    try:
        return max(0.0, float(s))
    except Exception:
        return 0.0


@dataclass
class ImpactMoment:
    index: int
    timestamp_sec: float
    timestamp_label: str
    freeze_duration: float
    moment_title: str
    subjects_action: str
    manga_prompt: str
    sfx_type: str = "bass_drop"
    source_frame_path: str = ""
    storyboard_image_path: str = ""
    storyboard_url: str = ""
    source_frame_url: str = ""
    art_engine: str = ""


@dataclass
class VideoProposal:
    job_id: str
    source_url: str
    platform: str
    original_analysis: str
    subjects_and_roles: str
    new_title: str
    voiceover_script: str
    visual_prompt: str
    scenes: List[Dict[str, Any]]
    social_caption: str
    impact_moments: List[Dict[str, Any]] = field(default_factory=list)

    def to_telegram_summary(self) -> str:
        moments_lines = []
        for m in self.impact_moments:
            idx = m.get("index", 1)
            ts = m.get("timestamp_label") or format_timestamp(m.get("timestamp_sec", 0))
            freeze = float(m.get("freeze_duration", IMPACT_HOLD_SECONDS))
            action = m.get("subjects_action", "")
            prompt_txt = m.get("manga_prompt", "")
            eng = m.get("art_engine", "Manga Engine")
            moments_lines.append(
                f"  ⚡ <b>Corte #{idx} en <code>{ts}</code> (Freeze: <code>{freeze:.2f}s</code>)</b>\n"
                f"      🥊 <b>Acción:</b> <i>{action}</i>\n"
                f"      🖋️ <b>Prompt Seinen:</b> <code>{prompt_txt[:220]}...</code>\n"
                f"      🎨 <b>Motor:</b> <code>{eng}</code>"
            )
        moments_block = "\n\n".join(moments_lines) or "  • Sin paneles generados"

        return (
            f"🔥 <b>STORYBOARD SEINEN / BAKI IMPACT ({self.platform})</b>\n"
            f"🆔 Job: <code>{self.job_id}</code>\n\n"
            f"📌 <b>Título:</b> {self.new_title}\n"
            f"🥊 <b>Acción Detectada:</b> {self.subjects_and_roles}\n\n"
            f"⏱️ <b>Momento Exacto de Impacto:</b>\n{moments_block}\n\n"
            f"📲 <b>Copy Sugerido:</b>\n{self.social_caption}"
        )


def _get_client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise ValueError("Falta GEMINI_API_KEY en el archivo .env")
    return genai.Client(api_key=GEMINI_API_KEY)


def _get_model_candidates() -> List[str]:
    candidates = []
    if GEMINI_ANALYSIS_MODEL:
        candidates.append(GEMINI_ANALYSIS_MODEL)
    for m in FALLBACK_MODELS:
        if m not in candidates:
            candidates.append(m)
    return candidates


def _generate_with_fallback(client: genai.Client, contents, config: types.GenerateContentConfig):
    last_err = None
    for model_name in _get_model_candidates():
        for attempt in range(2):
            try:
                return client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                last_err = exc
                err_str = str(exc)
                print(
                    f"[Analyzer] Modelo {model_name} (intento {attempt+1}) no disponible: {err_str[:140]}",
                    file=sys.stderr,
                )
                if "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str:
                    time.sleep(2 * (attempt + 1))
                    continue
                break
    raise last_err


def _build_single_moment_storyboard(
    job_id: str,
    video_path: Path,
    cut_sec: float,
    freeze_dur: float,
    action_desc: str,
    img_prompt: str,
    video_duration: float,
) -> List[Dict[str, Any]]:
    safe_dur = max(2.0, float(video_duration or 8.0))
    if cut_sec <= 0.1 or cut_sec >= safe_dur - 0.25:
        cut_sec = round(safe_dur * 0.52, 2)

    freeze_dur = max(0.6, min(1.0, float(freeze_dur or 0.8)))
    ts_label = format_timestamp(cut_sec)

    frame_file = FRAMES_DIR / f"{job_id}_p1_frame.jpg"
    panel_file = STORYBOARDS_DIR / f"{job_id}_p1_manga.jpg"

    extract_frame_at_timestamp(video_path, cut_sec, frame_file)
    _, engine_used = generate_manga_panel(
        source_frame_path=frame_file,
        manga_prompt=img_prompt,
        output_panel_path=panel_file,
        seed=-1,
    )

    impact = ImpactMoment(
        index=1,
        timestamp_sec=round(cut_sec, 2),
        timestamp_label=ts_label,
        freeze_duration=round(freeze_dur, 2),
        moment_title=f"Hard Cut @ {ts_label} ({freeze_dur:.1f}s)",
        subjects_action=action_desc,
        manga_prompt=img_prompt,
        sfx_type="bass_drop",
        source_frame_path=str(frame_file),
        storyboard_image_path=str(panel_file),
        storyboard_url=f"/media/storyboards/{panel_file.name}",
        source_frame_url=f"/media/frames/{frame_file.name}",
        art_engine=engine_used,
    )
    return [asdict(impact)]


def _build_draft_proposal(video: DownloadedVideo, reason: str = "") -> VideoProposal:
    dur = max(3.0, float(video.duration or 8.0))
    cut_sec = round(dur * 0.50, 2)
    action_desc = f"Clímax físico e impacto principal detectado en '{video.title}'"
    img_prompt = (
        "Gritty black and white manga drawing, ink on paper, heavy cross-hatching, Baki artstyle. "
        "A menacing hyper-muscular combat monster in an aggressive dynamic low-angle pose replicating the exact impact frame, "
        "hyper-vascular muscles, glowing pupil-less eyes, black combat aura radiating, impact shockwave, "
        "speed lines radiating outward, raw sketch, extreme contrast."
    )

    moments = _build_single_moment_storyboard(
        video.job_id, video.file_path, cut_sec, 0.8, action_desc, img_prompt, dur
    )
    m0 = moments[0]
    note = f" ({reason})" if reason else ""

    return VideoProposal(
        job_id=video.job_id,
        source_url=video.url,
        platform=video.platform,
        original_analysis=f"Corte seco programado en {m0['timestamp_label']} (freeze: {m0['freeze_duration']}s). {action_desc}{note}",
        subjects_and_roles=action_desc,
        new_title=f"{video.title[:45]} — BAKI / SEINEN EDIT",
        voiceover_script=json.dumps(
            {
                "cut_timestamp": m0["timestamp_label"],
                "freeze_duration": m0["freeze_duration"],
                "action_description": action_desc,
                "image_generation_prompt": img_prompt,
            },
            ensure_ascii=False,
            indent=2,
        ),
        visual_prompt=build_seinen_master_prompt(img_prompt),
        scenes=[
            {
                "visual": f"[{m0['timestamp_label']}] Freeze {m0['freeze_duration']}s ({m0['art_engine']})",
                "narration": action_desc,
            }
        ],
        social_caption=f"🔥 {video.title}\n\n#baki #berserk #seinen #mangaedit #phonk #aura #shorts",
        impact_moments=moments,
    )


def analyze_and_create_proposal(
    video: DownloadedVideo, custom_instructions: str = ""
) -> VideoProposal:
    """
    Paso 2 y Paso 3 del Pipeline usando el prompt exacto de Director de Animación de Anime/Manga (Baki, Berserk, Kengan Ashura).
    """
    dur = max(2.0, float(video.duration or 8.0))

    if not GEMINI_API_KEY or GEMINI_API_KEY == "tu_gemini_api_key_aqui":
        return _build_draft_proposal(video)

    client = _get_client()
    uploaded_file = None

    try:
        uploaded_file = client.files.upload(file=str(video.file_path))
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)

        if uploaded_file.state.name == "FAILED":
            raise RuntimeError("Error al procesar el video en Gemini File API.")

        extra_note = (
            f"\nNota adicional del usuario: {custom_instructions.strip()}\n"
            if custom_instructions.strip()
            else ""
        )

        prompt = f"""Actúa como un director de animación de anime y manga de peleas extremas (estilo Baki, Berserk, Kengan Ashura). Tu tarea es analizar el video proporcionado (duración total: {dur:.2f} segundos) y encontrar el momento exacto de impacto o tensión física para transformar la escena en un panel de manga ultra dramático.{extra_note}

Instrucciones de análisis:
1. Detecta el fotograma exacto donde se lanza el golpe más potente, la mirada más intimidante o el clímax físico.
2. Identifica la pose y acción del sujeto en ese instante.
3. Transforma mentalmente al sujeto en un monstruo de combate hipertrofiado, amenazante y cubierto de aura asesina.

Devuelve estrictamente un objeto JSON con este formato:
{{
  "cut_timestamp": "00:00.00",
  "freeze_duration": 0.8,
  "action_description": "Breve descripción del movimiento detectado",
  "image_generation_prompt": "Gritty black and white manga drawing, ink on paper, heavy cross-hatching, Baki artstyle. [Describir al sujeto replicando exactamente la pose en ángulo dinámico o contra-picado], hyper-vascular muscles, glowing pupil-less eyes, black combat aura radiating, impact shockwave, speed lines radiating outward, raw sketch, extreme contrast."
}}"""

        response = _generate_with_fallback(
            client=client,
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.6,
            ),
        )

        data = json.loads(response.text)
        if isinstance(data, list) and data:
            data = data[0]

        cut_sec = parse_timestamp_to_seconds(data.get("cut_timestamp", "00:02.00"))
        freeze_dur = float(data.get("freeze_duration", 0.8) or 0.8)
        freeze_dur = max(0.6, min(1.0, freeze_dur))
        action_desc = data.get("action_description") or "Momento exacto de impacto y tensión física"
        img_prompt = data.get("image_generation_prompt") or (
            "Gritty black and white manga drawing, ink on paper, heavy cross-hatching, Baki artstyle. "
            "A menacing hyper-muscular fighter in a dynamic low-angle pose, hyper-vascular muscles, "
            "glowing pupil-less eyes, black combat aura radiating, impact shockwave, speed lines radiating outward, raw sketch, extreme contrast."
        )

        moments = _build_single_moment_storyboard(
            video.job_id,
            video.file_path,
            cut_sec,
            freeze_dur,
            action_desc,
            img_prompt,
            dur,
        )
        m0 = moments[0]

        analysis_summary = (
            f"Cut Timestamp: {m0['timestamp_label']} | Freeze Duration: {m0['freeze_duration']}s\n"
            f"Movimiento detectado: {action_desc}"
        )

        return VideoProposal(
            job_id=video.job_id,
            source_url=video.url,
            platform=video.platform,
            original_analysis=analysis_summary,
            subjects_and_roles=action_desc,
            new_title=f"{video.title[:45]} — SEINEN IMPACT EDIT",
            voiceover_script=json.dumps(
                {
                    "cut_timestamp": m0["timestamp_label"],
                    "freeze_duration": m0["freeze_duration"],
                    "action_description": action_desc,
                    "image_generation_prompt": img_prompt,
                },
                ensure_ascii=False,
                indent=2,
            ),
            visual_prompt=build_seinen_master_prompt(img_prompt),
            scenes=[
                {
                    "visual": f"[{m0['timestamp_label']}] Freeze {m0['freeze_duration']}s ({m0['art_engine']})",
                    "narration": action_desc,
                }
            ],
            social_caption=f"🔥 {video.title}\n\n#baki #berserk #kenganashura #seinen #mangaedit #phonk #shorts",
            impact_moments=moments,
        )
    except Exception as exc:
        print(f"[Analyzer] Fallback activado ({exc}), generando storyboard base.", file=sys.stderr)
        return _build_draft_proposal(video, reason="Análisis directo de fotograma")
    finally:
        if uploaded_file:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass


def regenerate_storyboard_panel(
    proposal: VideoProposal,
    video_path: Path,
    panel_index: Optional[int] = None,
    custom_prompt: str = "",
    custom_timestamp: Optional[float] = None,
) -> VideoProposal:
    """Regenera la imagen del panel Manga con nueva semilla (y nuevo timestamp/prompt si se especifica)."""
    moments = proposal.impact_moments or []
    for m in moments:
        idx = int(m.get("index", 1))
        if panel_index is not None and panel_index > 0 and idx != panel_index:
            continue

        if custom_timestamp is not None and custom_timestamp >= 0:
            m["timestamp_sec"] = round(float(custom_timestamp), 2)
            m["timestamp_label"] = format_timestamp(m["timestamp_sec"])
            m["moment_title"] = f"Hard Cut @ {m['timestamp_label']} ({m.get('freeze_duration', 0.8):.1f}s)"

        if custom_prompt.strip():
            m["manga_prompt"] = custom_prompt.strip()

        frame_file = FRAMES_DIR / f"{proposal.job_id}_p{idx}_frame.jpg"
        panel_file = STORYBOARDS_DIR / f"{proposal.job_id}_p{idx}_manga.jpg"

        extract_frame_at_timestamp(video_path, float(m.get("timestamp_sec", 1.0)), frame_file)
        new_seed = random.randint(1000, 9999999)
        _, engine_used = generate_manga_panel(
            source_frame_path=frame_file,
            manga_prompt=m.get("manga_prompt", ""),
            output_panel_path=panel_file,
            seed=new_seed,
        )
        m["source_frame_path"] = str(frame_file)
        m["storyboard_image_path"] = str(panel_file)
        m["storyboard_url"] = f"/media/storyboards/{panel_file.name}?v={new_seed}"
        m["source_frame_url"] = f"/media/frames/{frame_file.name}?v={new_seed}"
        m["art_engine"] = engine_used

    if moments:
        m0 = moments[0]
        proposal.visual_prompt = build_seinen_master_prompt(m0.get("manga_prompt", ""))
        proposal.original_analysis = (
            f"Cut Timestamp: {m0['timestamp_label']} | Freeze Duration: {m0.get('freeze_duration', 0.8)}s\n"
            f"Movimiento detectado: {m0.get('subjects_action', '')}"
        )
    return proposal


def refine_proposal(
    proposal: VideoProposal, feedback: str, video_path: Optional[Path] = None
) -> VideoProposal:
    """Actualiza cut_timestamp, freeze_duration o image_generation_prompt según el feedback del usuario."""
    if GEMINI_API_KEY and GEMINI_API_KEY != "tu_gemini_api_key_aqui":
        try:
            client = _get_client()
            m0 = (proposal.impact_moments or [{}])[0]
            current_json = {
                "cut_timestamp": m0.get("timestamp_label", "00:02.00"),
                "freeze_duration": m0.get("freeze_duration", 0.8),
                "action_description": m0.get("subjects_action", proposal.subjects_and_roles),
                "image_generation_prompt": m0.get("manga_prompt", ""),
            }
            prompt = f"""Tenemos este análisis de impacto actual estilo Baki/Berserk:
{json.dumps(current_json, ensure_ascii=False, indent=2)}

El usuario pidió el siguiente ajuste:
"{feedback}"

Devuelve estrictamente el objeto JSON actualizado con este formato:
{{
  "cut_timestamp": "00:00.00",
  "freeze_duration": 0.8,
  "action_description": "Breve descripción del movimiento detectado",
  "image_generation_prompt": "Gritty black and white manga drawing, ink on paper, heavy cross-hatching, Baki artstyle. [Describir al sujeto replicando exactamente la pose en ángulo dinámico o contra-picado], hyper-vascular muscles, glowing pupil-less eyes, black combat aura radiating, impact shockwave, speed lines radiating outward, raw sketch, extreme contrast."
}}"""
            response = _generate_with_fallback(
                client=client,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.65,
                ),
            )
            data = json.loads(response.text)
            if proposal.impact_moments:
                m = proposal.impact_moments[0]
                if "cut_timestamp" in data:
                    m["timestamp_sec"] = round(parse_timestamp_to_seconds(data["cut_timestamp"]), 2)
                    m["timestamp_label"] = format_timestamp(m["timestamp_sec"])
                if "freeze_duration" in data:
                    m["freeze_duration"] = max(0.6, min(1.0, float(data["freeze_duration"])))
                if "action_description" in data:
                    m["subjects_action"] = data["action_description"]
                    proposal.subjects_and_roles = data["action_description"]
                if "image_generation_prompt" in data:
                    m["manga_prompt"] = data["image_generation_prompt"]
                m["moment_title"] = f"Hard Cut @ {m['timestamp_label']} ({m['freeze_duration']:.1f}s)"
        except Exception as exc:
            print(f"[Analyzer] Refine info: {exc}", file=sys.stderr)
            for m in proposal.impact_moments:
                m["manga_prompt"] = f"{m.get('manga_prompt', '')}, {feedback}"
    else:
        for m in proposal.impact_moments:
            m["manga_prompt"] = f"{m.get('manga_prompt', '')}, {feedback}"

    if video_path and video_path.exists():
        proposal = regenerate_storyboard_panel(proposal, video_path, panel_index=None)

    return proposal
