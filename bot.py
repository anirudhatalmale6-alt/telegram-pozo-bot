#!/usr/bin/env python3
"""
Telegram Pozo Bot - Sistema de pujas en tiempo real
"""

import asyncio
import logging
import time
import json
import os

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.error import TelegramError, BadRequest

# ─── Configuration ───────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8799804918:AAEYl_DXbjtgAP6ubkTmDY6ix4RdRyTFkK0")
OWNER_ID = int(os.environ.get("OWNER_ID", "6095017121"))
BID_COST = 0.25
INITIAL_TIME_SECONDS = 60 * 60  # 60 minutes
TIME_PENALTY_SECONDS = 60       # 1 minute per bid
PRIZE_PERCENT = 0.50
DATA_FILE = "data.json"

PAYMENT_INFO = """💰 DATOS DE PAGO 💰

📱 Pago Móvil (Venezuela):
  Teléfono: 04163901356
  Cédula: 27955233

💱 Binance Pay:
  ID: 578531980

Envía el capture de tu pago aquí y será verificado."""

# Persistent keyboard buttons (always visible at bottom)
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("⚡ TOMAR POSICIÓN")],
        [KeyboardButton("💰 GESTIONAR ACTIVO"), KeyboardButton("💳 MI SALDO")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# Inline buttons on the board message itself
BOARD_INLINE = InlineKeyboardMarkup([
    [InlineKeyboardButton("⚡ TOMAR POSICIÓN ($0.25)", callback_data="bid")],
    [
        InlineKeyboardButton("💰 GESTIONAR ACTIVO", callback_data="payment_info"),
        InlineKeyboardButton("💳 MI SALDO", callback_data="my_balance"),
    ]
])

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ─── Persistence ─────────────────────────────────────────────────────────────
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"balances": {}, "pozo": None}


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


# Global state
data = load_data()
pozo_task = None
pozo_lock = asyncio.Lock()


# ─── Board rendering ─────────────────────────────────────────────────────────
def render_board(pozo):
    remaining = max(0, pozo["end_time"] - time.time())
    minutes = int(remaining) // 60
    seconds = int(remaining) % 60

    titular_user = pozo.get("titular_username", "")
    titular_name = pozo.get("titular_name", "Nadie")
    titular_display = f"@{titular_user}" if titular_user else titular_name

    fund = pozo["fund"]
    prize = fund * PRIZE_PERCENT
    bids = pozo.get("bid_count", 0)

    anim = "⏳" if int(time.time()) % 2 == 0 else "⌛"

    if remaining <= 0:
        return (
            f"{'='*28}\n"
            f"    🏆 POZO FINALIZADO 🏆\n"
            f"{'='*28}\n\n"
            f"🎉 GANADOR: {titular_display}\n\n"
            f"💰 Fondo total: ${fund:.2f}\n"
            f"🎁 Premio: ${prize:.2f}\n"
            f"⚡ Pujas totales: {bids}\n\n"
            f"{'='*28}"
        )

    return (
        f"{'='*28}\n"
        f"    🏆 POZO ACTIVO 🏆\n"
        f"{'='*28}\n\n"
        f"{anim} Tiempo: {minutes:02d}:{seconds:02d}\n\n"
        f"👑 Titular: {titular_display}\n\n"
        f"💰 Fondo: ${fund:.2f}\n"
        f"🎁 Premio (50%): ${prize:.2f}\n"
        f"⚡ Pujas: {bids}\n\n"
        f"{'='*28}"
    )


# ─── Helper: resend board at bottom ──────────────────────────────────────────
async def resend_board(bot, pozo, reply_markup=None, disable_notification=True):
    """Delete old board message and send a new one so it stays at the bottom."""
    chat_id = pozo["chat_id"]
    old_msg_id = pozo.get("message_id")

    # Delete old board message
    if old_msg_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except (BadRequest, TelegramError):
            pass

    # Send new board at bottom
    text = render_board(pozo)
    markup = reply_markup if reply_markup else BOARD_INLINE
    msg = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=markup,
        disable_notification=disable_notification
    )
    pozo["message_id"] = msg.message_id
    return msg


