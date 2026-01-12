import os
import io
import re
import time
import smtplib
import threading
from email.message import EmailMessage

import telebot
from flask import Flask, request

# =========================
# CONFIG (Render env vars)
# =========================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
KINDLE_EMAIL = os.getenv("KINDLE_EMAIL", "")
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

# Sleep after 2h idle (seconds)
IDLE_SLEEP_SECONDS = 2 * 60 * 60

if not BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
if not ALLOWED_USER_ID:
    raise RuntimeError("Missing/invalid ALLOWED_USER_ID")
if not KINDLE_EMAIL:
    raise RuntimeError("Missing KINDLE_EMAIL")
if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
    raise RuntimeError("Missing Gmail credentials")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

# =========================
# State
# =========================
state = {
    "kindle_mode": False,
    "received": 0,
    "sent_ok": 0,
    "sent_fail": 0,
    "errors": [],
    "last_activity": time.time(),
}

lock = threading.Lock()

# =========================
# Helpers
# =========================
def only_owner(message):
    return message.from_user and message.from_user.id == ALLOWED_USER_ID

def touch():
    with lock:
        state["last_activity"] = time.time()

def prettify_title(filename: str) -> str:
    # remove extension
    name = re.sub(r"\.epub$", "", filename, flags=re.IGNORECASE)
    # replace _ and - by space
    name = name.replace("_", " ").replace("-", " ")
    # collapse spaces
    name = re.sub(r"\s+", " ", name).strip()
    return name

def send_email_to_kindle(epub_bytes: bytes, filename: str):
    msg = EmailMessage()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = KINDLE_EMAIL
    msg["Subject"] = "Send to Kindle"
    msg.set_content("Kindlinho 🫶🏻")

    msg.add_attachment(
        epub_bytes,
        maintype="application",
        subtype="epub+zip",
        filename=filename
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)

def deny_if_not_owner(message):
    if not only_owner(message):
        try:
            bot.reply_to(message, "🚫 Este bot é privado.")
        except:
            pass
        return True
    return False

def ensure_kindle_mode(message):
    with lock:
        return state["kindle_mode"]

# =========================
# Idle sleep monitor
# =========================
def idle_monitor():
    while True:
        time.sleep(30)
        with lock:
            if state["kindle_mode"]:
                idle = time.time() - state["last_activity"]
                if idle >= IDLE_SLEEP_SECONDS:
                    # auto stop
                    state["kindle_mode"] = False
                    summary = (
                        f"😴 Sem atividade há 2h.\n"
                        f"Modo Kindle desativado 🫶🏻\n\n"
                        f"📥 Recebidos: <b>{state['received']}</b>\n"
                        f"✅ Enviados com sucesso: <b>{state['sent_ok']}</b>\n"
                        f"❌ Erros: <b>{state['sent_fail']}</b>"
                    )
                    # reset counters after stopping
                    state["received"] = 0
                    state["sent_ok"] = 0
                    state["sent_fail"] = 0
                    state["errors"] = []
                    try:
                        bot.send_message(ALLOWED_USER_ID, summary)
                    except:
                        pass

threading.Thread(target=idle_monitor, daemon=True).start()

# =========================
# Commands
# =========================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    if deny_if_not_owner(message):
        return
    touch()
    bot.reply_to(
        message,
        "Olá Lu 🫶🏻\n"
        "Sou o <b>BOT Kindlinho 🫶🏻</b>.\n"
        "Estou pronto para enviar livros para o teu Kindle 📚\n\n"
        "Quando quiseres começar, usa <b>/kindle</b>."
    )

@bot.message_handler(commands=["kindle"])
def cmd_kindle(message):
    if deny_if_not_owner(message):
        return
    touch()
    with lock:
        state["kindle_mode"] = True
        state["received"] = 0
        state["sent_ok"] = 0
        state["sent_fail"] = 0
        state["errors"] = []
    bot.reply_to(
        message,
        "Modo Kindle ativo ✅\n"
        "Agora envia os teus EPUBs (podes mandar vários)."
    )

@bot.message_handler(commands=["stop"])
def cmd_stop(message):
    if deny_if_not_owner(message):
        return
    touch()

    with lock:
        was_on = state["kindle_mode"]
        state["kindle_mode"] = False

        received = state["received"]
        sent_ok = state["sent_ok"]
        sent_fail = state["sent_fail"]
        errors = list(state["errors"])

        # reset after summary
        state["received"] = 0
        state["sent_ok"] = 0
        state["sent_fail"] = 0
        state["errors"] = []

    if not was_on:
        bot.reply_to(message, "Eu já estava em descanso 🫶🏻")
        return

    txt = (
        "Modo Kindle desativado 🫶🏻\n\n"
        f"📥 Recebidos: <b>{received}</b>\n"
        f"✅ Enviados com sucesso: <b>{sent_ok}</b>\n"
        f"❌ Erros: <b>{sent_fail}</b>\n"
    )
    if errors:
        txt += "\n⚠️ Erros encontrados:\n" + "\n".join(f"• {e}" for e in errors[:10])

    txt += "\n\nAté já 📚✨"

    bot.reply_to(message, txt)

# =========================
# File handler
# =========================
@bot.message_handler(content_types=["document"])
def handle_document(message):
    if deny_if_not_owner(message):
        return

    touch()

    if not ensure_kindle_mode(message):
        bot.reply_to(message, "Antes disso usa <b>/kindle</b> para eu começar a enviar 📚")
        return

    doc = message.document
    filename = doc.file_name or "livro.epub"

    # Accept only EPUB
    if not filename.lower().endswith(".epub"):
        bot.reply_to(message, "Esse ficheiro não é EPUB 😅\nEnvia um <b>.epub</b> e eu trato do resto.")
        return

    with lock:
        state["received"] += 1

    # Download file from Telegram
    try:
        file_info = bot.get_file(doc.file_id)
        file_bytes = bot.download_file(file_info.file_path)
    except Exception as e:
        with lock:
            state["sent_fail"] += 1
            state["errors"].append(f"{filename}: falha a descarregar ({e})")
        bot.reply_to(message, f"❌ Erro ao descarregar: <b>{filename}</b>")
        return

    # Send email
    try:
        send_email_to_kindle(file_bytes, filename)
        with lock:
            state["sent_ok"] += 1

        title = prettify_title(filename)
        bot.reply_to(message, f"✅ Livro <b>{title}</b> foi enviado para o Kindlinho 🫶🏻")

    except Exception as e:
        with lock:
            state["sent_fail"] += 1
            state["errors"].append(f"{filename}: falha ao enviar email ({e})")

        bot.reply_to(message, f"❌ Erro ao enviar para Kindle: <b>{filename}</b>")

# =========================
# Webhook endpoints for Render
# =========================
@app.route("/", methods=["GET"])
def home():
    return "Kindlinho 🫶🏻 online!"

@app.route("/webhook", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.stream.read().decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200

def set_webhook(render_url: str):
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=f"{render_url}/webhook")

if __name__ == "__main__":
    # When running on Render, RENDER_EXTERNAL_URL is usually available
    render_external = os.getenv("RENDER_EXTERNAL_URL", "")
    if render_external:
        set_webhook(render_external)

    # Run flask
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
