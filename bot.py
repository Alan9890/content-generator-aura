import asyncio
import re
from dataclasses import dataclass
from typing import Dict, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
from analyzer import VideoProposal, analyze_and_create_proposal, refine_proposal
from generator import generate_final_video

URL_REGEX = re.compile(r"(https?://[^\s]+)")


@dataclass
class JobSession:
    video: DownloadedVideo
    proposal: VideoProposal
    awaiting_edit: bool = False


# Memoria en ejecución por job_id y por chat_id
JOBS: Dict[str, JobSession] = {}
ACTIVE_JOB_BY_CHAT: Dict[int, str] = {}
CUSTOM_STYLE_BY_CHAT: Dict[int, str] = {}


def is_authorized(update: Update) -> bool:
    if not ALLOWED_TELEGRAM_USER_ID:
        return True
    user = update.effective_user
    return bool(user and str(user.id) == str(ALLOWED_TELEGRAM_USER_ID).strip())


def build_approval_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Aprobar y Generar Video", callback_data=f"approve:{job_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "✏️ Editar Guion / Prompt", callback_data=f"edit:{job_id}"
                ),
                InlineKeyboardButton("❌ Cancelar", callback_data=f"cancel:{job_id}"),
            ],
        ]
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("⛔ No estás autorizado para usar este bot.")
        return

    chat_id = update.effective_chat.id
    current_style = CUSTOM_STYLE_BY_CHAT.get(chat_id, DEFAULT_CONTENT_STYLE)
    await update.message.reply_text(
        "🤖 <b>Agente Recreador de Videos IA</b>\n\n"
        "Envíame un link de <b>YouTube, Instagram, Facebook o TikTok</b>.\n"
        "Opcionalmente escribe después del link de qué tema o estilo quieres la nueva versión.\n\n"
        "<b>Flujo de trabajo:</b>\n"
        "1️⃣ Descargo el video original.\n"
        "2️⃣ Lo analizo con IA y creo un <b>nuevo Guion + Prompt de Video IA</b>.\n"
        "3️⃣ Te envío la propuesta aquí para que <b>apruebes o edites</b>.\n"
        "4️⃣ Al aprobar, genero el video nuevo y te lo entrego listo para publicar.\n\n"
        f"🎨 <b>Estilo actual:</b> <i>{current_style}</i>\n"
        "Puedes cambiarlo con: <code>/estilo tu nuevo estilo o nicho</code>",
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
            "Para cambiarlo usa:\n<code>/estilo Videos educativos de IA con tono energético</code>",
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

    # Caso 1: El usuario envió una URL de video
    if match:
        url = match.group(1)
        extra_instructions = text.replace(url, "").strip()
        if not extra_instructions:
            extra_instructions = CUSTOM_STYLE_BY_CHAT.get(chat_id, DEFAULT_CONTENT_STYLE)

        status_msg = await update.message.reply_text(
            "⬇️ <b>Paso 1/2:</b> Descargando video desde el enlace...",
            parse_mode=ParseMode.HTML,
        )
        try:
            video = await asyncio.to_thread(download_video, url)
            await status_msg.edit_text(
                f"✅ Video descargado de <b>{video.platform}</b> (<i>{video.title[:60]}</i>).\n"
                "🧠 <b>Paso 2/2:</b> Analizando video con IA y redactando Guion + Prompt...",
                parse_mode=ParseMode.HTML,
            )

            proposal = await asyncio.to_thread(
                analyze_and_create_proposal, video, extra_instructions
            )
            JOBS[video.job_id] = JobSession(video=video, proposal=proposal)
            ACTIVE_JOB_BY_CHAT[chat_id] = video.job_id

            await status_msg.delete()
            await update.message.reply_text(
                proposal.to_telegram_summary(),
                parse_mode=ParseMode.HTML,
                reply_markup=build_approval_keyboard(video.job_id),
            )
        except Exception as exc:
            await status_msg.edit_text(f"❌ Error al procesar el video: {exc}")
        return

    # Caso 2: El usuario envió correcciones de texto para editar la propuesta activa
    active_job_id: Optional[str] = ACTIVE_JOB_BY_CHAT.get(chat_id)
    if active_job_id and active_job_id in JOBS:
        session = JOBS[active_job_id]
        wait_msg = await update.message.reply_text(
            "✏️ Ajustando el Guion y el Prompt con tus indicaciones..."
        )
        try:
            updated = await asyncio.to_thread(refine_proposal, session.proposal, text)
            session.proposal = updated
            session.awaiting_edit = False
            await wait_msg.delete()
            await update.message.reply_text(
                updated.to_telegram_summary(),
                parse_mode=ParseMode.HTML,
                reply_markup=build_approval_keyboard(active_job_id),
            )
        except Exception as exc:
            await wait_msg.edit_text(f"❌ Error al actualizar propuesta: {exc}")
        return

    await update.message.reply_text(
        "Envíame un enlace de YouTube, Instagram o Facebook para comenzar."
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if ":" not in data:
        return

    action, job_id = data.split(":", 1)
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
            f"✏️ <b>Modo edición activado (Job <code>{job_id}</code>)</b>\n"
            "Escribe qué cambios quieres hacerle al guion, al tono o al prompt visual:",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "approve":
        await query.edit_message_reply_markup(reply_markup=None)
        progress_msg = await query.message.reply_text(
            "🎬 <b>¡Autorizado!</b> Generando voz en off y renderizando el nuevo video con IA...\n"
            "<i>Esto puede tardar entre 30 segundos y 2 minutos.</i>",
            parse_mode=ParseMode.HTML,
        )
        try:
            final_video_path, engine_used = await generate_final_video(
                session.proposal, session.video.file_path
            )
            await progress_msg.edit_text(
                f"📤 Video generado con <b>{engine_used}</b>. Subiendo a Telegram...",
                parse_mode=ParseMode.HTML,
            )

            caption = (
                f"✅ <b>{session.proposal.new_title}</b>\n"
                f"🛠 <i>{engine_used}</i>\n\n"
                f"📋 <b>Copy listo para publicar:</b>\n{session.proposal.social_caption}"
            )
            with open(final_video_path, "rb") as vf:
                await query.message.reply_video(
                    video=vf,
                    caption=caption[:1024],
                    parse_mode=ParseMode.HTML,
                )
            await progress_msg.delete()
        except Exception as exc:
            await progress_msg.edit_text(f"❌ Error durante la generación del video: {exc}")


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

    print("🤖 Agente de Telegram iniciado. Esperando links de videos...")
    app.run_polling()


if __name__ == "__main__":
    main()