# ─── Pozo update loop ────────────────────────────────────────────────────────
async def pozo_update_loop(app: Application):
    """Edit board in place every 5 seconds (no new messages)."""
    global data

    while True:
        try:
            await asyncio.sleep(5)

            if data["pozo"] is None:
                break

            pozo = data["pozo"]
            remaining = pozo["end_time"] - time.time()

            if remaining <= 0:
                # Pozo ended - delete board and send final result
                old_msg_id = pozo.get("message_id")
                if old_msg_id:
                    try:
                        await app.bot.delete_message(
                            chat_id=pozo["chat_id"], message_id=old_msg_id
                        )
                    except (BadRequest, TelegramError):
                        pass

                text = render_board(pozo)
                await app.bot.send_message(
                    chat_id=pozo["chat_id"],
                    text=text
                )

                # Announce winner in group
                titular_user = pozo.get("titular_username", "")
                titular_name = pozo.get("titular_name", "Nadie")
                titular_id = pozo.get("titular_id")
                prize = pozo["fund"] * PRIZE_PERCENT
                winner = f"@{titular_user}" if titular_user else titular_name

                await app.bot.send_message(
                    chat_id=pozo["chat_id"],
                    text=(
                        f"🎉🎉🎉 POZO FINALIZADO 🎉🎉🎉\n\n"
                        f"👑 GANADOR: {winner}\n"
                        f"🎁 Premio: ${prize:.2f}\n\n"
                        f"¡Felicidades!"
                    ),
                    reply_markup=MAIN_KEYBOARD
                )

                # Send private message to winner
                if titular_id:
                    try:
                        await app.bot.send_message(
                            chat_id=int(titular_id),
                            text=(
                                f"🎉🎉🎉 ¡FELICIDADES! 🎉🎉🎉\n\n"
                                f"¡Eres el GANADOR del pozo!\n"
                                f"🎁 Tu premio: ${prize:.2f}\n\n"
                                f"📋 Envía tus datos para recibir el pago:\n"
                                f"- Nombre completo\n"
                                f"- Número de teléfono / Binance ID\n"
                                f"- Banco"
                            )
                        )
                    except TelegramError:
                        pass

                data["pozo"] = None
                save_data(data)
                break

            # Edit board in place (no new message, just updates the text)
            text = render_board(pozo)
            try:
                await app.bot.edit_message_text(
                    chat_id=pozo["chat_id"],
                    message_id=pozo["message_id"],
                    text=text,
                    reply_markup=BOARD_INLINE
                )
            except BadRequest as e:
                if "not modified" not in str(e).lower():
                    logger.warning(f"Edit failed: {e}")
            except TelegramError as e:
                logger.warning(f"Telegram error in loop: {e}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in pozo loop: {e}")
            await asyncio.sleep(5)


# ─── Command handlers ────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if chat.type == "private":
        await update.message.reply_text(
            "🎰 Bienvenido al Bot de Pozos\n\n"
            "📸 Envía aquí tu capture de pago para recargar saldo.\n"
            "Usa los botones de abajo para participar.",
            reply_markup=MAIN_KEYBOARD
        )
    else:
        # In group: send keyboard so THIS user sees the persistent buttons
        await update.message.reply_text(
            "💎 SISTEMA ON\n\nUsa los botones de abajo para participar.",
            reply_markup=MAIN_KEYBOARD
        )
        try:
            await update.message.delete()
        except TelegramError:
            pass


async def cmd_nuevopozo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global data, pozo_task

    user_id = update.effective_user.id

    if user_id != OWNER_ID:
        try:
            await update.message.delete()
        except TelegramError:
            pass
        return

    # Cancel existing pozo task if any
    if pozo_task and not pozo_task.done():
        pozo_task.cancel()
        try:
            await pozo_task
        except asyncio.CancelledError:
            pass

    # Create new pozo
    pozo = {
        "end_time": time.time() + INITIAL_TIME_SECONDS,
        "titular_id": None,
        "titular_name": "Nadie",
        "titular_username": "",
        "fund": 0.0,
        "bid_count": 0,
        "chat_id": update.effective_chat.id,
        "message_id": None,
    }

    # Send init message with persistent keyboard (so all users see buttons)
    await update.effective_chat.send_message(
        text="⏳ INICIANDO RELOJ...",
        reply_markup=MAIN_KEYBOARD
    )

    # Send the board WITH inline buttons on the message itself
    text = render_board(pozo)
    msg = await update.effective_chat.send_message(
        text=text,
        reply_markup=BOARD_INLINE
    )
    pozo["message_id"] = msg.message_id

    data["pozo"] = pozo
    save_data(data)

    # Start update loop
    pozo_task = asyncio.create_task(pozo_update_loop(context.application))

    try:
        await update.message.delete()
    except TelegramError:
        pass


