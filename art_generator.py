import base64
import io
import math
import random
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional

import imageio_ffmpeg
import requests
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from config import (
    ART_PROVIDER,
    COMFYUI_URL,
    GEMINI_API_KEY,
    IMG2IMG_DENOISING,
    MANGA_LORA_TAG,
    MANGA_NEGATIVE_PROMPT,
    SD_WEBUI_URL,
)

# Estilo maestro forzado en todos los prompts para estética Seinen / Baki / Berserk
SEINEN_STYLE_SUFFIX = (
    "masterpiece manga panel, Keisuke Itagaki Baki the Grappler art style, Kentaro Miura Berserk heavy ink, "
    "extreme muscular tension, dynamic action speedlines, dramatic cross-hatching, monochrome black and white manga, "
    "high contrast sumi-e ink brush, fierce menacing aura, screentone shading, ultra detailed lineart"
)


def extract_frame_at_timestamp(
    video_path: Path, timestamp_sec: float, output_frame_path: Path
) -> Path:
    """Extrae el cuadro exacto del video en el timestamp indicado en formato vertical 9:16 (720x1280)."""
    output_frame_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    safe_ts = max(0.0, float(timestamp_sec))

    vf = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280"
    cmd = [
        ffmpeg_exe,
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{safe_ts:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        vf,
        "-q:v",
        "2",
        str(output_frame_path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0 or not output_frame_path.exists():
        # Fallback al primer segundo si el timestamp excedió la duración
        cmd[cmd.index("-ss") + 1] = "0.500"
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not output_frame_path.exists():
        # Crear lienzo base negro si el video no pudo decodificarse
        Image.new("RGB", (720, 1280), (15, 15, 15)).save(output_frame_path, "JPEG", quality=92)

    return output_frame_path


def _try_a1111_img2img(
    source_frame_path: Path, full_prompt: str, output_panel_path: Path, seed: int
) -> tuple[bool, str]:
    """
    Ejecución local en Automatic1111 / SD WebUI Forge (optimizado para RTX 3050 6GB VRAM: 576x1024).
    Convierte el frame real del timestamp a estilo Manga/Baki respetando la pose y sujetos.
    """
    try:
        ping = requests.get(f"{SD_WEBUI_URL}/sdapi/v1/sd-models", timeout=2.5)
        if ping.status_code != 200:
            return False, ""
    except Exception:
        return False, ""

    try:
        frame_bytes = source_frame_path.read_bytes()
        b64_img = base64.b64encode(frame_bytes).decode("utf-8")
        prompt_with_lora = f"{full_prompt}, {MANGA_LORA_TAG}".strip(", ")

        payload = {
            "init_images": [b64_img],
            "prompt": prompt_with_lora,
            "negative_prompt": MANGA_NEGATIVE_PROMPT,
            "denoising_strength": IMG2IMG_DENOISING,
            "steps": 20,
            "cfg_scale": 6.5,
            "width": 576,
            "height": 1024,
            "sampler_name": "DPM++ 2M Karras",
            "seed": seed,
        }
        resp = requests.post(
            f"{SD_WEBUI_URL}/sdapi/v1/img2img", json=payload, timeout=180
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("images"):
            raw_bytes = base64.b64decode(data["images"][0])
            img = Image.open(io.BytesIO(raw_bytes)).convert("RGB").resize((720, 1280), Image.Resampling.LANCZOS)
            img = _apply_manga_finishing_overlay(img)
            img.save(output_panel_path, "JPEG", quality=94)
            return True, "Local GPU (A1111 / Forge SDXL+LoRA)"
    except Exception as exc:
        print(f"[ArtGen] A1111 local error: {exc}", file=sys.stderr)
    return False, ""


def _try_comfyui_local(
    source_frame_path: Path, full_prompt: str, output_panel_path: Path, seed: int
) -> tuple[bool, str]:
    """
    Ejecución local en ComfyUI (http://127.0.0.1:8188).
    Detecta automáticamente el checkpoint instalado y ejecuta img2img en 576x1024 (ideal para 6GB VRAM).
    """
    try:
        info_resp = requests.get(
            f"{COMFYUI_URL}/object_info/CheckpointLoaderSimple", timeout=2.5
        )
        if info_resp.status_code != 200:
            return False, ""
        info_json = info_resp.json()
        ckpts = (
            info_json.get("CheckpointLoaderSimple", {})
            .get("input", {})
            .get("required", {})
            .get("ckpt_name", [[]])[0]
        )
        if not ckpts:
            return False, ""
        ckpt_name = ckpts[0]
    except Exception:
        return False, ""

    try:
        # Subir frame base del timestamp a ComfyUI
        with open(source_frame_path, "rb") as f:
            up_resp = requests.post(
                f"{COMFYUI_URL}/upload/image",
                files={"image": (source_frame_path.name, f, "image/jpeg")},
                data={"overwrite": "true"},
                timeout=15,
            )
        up_resp.raise_for_status()
        uploaded_name = up_resp.json().get("name", source_frame_path.name)

        actual_seed = seed if seed >= 0 else random.randint(1, 2**31 - 1)
        workflow = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": ckpt_name},
            },
            "2": {
                "class_type": "LoadImage",
                "inputs": {"image": uploaded_name},
            },
            "3": {
                "class_type": "VAEEncode",
                "inputs": {"pixels": ["2", 0], "vae": ["1", 2]},
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": full_prompt, "clip": ["1", 1]},
            },
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": MANGA_NEGATIVE_PROMPT, "clip": ["1", 1]},
            },
            "6": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": actual_seed,
                    "steps": 18,
                    "cfg": 6.5,
                    "sampler_name": "euler_ancestral",
                    "scheduler": "normal",
                    "denoise": IMG2IMG_DENOISING,
                    "model": ["1", 0],
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "latent_image": ["3", 0],
                },
            },
            "7": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["6", 0], "vae": ["1", 2]},
            },
            "8": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "aura_manga", "images": ["7", 0]},
            },
        }

        client_id = uuid.uuid4().hex
        q_resp = requests.post(
            f"{COMFYUI_URL}/prompt",
            json={"prompt": workflow, "client_id": client_id},
            timeout=15,
        )
        q_resp.raise_for_status()
        prompt_id = q_resp.json()["prompt_id"]

        for _ in range(90):
            time.sleep(2)
            hist = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10).json()
            if prompt_id in hist:
                outputs = hist[prompt_id].get("outputs", {})
                for node_out in outputs.values():
                    if "images" in node_out and node_out["images"]:
                        img_Meta = node_out["images"][0]
                        view_url = (
                            f"{COMFYUI_URL}/view?filename={urllib.parse.quote(img_Meta['filename'])}"
                            f"&subfolder={urllib.parse.quote(img_Meta.get('subfolder', ''))}"
                            f"&type={img_Meta.get('type', 'output')}"
                        )
                        raw_img = requests.get(view_url, timeout=20).content
                        img = (
                            Image.open(io.BytesIO(raw_img))
                            .convert("RGB")
                            .resize((720, 1280), Image.Resampling.LANCZOS)
                        )
                        img = _apply_manga_finishing_overlay(img)
                        img.save(output_panel_path, "JPEG", quality=94)
                        return True, f"Local GPU (ComfyUI: {ckpt_name})"
    except Exception as exc:
        print(f"[ArtGen] ComfyUI local error: {exc}", file=sys.stderr)

    return False, ""


