import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from config import TELEGRAM_BOT_TOKEN, ALLOWED_TELEGRAM_USER_ID, DEFAULT_CONTENT_STYLE
from downloader import DownloadedVideo, download_video
from analyzer import (
    VideoProposal,
    analyze_and_create_proposal,
    refine_proposal,
    regenerate_storyboard_panel,
)
from generator import generate_final_video

URL_REGEX = re.compile(r"(https?://[^\s]+)")


@dataclass
class JobSession:
    video: DownloadedVideo
    proposal: VideoProposal
    awaiting_edit: bool = False


JOBS: Dict[str, JobSession] = {}
ACTIVE_JOB_BY_CHAT: Dict[int, str] = {}
CUSTOM_STYLE_BY_CHAT: Dict[int, str] = {}


def is_authorized(update: Update) -> bool:
    if not ALLOWED_TELEGRAM_USER_ID:
        return True
    user = update.effective_user
    return bool(user and str(user.id) == str(ALLOWED_TELEGRAM_USER_ID).strip())


def build_approval_keyboard(job_id: str, num_panels: int = 3) -> InlineKeyboardMarkup:
    regen_row = [
        InlineKeyboardButton(
            f"🔄 Regen #{i}", callback_data=f"regen:{job_id}:{i}"
        )
        for i in range(1, min(num_panels, 4) + 1)
    ]
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Aprobar y Ensamblar Video (9:16)",
                    callback_data=f"approve:{job_id}",
                )
            ],
            regen_row,
            [
                InlineKeyboardButton(
                    "🔄 Regenerar Todas", callback_data=f"regen:{job_id}:0"
                ),
                InlineKeyboardButton(
                    "✏️ Editar Prompts/Tiempos", callback_data=f"edit:{job_id}"
                ),
            ],
            [
                InlineKeyboardButton("❌ Cancelar", callback_data=f"cancel:{job_id}"),
            ],
        ]
    )


async def send_storyboard_album(chat_message, proposal: VideoProposal) -> None:
    """Envía a Telegram las imágenes generadas del storyboard Manga/Baki con sus timestamps."""
    media_group = []
    open_files = []
    try:
        for m in proposal.impact_moments or []:
            img_path = Path(m.get("storyboard_image_path", ""))
            if img_path.exists():
                f = open(img_path, "rb")
                open_files.append(f)
                idx = m.get("index", 1)
                ts = m.get("timestamp_label", "00:00")
                title = m.get("moment_title", "Impacto")
                action = m.get("subjects_action", "")
                cap = f"⚡ Panel #{idx} [{ts}] — {title}\n{action}"[:1024]
                media_group.append(InputMediaPhoto(media=f, caption=cap))

        if len(media_group) == 1:
            open_files[0].seek(0)
            await chat_message.reply_photo(
                photo=open_files[0],
                caption=media_group[0].caption,
            )
        elif len(media_group) > 1:
            await chat_message.reply_media_group(media=media_group)
    finally:
        for f in open_files:
            try:
                f.close()
            except Exception:
                pass


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("⛔ No estás autorizado para usar este bot.")
        return

    chat_id = update.effective_chat.id
    current_style = CUSTOM_STYLE_BY_CHAT.get(chat_id, DEFAULT_CONTENT_STYLE)
    await update.message.reply_text(
        "🔥 <b>Agente Aura / Manga Impact Edit (Seinen / Baki + Phonk)</b>\n\n"
        "Envíame un link de <b>YouTube, Instagram, Facebook o TikTok</b>.\n\n"
        "<b>Pipeline Automatizado:</b>\n"
        "1️⃣ <b>Ingesta:</b> Descargo el video base.\n"
        "2️⃣ <b>Análisis Multimodal:</b> Extraigo timestamps de clímax, identifico sujetos/roles y creo prompts estilo Manga/Seinen.\n"
        "3️⃣ <b>Generación de Arte:</b> Genero los paneles Manga/Baki de cada timestamp.\n"
        "4️⃣ <b>Autorización:</b> Te envío los storyboards + timestamps con botones para <b>Aprobar</b> o <b>Regenerar imagen</b>.\n"
        "5️⃣ <b>Ensamblado FFmpeg:</b> Corto en los timestamps, inserto las imágenes con <b>Zoom/Shake</b>, monto audio <b>Phonk + SFX de golpe</b> y te entrego el <code>.mp4</code> en 9:16.\n\n"
        f"🎨 <b>Estilo actual:</b> <i>{current_style}</i>\n"
        "Cambia el estilo con: <code>/estilo tu estilo manga o temática</code>",
        parse_mode=ParseMode.HTML,
    )