async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command: /saldo todos"""
    if update.effective_user.id != OWNER_ID:
        try:
            await update.message.delete()
        except TelegramError:
            pass
        return

    args = context.args
    if not args:
        await update.message.reply_text("Uso: /saldo todos")
        return

    if args[0] == "todos":
        if not data["balances"]:
            await update.message.reply_text("No hay saldos registrados.")
        else:
            lines = []
            for uid, info in data["balances"].items():
                name = info.get("name", uid)
                bal = info.get("balance", 0)
                lines.append(f"  {name}: ${bal:.2f}")
            await update.message.reply_text("💰 Saldos:\n" + "\n".join(lines))
    try:
        await update.message.delete()
    except TelegramError:
        pass


# ─── Bid logic (shared by keyboard button and inline button) ─────────────────
async def do_bid(user, chat_id, context, is_callback=False, query=None):
    """Process a bid. Returns True if successful."""
    global data

    if data["pozo"] is None:
        return False, "❌ No hay pozo activo."

    pozo = data["pozo"]
    remaining = pozo["end_time"] - time.time()
    if remaining <= 0:
        return False, "❌ El pozo ya finalizó."

    user_id = str(user.id)

    if pozo["titular_id"] == user_id:
        return False, "⚠️ Ya eres el titular actual."

    user_data = data["balances"].get(user_id, {"balance": 0, "name": user.full_name})
    if user_data["balance"] < BID_COST:
        return False, (
            f"❌ Saldo insuficiente. Tienes ${user_data['balance']:.2f}, "
            f"necesitas ${BID_COST:.2f}.\nUsa 💰 GESTIONAR ACTIVO para recargar."
        )

    async with pozo_lock:
        user_data["balance"] -= BID_COST
        user_data["name"] = user.full_name
        data["balances"][user_id] = user_data

        pozo["titular_id"] = user_id
        pozo["titular_name"] = user.full_name
        pozo["titular_username"] = user.username or ""
        pozo["fund"] += BID_COST
        pozo["bid_count"] += 1
        pozo["end_time"] -= TIME_PENALTY_SECONDS

        data["pozo"] = pozo
        save_data(data)

    # Send notification to group (with sound for push notification)
    username = f"@{user.username}" if user.username else user.full_name
    await context.bot.send_message(
        chat_id=pozo["chat_id"],
        text=f"⚡ ¡NUEVO LÍDER! {username} tomó el mando y restó 1 minuto. ⏱️",
        reply_markup=MAIN_KEYBOARD
    )

    # Resend board at bottom (silent) so it's visible after the notification
    await resend_board(context.bot, pozo, BOARD_INLINE, disable_notification=True)
    data["pozo"] = pozo
    save_data(data)

    return True, "⚡ ¡Tomaste la posición!"


# ─── Keyboard button handlers (text-based, persistent keyboard) ──────────────
async def handle_tomar_posicion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the TOMAR POSICIÓN keyboard button."""
    user = update.effective_user
    chat = update.effective_chat

    # Delete the button text message
    try:
        await update.message.delete()
    except TelegramError:
        pass

    success, msg_text = await do_bid(user, chat.id, context)
    if not success:
        try:
            msg = await chat.send_message(msg_text)
            await asyncio.sleep(4)
            await msg.delete()
        except TelegramError:
            pass


async def handle_gestionar_activo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle GESTIONAR ACTIVO keyboard button."""
    try:
        await update.message.delete()
    except TelegramError:
        pass

    try:
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=PAYMENT_INFO
        )
    except TelegramError:
        try:
            msg = await update.effective_chat.send_message(
                "❌ No pude enviarte mensaje privado. Primero escríbele /start al bot por privado."
            )
            await asyncio.sleep(5)
            await msg.delete()
        except TelegramError:
            pass


async def handle_mi_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle MI SALDO keyboard button."""
    try:
        await update.message.delete()
    except TelegramError:
        pass

    user_id = str(update.effective_user.id)
    balance = data["balances"].get(user_id, {}).get("balance", 0)

    try:
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=f"💳 Tu saldo: ${balance:.2f}"
        )
    except TelegramError:
        try:
            msg = await update.effective_chat.send_message(f"💳 Tu saldo: ${balance:.2f}")
            await asyncio.sleep(5)
            await msg.delete()
        except TelegramError:
            pass


# ─── Callback handlers (inline buttons on board + approve/reject) ────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route callback queries."""
    query = update.callback_query
    cb_data = query.data

    if cb_data == "bid":
        success, msg_text = await do_bid(query.from_user, query.message.chat_id, context, is_callback=True, query=query)
        await query.answer(msg_text, show_alert=not success or True)

    elif cb_data == "payment_info":
        try:
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text=PAYMENT_INFO
            )
            await query.answer("📩 Te envié los datos de pago por privado.")
        except TelegramError:
            await query.answer(
                "❌ No pude enviarte mensaje privado. "
                "Primero escríbele /start al bot por privado.",
                show_alert=True
            )

    elif cb_data == "my_balance":
        user_id = str(query.from_user.id)
        balance = data["balances"].get(user_id, {}).get("balance", 0)
        try:
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text=f"💳 Tu saldo: ${balance:.2f}"
            )
            await query.answer("📩 Te envié tu saldo por privado.")
        except TelegramError:
            await query.answer(f"💳 Tu saldo: ${balance:.2f}", show_alert=True)

    elif cb_data.startswith("approve_"):
        await handle_approve(update, context)

    elif cb_data.startswith("reject_"):
        await handle_reject(update, context)


