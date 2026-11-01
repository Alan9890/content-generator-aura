# 🤖 Agente Recreador de Videos con IA (Telegram + Gemini/Veo + OpenClaw/Hermes)

Este agente recibe un link de **Facebook, Instagram, YouTube o TikTok** por Telegram, descarga el video, lo analiza multimodalmente con IA, diseña un **nuevo Guion + Prompt de Video IA**, te lo envía a Telegram para tu **autorización o edición**, y al aprobarlo genera el nuevo video `.mp4` listo para que lo publiques.

## 🚀 Dos formas de usarlo

### Opción A: Bot de Telegram Autónomo (Recomendado, con botones interactivos)
1. Instala las dependencias:
   ```powershell
   cd video_recreator_agent
   pip install -r requirements.txt
   ```
2. Copia `.env.example` a `.env` y coloca tu `TELEGRAM_BOT_TOKEN` (de `@BotFather`) y tu `GEMINI_API_KEY` (de Google AI Studio):
   ```powershell
   copy .env.example .env
   ```
3. Inicia el bot:
   ```powershell
   python bot.py
   ```
4. En Telegram:
   - Envía `/estilo <tu nicho o tipo de contenido>` para fijar tu estilo base.
   - Envía cualquier link de **Facebook, Instagram o YouTube** (ej. `https://www.instagram.com/reel/...`).
   - Recibirás el **Análisis + Guion + Prompt Visual** con los botones:
     - `✅ Aprobar y Generar Video`
     - `✏️ Editar Guion / Prompt`
     - `❌ Cancelar`

### Opción B: Como Skill de OpenClaw o Hermes Agent
Si prefieres correrlo dentro de **OpenClaw** o **Hermes Agent**, copia o enlaza esta carpeta dentro de tu directorio de skills (`~/.openclaw/skills/video-recreator` o `~/.hermes/skills/video-recreator`). El archivo `SKILL.md` y el script `cli.py` permiten que OpenClaw/Hermes ejecute el paso 1 (`analyze`), te pida confirmación en tu chat de Telegram y luego ejecute el paso 2 (`generate`).
