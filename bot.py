"""
Bot de Telegram: "Ve anuncios y gana"
--------------------------------------
Sistema de recompensas transparente:
- El usuario ve un anuncio (o completa una tarea) y gana puntos.
- Puede invitar amigos y ganar puntos extra por cada referido.
- Puede consultar su saldo y pedir un retiro cuando llegue al mínimo.

Antes de lanzarlo en producción:
1. Regístrate en una red de anuncios como Adsgram (https://adsgram.ai) o
   activa la monetización nativa de Telegram para tu bot.
2. Reemplaza la función `mostrar_anuncio()` con la integración real de tu
   red de anuncios (normalmente te dan un enlace o un "Web App" de Telegram
   para insertar).
3. Ajusta los valores de PUNTOS_POR_ANUNCIO, PUNTOS_POR_REFERIDO y
   MINIMO_RETIRO según lo que te paguen los anunciantes.
"""

import os
import logging
import sqlite3
import time
import threading
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ============== CONFIGURACIÓN ==============
# En Railway, configura estas variables en Settings -> Variables:
#   BOT_TOKEN = tu token de BotFather
# El puerto lo asigna Railway automáticamente en la variable PORT.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PON_AQUI_TU_TOKEN_DE_BOTFATHER")

PUNTOS_POR_ANUNCIO = 10          # puntos que gana el usuario por cada anuncio visto
PUNTOS_POR_REFERIDO = 50         # puntos de bono cuando invita a un amigo
MINIMO_RETIRO = 1000             # puntos mínimos para poder retirar
SEGUNDOS_ENTRE_ANUNCIOS = 30     # anti-abuso: tiempo mínimo entre anuncios vistos
REWARD_SERVER_PORT = int(os.environ.get("PORT", 8080))  # Railway asigna el puerto en PORT

DB_PATH = "bot_data.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ============== BASE DE DATOS ==============
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            puntos INTEGER DEFAULT 0,
            referido_por INTEGER,
            total_referidos INTEGER DEFAULT 0,
            ultimo_anuncio REAL DEFAULT 0,
            creado REAL
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS retiros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            puntos INTEGER,
            estado TEXT DEFAULT 'pendiente',
            fecha REAL
        )
        """
    )
    conn.commit()
    conn.close()


def obtener_usuario(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM usuarios WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row


def crear_usuario(user_id: int, username: str, referido_por: int = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO usuarios (user_id, username, referido_por, creado) VALUES (?, ?, ?, ?)",
        (user_id, username, referido_por, time.time()),
    )
    conn.commit()
    conn.close()


def sumar_puntos(user_id: int, puntos: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE usuarios SET puntos = puntos + ? WHERE user_id = ?", (puntos, user_id))
    conn.commit()
    conn.close()


def actualizar_ultimo_anuncio(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE usuarios SET ultimo_anuncio = ? WHERE user_id = ?", (time.time(), user_id))
    conn.commit()
    conn.close()


def incrementar_referidos(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE usuarios SET total_referidos = total_referidos + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def crear_solicitud_retiro(user_id: int, username: str, puntos: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO retiros (user_id, username, puntos, fecha) VALUES (?, ?, ?, ?)",
        (user_id, username, puntos, time.time()),
    )
    c.execute("UPDATE usuarios SET puntos = puntos - ? WHERE user_id = ?", (puntos, user_id))
    conn.commit()
    conn.close()


# ============== COMANDOS ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existente = obtener_usuario(user.id)

    referido_por = None
    if not existente and context.args:
        try:
            posible_referidor = int(context.args[0])
            if posible_referidor != user.id and obtener_usuario(posible_referidor):
                referido_por = posible_referidor
        except (ValueError, IndexError):
            pass

    if not existente:
        crear_usuario(user.id, user.username or user.first_name, referido_por)
        if referido_por:
            sumar_puntos(referido_por, PUNTOS_POR_REFERIDO)
            incrementar_referidos(referido_por)
            try:
                await context.bot.send_message(
                    referido_por,
                    f"🎉 ¡Alguien se unió con tu enlace! Ganaste {PUNTOS_POR_REFERIDO} puntos.",
                )
            except Exception:
                pass

    texto = (
        f"👋 ¡Hola {user.first_name}!\n\n"
        "Bienvenido al bot de recompensas. Aquí puedes:\n"
        f"• Ver anuncios y ganar {PUNTOS_POR_ANUNCIO} puntos cada vez\n"
        f"• Invitar amigos y ganar {PUNTOS_POR_REFERIDO} puntos por cada uno\n"
        f"• Retirar tus puntos al llegar a {MINIMO_RETIRO}\n\n"
        "Usa los botones de abajo para empezar 👇"
    )
    teclado = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📺 Ver anuncio y ganar", callback_data="ver_anuncio")],
            [InlineKeyboardButton("💰 Mi saldo", callback_data="saldo")],
            [InlineKeyboardButton("👥 Invitar amigos", callback_data="invitar")],
            [InlineKeyboardButton("🏧 Retirar", callback_data="retirar")],
        ]
    )
    await update.message.reply_text(texto, reply_markup=teclado)


async def mostrar_anuncio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    AQUÍ VA TU INTEGRACIÓN REAL DE ANUNCIOS.
    Por ahora simula la visualización de un anuncio con un botón de confirmación.
    Reemplaza esto con el link/Web App que te dé tu red de anuncios (ej. Adsgram).
    """
    query = update.callback_query
    user_id = query.from_user.id
    fila = obtener_usuario(user_id)

    if not fila:
        crear_usuario(user_id, query.from_user.username or query.from_user.first_name)
        fila = obtener_usuario(user_id)

    ultimo_anuncio = fila[5]
    ahora = time.time()
    if ahora - ultimo_anuncio < SEGUNDOS_ENTRE_ANUNCIOS:
        espera = int(SEGUNDOS_ENTRE_ANUNCIOS - (ahora - ultimo_anuncio))
        await query.answer(f"⏳ Espera {espera}s antes de ver otro anuncio.", show_alert=True)
        return

    # --- Placeholder: aquí normalmente insertarías el link/widget del anunciante ---
    teclado = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Ya vi el anuncio", callback_data="confirmar_anuncio")]]
    )
    await query.answer()
    await query.message.reply_text(
        "📺 [Aquí se mostraría el anuncio real de tu red publicitaria]\n\n"
        "Cuando termines de verlo, confirma abajo:",
        reply_markup=teclado,
    )


