---
name: video-recreator
description: Descarga videos de YouTube, Instagram, Facebook o TikTok, los analiza con Gemini para proponer un nuevo guion y prompt visual, solicita autorización del usuario en Telegram y genera el nuevo video con IA.
---

# Video Recreator Skill (OpenClaw / Hermes / Antigravity)

Usa este skill cuando el usuario envíe un enlace de **YouTube, Instagram, Facebook o TikTok** y quiera recrear el video con IA bajo su autorización.

## Flujo obligatorio con autorización humana (Human-in-the-Loop)

1. **Descargar y Analizar (NO generar aún)**:
   Ejecuta el comando `analyze` pasando la URL y el estilo deseado por el usuario:
   ```bash
   python cli.py analyze --url "<URL>" --style "<ESTILO_DEL_USUARIO>"
   ```
2. **Presentar propuesta y pedir autorización en Telegram**:
   Muestra al usuario el JSON devuelto:
   - `original_analysis` (análisis del video original)
   - `new_title` (nuevo título)
   - `voiceover_script` (guion de voz en off propuesto)
   - `visual_prompt` (prompt para el generador de video IA)
   - `social_caption` (copy y hashtags)
   **DETENTE AQUÍ** y pregunta al usuario si aprueba la propuesta o si desea modificar el guion o el prompt.

3. **Generar el video (SOLO tras la aprobación explícita del usuario)**:
   Cuando el usuario responda que aprueba, ejecuta:
   ```bash
   python cli.py generate --job-id "<JOB_ID>"
   ```
   (Si el usuario pidió cambios al guion o prompt, pásalos en `--script "..."` y `--prompt "..."`).
4. **Entregar el archivo final**:
   Envía el archivo MP4 (`final_video_path`) al chat de Telegram junto con `social_caption` para que el usuario lo publique manualmente en sus redes.
