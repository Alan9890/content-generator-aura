import json
import sys
import time
from dataclasses import dataclass, asdict
from typing import List, Dict
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GEMINI_ANALYSIS_MODEL, DEFAULT_CONTENT_STYLE
from downloader import DownloadedVideo

# Cascada automática de modelos ante picos de demanda (503 UNAVAILABLE / 429) o disponibilidad por cuenta
FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
]


@dataclass
class VideoProposal:
    job_id: str
    source_url: str
    platform: str
    original_analysis: str
    new_title: str
    voiceover_script: str
    visual_prompt: str
    scenes: List[Dict[str, str]]
    social_caption: str

    def to_telegram_summary(self) -> str:
        scenes_text = "\n".join(
            [
                f"  • <b>Escena {i+1}:</b> {s.get('visual', '')} | <i>\"{s.get('narration', '')}\"</i>"
                for i, s in enumerate(self.scenes[:5])
            ]
        )
        return (
            f"🎬 <b>Propuesta de Video IA ({self.platform})</b>\n"
            f"🆔 Job: <code>{self.job_id}</code>\n\n"
            f"🔍 <b>Análisis del video original:</b>\n{self.original_analysis}\n\n"
            f"📌 <b>Nuevo Título:</b> {self.new_title}\n\n"
            f"🎙️ <b>Guion (Voz en Off / Avatar):</b>\n<i>{self.voiceover_script}</i>\n\n"
            f"🎨 <b>Prompt Maestro de Video IA:</b>\n<code>{self.visual_prompt}</code>\n\n"
            f"🎞️ <b>Escenas clave:</b>\n{scenes_text}\n\n"
            f"📲 <b>Copy para Redes (YT / IG / FB / TikTok):</b>\n{self.social_caption}"
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
                # Si es 404 NOT_FOUND u otro error de modelo, saltar de inmediato al siguiente modelo
                break
    raise last_err


def _build_draft_proposal(video: DownloadedVideo, reason: str = "") -> VideoProposal:
    short_desc = (video.description or video.title)[:220].replace("\n", " ")
    note = (
        f"Nota: {reason}"
        if reason
        else "💡 Configura tu GEMINI_API_KEY en '⚙️ Configuración' para habilitar el análisis cuadro por cuadro."
    )
    return VideoProposal(
        job_id=video.job_id,
        source_url=video.url,
        platform=video.platform,
        original_analysis=(
            f"Video descargado de {video.platform} ('{video.title}', {video.duration:.0f}s). {note}"
        ),
        new_title=f"{video.title[:50]} (Versión IA)",
        voiceover_script=(
            f"¿Ya viste esto sobre {video.title[:45]}? "
            f"En pocos segundos te explico la clave principal y cómo puedes aplicarlo hoy mismo. "
            f"Síguenos para más contenido como este."
        ),
        visual_prompt=(
            f"Vertical 9:16 cinematic high-impact short video inspired by '{video.title}', "
            "dynamic camera movement, studio lighting, ultra detailed, 4k."
        ),
        scenes=[
            {
                "visual": "Dynamic hook shot 9:16 with bold cinematic lighting",
                "narration": f"¿Ya viste esto sobre {video.title[:45]}?",
            }
        ],
        social_caption=f"🔥 {video.title}\n\n{short_desc}\n\n#shorts #reels #viral #ia",
    )


def analyze_and_create_proposal(
    video: DownloadedVideo, custom_instructions: str = ""
) -> VideoProposal:
    """Sube el video descargado a Gemini, lo analiza con fallback multimodelo y genera el guion + prompt IA."""
    style_guide = custom_instructions.strip() or DEFAULT_CONTENT_STYLE

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
Analiza este video descargado de {video.platform} (Título original: "{video.title}").
Quiero recrear un NUEVO video inspirado en su estructura ganadora, adaptado a este estilo/objetivo:
"{style_guide}"

Devuelve ÚNICAMENTE un objeto JSON válido con la siguiente estructura exacta:
{{
  "original_analysis": "Resumen breve (3-4 líneas) de qué trata el video original, cuál es su gancho (hook), ritmo visual y por qué funciona.",
  "new_title": "Título atractivo para el nuevo video recreado",
  "voiceover_script": "Guion completo de voz en off en español (natural, dinámico, sin acotaciones entre corchetes para poder pasarlo directo a Text-to-Speech, de 15 a 35 segundos).",
  "visual_prompt": "Prompt detallado en inglés para un modelo generativo de video (Google Veo / Runway Gen-3 / Kling), describiendo estética, iluminación, movimiento de cámara, sujeto y acción en formato vertical 9:16.",
  "scenes": [
    {{
      "visual": "Prompt visual en inglés para esta escena",
      "narration": "Fragmento del guion correspondiente"
    }}
  ],
  "social_caption": "Descripción lista para publicar en YouTube Shorts, Instagram Reels, Facebook y TikTok con hashtags relevantes."
}}
"""

        response = _generate_with_fallback(
            client=client,
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7,
            ),
        )

        data = json.loads(response.text)
        return VideoProposal(
            job_id=video.job_id,
            source_url=video.url,
            platform=video.platform,
            original_analysis=data.get("original_analysis", ""),
            new_title=data.get("new_title", "Nuevo Video IA"),
            voiceover_script=data.get("voiceover_script", ""),
            visual_prompt=data.get("visual_prompt", ""),
            scenes=data.get("scenes", []),
            social_caption=data.get("social_caption", ""),
        )
    except Exception as exc:
        print(f"[Analyzer] Todos los modelos saturados ({exc}), generando borrador inteligente.", file=sys.stderr)
        return _build_draft_proposal(
            video,
            reason="Los servidores de Gemini reportaron alta demanda temporal (503). Se creó este borrador base que puedes editar o refinar con el botón '✨ Refinar'.",
        )
    finally:
        if uploaded_file:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass


def refine_proposal(proposal: VideoProposal, feedback: str) -> VideoProposal:
    """Actualiza el guion y el prompt visual según las correcciones enviadas en Telegram o la UI."""
    client = _get_client()

    prompt = f"""
Tenemos esta propuesta actual de video:
{json.dumps(asdict(proposal), ensure_ascii=False, indent=2)}

El usuario pidió los siguientes cambios o ajustes:
"{feedback}"

Devuelve ÚNICAMENTE el JSON actualizado con las mismas claves:
"original_analysis", "new_title", "voiceover_script", "visual_prompt", "scenes", "social_caption".
Recuerda que "voiceover_script" debe ser solo el texto hablado listo para Text-to-Speech.
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
    return VideoProposal(
        job_id=proposal.job_id,
        source_url=proposal.source_url,
        platform=proposal.platform,
        original_analysis=data.get("original_analysis", proposal.original_analysis),
        new_title=data.get("new_title", proposal.new_title),
        voiceover_script=data.get("voiceover_script", proposal.voiceover_script),
        visual_prompt=data.get("visual_prompt", proposal.visual_prompt),
        scenes=data.get("scenes", proposal.scenes),
        social_caption=data.get("social_caption", proposal.social_caption),
    )