def _try_gemini_frame_to_manga(
    source_frame_path: Path, full_prompt: str, output_panel_path: Path
) -> tuple[bool, str]:
    """
    Transforma el frame real del timestamp en una ilustración Manga/Baki usando Gemini Multimodal Image Generation.
    Conserva los sujetos reales del frame (ej. cangrejo vs figura) con estética Seinen.
    """
    if not GEMINI_API_KEY or GEMINI_API_KEY == "tu_gemini_api_key_aqui":
        return False, ""

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        frame_img = Image.open(source_frame_path).convert("RGB")
        instruction = (
            "Transform this exact video frame into an extreme high-contrast black-and-white Japanese Seinen Manga panel "
            "in the style of Baki the Grappler (Keisuke Itagaki) and Berserk (Kentaro Miura). "
            "Keep the exact subjects, poses, and composition from the frame, but redraw them with heavy aggressive black ink, "
            "dramatic cross-hatching, fierce glowing eyes, action speedlines, and intense combat aura. "
            f"Scene details: {full_prompt}"
        )

        for model_name in ("gemini-2.0-flash-exp-image-generation", "gemini-2.5-flash-image-preview"):
            try:
                resp = client.models.generate_content(
                    model=model_name,
                    contents=[frame_img, instruction],
                    config=types.GenerateContentConfig(
                        response_modalities=["TEXT", "IMAGE"],
                    ),
                )
                for cand in getattr(resp, "candidates", []) or []:
                    for part in getattr(cand.content, "parts", []) or []:
                        inline = getattr(part, "inline_data", None)
                        if inline and getattr(inline, "data", None):
                            img = (
                                Image.open(io.BytesIO(inline.data))
                                .convert("RGB")
                                .resize((720, 1280), Image.Resampling.LANCZOS)
                            )
                            img = _apply_manga_finishing_overlay(img)
                            img.save(output_panel_path, "JPEG", quality=94)
                            return True, f"Gemini Manga Img2Img ({model_name})"
            except Exception:
                continue
    except Exception as exc:
        print(f"[ArtGen] Gemini Image Gen info: {exc}", file=sys.stderr)

    return False, ""


