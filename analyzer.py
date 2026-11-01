import json
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from art_generator import extract_frame_at_timestamp, generate_manga_panel
from config import (
    DEFAULT_CONTENT_STYLE,
    FRAMES_DIR,
    GEMINI_API_KEY,
    GEMINI_ANALYSIS_MODEL,
    MAX_IMPACT_MOMENTS,
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
    return f"{mins:02d}:{rem:04.1f}"


@dataclass
class ImpactMoment:
    index: int
    timestamp_sec: float
    timestamp_label: str
    moment_title: str
    subjects_action: str
    manga_prompt: str
    sfx_type: str = "hit"
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
            title = m.get("moment_title", "Clímax")
            action = m.get("subjects_action", "")
            eng = m.get("art_engine", "Manga Engine")
            moments_lines.append(
                f"  ⚡ <b>Panel #{idx} [{ts}] — {title}:</b>\n"
                f"      <i>{action}</i>\n"
                f"      🎨 Motor: <code>{eng}</code>"
            )
        moments_block = "\n\n".join(moments_lines) or "  • Sin paneles generados"

        return (
            f"🔥 <b>STORYBOARD MANGA / AURA EDIT ({self.platform})</b>\n"
            f"🆔 Job: <code>{self.job_id}</code>\n\n"
            f"📌 <b>Título:</b> {self.new_title}\n\n"
            f"🥊 <b>Sujetos y Roles Identificados:</b>\n{self.subjects_and_roles}\n\n"
            f"🔍 <b>Análisis de Tensión / Clímax:</b>\n{self.original_analysis}\n\n"
            f"⏱️ <b>Timestamps de Impacto y Storyboards:</b>\n{moments_block}\n\n"
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


def _generate_storyboards_for_moments(
    job_id: str,
    video_path: Path,
    raw_moments: List[Dict[str, Any]],
    duration: float,
) -> List[Dict[str, Any]]:
    """
    Ejecuta el Paso 3 para cada timestamp detectado:
    1. Extrae el frame real del video en ese segundo exacto.
    2. Genera la ilustración estilo Manga/Seinen/Baki (Local GPU A1111/ComfyUI -> Gemini/Flux -> Seinen Ink).
    """
    safe_duration = max(3.0, float(duration or 10.0))
    enriched: List[Dict[str, Any]] = []

    for i, m in enumerate(raw_moments[:MAX_IMPACT_MOMENTS], start=1):
        raw_ts = float(m.get("timestamp_sec", 0.0) or 0.0)
        # Asegurar que el timestamp caiga dentro del rango válido del video
        if raw_ts <= 0.1 or raw_ts >= safe_duration - 0.3:
            raw_ts = round((safe_duration * i) / (len(raw_moments[:MAX_IMPACT_MOMENTS]) + 1), 2)

        ts_label = format_timestamp(raw_ts)
        frame_file = FRAMES_DIR / f"{job_id}_p{i}_frame.jpg"
        panel_file = STORYBOARDS_DIR / f"{job_id}_p{i}_manga.jpg"

        extract_frame_at_timestamp(video_path, raw_ts, frame_file)
        manga_prompt = m.get("manga_prompt") or (
            "Extreme close-up manga impact frame, Baki Hanma and Berserk style, intense menacing aura, "
            "heavy black ink cross-hatching, radial speedlines"
        )
        _, engine_used = generate_manga_panel(
            source_frame_path=frame_file,
            manga_prompt=manga_prompt,
            output_panel_path=panel_file,
            seed=-1,
        )

        impact = ImpactMoment(
            index=i,
            timestamp_sec=round(raw_ts, 2),
            timestamp_label=ts_label,
            moment_title=m.get("moment_title") or f"Impacto #{i}",
            subjects_action=m.get("subjects_action") or "Momento de máxima tensión y enfrentamiento",
            manga_prompt=manga_prompt,
            sfx_type=m.get("sfx_type") if m.get("sfx_type") in ("hit", "slash") else ("hit" if i % 2 == 1 else "slash"),
            source_frame_path=str(frame_file),
            storyboard_image_path=str(panel_file),
            storyboard_url=f"/media/storyboards/{panel_file.name}",
            source_frame_url=f"/media/frames/{frame_file.name}",
            art_engine=engine_used,
        )
        enriched.append(asdict(impact))

    return enriched


def _build_draft_proposal(video: DownloadedVideo, reason: str = "") -> VideoProposal:
    dur = max(4.0, float(video.duration or 9.0))
    t1 = round(dur * 0.28, 2)
    t2 = round(dur * 0.62, 2)
    t3 = round(dur * 0.85, 2)

    raw_moments = [
        {
            "timestamp_sec": t1,
            "moment_title": "Inicio de Tensión / Guardia",
            "subjects_action": "Los sujetos entran en zona de confrontación con postura amenazante.",
            "manga_prompt": (
                f"Dramatic manga panel inspired by '{video.title}', two opponents facing off, "
                "Keisuke Itagaki Baki style, intense dark aura, heavy black ink, radial speedlines"
            ),
            "sfx_type": "hit",
        },
        {
            "timestamp_sec": t2,
            "moment_title": "Choque Principal / Impacto",
            "subjects_action": "Movimiento brusco de ataque o reacción de máxima tensión.",
            "manga_prompt": (
                f"Extreme impact frame inspired by '{video.title}', explosive motion, Berserk and Baki manga style, "
                "fierce eyes, shockwave lines, ultra high contrast monochrome ink"
            ),
            "sfx_type": "slash",
        },
        {
            "timestamp_sec": t3,
            "moment_title": "Clímax Final / Dominio",
            "subjects_action": "Postura dominante final en el clímax del video.",
            "manga_prompt": (
                f"Final climax manga panel inspired by '{video.title}', overwhelming menacing aura, "
                "Vagabond and Baki heavy brush ink, dramatic shadows, screentone"
            ),
            "sfx_type": "hit",
        },
    ]

    moments = _generate_storyboards_for_moments(
        video.job_id, video.file_path, raw_moments[:MAX_IMPACT_MOMENTS], dur
    )
    scenes = [
        {
            "visual": f"[{m['timestamp_label']}] {m['moment_title']} — {m['manga_prompt']}",
            "narration": m["subjects_action"],
        }
        for m in moments
    ]
    note = f" ({reason})" if reason else ""

    return VideoProposal(
        job_id=video.job_id,
        source_url=video.url,
        platform=video.platform,
        original_analysis=(
            f"Video de {video.platform} ('{video.title}', {dur:.1f}s) analizado para edición de impacto Manga/Seinen.{note}"
        ),
        subjects_and_roles=f"Sujetos principales detectados en '{video.title}' en dinámica de tensión/enfrentamiento.",
        new_title=f"{video.title[:45]} — MANGA AURA EDIT",
        voiceover_script=f"Sujetos y roles: {video.title}. Timestamps de impacto: "
        + ", ".join(m["timestamp_label"] for m in moments),
        visual_prompt=moments[0]["manga_prompt"] if moments else "",
        scenes=scenes,
        social_caption=f"🔥 {video.title} (Manga Aura Edit)\n\n#aura #mangaedit #baki #seinen #phonk #shorts #reels",
        impact_moments=moments,
    )


def analyze_and_create_proposal(
    video: DownloadedVideo, custom_instructions: str = ""
) -> VideoProposal:
    """
    Paso 2 y Paso 3 del Pipeline:
    1. Sube el video a Gemini y realiza el Análisis Multimodal:
       - Extrae timestamps exactos de clímax / tensión
       - Identifica sujetos y roles (ej. cangrejo vs figura)
       - Genera prompts de imagen estilo manga/seinen (Baki / Berserk)
    2. Extrae los frames reales en esos timestamps y genera los paneles de arte Manga/Baki.
    """
    style_guide = custom_instructions.strip() or DEFAULT_CONTENT_STYLE
    dur = max(3.0, float(video.duration or 10.0))

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

        prompt = f"""
Analiza multimodalmente este video de {video.platform} (Título: "{video.title}", Duración: {dur:.1f} segundos).
Estamos creando un "Manga / Seinen Aura Impact Edit" (estilo Baki Hanma / Berserk / Vagabond con música Phonk y cortes de impacto).
Directriz de estilo del usuario: "{style_guide}"

Tu tarea:
1. Identifica claramente los SUJETOS Y ROLES en el video (por ejemplo: "Cangrejo en postura defensiva con tenazas levantadas vs Figura humana desafiante", o quién es el atacante/defensor/protagonista).
2. Encuentra exactamente {MAX_IMPACT_MOMENTS} TIMESTAMPS CLAVE (en segundos con decimales, dentro del rango 0.3 a {max(1.0, dur - 0.4):.1f}) donde ocurra el mayor clímax, tensión, golpe, giro brusco o mirada intimidante.
3. Para cada timestamp, diseña un prompt de imagen en inglés estilo Manga Seinen (Keisuke Itagaki Baki style / Kentaro Miura Berserk style, monochrome black and white ink, extreme muscular/anatomical tension, radial speedlines, menacing aura) que represente exactamente a los sujetos de ese frame en versión manga épico.

Devuelve ÚNICAMENTE un objeto JSON válido con esta estructura exacta:
{{
  "original_analysis": "Resumen de 2-3 líneas sobre la tensión del video y por qué estos momentos funcionan para un edit de impacto.",
  "subjects_and_roles": "Descripción clara de los sujetos en escena y el rol de cada uno (ej. Sujeto A: Cangrejo agresivo [Amenaza] vs Sujeto B: Figura gigante [Retador]).",
  "new_title": "Título épico para el edit (ej. CANGREJO VS TITÁN — BAKI AURA EDIT)",
  "impact_moments": [
    {{
      "timestamp_sec": 2.3,
      "moment_title": "Nombre corto del clímax (ej. Guardia de Tenazas / Choque de Miradas)",
      "subjects_action": "Qué hacen exactamente los sujetos en este segundo exacto",
      "manga_prompt": "Detailed English prompt for Flux Schnell / SDXL + Baki Manga LoRA describing the exact subjects and pose in this frame in monochrome high-contrast Seinen manga style, heavy ink cross-hatching, menacing aura, action speedlines, 9:16 vertical",
      "sfx_type": "hit"
    }}
  ],
  "social_caption": "Caption corto y potente listo para TikTok / Reels / Shorts con hashtags #mangaedit #baki #phonk #aura"
}}
"""

        response = _generate_with_fallback(
            client=client,
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.65,
            ),
        )

        data = json.loads(response.text)
        raw_moments = data.get("impact_moments") or []
        if not raw_moments:
            return _build_draft_proposal(video)

        # Ordenar momentos cronológicamente
        raw_moments.sort(key=lambda x: float(x.get("timestamp_sec", 0.0) or 0.0))

        # Paso 3: Generar los paneles de arte Manga/Baki a partir de los frames reales
        moments = _generate_storyboards_for_moments(
            video.job_id, video.file_path, raw_moments, dur
        )

        scenes = [
            {
                "visual": f"[{m['timestamp_label']}] {m['moment_title']} ({m['art_engine']})",
                "narration": f"{m['subjects_action']} | Prompt: {m['manga_prompt']}",
            }
            for m in moments
        ]
        subjects_roles = data.get("subjects_and_roles", "Sujetos principales en confrontación.")
        summary_script = f"Sujetos: {subjects_roles}\n" + "\n".join(
            [f"[{m['timestamp_label']}] {m['moment_title']}: {m['subjects_action']}" for m in moments]
        )
        combined_prompts = "\n\n".join(
            [f"[Panel #{m['index']} @ {m['timestamp_label']}] {m['manga_prompt']}" for m in moments]
        )

        return VideoProposal(
            job_id=video.job_id,
            source_url=video.url,
            platform=video.platform,
            original_analysis=data.get("original_analysis", ""),
            subjects_and_roles=subjects_roles,
            new_title=data.get("new_title", "Nuevo Manga Aura Edit"),
            voiceover_script=summary_script,
            visual_prompt=combined_prompts,
            scenes=scenes,
            social_caption=data.get("social_caption", "#mangaedit #baki #phonk #aura"),
            impact_moments=moments,
        )
    except Exception as exc:
        print(f"[Analyzer] Fallback activado ({exc}), generando storyboard base.", file=sys.stderr)
        return _build_draft_proposal(video, reason="Modo directo sobre frames del video")
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
    """
    Regenera uno o todos los paneles del storyboard (cuando el usuario pulsa 'Regenerar imagen' en Telegram o UI).
    """
    moments = proposal.impact_moments or []
    for m in moments:
        idx = int(m.get("index", 1))
        if panel_index is not None and idx != panel_index:
            continue

        if custom_timestamp is not None and custom_timestamp >= 0:
            m["timestamp_sec"] = round(float(custom_timestamp), 2)
            m["timestamp_label"] = format_timestamp(m["timestamp_sec"])

        if custom_prompt.strip():
            m["manga_prompt"] = custom_prompt.strip()

        frame_file = FRAMES_DIR / f"{proposal.job_id}_p{idx}_frame.jpg"
        # Añadimos sufijo de versión o sobreescribimos con nueva semilla
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

    proposal.scenes = [
        {
            "visual": f"[{m['timestamp_label']}] {m['moment_title']} ({m['art_engine']})",
            "narration": f"{m['subjects_action']} | Prompt: {m['manga_prompt']}",
        }
        for m in moments
    ]
    proposal.visual_prompt = "\n\n".join(
        [f"[Panel #{m['index']} @ {m['timestamp_label']}] {m['manga_prompt']}" for m in moments]
    )
    return proposal


def refine_proposal(
    proposal: VideoProposal, feedback: str, video_path: Optional[Path] = None
) -> VideoProposal:
    """
    Actualiza prompts, timestamps o estilo según instrucciones del usuario y regenera las imágenes manga.
    """
    if GEMINI_API_KEY and GEMINI_API_KEY != "tu_gemini_api_key_aqui":
        try:
            client = _get_client()
            prompt = f"""
Tenemos este storyboard actual para un Manga/Baki Impact Edit:
{json.dumps(asdict(proposal), ensure_ascii=False, indent=2)}

El usuario pidió este ajuste o cambio ( o bien quiere regenerar con otro estilo/timestamp ):
"{feedback}"

Devuelve ÚNICAMENTE el JSON actualizado manteniendo "original_analysis", "subjects_and_roles", "new_title", "social_caption" y la lista "impact_moments" (cada uno con "index", "timestamp_sec", "moment_title", "subjects_action", "manga_prompt", "sfx_type").
"""
            response = _generate_with_fallback(
                client=client,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.7,
                ),
            )
            data = json.loads(response.text)
            proposal.original_analysis = data.get("original_analysis", proposal.original_analysis)
            proposal.subjects_and_roles = data.get("subjects_and_roles", proposal.subjects_and_roles)
            proposal.new_title = data.get("new_title", proposal.new_title)
            proposal.social_caption = data.get("social_caption", proposal.social_caption)

            new_moments = data.get("impact_moments")
            if new_moments and isinstance(new_moments, list):
                for i, nm in enumerate(new_moments):
                    if i < len(proposal.impact_moments):
                        proposal.impact_moments[i]["timestamp_sec"] = float(
                            nm.get("timestamp_sec", proposal.impact_moments[i]["timestamp_sec"])
                        )
                        proposal.impact_moments[i]["timestamp_label"] = format_timestamp(
                            proposal.impact_moments[i]["timestamp_sec"]
                        )
                        proposal.impact_moments[i]["moment_title"] = nm.get(
                            "moment_title", proposal.impact_moments[i]["moment_title"]
                        )
                        proposal.impact_moments[i]["subjects_action"] = nm.get(
                            "subjects_action", proposal.impact_moments[i]["subjects_action"]
                        )
                        proposal.impact_moments[i]["manga_prompt"] = nm.get(
                            "manga_prompt", proposal.impact_moments[i]["manga_prompt"]
                        )
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