async def style_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    new_style = " ".join(context.args).strip()
    if not new_style:
        current = CUSTOM_STYLE_BY_CHAT.get(chat_id, DEFAULT_CONTENT_STYLE)
        await update.message.reply_text(
            f"🎨 <b>Estilo actual:</b>\n<i>{current}</i>\n\n"
            "Para cambiarlo usa:\n<code>/estilo Estilo Baki Hanma tinta negra agresiva</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    CUSTOM_STYLE_BY_CHAT[chat_id] = new_style
    await update.message.reply_text(
        f"✅ <b>Nuevo estilo guardado:</b>\n<i>{new_style}</i>",
        parse_mode=ParseMode.HTML,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update) or not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    match = URL_REGEX.search(text)

    if match:
        url = match.group(1)
        extra_instructions = text.replace(url, "").strip()
        if not extra_instructions:
            extra_instructions = CUSTOM_STYLE_BY_CHAT.get(chat_id, DEFAULT_CONTENT_STYLE)

        status_msg = await update.message.reply_text(
            "⬇️ <b>Paso 1/3:</b> Descargando video base...",
            parse_mode=ParseMode.HTML,
        )
        try:
            video = await asyncio.to_thread(download_video, url)
            await status_msg.edit_text(
                f"✅ Video descargado de <b>{video.platform}</b> (<i>{video.title[:55]}</i>).\n"
                "🧠 <b>Paso 2 y 3:</b> Extrayendo timestamps de clímax, identificando sujetos y generando paneles Manga/Baki...",
                parse_mode=ParseMode.HTML,
            )

            proposal = await asyncio.to_thread(
                analyze_and_create_proposal, video, extra_instructions
            )
            JOBS[video.job_id] = JobSession(video=video, proposal=proposal)
            ACTIVE_JOB_BY_CHAT[chat_id] = video.job_id

            await status_msg.delete()
            await send_storyboard_album(update.message, proposal)
            await update.message.reply_text(
                proposal.to_telegram_summary(),
                parse_mode=ParseMode.HTML,
                reply_markup=build_approval_keyboard(
                    video.job_id, len(proposal.impact_moments)
                ),
            )
        except Exception as exc:
            await status_msg.edit_text(f"❌ Error al procesar el video: {exc}")
        return

    # Edición de prompts o timestamps por texto
    active_job_id: Optional[str] = ACTIVE_JOB_BY_CHAT.get(chat_id)
    if active_job_id and active_job_id in JOBS:
        session = JOBS[active_job_id]
        wait_msg = await update.message.reply_text(
            "🎨 Ajustando timestamps/prompts y regenerando storyboards Manga..."
        )
        try:
            updated = await asyncio.to_thread(
                refine_proposal, session.proposal, text, session.video.file_path
            )
            session.proposal = updated
            session.awaiting_edit = False
            await wait_msg.delete()
            await send_storyboard_album(update.message, updated)
            await update.message.reply_text(
                updated.to_telegram_summary(),
                parse_mode=ParseMode.HTML,
                reply_markup=build_approval_keyboard(
                    active_job_id, len(updated.impact_moments)
                ),
            )
        except Exception as exc:
            await wait_msg.edit_text(f"❌ Error al actualizar storyboard: {exc}")
        return

    await update.message.reply_text(
        "Envíame un enlace de YouTube, Instagram, Facebook o TikTok para iniciar el Aura/Manga Edit."
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if ":" not in data:
        return

    parts = data.split(":")
    action = parts[0]
    job_id = parts[1]
    session = JOBS.get(job_id)
    if not session:
        await query.edit_message_text("⚠️ Esta sesión ya expiró. Envía el link nuevamente.")
        return

    chat_id = query.message.chat_id

    if action == "cancel":
        JOBS.pop(job_id, None)
        ACTIVE_JOB_BY_CHAT.pop(chat_id, None)
        await query.edit_message_text("🚫 Propuesta cancelada.")
        return

    if action == "edit":
        session.awaiting_edit = True
        ACTIVE_JOB_BY_CHAT[chat_id] = job_id
        await query.message.reply_text(
            f"✏️ <b>Editar Storyboard (Job <code>{job_id}</code>)</b>\n"
            "Escribe qué cambios quieres hacer en los prompts de imagen, sujetos o en qué segundo cortar:",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "regen":
        panel_num = int(parts[2]) if len(parts) > 2 else 0
        target_label = f"el Panel #{panel_num}" if panel_num > 0 else "todas las imágenes del storyboard"
        wait_msg = await query.message.reply_text(
            f"🔄 Regenerando {target_label} con nueva semilla estilo Manga/Baki..."
        )
        try:
            updated = await asyncio.to_thread(
                regenerate_storyboard_panel,
                session.proposal,
                session.video.file_path,
                panel_num if panel_num > 0 else None,
            )
            session.proposal = updated
            await wait_msg.delete()
            await send_storyboard_album(query.message, updated)
            await query.message.reply_text(
                updated.to_telegram_summary(),
                parse_mode=ParseMode.HTML,
                reply_markup=build_approval_keyboard(
                    job_id, len(updated.impact_moments)
                ),
            )
        except Exception as exc:
            await wait_msg.edit_text(f"❌ Error al regenerar imagen: {exc}")
        return

    if action == "approve":
        await query.edit_message_reply_markup(reply_markup=None)
        progress_msg = await query.message.reply_text(
            "🎬 <b>¡Aprobado! Paso 5/6:</b> Cortando video en timestamps clave, insertando paneles Manga con Zoom/Shake y sincronizando Phonk + SFX de golpe...",
            parse_mode=ParseMode.HTML,
        )
        try:
            final_video_path, engine_used = await generate_final_video(
                session.proposal, session.video.file_path
            )
            await progress_msg.edit_text(
                f"📤 <b>Paso 6/6:</b> Ensamblado completado (<i>{engine_used}</i>). Enviando MP4...",
                parse_mode=ParseMode.HTML,
            )

            caption = (
                f"🔥 <b>{session.proposal.new_title}</b>\n"
                f"🥊 <b>Sujetos:</b> {session.proposal.subjects_and_roles}\n"
                f"🛠 <i>{engine_used}</i>\n\n"
                f"📲 <b>Copy:</b>\n{session.proposal.social_caption}"
            )
            with open(final_video_path, "rb") as vf:
                await query.message.reply_video(
                    video=vf,
                    caption=caption[:1024],
                    parse_mode=ParseMode.HTML,
                )
            await progress_msg.delete()
        except Exception as exc:
            await progress_msg.edit_text(f"❌ Error durante el ensamblado del video: {exc}")


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "Falta TELEGRAM_BOT_TOKEN en el archivo .env. Copia .env.example a .env y configura tus claves."
        )
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("estilo", style_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🔥 Bot de Telegram (Aura / Manga Impact Edit) iniciado. Esperando links...")
    app.run_polling()


if __name__ == "__main__":
    main()