def _try_flux_schnell_api(
    full_prompt: str, output_panel_path: Path, seed: int
) -> tuple[bool, str]:
    """Genera el panel Manga/Baki con Flux Schnell en formato 9:16 (720x1280)."""
    try:
        actual_seed = seed if seed >= 0 else random.randint(1, 9999999)
        encoded = urllib.parse.quote(full_prompt)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width=720&height=1280&model=flux&nologo=true&seed={actual_seed}"
        )
        resp = requests.get(url, timeout=35)
        if resp.status_code == 200 and len(resp.content) > 10_000:
            img = (
                Image.open(io.BytesIO(resp.content))
                .convert("RGB")
                .resize((720, 1280), Image.Resampling.LANCZOS)
            )
            img = _apply_manga_finishing_overlay(img)
            img.save(output_panel_path, "JPEG", quality=94)
            return True, "Flux Schnell (Manga/Baki 9:16)"
    except Exception as exc:
        print(f"[ArtGen] Flux Schnell API info: {exc}", file=sys.stderr)
    return False, ""


def _apply_manga_finishing_overlay(img: Image.Image, force_bw_ink: bool = False) -> Image.Image:
    """
    Aplica acabado de viñeta Seinen de alto impacto:
    - Contraste dramático B&W (opcional o preservando acentos de aura)
    - Líneas cinéticas radiales (speedlines) en los bordes
    - Marco de impacto estilo manga
    """
    w, h = img.size
    if force_bw_ink:
        gray = ImageOps.grayscale(img)
        gray = ImageOps.autocontrast(gray, cutoff=3)
        edges = gray.filter(ImageFilter.FIND_EDGES)
        edges = ImageOps.invert(edges)
        edges = ImageEnhance.Contrast(edges).enhance(3.2)
        hi_con = ImageEnhance.Contrast(gray).enhance(2.6)
        blended = ImageChops.multiply(hi_con, edges)
        img = ImageOps.colorize(blended, black="#050507", white="#f5f5f0").convert("RGB")
    else:
        # Asegurar estética manga de alto contraste
        gray = ImageOps.grayscale(img)
        hi = ImageEnhance.Contrast(gray).enhance(1.55)
        img = ImageOps.colorize(hi, black="#060609", white="#f8f8f5").convert("RGB")

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    cx, cy = w // 2, h // 2
    rng = random.Random(w * h + 77)

    # Speedlines cinéticas radiales desde los bordes hacia el centro
    num_lines = 95
    max_r = math.hypot(cx, cy) * 1.15
    for i in range(num_lines):
        angle = (2.0 * math.pi * i / num_lines) + rng.uniform(-0.015, 0.015)
        inner_r = max_r * rng.uniform(0.46, 0.72)
        x1 = cx + math.cos(angle) * inner_r
        y1 = cy + math.sin(angle) * inner_r
        x2 = cx + math.cos(angle) * max_r
        y2 = cy + math.sin(angle) * max_r
        width_px = rng.choice([2, 3, 4, 5])
        # Trazo negro con borde blanco sutil para visibilidad sobre fondos oscuros o claros
        draw.line([(x1, y1), (x2, y2)], fill=(255, 255, 255, 115), width=width_px + 2)
        draw.line([(x1, y1), (x2, y2)], fill=(8, 8, 12, 220), width=width_px)

    # Marco de viñeta manga doble
    draw.rectangle([8, 8, w - 9, h - 9], outline=(255, 255, 255, 235), width=5)
    draw.rectangle([14, 14, w - 15, h - 15], outline=(0, 0, 0, 255), width=4)

    combined = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    return combined


