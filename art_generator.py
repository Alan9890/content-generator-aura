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


def build_seinen_master_prompt(subject_or_scene_prompt: str) -> str:
    """
    Construye el prompt maestro exacto de Seinen / Baki / Berserk / Murata:
    """
    clean_subj = subject_or_scene_prompt.strip()
    return (
        "Gritty dark seinen manga battle panel, traditional Japanese ink drawing, "
        "rough black ink linework, aggressive brush strokes, heavy cross-hatching, "
        "extreme chiaroscuro, intense shadows. "
        f"Subject: {clean_subj}. "
        "Extreme anatomical definition, bulging veins, intense ferocious battle expression, "
        "glowing malicious white eyes, heavy steam rising from the body, explosive shockwave aura, "
        "dynamic foreshortening, fish-eye perspective. "
        "Style of Kentaro Miura (Berserk), Keisuke Itagaki (Baki the Grappler), and Yusuke Murata. "
        "Screentone dots, speed lines radiating from the fist, monochromatic black and white, "
        "zero color, hand-drawn paper texture, raw sketch aesthetic."
    )


def extract_frame_at_timestamp(
    video_path: Path, timestamp_sec: float, output_frame_path: Path
) -> Path:
    """Extrae el fotograma exacto del video en el timestamp indicado en formato vertical 9:16 (720x1280)."""
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
        cmd[cmd.index("-ss") + 1] = "0.500"
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not output_frame_path.exists():
        Image.new("RGB", (720, 1280), (15, 15, 15)).save(output_frame_path, "JPEG", quality=92)

    return output_frame_path


def _enforce_monochrome_manga_ink(img: Image.Image) -> Image.Image:
    """Garantiza acabado monocromático blanco y negro (zero color) con contraste de tinta sobre papel."""
    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray, cutoff=2)
    hi = ImageEnhance.Contrast(gray).enhance(1.38)
    sharp = hi.filter(ImageFilter.UnsharpMask(radius=1.4, percent=145, threshold=2))
    return ImageOps.colorize(sharp, black="#050507", white="#f6f5f0").convert("RGB")


