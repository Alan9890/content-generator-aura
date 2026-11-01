---
name: video-recreator-aura
description: Pipeline de 6 pasos para crear edits verticales 9:16 estilo Manga/Seinen/Baki con cortes en timestamps clave, paneles de arte IA con zoom/shake y montaje de Phonk + SFX con autorización en Telegram.
---

# Aura / Manga Impact Video Editor Skill

## Flujo de 6 Pasos con Autorización Humana (Human-in-the-Loop)

1. **Pasos 1, 2 y 3 — Ingesta, Análisis Multimodal y Generación de Storyboards**:
   ```bash
   python cli.py analyze --url "<URL>" --style "<ESTILO_MANGA_OPCIONAL>"
   ```
   Esto descarga el video, identifica sujetos/roles (`subjects_and_roles`), extrae los timestamps de clímax (`impact_moments`) y genera las imágenes estilo Manga/Baki en `workspace/storyboards/`.

2. **Paso 4 — Presentar Storyboards y pedir autorización**:
   Muestra al usuario las imágenes generadas, los timestamps y los sujetos detectados.
   - Si el usuario pide **regenerar una imagen** (ej. el panel 1 o todas):
     ```bash
     python cli.py regenerate --job-id "<JOB_ID>" --panel 1
     ```
   - Si pide cambios de estilo o tiempos:
     ```bash
     python cli.py refine --job-id "<JOB_ID>" --feedback "<CAMBIOS>"
     ```

3. **Pasos 5 y 6 — Ensamblado Automático FFmpeg y Entrega**:
   Al aprobar el usuario, ejecuta:
   ```bash
   python cli.py generate --job-id "<JOB_ID>"
   ```
   Esto corta el video en los timestamps clave, inserta los paneles Manga con efecto Flash + Zoom Punch + Camera Shake, sincroniza el audio Phonk + SFX de golpe y exporta el `.mp4` final en 9:16.