# ─── Payment verification ────────────────────────────────────────────────────
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """When user sends a photo (payment capture), forward to owner for approval."""
    global data
    user = update.effective_user
    chat = update.effective_chat

    photo = update.message.photo[-1]
    user_id = str(user.id)
    username = f"@{user.username}" if user.username else user.full_name

    if user_id not in data["balances"]:
        data["balances"][user_id] = {"balance": 0, "name": user.full_name}
    else:
        data["balances"][user_id]["name"] = user.full_name
    save_data(data)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ APROBAR $1", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton("❌ RECHAZAR", callback_data=f"reject_{user_id}"),
        ]
    ])

    try:
        await context.bot.send_photo(
            chat_id=OWNER_ID,
            photo=photo.file_id,
            caption=f"📸 Capture de pago de {username} (ID: {user_id})\nNombre: {user.full_name}",
            reply_markup=keyboard
        )
    except TelegramError as e:
        logger.error(f"Could not forward payment to owner: {e}")

    # Delete from group
    if chat.type in ("group", "supergroup"):
        try:
            await update.message.delete()
        except TelegramError:
            pass

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text="📸 Tu capture fue recibido y está en revisión. Te notificaré cuando sea aprobado."
        )
    except TelegramError:
        pass


async def handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global data
    query = update.callback_query

    if query.from_user.id != OWNER_ID:
        await query.answer("❌ Solo la dueña puede aprobar pagos.", show_alert=True)
        return

    user_id = query.data.replace("approve_", "")

    if user_id not in data["balances"]:
        data["balances"][user_id] = {"balance": 0, "name": user_id}

    data["balances"][user_id]["balance"] += 1.00
    new_balance = data["balances"][user_id]["balance"]
    user_name = data["balances"][user_id].get("name", user_id)
    save_data(data)

    await query.answer("✅ Pago aprobado.")
    await query.edit_message_caption(
        caption=f"✅ APROBADO - {user_name} (ID: {user_id})\nNuevo saldo: ${new_balance:.2f}"
    )

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text=f"✅ ¡Tu pago fue aprobado! Se acreditó $1.00 a tu saldo.\n💳 Saldo actual: ${new_balance:.2f}"
        )
    except TelegramError:
        pass


async def handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.from_user.id != OWNER_ID:
        await query.answer("❌ Solo la dueña puede rechazar pagos.", show_alert=True)
        return

    user_id = query.data.replace("reject_", "")
    user_name = data["balances"].get(user_id, {}).get("name", user_id)

    await query.answer("❌ Pago rechazado.")
    await query.edit_message_caption(
        caption=f"❌ RECHAZADO - {user_name} (ID: {user_id})"
    )

    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text="❌ Tu pago fue rechazado. Verifica el capture y envíalo nuevamente."
        )
    except TelegramError:
        pass


# ─── Moderation ───────────────────────────────────────────────────────────────
async def handle_text_moderation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete non-button text messages in group."""
    if update.effective_chat.type not in ("group", "supergroup"):
        return

    if update.effective_user.id == OWNER_ID:
        return

    try:
        await update.message.delete()
    except TelegramError:
        pass


# ─── Post-init ────────────────────────────────────────────────────────────────
async def post_init(app: Application):
    global pozo_task
    if data["pozo"] is not None:
        remaining = data["pozo"]["end_time"] - time.time()
        if remaining > 0:
            logger.info("Resuming active pozo...")
            pozo_task = asyncio.create_task(pozo_update_loop(app))
        else:
            data["pozo"] = None
            save_data(data)


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("nuevopozo", cmd_nuevopozo))
    app.add_handler(CommandHandler("saldo", cmd_saldo))

    # Keyboard button handlers (persistent keyboard at bottom)
    app.add_handler(MessageHandler(
        filters.Regex("^⚡ TOMAR POSICIÓN$"), handle_tomar_posicion
    ))
    app.add_handler(MessageHandler(
        filters.Regex("^💰 GESTIONAR ACTIVO$"), handle_gestionar_activo
    ))
    app.add_handler(MessageHandler(
        filters.Regex("^💳 MI SALDO$"), handle_mi_saldo
    ))

    # Callback queries (inline buttons on board + approve/reject)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Photos (payment captures)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Text moderation (delete other text in groups)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        handle_text_moderation
    ))

    logger.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
