# 🔥 Content Generator Aura — Agente de Edición Manga / Seinen Impact (Telegram + GPU Local + Gemini + FFmpeg)

Pipeline automatizado con autorización *Human-in-the-Loop* en Telegram y Estudio Web para transformar videos de **YouTube, Instagram, Facebook o TikTok** en edits verticales (9:16) de alto impacto estilo **Manga Seinen / Baki Hanma / Berserk** con **Zoom Punch, Camera Shake, Phonk y SFX de golpe sincronizados**.

```text
[Link de entrada] 
       │
       ▼
1. Ingesta  ──► Video Base (yt-dlp)
       │
       ▼
2. Análisis Multimodal (Gemini Vision)
   ├── Extrae timestamps exactos de clímax / tensión
   ├── Identifica sujetos y roles (ej. cangrejo vs figura)
   └── Genera prompts de imagen estilo manga/seinen
       │
       ▼
3. Generación de Arte (GPU Local RTX 3050: A1111/Forge / ComfyUI + LoRA Manga/Baki + Fallbacks)
       │
       ▼
4. Interacción Telegram Bot + UI Web
   ├── Envía storyboards / imágenes generadas + timestamps
   └── Botones inline: [✅ Aprobar] / [🔄 Regenerar imagen] / [❌ Cancelar]
       │
  (Al aprobar)
       ▼
5. Ensamblado Automático (FFmpeg + Pillow)
   ├── Cortes en el video original en timestamps clave
   ├── Inserción de imágenes con Flash + Zoom Punch + Camera Shake (efecto impacto)
   ├── Montaje de audio Phonk / SFX de golpe sincronizados al milisegundo
   └── Exportación final en vertical (9:16)
       │
       ▼
6. Entrega (Envío automático del archivo .mp4 resultante a Telegram y UI)
```

---

## 🚀 Cómo iniciarlo

### Opción 1: Estudio de Escritorio + Bot de Telegram Sincronizado
Ejecuta `start_ui.bat` o directamente:
```powershell
node server_light.js
```
Se abrirá la interfaz en `http://127.0.0.1:8765` y el bot de Telegram quedará escuchando enlaces en segundo plano.

### Opción 2: Bot de Telegram Autónomo en Python
```powershell
pip install -r requirements.txt
python bot.py
```

---

## 🎨 Conexión con tu GPU Local (NVIDIA RTX 3050 6GB VRAM)

El módulo `art_generator.py` detecta automáticamente tu servidor local en modo `ART_PROVIDER=auto`:
1. **Automatic1111 / SD WebUI Forge (`http://127.0.0.1:7860`)**: Ejecútalo con la bandera `--api` (y `--medvram` para 6GB VRAM). Toma el frame real extraído del timestamp (`img2img` a `576x1024`) y aplica tu LoRA configurado en `MANGA_LORA_TAG`.
2. **ComfyUI (`http://127.0.0.1:8188`)**: Conecta directo a la API local de ComfyUI subiendo el frame del timestamp.
3. **Fallback Automático**: Si ComfyUI/A1111 están cerrados, utiliza Gemini Multimodal Image Edit / Flux Schnell / Seinen Ink Engine sobre el frame real para que el flujo nunca se detenga.

## 🔊 Música Phonk y SFX (`assets/audio/`)
- Al iniciar, `audio_assets.py` genera automáticamente en `assets/audio/sfx/` efectos de impacto (`baki_impact_hit.wav`, `seinen_slash_flash.wav`) y en `assets/audio/phonk/` un loop Drift Phonk (`drift_aura_phonk_beat.wav`).
- Puedes colocar tus propios archivos `.mp3` o `.wav` dentro de `assets/audio/phonk/` y `assets/audio/sfx/` y el ensamblador los usará automáticamente.