async def confirmar_anuncio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    fila = obtener_usuario(user_id)
    ahora = time.time()

    sumar_puntos(user_id, PUNTOS_POR_ANUNCIO)
    actualizar_ultimo_anuncio(user_id)
    await query.answer()
    await query.edit_message_text(f"✅ ¡Ganaste {PUNTOS_POR_ANUNCIO} puntos!")


async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    fila = obtener_usuario(user_id)
    puntos = fila[2] if fila else 0
    referidos = fila[4] if fila else 0
    await query.answer()
    await query.message.reply_text(
        f"💰 Tu saldo actual: {puntos} puntos\n"
        f"👥 Amigos invitados: {referidos}\n"
        f"🏧 Mínimo para retirar: {MINIMO_RETIRO} puntos"
    )


async def invitar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    bot_username = (await context.bot.get_me()).username
    enlace = f"https://t.me/{bot_username}?start={user_id}"
    await query.answer()
    await query.message.reply_text(
        f"👥 Comparte tu enlace de invitación:\n{enlace}\n\n"
        f"Ganas {PUNTOS_POR_REFERIDO} puntos por cada amigo que se una."
    )


async def retirar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    fila = obtener_usuario(user_id)
    puntos = fila[2] if fila else 0

    await query.answer()
    if puntos < MINIMO_RETIRO:
        await query.message.reply_text(
            f"❌ Necesitas al menos {MINIMO_RETIRO} puntos para retirar.\n"
            f"Tu saldo actual: {puntos} puntos."
        )
        return

    crear_solicitud_retiro(user_id, username, puntos)
    await query.message.reply_text(
        f"✅ Solicitud de retiro creada por {puntos} puntos.\n"
        "Un administrador la procesará pronto."
    )
    # Opcional: notifica a un chat de administración
    # await context.bot.send_message(ADMIN_CHAT_ID, f"Nueva solicitud de retiro: {username} - {puntos} pts")


async def manejar_botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    accion = query.data

    if accion == "ver_anuncio":
        await mostrar_anuncio(update, context)
    elif accion == "confirmar_anuncio":
        await confirmar_anuncio(update, context)
    elif accion == "saldo":
        await saldo(update, context)
    elif accion == "invitar":
        await invitar(update, context)
    elif accion == "retirar":
        await retirar(update, context)


# ============== SERVIDOR WEBHOOK PARA ADSGRAM ==============
# Adsgram llamará a esta URL cuando el usuario termine de ver el anuncio:
#   https://tu-dominio.com/reward?userid=[userId]
# donde [userId] es reemplazado automáticamente por Adsgram con el ID
# real de Telegram del usuario.

flask_app = Flask(__name__)
telegram_app = None  # se asigna en main() para poder enviar mensajes desde Flask


@flask_app.route("/reward", methods=["GET"])
def recibir_recompensa():
    user_id = request.args.get("userid")

    if not user_id:
        return {"status": "error", "message": "Falta el parámetro userid"}, 400

    try:
        user_id = int(user_id)
    except ValueError:
        return {"status": "error", "message": "userid inválido"}, 400

    fila = obtener_usuario(user_id)
    if not fila:
        crear_usuario(user_id, "desconocido")

    sumar_puntos(user_id, PUNTOS_POR_ANUNCIO)
    actualizar_ultimo_anuncio(user_id)
    logger.info(f"Recompensa acreditada: usuario {user_id} +{PUNTOS_POR_ANUNCIO} puntos")

    # Notifica al usuario dentro de Telegram (opcional pero recomendado)
    if telegram_app:
        try:
            import asyncio
            asyncio.run_coroutine_threadsafe(
                telegram_app.bot.send_message(
                    user_id, f"✅ ¡Ganaste {PUNTOS_POR_ANUNCIO} puntos por ver el anuncio!"
                ),
                telegram_app.loop if hasattr(telegram_app, "loop") else asyncio.get_event_loop(),
            )
        except Exception as e:
            logger.warning(f"No se pudo notificar al usuario {user_id}: {e}")

    return {"status": "ok", "puntos_sumados": PUNTOS_POR_ANUNCIO}, 200


def iniciar_servidor_flask():
    flask_app.run(host="0.0.0.0", port=REWARD_SERVER_PORT)


def main():
    global telegram_app
    init_db()

    # Inicia el servidor Flask en un hilo separado (para no bloquear el bot)
    hilo_flask = threading.Thread(target=iniciar_servidor_flask, daemon=True)
    hilo_flask.start()
    logger.info(f"Servidor de recompensas escuchando en el puerto {REWARD_SERVER_PORT}")

    app = Application.builder().token(BOT_TOKEN).build()
    telegram_app = app

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(manejar_botones))

    logger.info("Bot iniciado correctamente 🚀")
    app.run_polling()


if __name__ == "__main__":
    main()