def _render_local_seinen_ink_from_frame(
    source_frame_path: Path, output_panel_path: Path
) -> tuple[Path, str]:
    """
    Motor de entintado Seinen 100% local sobre el frame real del video.
    Garantiza que siempre se entregue un panel estilo manga del frame exacto incluso sin internet ni WebUI.
    """
    base = Image.open(source_frame_path).convert("RGB").resize((720, 1280), Image.Resampling.LANCZOS)
    sharp = base.filter(ImageFilter.UnsharpMask(radius=2.5, percent=220, threshold=3))
    panel = _apply_manga_finishing_overlay(sharp, force_bw_ink=True)
    panel.save(output_panel_path, "JPEG", quality=94)
    return output_panel_path, "Seinen Ink Frame Engine (Local)"


def generate_manga_panel(
    source_frame_path: Path,
    manga_prompt: str,
    output_panel_path: Path,
    seed: int = -1,
) -> tuple[Path, str]:
    """
    Paso 3 del Pipeline: Genera el arte estilo Manga/Seinen/Baki para un timestamp clave.
    Orden de prioridad en modo 'auto':
      1. Automatic1111 / SD WebUI Forge local en GPU (RTX 3050) con img2img sobre el frame extraído + LoRA
      2. ComfyUI local en GPU (RTX 3050) con img2img sobre el frame extraído
      3. Gemini Multimodal Image Edit sobre el frame real del timestamp
      4. Flux Schnell 9:16 (Pollinations API)
      5. Seinen Ink Frame Engine (Entintado manga de alto contraste + speedlines sobre el frame real)
    """
    output_panel_path.parent.mkdir(parents=True, exist_ok=True)
    full_prompt = f"{manga_prompt}, {SEINEN_STYLE_SUFFIX}"

    provider = ART_PROVIDER

    if provider in ("auto", "a1111"):
        ok, engine = _try_a1111_img2img(source_frame_path, full_prompt, output_panel_path, seed)
        if ok:
            return output_panel_path, engine

    if provider in ("auto", "comfyui"):
        ok, engine = _try_comfyui_local(source_frame_path, full_prompt, output_panel_path, seed)
        if ok:
            return output_panel_path, engine

    if provider in ("auto", "gemini"):
        ok, engine = _try_gemini_frame_to_manga(source_frame_path, full_prompt, output_panel_path)
        if ok:
            return output_panel_path, engine

    if provider in ("auto", "flux", "flux_pollinations"):
        ok, engine = _try_flux_schnell_api(full_prompt, output_panel_path, seed)
        if ok:
            return output_panel_path, engine

    return _render_local_seinen_ink_from_frame(source_frame_path, output_panel_path)