def _try_a1111_img2img(
    source_frame_path: Path, full_prompt: str, output_panel_path: Path, seed: int
) -> tuple[bool, str]:
    """
    Ejecución local en Automatic1111 / SD WebUI Forge (optimizado para RTX 3050 6GB VRAM: 576x1024).
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
        prompt_with_lora = f"{full_prompt} {MANGA_LORA_TAG}".strip()

        payload = {
            "init_images": [b64_img],
            "prompt": prompt_with_lora,
            "negative_prompt": MANGA_NEGATIVE_PROMPT,
            "denoising_strength": IMG2IMG_DENOISING,
            "steps": 22,
            "cfg_scale": 7.0,
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
            img = (
                Image.open(io.BytesIO(raw_bytes))
                .convert("RGB")
                .resize((720, 1280), Image.Resampling.LANCZOS)
            )
            img = _enforce_monochrome_manga_ink(img)
            img.save(output_panel_path, "JPEG", quality=95)
            return True, "Local GPU (A1111 / Forge SDXL+LoRA)"
    except Exception as exc:
        print(f"[ArtGen] A1111 local error: {exc}", file=sys.stderr)
    return False, ""


def _try_comfyui_local(
    source_frame_path: Path, full_prompt: str, output_panel_path: Path, seed: int
) -> tuple[bool, str]:
    """
    Ejecución local en ComfyUI (http://127.0.0.1:8188).
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
                    "steps": 20,
                    "cfg": 7.0,
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
                        img_meta = node_out["images"][0]
                        view_url = (
                            f"{COMFYUI_URL}/view?filename={urllib.parse.quote(img_meta['filename'])}"
                            f"&subfolder={urllib.parse.quote(img_meta.get('subfolder', ''))}"
                            f"&type={img_meta.get('type', 'output')}"
                        )
                        raw_img = requests.get(view_url, timeout=20).content
                        img = (
                            Image.open(io.BytesIO(raw_img))
                            .convert("RGB")
                            .resize((720, 1280), Image.Resampling.LANCZOS)
                        )
                        img = _enforce_monochrome_manga_ink(img)
                        img.save(output_panel_path, "JPEG", quality=95)
                        return True, f"Local GPU (ComfyUI: {ckpt_name})"
    except Exception as exc:
        print(f"[ArtGen] ComfyUI local error: {exc}", file=sys.stderr)

    return False, ""


def _try_gemini_frame_to_manga(
    source_frame_path: Path, full_prompt: str, output_panel_path: Path
) -> tuple[bool, str]:
    """
    Transforma el fotograma exacto del timestamp en un panel de manga Seinen (Baki/Berserk/Murata)
    replicando la pose exacta con Gemini Multimodal Image Generation.
    """
    if not GEMINI_API_KEY or GEMINI_API_KEY == "tu_gemini_api_key_aqui":
        return False, ""

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        frame_img = Image.open(source_frame_path).convert("RGB")
        instruction = (
            f"{full_prompt}\n\n"
            f"Negative prompt (avoid strictly): {MANGA_NEGATIVE_PROMPT}.\n"
            "Replicate the exact pose and camera angle from the attached reference frame, transforming the subject "
            "into a hyper-muscular, menacing combat monster with glowing white eyes and black combat aura."
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
                            img = _enforce_monochrome_manga_ink(img)
                            img.save(output_panel_path, "JPEG", quality=95)
                            return True, f"Gemini Seinen Manga ({model_name})"
            except Exception:
                continue
    except Exception as exc:
        print(f"[ArtGen] Gemini Image Gen info: {exc}", file=sys.stderr)

    return False, ""


def _try_flux_schnell_api(
    full_prompt: str, output_panel_path: Path, seed: int
) -> tuple[bool, str]:
    """Genera el panel Manga Seinen (Baki/Berserk/Murata) con Flux Schnell en 9:16."""
    try:
        actual_seed = seed if seed >= 0 else random.randint(1, 9999999)
        encoded = urllib.parse.quote(full_prompt)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width=720&height=1280&model=flux&nologo=true&seed={actual_seed}"
        )
        resp = requests.get(url, timeout=40)
        if resp.status_code == 200 and len(resp.content) > 10_000:
            img = (
                Image.open(io.BytesIO(resp.content))
                .convert("RGB")
                .resize((720, 1280), Image.Resampling.LANCZOS)
            )
            img = _enforce_monochrome_manga_ink(img)
            img.save(output_panel_path, "JPEG", quality=95)
            return True, "Flux Schnell (Seinen Manga 9:16)"
    except Exception as exc:
        print(f"[ArtGen] Flux Schnell API info: {exc}", file=sys.stderr)
    return False, ""


def _render_local_seinen_ink_from_frame(
    source_frame_path: Path, output_panel_path: Path
) -> tuple[Path, str]:
    """
    Motor de entintado Seinen de respaldo 100% local sobre el fotograma real del video.
    """
    base = Image.open(source_frame_path).convert("RGB").resize((720, 1280), Image.Resampling.LANCZOS)
    sharp = base.filter(ImageFilter.UnsharpMask(radius=2.5, percent=240, threshold=3))
    gray = ImageOps.grayscale(sharp)
    gray = ImageOps.autocontrast(gray, cutoff=3)
    edges = ImageOps.invert(gray.filter(ImageFilter.FIND_EDGES))
    edges = ImageEnhance.Contrast(edges).enhance(3.4)
    hi_con = ImageEnhance.Contrast(gray).enhance(2.8)
    blended = ImageChops.multiply(hi_con, edges)
    panel = ImageOps.colorize(blended, black="#040406", white="#f5f4ee").convert("RGB")

    # Speedlines radiales de tinta negra únicamente en este fallback offline
    w, h = panel.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    cx, cy = w // 2, h // 2
    rng = random.Random(w * h + 77)
    max_r = math.hypot(cx, cy) * 1.15
    for i in range(90):
        angle = (2.0 * math.pi * i / 90) + rng.uniform(-0.015, 0.015)
        inner_r = max_r * rng.uniform(0.50, 0.76)
        x1 = cx + math.cos(angle) * inner_r
        y1 = cy + math.sin(angle) * inner_r
        x2 = cx + math.cos(angle) * max_r
        y2 = cy + math.sin(angle) * max_r
        draw.line([(x1, y1), (x2, y2)], fill=(8, 8, 12, 215), width=rng.choice([2, 3, 4]))

    final_img = Image.alpha_composite(panel.convert("RGBA"), overlay).convert("RGB")
    final_img.save(output_panel_path, "JPEG", quality=95)
    return output_panel_path, "Seinen Ink Frame Engine (Local)"


def generate_manga_panel(
    source_frame_path: Path,
    manga_prompt: str,
    output_panel_path: Path,
    seed: int = -1,
) -> tuple[Path, str]:
    """
    Paso 3 del Pipeline: Genera el panel Manga Seinen (Berserk / Baki / Murata) usando la estructura exacta.
    """
    output_panel_path.parent.mkdir(parents=True, exist_ok=True)
    full_prompt = build_seinen_master_prompt(manga_prompt)

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
