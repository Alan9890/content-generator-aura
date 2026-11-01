import math
import random
import struct
import wave
from pathlib import Path
from typing import Optional
from config import AUDIO_PHONK_DIR, AUDIO_SFX_DIR


def _write_stereo_wav(path: Path, samples_l: list[float], samples_r: list[float], sample_rate: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for l_val, r_val in zip(samples_l, samples_r):
            clamped_l = max(-1.0, min(1.0, l_val))
            clamped_r = max(-1.0, min(1.0, r_val))
            frames.extend(struct.pack("<hh", int(clamped_l * 32767), int(clamped_r * 32767)))
        wf.writeframes(bytes(frames))


def _generate_baki_impact_hit(path: Path, sr: int = 44100) -> None:
    """Genera un efecto de sonido de impacto pesado estilo Baki/Anime (Sub-bass punch + transient snap + shockwave)."""
    duration = 0.85
    n = int(sr * duration)
    rng = random.Random(42)
    left, right = [], []
    for i in range(n):
        t = i / sr
        # Pitch drop rápido de 190Hz a 38Hz para el golpe de pecho (punch)
        freq = 38.0 + 155.0 * math.exp(-t * 28.0)
        sub = math.sin(2.0 * math.pi * freq * t) * math.exp(-t * 5.2)
        # Armónico agresivo distorsionado
        growl = math.tanh(sub * 3.8) * 0.65
        # Crujido/snap de impacto en los primeros 65ms
        noise_env = math.exp(-t * 38.0)
        snap = (rng.uniform(-1.0, 1.0) * 0.75 + math.sin(2.0 * math.pi * 1150.0 * t) * 0.35) * noise_env
        # Reverberación corta de arena/combate
        reverb_tail = rng.uniform(-1.0, 1.0) * math.exp(-t * 8.5) * 0.18
        val_l = math.tanh((growl + snap + reverb_tail) * 1.45)
        val_r = math.tanh((growl + snap * 0.92 - reverb_tail * 0.8) * 1.45)
        left.append(val_l)
        right.append(val_r)
    _write_stereo_wav(path, left, right, sr)


def _generate_seinen_slash_flash(path: Path, sr: int = 44100) -> None:
    """Genera un efecto de destello/corte de alta tensión + golpe seco."""
    duration = 0.75
    n = int(sr * duration)
    rng = random.Random(99)
    left, right = [], []
    for i in range(n):
        t = i / sr
        # Barrido metálico descendente (flash / speedline)
        sweep_freq = 2400.0 * math.exp(-t * 14.0) + 90.0
        metallic = math.sin(2.0 * math.pi * sweep_freq * t) * math.exp(-t * 10.0) * 0.55
        # Sub-golpe profundo
        sub_freq = 45.0 + 110.0 * math.exp(-t * 24.0)
        sub = math.sin(2.0 * math.pi * sub_freq * t) * math.exp(-t * 6.0) * 0.85
        burst = rng.uniform(-1.0, 1.0) * math.exp(-t * 26.0) * 0.55
        left.append(math.tanh((sub + metallic + burst) * 1.4))
        right.append(math.tanh((sub + metallic * 0.85 - burst * 0.7) * 1.4))
    _write_stereo_wav(path, left, right, sr)


def _generate_drift_phonk_loop(path: Path, sr: int = 44100) -> None:
    """
    Genera una base rítmica Drift Phonk oscura (135 BPM, 8 compases) con 808 distorsionado,
    Phonk Cowbell melódico y hi-hats para que el sistema funcione out-of-the-box.
    El usuario puede añadir cualquier archivo .mp3/.wav en assets/audio/phonk/ para usar su música.
    """
    bpm = 135.0
    step_sec = (60.0 / bpm) / 4.0  # 1/16 de nota
    total_steps = 64  # ~7.1 segundos loopables
    total_samples = int(total_steps * step_sec * sr)
    rng = random.Random(1337)

    # Melodía oscura en Frigio dominante (frecuencias de cowbell en Hz)
    cowbell_pattern = [
        523.25, 0, 554.37, 523.25, 0, 659.25, 554.37, 523.25,
        493.88, 0, 523.25, 0, 440.00, 0, 493.88, 523.25,
    ] * 4
    kick_steps = {0, 3, 6, 8, 11, 14}
    snare_steps = {4, 12}

    left, right = [], []
    for i in range(total_samples):
        t = i / sr
        step_idx = min(total_steps - 1, int(t / step_sec))
        local_t = t - (step_idx * step_sec)
        step_in_bar = step_idx % 16

        # 1. 808 Sub Bass + Kick distorsionado
        bass = 0.0
        if step_in_bar in kick_steps:
            k_freq = 55.0 + 95.0 * math.exp(-local_t * 32.0)
            bass = math.tanh(math.sin(2.0 * math.pi * k_freq * local_t) * 2.6) * math.exp(-local_t * 5.5) * 0.75

        # 2. Phonk 808 Cowbell (dos ondas cuadradas/saturadas en proporción 1 : 1.504)
        cb = 0.0
        cb_freq = cowbell_pattern[step_idx]
        if cb_freq > 0:
            osc1 = math.sin(2.0 * math.pi * cb_freq * local_t)
            osc2 = math.sin(2.0 * math.pi * (cb_freq * 1.504) * local_t)
            cb_raw = math.tanh((osc1 + osc2) * 2.2)
            cb = cb_raw * math.exp(-local_t * 14.0) * 0.42

        # 3. Snare / Clap en pasos 4 y 12
        snare = 0.0
        if step_in_bar in snare_steps:
            snare = (rng.uniform(-1.0, 1.0) * 0.65 + math.sin(2.0 * math.pi * 185.0 * local_t) * 0.35) * math.exp(-local_t * 22.0) * 0.55

        # 4. Hi-hat en cada octavo
        hat = 0.0
        if step_idx % 2 == 0:
            hat = rng.uniform(-1.0, 1.0) * math.exp(-local_t * 48.0) * 0.16

        mix_l = math.tanh((bass + cb * 1.05 + snare + hat) * 1.15)
        mix_r = math.tanh((bass + cb * 0.90 + snare - hat * 0.8) * 1.15)
        left.append(mix_l)
        right.append(mix_r)

    _write_stereo_wav(path, left, right, sr)


def ensure_default_audio_assets() -> None:
    """Garantiza que existan SFX de impacto y pista Phonk base en assets/audio/."""
    AUDIO_SFX_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_PHONK_DIR.mkdir(parents=True, exist_ok=True)

    hit_file = AUDIO_SFX_DIR / "baki_impact_hit.wav"
    slash_file = AUDIO_SFX_DIR / "seinen_slash_flash.wav"
    phonk_file = AUDIO_PHONK_DIR / "drift_aura_phonk_beat.wav"

    if not hit_file.exists():
        _generate_baki_impact_hit(hit_file)
    if not slash_file.exists():
        _generate_seinen_slash_flash(slash_file)
    if not phonk_file.exists():
        _generate_drift_phonk_loop(phonk_file)


def get_sfx_files() -> list[Path]:
    ensure_default_audio_assets()
    files = sorted(
        [
            p
            for p in AUDIO_SFX_DIR.iterdir()
            if p.suffix.lower() in (".mp3", ".wav", ".ogg", ".m4a")
        ]
    )
    return files


def get_phonk_track(preferred_name: str = "") -> Optional[Path]:
    ensure_default_audio_assets()
    tracks = sorted(
        [
            p
            for p in AUDIO_PHONK_DIR.iterdir()
            if p.suffix.lower() in (".mp3", ".wav", ".ogg", ".m4a")
        ]
    )
    if not tracks:
        return None
    if preferred_name:
        for t in tracks:
            if preferred_name.lower() in t.name.lower():
                return t
    # Priorizar archivos .mp3 personalizados que el usuario haya colocado en assets/audio/phonk/
    user_mp3s = [t for t in tracks if t.suffix.lower() == ".mp3"]
    if user_mp3s:
        return user_mp3s[0]
    return tracks[0]
