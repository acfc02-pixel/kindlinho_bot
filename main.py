import os
import re
import time
import smtplib
import asyncio
from email.message import EmailMessage
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# ENV VARS (Render)
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
KINDLE_EMAIL = os.getenv("KINDLE_EMAIL", "").strip()
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()

# Bot behaviour
IDLE_SLEEP_SECONDS = 2 * 60 * 60  # 2h
SUPPORTED_EXT = (".epub",)


# =========================
# GLOBAL STATE
# =========================
kindle_mode = False
received = 0
sent_ok = 0
sent_fail = 0
errors = []
last_activity = time.time()


def ensure_env():
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not ALLOWED_USER_ID:
        missing.append("ALLOWED_USER_ID")
    if not KINDLE_EMAIL:
        missing.append("KINDLE_EMAIL")
    if not GMAIL_ADDRESS:
        missing.append("GMAIL_ADDRESS")
    if not GMAIL_APP_PASSWORD:
        missing.append("GMAIL_APP_PASSWORD")
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))


def is_owner(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == ALLOWED_USER_ID)


async def deny_if_not_owner(update: Update) -> bool:
    """Returns True if denied."""
    if is_owner(update):
        return False
    if update.message:
        await update.message.reply_text("🚫 Este bot é privado.")
    return True


def touch():
    global last_activity
    last_activity = time.time()


def prettify_title(filename: str) -> str:
    name = filename
    for ext in SUPPORTED_EXT:
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    name = name.replace("_", " ").replace("-", " ")
    name = re.sub(r"\s+", " ", name).strip()
    return name


def send_email_to_kindle(file_bytes: bytes, filename: str):
    msg = EmailMessage()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = KINDLE_EMAIL
    msg["Subject"] = "Send to Kindle"
    msg.set_content("Enviado pelo BOT Kindlinho 🫶🏻")

    # For EPUB: application/epub+zip
    msg.add_attachment(
        file_bytes,
        maintype="application",
        subtype="epub+zip",
        filename=filename,
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)


async def idle_monitor(app: Application):
    """Auto /stop after 2 hours idle while in kindle mode."""
    global kindle_mode, received, sent_ok, sent_fail, errors
    while True:
        await asyncio.sleep(30)
        if kindle_mode:
            idle = time.time() - last_activity
            if idle >= IDLE_SLEEP_SECONDS:
                # auto stop
                kindle_mode = False

                summary = (
                    "😴 Sem atividade há 2h.\n"
                    "Modo Kindle desativado 🫶🏻\n\n"
                    f"📥 Recebidos: {received}\n"
                    f"✅ Enviados com sucesso: {sent_ok}\n"
                    f"❌ Erros: {sent_fail}"
                )
                if errors:
                    summary += "\n\n⚠️ Erros:\n" + "\n".join(f"• {e}" for e in errors[:10])

                # reset
                received = 0
                sent_ok = 0
                sent_fail = 0
                errors = []

                try:
                    await app.bot.send_message(chat_id=ALLOWED_USER_ID, text=summary)
                except:
                    pass


# =========================
# COMMANDS
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_owner(update):
        return
    touch()
    await update.message.reply_text(
        "Olá Lu 🫶🏻\n"
        "Sou o BOT Kindlinho 🫶🏻.\n\n"
        "Quando quiseres enviar livros para o Kindle, usa /kindle 📚"
    )


async def cmd_kindle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global kindle_mode, received, sent_ok, sent_fail, errors
    if await deny_if_not_owner(update):
        return
    touch()

    kindle_mode = True
    received = 0
    sent_ok = 0
    sent_fail = 0
    errors = []

    await update.message.reply_text(
        "Modo Kindle ativo ✅\n"
        "Agora envia os teus EPUBs (podes mandar vários)."
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global kindle_mode, received, sent_ok, sent_fail, errors
    if await deny_if_not_owner(update):
        return
    touch()

    if not kindle_mode:
        await update.message.reply_text("Eu já estava em descanso 🫶🏻")
        return

    kindle_mode = False

    msg = (
        "Modo Kindle desativado 🫶🏻\n\n"
        f"📥 Recebidos: {received}\n"
        f"✅ Enviados com sucesso: {sent_ok}\n"
        f"❌ Erros: {sent_fail}"
    )
    if errors:
        msg += "\n\n⚠️ Erros:\n" + "\n".join(f"• {e}" for e in errors[:10])

    msg += "\n\nAté já 📚✨"

    # reset counters after summary
    received = 0
    sent_ok = 0
    sent_fail = 0
    errors = []

    await update.message.reply_text(msg)


# =========================
# DOCUMENT HANDLER
# =========================
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global received, sent_ok, sent_fail, errors

    if await deny_if_not_owner(update):
        return
    touch()

    if not kindle_mode:
        await update.message.reply_text("Antes disso usa /kindle para eu começar a enviar 📚")
        return

    doc = update.message.document
    filename = doc.file_name or "livro.epub"

    # Accept only EPUB
    if not filename.lower().endswith(SUPPORTED_EXT):
        await update.message.reply_text("Esse ficheiro não é EPUB 😅\nEnvia um .epub e eu trato do resto.")
        return

    received += 1

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        file_bytes = await tg_file.download_as_bytearray()
    except Exception as e:
        sent_fail += 1
        errors.append(f"{filename}: falha a descarregar ({e})")
        await update.message.reply_text(f"❌ Erro ao descarregar: {filename}")
        return

    try:
        send_email_to_kindle(bytes(file_bytes), filename)
        sent_ok += 1
        title = prettify_title(filename)
        await update.message.reply_text(f"✅ Livro {title} foi enviado para o Kindlinho 🫶🏻")
    except Exception as e:
        sent_fail += 1
        errors.append(f"{filename}: falha ao enviar email ({e})")
        await update.message.reply_text(f"❌ Erro ao enviar para Kindle: {filename}")


# =========================
# 
# =========================
def main():
    ensure_env()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("kindle", cmd_kindle))
    application.add_handler(CommandHandler("stop", cmd_stop))

    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # background idle monitor (sem job queue)
    application.create_task(idle_monitor(application))

    application.run_polling()



if __name__ == "__main__":
    main()
