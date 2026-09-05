import asyncio
import logging
import json
import random
import re
from datetime import datetime
from typing import Optional, List, Dict, Any

import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode
import pytesseract
from PIL import Image
import io

# Попытка импорта генератора карточки профиля
try:
    from faceit_card_pillow import generate_profile_card
except ImportError:
    logging.warning("faceit_card_pillow.py не найден. Профиль будет текстовым.")
    generate_profile_card = None

# ==================== Конфигурация ====================
TOKEN = "8873833506:AAG78i4w3wuFdrk18aKAYyXQXuXwoJVY1Kk"
CHANNEL_USERNAME = "@hp404faceit"
CREATOR_USERNAME = "@nelinner"
IMAGE_URL = "https://i.postimg.cc/vBPMQmWy/Bez-nazvania150-20260905144110.jpg"

LOBBY_CHAT_IDS = {
    ("default", "5x5"): 5,
    ("default", "2x2"): 8,
    ("default", "1x1"): 9,
    ("pro", "5x5"): 10,
    ("pro", "2x2"): 11,
    ("pro", "1x1"): 12,
}

RESULT_CHANNEL_ID = 17
DRAW_CHANNEL_ID = 16

MAPS = ["Dune", "Hanami", "Rust", "Prison", "Breeze", "Sandstone", "Province"]

MAX_PLAYERS = {
    "5x5": 10,
    "2x2": 4,
    "1x1": 2,
}

DB_PATH = "faceit_bot.db"

# ==================== Инициализация БД ====================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                nickname TEXT UNIQUE,
                standoff_id TEXT,
                kills INTEGER DEFAULT 0,
                deaths INTEGER DEFAULT 0,
                elo INTEGER DEFAULT 1000,
                level INTEGER DEFAULT 1,
                pro_league INTEGER DEFAULT 0,
                avatar_file_id TEXT,
                is_banned INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                registered INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lobbies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                league_type TEXT,
                mode TEXT,
                message_id INTEGER,
                current_players TEXT,
                max_players INTEGER,
                map TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, league_type, mode)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                host_user_id INTEGER,
                league_type TEXT,
                mode TEXT,
                map TEXT,
                players TEXT,
                status TEXT DEFAULT 'pending_result',
                score_ct INTEGER,
                score_t INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                cancel_reason TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type INTEGER,
                target_nickname TEXT,
                text TEXT,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bans (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                banned_until TIMESTAMP
            )
        """)
        await db.commit()

# ==================== Вспомогательные функции ====================
async def is_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    try:
        chat_member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return chat_member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except Exception:
        return False

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            return dict(row)
    return None

async def update_user(user_id: int, **kwargs):
    fields = ", ".join([f"{k} = ?" for k in kwargs.keys()])
    values = list(kwargs.values()) + [user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {fields} WHERE user_id = ?", values)
        await db.commit()

async def is_admin(user_id: int) -> bool:
    user = await get_user(user_id)
    return user and user["is_admin"] == 1

async def is_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT banned_until FROM bans WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            banned_until = row[0]
            if banned_until:
                banned_until = datetime.fromisoformat(banned_until)
                if banned_until > datetime.now():
                    return True
                else:
                    await db.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
                    await db.commit()
            else:
                return True
    return False

async def get_all_admins() -> List[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM users WHERE is_admin = 1")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def create_lobby(league_type: str, mode: str) -> Optional[int]:
    chat_id = LOBBY_CHAT_IDS[(league_type, mode)]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM lobbies WHERE chat_id = ? AND league_type = ? AND mode = ?",
                         (chat_id, league_type, mode))
        await db.commit()
    map_name = random.choice(MAPS)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO lobbies (chat_id, league_type, mode, max_players, map, current_players)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (chat_id, league_type, mode, MAX_PLAYERS[mode], map_name, json.dumps([])))
        lobby_id = cursor.lastrowid
        await db.commit()
    return lobby_id

async def get_lobby(league_type: str, mode: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM lobbies WHERE league_type = ? AND mode = ? ORDER BY id DESC LIMIT 1
        """, (league_type, mode))
        row = await cursor.fetchone()
        if row:
            return dict(row)
    return None

async def update_lobby_players(lobby_id: int, players: List[int]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE lobbies SET current_players = ? WHERE id = ?",
                         (json.dumps(players), lobby_id))
        await db.commit()

async def get_lobby_by_id(lobby_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM lobbies WHERE id = ?", (lobby_id,))
        row = await cursor.fetchone()
        if row:
            return dict(row)
    return None

async def delete_lobby(lobby_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM lobbies WHERE id = ?", (lobby_id,))
        await db.commit()

async def send_lobby_post(context: ContextTypes.DEFAULT_TYPE, lobby_id: int):
    lobby = await get_lobby_by_id(lobby_id)
    if not lobby:
        return
    chat_id = lobby["chat_id"]
    league_type = lobby["league_type"]
    mode = lobby["mode"]
    players_ids = json.loads(lobby["current_players"])
    players_info = []
    for uid in players_ids:
        user = await get_user(uid)
        if user:
            players_info.append(f"{user['nickname']} | {user['standoff_id']}")
    text = f"🎮 Регистрация игроков в lobby {mode} | {league_type} league\n"
    text += "━━━━━━━━━━━\n"
    text += f"Карта: {lobby['map']}\n"
    text += "━━━━━━━━━━━\n"
    text += "[👥] Список игроков:\n"
    if players_info:
        text += "\n".join(players_info)
    else:
        text += "Пусто"
    text += "\n━━━━━━━━━━━"
    keyboard = [
        [
            InlineKeyboardButton("🚪 Присоединиться", callback_data=f"join_lobby:{lobby_id}"),
            InlineKeyboardButton("⬅️ Выйти", callback_data=f"leave_lobby:{lobby_id}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if lobby["message_id"]:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=lobby["message_id"],
                text=text,
                reply_markup=reply_markup
            )
        except Exception as e:
            logging.error(f"Failed to edit lobby message: {e}")
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=lobby["message_id"])
            except:
                pass
            msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE lobbies SET message_id = ? WHERE id = ?", (msg.message_id, lobby_id))
                await db.commit()
    else:
        msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE lobbies SET message_id = ? WHERE id = ?", (msg.message_id, lobby_id))
            await db.commit()

async def reset_lobby(context: ContextTypes.DEFAULT_TYPE, league_type: str, mode: str):
    lobby = await get_lobby(league_type, mode)
    if lobby:
        try:
            await context.bot.delete_message(chat_id=lobby["chat_id"], message_id=lobby["message_id"])
        except:
            pass
        await delete_lobby(lobby["id"])
    new_lobby_id = await create_lobby(league_type, mode)
    if new_lobby_id:
        await send_lobby_post(context, new_lobby_id)
        # Планируем следующий сброс через 10-15 минут
        context.job_queue.run_once(
            reset_lobby_job,
            random.randint(600, 900),
            data=(league_type, mode)
        )

async def reset_lobby_job(context: ContextTypes.DEFAULT_TYPE):
    league_type, mode = context.job.data
    await reset_lobby(context, league_type, mode)

async def draw_match(context: ContextTypes.DEFAULT_TYPE, lobby_id: int):
    lobby = await get_lobby_by_id(lobby_id)
    if not lobby:
        return
    players_ids = json.loads(lobby["current_players"])
    if len(players_ids) != lobby["max_players"]:
        return
    host_id = random.choice(players_ids)
    ct_players = players_ids[::2]
    t_players = players_ids[1::2]
    ct_list = []
    for uid in ct_players:
        user = await get_user(uid)
        if user:
            ct_list.append(f"[👤] {user['nickname']} | {user['standoff_id']}")
    t_list = []
    for uid in t_players:
        user = await get_user(uid)
        if user:
            t_list.append(f"[👤] {user['nickname']} | {user['standoff_id']}")
    host_user = await get_user(host_id)
    text = f"👥 Жеребьёвка игроков\n"
    text += f"host by: {host_user['nickname']}\n"
    text += "━━━━━━━━━━━\n"
    text += "🔵 CT:\n"
    text += "\n".join(ct_list) + "\n\n"
    text += "🔴 T:\n"
    text += "\n".join(t_list) + "\n"
    text += "━━━━━━━━━━━\n"
    text += f"📃 Если в течении 15-ти минут хост {host_user['nickname']} не пригласит вас в лобби то обратитесь к администрации проекта с решением проблемы"
    await context.bot.send_message(chat_id=DRAW_CHANNEL_ID, text=text)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO matches (host_user_id, league_type, mode, map, players, status)
            VALUES (?, ?, ?, ?, ?, 'pending_result')
        """, (host_id, lobby["league_type"], lobby["mode"], lobby["map"], json.dumps(players_ids)))
        await db.commit()
    await reset_lobby(context, lobby["league_type"], lobby["mode"])

# ==================== Обработчики ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await is_banned(user.id):
        await update.message.reply_text("Вы забанены в проекте.")
        return
    db_user = await get_user(user.id)
    if db_user and db_user["registered"]:
        await show_main_menu(update, context)
        return
    if not await is_subscribed(update, context):
        keyboard = [[InlineKeyboardButton("Подписаться", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Для использования бота необходимо подписаться на канал @hp404faceit.",
            reply_markup=reply_markup
        )
        return
    # Отправляем фото с инструкцией
    await update.message.reply_photo(
        photo=IMAGE_URL,
        caption=(
            "Добро пожаловать! Пройдите регистрацию.\n\n"
            "1. [📃] Напишите ваш nickname которое используется в игре Standoff 2"
        )
    )
    context.user_data["registration_step"] = "nickname"

async def handle_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("registration_step")
    if not step:
        return
    if step == "nickname":
        nickname = update.message.text.strip()
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT user_id FROM users WHERE nickname = ?", (nickname,))
            existing = await cursor.fetchone()
        if existing:
            await update.message.reply_text("Этот nickname уже занят. Введите другой.")
            return
        context.user_data["nickname"] = nickname
        context.user_data["registration_step"] = "standoff_id"
        await update.message.reply_text("2. [ℹ️] Напишите свое ID из профиля игры Standoff 2")
    elif step == "standoff_id":
        standoff_id = update.message.text.strip()
        nickname = context.user_data.get("nickname")
        user = update.effective_user
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO users (user_id, username, nickname, standoff_id, registered)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    nickname = excluded.nickname,
                    standoff_id = excluded.standoff_id,
                    registered = 1
            """, (user.id, user.username, nickname, standoff_id))
            await db.commit()
        context.user_data.pop("registration_step", None)
        context.user_data.pop("nickname", None)
        await update.message.reply_text("Регистрация завершена!")
        await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = [
        [InlineKeyboardButton("ℹ️ Профиль", callback_data=f"menu:profile:{user_id}")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data=f"menu:settings:{user_id}")],
        [InlineKeyboardButton("🏆 Лидерборд", callback_data=f"menu:leaderboard:{user_id}")],
        [InlineKeyboardButton("🔎 Поиск лобби", callback_data=f"menu:find_lobby:{user_id}")],
        [InlineKeyboardButton("🎮 Мои лобби хоста", callback_data=f"menu:my_lobbies:{user_id}")],
        [InlineKeyboardButton("🎟 Ticket", callback_data=f"menu:ticket:{user_id}")],
        [InlineKeyboardButton("🤝 Party", callback_data=f"menu:party:{user_id}")],
    ]
    if await is_admin(user_id):
        keyboard.append([InlineKeyboardButton("👑 Admin panel", callback_data=f"menu:admin:{user_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text("Главное меню:", reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text("Главное меню:", reply_markup=reply_markup)

# ==================== Callback обработчики ====================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    if await is_banned(user_id):
        await query.message.reply_text("Вы забанены.")
        return
    if ":" in data:
        parts = data.split(":")
        action = parts[0]
        if action == "menu":
            target_user_id = int(parts[2])
            if target_user_id != user_id:
                await query.answer("Это не ваше меню!", show_alert=True)
                return
            await handle_menu_callback(update, context)
            return
        elif action == "join_lobby":
            await handle_join_lobby(update, context, int(parts[1]))
            return
        elif action == "leave_lobby":
            await handle_leave_lobby(update, context, int(parts[1]))
            return
        elif action == "cancel_match":
            await handle_cancel_match(update, context, int(parts[1]))
            return
        elif action == "register_results":
            await handle_register_results(update, context, int(parts[1]))
            return
        elif action == "approve_cancel":
            await handle_approve_cancel(update, context, int(parts[1]))
            return
        elif action == "reject_cancel":
            await handle_reject_cancel(update, context, int(parts[1]))
            return
        elif action == "ticket_consider":
            await handle_ticket_consider(update, context, int(parts[1]))
            return
        elif action == "ticket_reject":
            await handle_ticket_reject(update, context, int(parts[1]))
            return
        elif action == "ticket_message":
            await handle_ticket_message(update, context, int(parts[1]))
            return
        elif action == "settings":
            await handle_settings_action(update, context, parts)
            return
        elif action == "admin":
            await handle_admin_action(update, context, parts)
            return
        elif action == "find":
            await handle_find_lobby(update, context)
            return
        elif action == "match_select":
            await handle_match_select(update, context)
            return
        elif action == "ticket_type":
            await handle_ticket_type(update, context)
            return
        else:
            await handle_menu_callback(update, context)
            return
    else:
        await handle_menu_callback(update, context)

async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    if data.startswith("menu:profile"):
        await show_profile(update, context)
    elif data.startswith("menu:settings"):
        await show_settings(update, context)
    elif data.startswith("menu:leaderboard"):
        await show_leaderboard(update, context)
    elif data.startswith("menu:find_lobby"):
        await show_find_lobby(update, context)
    elif data.startswith("menu:my_lobbies"):
        await show_my_lobbies(update, context)
    elif data.startswith("menu:ticket"):
        await show_ticket_menu(update, context)
    elif data.startswith("menu:party"):
        await query.message.reply_text("🤝 Party - функционал в разработке.")
    elif data.startswith("menu:admin"):
        if await is_admin(user_id):
            await show_admin_panel(update, context)
        else:
            await query.answer("Недостаточно прав.", show_alert=True)

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user = await get_user(user_id)
    if not user:
        return

    kd = user["kills"] / user["deaths"] if user["deaths"] > 0 else 0.0

    if generate_profile_card:
        # Генерация карточки
        card_data = {
            "nickname": user["nickname"],
            "standoff_id": user["standoff_id"],
            "kills": user["kills"],
            "deaths": user["deaths"],
            "kd": kd,
            "elo": user["elo"],
            "level": user["level"],
            "pro_league": user["pro_league"] == 1,
            "avatar_file_id": user["avatar_file_id"]
        }
        try:
            image_bytes = generate_profile_card(card_data)
            keyboard = [[InlineKeyboardButton("⬅️ Главное меню", callback_data=f"menu:main:{user_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_photo(
                photo=image_bytes,
                caption="Профиль игрока",
                reply_markup=reply_markup
            )
            # Удаляем исходное сообщение с меню, если это необходимо
            # await query.message.delete()
            return
        except Exception as e:
            logging.error(f"Ошибка генерации карточки: {e}")
            # Падаем на текстовый профиль
            pass

    # Текстовый профиль
    text = (
        f"🪪 Профиль игрока • 404hp faceit\n"
        f"━━━━━━━━━━━\n"
        f"[👤] {user['nickname']}\n"
        f"└ ID: {user['standoff_id']}\n\n"
        f"[📊] Статистика\n"
        f"└K/D: {kd:.2f} ( kill {user['kills']}, dead {user['deaths']} )\n\n"
        f"[ℹ️] Уровень: {user['level']}\n"
        f"└ ELO: {user['elo']}\n\n"
        f"[👑] Pro league\n"
        f"└ {'Yes' if user['pro_league'] else 'No'}\n"
        f"━━━━━━━━━━━"
    )
    keyboard = [[InlineKeyboardButton("⬅️ Главное меню", callback_data=f"menu:main:{user_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)

async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    keyboard = [
        [InlineKeyboardButton("🔄 Сбросить статистику", callback_data=f"settings:reset:{user_id}")],
        [InlineKeyboardButton("📸 Загрузить аватарку", callback_data=f"settings:upload_avatar:{user_id}")],
        [InlineKeyboardButton("🗑️ Удалить аватарку", callback_data=f"settings:delete_avatar:{user_id}")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data=f"menu:main:{user_id}")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("⚙️ Настройки:", reply_markup=reply_markup)

async def handle_settings_action(update: Update, context: ContextTypes.DEFAULT_TYPE, parts):
    query = update.callback_query
    action = parts[1]
    user_id = int(parts[2])
    if query.from_user.id != user_id:
        await query.answer("Это не ваше меню!", show_alert=True)
        return
    if action == "reset":
        await update_user(user_id, kills=0, deaths=0, elo=1000, level=1)
        await query.answer("Статистика сброшена.", show_alert=True)
        await show_profile(update, context)
    elif action == "upload_avatar":
        await query.message.reply_text("Отправьте фото для аватарки.")
        context.user_data["awaiting_avatar"] = True
    elif action == "delete_avatar":
        await update_user(user_id, avatar_file_id=None)
        await query.answer("Аватарка удалена.", show_alert=True)

async def handle_avatar_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_avatar"):
        return
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    file_id = photo.file_id
    await update_user(user_id, avatar_file_id=file_id)
    context.user_data.pop("awaiting_avatar", None)
    await update.message.reply_text("Аватарка сохранена!")

async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT nickname, elo FROM users WHERE registered = 1 ORDER BY elo DESC LIMIT 35")
        rows = await cursor.fetchall()
    text = "🏆 Лидерборд\n━━━━━━━━━━━\n"
    for i, row in enumerate(rows, 1):
        crown = "👑" if i == 1 else "👤"
        text += f"{crown} {row['nickname']}: {row['elo']}\n"
    text += "━━━━━━━━━━━"
    keyboard = [[InlineKeyboardButton("⬅️ Главное меню", callback_data=f"menu:main:{user_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)

async def show_find_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user = await get_user(user_id)
    keyboard = []
    keyboard.append([InlineKeyboardButton("Default League", callback_data=f"find:default:{user_id}")])
    if user and user["pro_league"]:
        keyboard.append([InlineKeyboardButton("Pro League", callback_data=f"find:pro:{user_id}")])
    keyboard.append([InlineKeyboardButton("⬅️ Главное меню", callback_data=f"menu:main:{user_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите лигу для поиска лобби:", reply_markup=reply_markup)

async def show_league_lobbies(update: Update, context: ContextTypes.DEFAULT_TYPE, league_type: str):
    query = update.callback_query
    user_id = query.from_user.id
    lobbies = []
    for mode in ["5x5", "2x2", "1x1"]:
        lobby = await get_lobby(league_type, mode)
        if lobby:
            players = json.loads(lobby["current_players"])
            lobbies.append((lobby, len(players)))
    if not lobbies:
        await query.edit_message_text("В данной лиге нет активных лобби.")
        return
    text = f"Активные лобби ({league_type} league):\n"
    keyboard = []
    for lobby, count in lobbies:
        text += f"\n• {lobby['mode']} | Карта: {lobby['map']} | Игроков: {count}/{lobby['max_players']}\n"
        keyboard.append([
            InlineKeyboardButton(f"Присоединиться {lobby['mode']}", callback_data=f"join_lobby:{lobby['id']}")
        ])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"menu:find_lobby:{user_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)

async def handle_find_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    parts = data.split(":")
    league_type = parts[1]
    user_id = int(parts[2])
    if query.from_user.id != user_id:
        await query.answer("Не ваше меню!", show_alert=True)
        return
    user = await get_user(user_id)
    if league_type == "pro" and (not user or not user["pro_league"]):
        await query.answer("У вас нет доступа к Pro League.", show_alert=True)
        return
    await show_league_lobbies(update, context, league_type)

async def handle_join_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE, lobby_id: int):
    query = update.callback_query
    user_id = query.from_user.id
    lobby = await get_lobby_by_id(lobby_id)
    if not lobby:
        await query.answer("Лобби не найдено.", show_alert=True)
        return
    user = await get_user(user_id)
    if not user:
        await query.answer("Вы не зарегистрированы.", show_alert=True)
        return
    if lobby["league_type"] == "pro" and not user["pro_league"]:
        await query.answer("Это лобби только для Pro League.", show_alert=True)
        return
    players = json.loads(lobby["current_players"])
    if user_id in players:
        await query.answer("Вы уже в лобби.", show_alert=True)
        return
    if len(players) >= lobby["max_players"]:
        await query.answer("Лобби заполнено.", show_alert=True)
        return
    players.append(user_id)
    await update_lobby_players(lobby_id, players)
    await send_lobby_post(context, lobby_id)
    await query.answer("Вы присоединились к лобби.")
    if len(players) == lobby["max_players"]:
        await draw_match(context, lobby_id)

async def handle_leave_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE, lobby_id: int):
    query = update.callback_query
    user_id = query.from_user.id
    lobby = await get_lobby_by_id(lobby_id)
    if not lobby:
        await query.answer("Лобби не найдено.", show_alert=True)
        return
    players = json.loads(lobby["current_players"])
    if user_id not in players:
        await query.answer("Вы не в этом лобби.", show_alert=True)
        return
    players.remove(user_id)
    await update_lobby_players(lobby_id, players)
    await send_lobby_post(context, lobby_id)
    await query.answer("Вы вышли из лобби.")

async def show_my_lobbies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM matches WHERE host_user_id = ? AND status = 'pending_result'", (user_id,))
        matches = await cursor.fetchall()
    if not matches:
        await query.edit_message_text("У вас нет незавершённых матчей как хост.")
        return
    keyboard = []
    for match in matches:
        mode = match["mode"]
        league = match["league_type"]
        map_name = match["map"]
        keyboard.append([
            InlineKeyboardButton(f"Матч {mode} {league} ({map_name})", callback_data=f"match_select:{match['id']}:{user_id}")
        ])
    keyboard.append([InlineKeyboardButton("⬅️ Главное меню", callback_data=f"menu:main:{user_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите матч:", reply_markup=reply_markup)

async def handle_match_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    parts = data.split(":")
    match_id = int(parts[1])
    user_id = int(parts[2])
    if query.from_user.id != user_id:
        await query.answer("Не ваше меню!", show_alert=True)
        return
    keyboard = [
        [InlineKeyboardButton("📊 Регистрировать результаты", callback_data=f"register_results:{match_id}")],
        [InlineKeyboardButton("❌ Отменить матч", callback_data=f"cancel_match:{match_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"menu:my_lobbies:{user_id}")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Действия с матчем:", reply_markup=reply_markup)

async def handle_cancel_match(update: Update, context: ContextTypes.DEFAULT_TYPE, match_id: int):
    query = update.callback_query
    user_id = query.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT host_user_id, status FROM matches WHERE id = ?", (match_id,))
        match = await cursor.fetchone()
    if not match or match["host_user_id"] != user_id or match["status"] != "pending_result":
        await query.answer("Матч не найден или вы не хост.", show_alert=True)
        return
    await query.message.reply_text("Введите причину отмены матча:")
    context.user_data["cancel_match_id"] = match_id
    context.user_data["awaiting_cancel_reason"] = True

async def handle_cancel_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_cancel_reason"):
        return
    reason = update.message.text.strip()
    match_id = context.user_data["cancel_match_id"]
    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE matches SET cancel_reason = ? WHERE id = ?", (reason, match_id))
        await db.commit()
    admins = await get_all_admins()
    host = await get_user(user_id)
    text = (
        f"⚠️ Заявка на отмену матча\n"
        f"━━━━━━━━━━━\n"
        f"Матч ID: {match_id}\n"
        f"Хост: {host['nickname']}\n"
        f"Причина: {reason}\n"
        f"━━━━━━━━━━━"
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ Отменить матч", callback_data=f"approve_cancel:{match_id}"),
            InlineKeyboardButton("❌ Отклонить заявку", callback_data=f"reject_cancel:{match_id}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    for admin_id in admins:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=reply_markup)
        except:
            pass
    context.user_data.pop("awaiting_cancel_reason", None)
    context.user_data.pop("cancel_match_id", None)
    await update.message.reply_text("Заявка на отмену отправлена администрации.")

async def handle_approve_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, match_id: int):
    query = update.callback_query
    admin_id = query.from_user.id
    if not await is_admin(admin_id):
        await query.answer("Недостаточно прав.", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE matches SET status = 'cancelled' WHERE id = ?", (match_id,))
        await db.commit()
    await query.edit_message_text("Матч отменён.")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT host_user_id FROM matches WHERE id = ?", (match_id,))
        match = await cursor.fetchone()
    if match:
        try:
            await context.bot.send_message(chat_id=match["host_user_id"], text="Ваш запрос на отмену матча одобрен.")
        except:
            pass

async def handle_reject_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, match_id: int):
    query = update.callback_query
    admin_id = query.from_user.id
    if not await is_admin(admin_id):
        await query.answer("Недостаточно прав.", show_alert=True)
        return
    await query.edit_message_text("Заявка отклонена.")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT host_user_id FROM matches WHERE id = ?", (match_id,))
        match = await cursor.fetchone()
    if match:
        try:
            await context.bot.send_message(chat_id=match["host_user_id"], text="Ваш запрос на отмену матча отклонён.")
        except:
            pass

async def handle_register_results(update: Update, context: ContextTypes.DEFAULT_TYPE, match_id: int):
    query = update.callback_query
    user_id = query.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT host_user_id, status FROM matches WHERE id = ?", (match_id,))
        match = await cursor.fetchone()
    if not match or match["host_user_id"] != user_id or match["status"] != "pending_result":
        await query.answer("Матч не найден или вы не хост.", show_alert=True)
        return
    await query.message.reply_text("📊 Введите счёт раундов\n└ Пример ( 13 1 ), 13 это сторона CT, 1 это сторона T")
    context.user_data["register_match_id"] = match_id
    context.user_data["awaiting_score"] = True

async def handle_score_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_score"):
        return
    text = update.message.text.strip()
    match = re.match(r"(\d+)\s+(\d+)", text)
    if not match:
        await update.message.reply_text("Неверный формат. Введите два числа через пробел: CT T")
        return
    ct = int(match.group(1))
    t = int(match.group(2))
    match_id = context.user_data["register_match_id"]
    context.user_data["score_ct"] = ct
    context.user_data["score_t"] = t
    context.user_data.pop("awaiting_score", None)
    context.user_data["awaiting_screenshot"] = True
    await update.message.reply_text("Отправьте скриншот с результатами (таблица K/D).")

async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_screenshot"):
        return
    user_id = update.effective_user.id
    match_id = context.user_data["register_match_id"]
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    try:
        image = Image.open(io.BytesIO(photo_bytes))
        text = pytesseract.image_to_string(image)
        # Предполагаем, что строки вида: Nickname Kills Deaths
        player_stats = []
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0].isalpha() and parts[1].isdigit() and parts[2].isdigit():
                nickname = parts[0]
                kills = int(parts[1])
                deaths = int(parts[2])
                player_stats.append((nickname, kills, deaths))
        if not player_stats:
            await update.message.reply_text("Не удалось распознать статистику. Попробуйте ещё раз.")
            return
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT players FROM matches WHERE id = ?", (match_id,))
            match_row = await cursor.fetchone()
            players_ids = json.loads(match_row["players"])
            for player_id in players_ids:
                player = await get_user(player_id)
                if not player:
                    continue
                nickname = player["nickname"]
                for stat_nick, kills, deaths in player_stats:
                    if stat_nick.lower() == nickname.lower():
                        new_kills = player["kills"] + kills
                        new_deaths = player["deaths"] + deaths
                        kd = kills / deaths if deaths > 0 else kills
                        elo_change = int((kd - 1) * 20)
                        new_elo = player["elo"] + elo_change
                        new_level = max(1, new_elo // 100)
                        await db.execute("""
                            UPDATE users SET kills = ?, deaths = ?, elo = ?, level = ?
                            WHERE user_id = ?
                        """, (new_kills, new_deaths, new_elo, new_level, player_id))
                        break
            await db.execute("UPDATE matches SET status = 'completed', score_ct = ?, score_t = ? WHERE id = ?",
                             (context.user_data["score_ct"], context.user_data["score_t"], match_id))
            await db.commit()
        post_text = f"📊 Результаты матча\n"
        post_text += f"━━━━━━━━━━━\n"
        post_text += f"Счёт: CT {context.user_data['score_ct']} - T {context.user_data['score_t']}\n"
        post_text += f"━━━━━━━━━━━\n"
        for player_id in players_ids:
            player = await get_user(player_id)
            if player:
                post_text += f"[👤] {player['nickname']} | {player['standoff_id']}\n"
                post_text += f"K/D: {player['kills']}/{player['deaths']}\n\n"
        post_text += "━━━━━━━━━━━"
        await context.bot.send_message(chat_id=RESULT_CHANNEL_ID, text=post_text)
        await update.message.reply_text("Результаты зарегистрированы!")
    except Exception as e:
        logging.error(f"OCR error: {e}")
        await update.message.reply_text("Произошла ошибка при обработке скриншота.")
    finally:
        context.user_data.pop("awaiting_screenshot", None)
        context.user_data.pop("register_match_id", None)
        context.user_data.pop("score_ct", None)
        context.user_data.pop("score_t", None)

async def show_ticket_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    keyboard = [
        [InlineKeyboardButton("1. Жалоба на игрока", callback_data=f"ticket_type:1:{user_id}")],
        [InlineKeyboardButton("2. Жалоба на админа", callback_data=f"ticket_type:2:{user_id}")],
        [InlineKeyboardButton("3. Своя проблема", callback_data=f"ticket_type:3:{user_id}")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data=f"menu:main:{user_id}")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите тип тикета:", reply_markup=reply_markup)

async def handle_ticket_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    parts = data.split(":")
    ticket_type = int(parts[1])
    user_id = int(parts[2])
    if query.from_user.id != user_id:
        await query.answer("Не ваше меню!", show_alert=True)
        return
    context.user_data["ticket_type"] = ticket_type
    if ticket_type in [1, 2]:
        await query.message.reply_text("[👤] Введите nickname пользователя:")
        context.user_data["awaiting_ticket_target"] = True
    else:
        await query.message.reply_text("[📃] Напишите текст жалобы:")
        context.user_data["awaiting_ticket_text"] = True

async def handle_ticket_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.user_data.get("awaiting_ticket_target"):
        target_nickname = update.message.text.strip()
        context.user_data["ticket_target"] = target_nickname
        context.user_data.pop("awaiting_ticket_target", None)
        await update.message.reply_text("[📃] Напишите текст жалобы:")
        context.user_data["awaiting_ticket_text"] = True
    elif context.user_data.get("awaiting_ticket_text"):
        text = update.message.text.strip()
        ticket_type = context.user_data.get("ticket_type")
        target = context.user_data.get("ticket_target")
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                INSERT INTO tickets (user_id, type, target_nickname, text)
                VALUES (?, ?, ?, ?)
            """, (user_id, ticket_type, target, text))
            ticket_id = cursor.lastrowid
            await db.commit()
        admins = await get_all_admins()
        user = await get_user(user_id)
        ticket_type_str = {1: "Жалоба на игрока", 2: "Жалоба на админа", 3: "Своя проблема"}[ticket_type]
        message = (
            f"🎟 Новый тикет\n"
            f"━━━━━━━━━━━\n"
            f"Пункт: {ticket_type_str}\n\n"
            f"[👤] От кого: @{user['username'] if user['username'] else 'нет username'}\n"
        )
        if target:
            message += f"[👤] На кого: {target}\n"
        message += f"━━━━━━━━━━━\n[📃] Текст жалобы:\n{text}"
        keyboard = [
            [
                InlineKeyboardButton("✅ Рассмотреть", callback_data=f"ticket_consider:{ticket_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"ticket_reject:{ticket_id}"),
                InlineKeyboardButton("📃 Написать сообщение пользователю", callback_data=f"ticket_message:{ticket_id}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        for admin_id in admins:
            try:
                await context.bot.send_message(chat_id=admin_id, text=message, reply_markup=reply_markup)
            except:
                pass
        await update.message.reply_text("Тикет отправлен.")
        context.user_data.pop("ticket_type", None)
        context.user_data.pop("ticket_target", None)
        context.user_data.pop("awaiting_ticket_text", None)

async def handle_ticket_consider(update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_id: int):
    query = update.callback_query
    admin_id = query.from_user.id
    if not await is_admin(admin_id):
        await query.answer("Недостаточно прав.", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tickets SET status = 'resolved' WHERE id = ?", (ticket_id,))
        await db.commit()
    await query.edit_message_text("Тикет отмечен как рассмотренный.")

async def handle_ticket_reject(update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_id: int):
    query = update.callback_query
    admin_id = query.from_user.id
    if not await is_admin(admin_id):
        await query.answer("Недостаточно прав.", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tickets SET status = 'rejected' WHERE id = ?", (ticket_id,))
        await db.commit()
    await query.edit_message_text("Тикет отклонён.")

async def handle_ticket_message(update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_id: int):
    query = update.callback_query
    admin_id = query.from_user.id
    if not await is_admin(admin_id):
        await query.answer("Недостаточно прав.", show_alert=True)
        return
    await query.message.reply_text("Введите сообщение для пользователя:")
    context.user_data["ticket_message_for"] = ticket_id
    context.user_data["awaiting_ticket_message"] = True

async def handle_ticket_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_ticket_message"):
        return
    ticket_id = context.user_data["ticket_message_for"]
    message_text = update.message.text.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT user_id FROM tickets WHERE id = ?", (ticket_id,))
        ticket = await cursor.fetchone()
    if ticket:
        try:
            await context.bot.send_message(chat_id=ticket["user_id"], text=f"Ответ администрации:\n{message_text}")
        except:
            pass
    await update.message.reply_text("Сообщение отправлено.")
    context.user_data.pop("awaiting_ticket_message", None)
    context.user_data.pop("ticket_message_for", None)

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if not await is_admin(user_id):
        await query.answer("Недостаточно прав.", show_alert=True)
        return
    keyboard = [
        [InlineKeyboardButton("1. Список всех игроков", callback_data=f"admin:list_players:{user_id}")],
        [InlineKeyboardButton("2. Управление картами", callback_data=f"admin:manage_maps:{user_id}")],
        [InlineKeyboardButton("3. Управление игроков в lobby", callback_data=f"admin:manage_lobby:{user_id}")],
        [InlineKeyboardButton("4. Забанить", callback_data=f"admin:ban:{user_id}")],
        [InlineKeyboardButton("5. Разбанить", callback_data=f"admin:unban:{user_id}")],
    ]
    if user_id == await get_creator_id():
        keyboard.append([InlineKeyboardButton("6. Выдать админку", callback_data=f"admin:grant_admin:{user_id}")])
        keyboard.append([InlineKeyboardButton("7. Забрать админку", callback_data=f"admin:revoke_admin:{user_id}")])
    keyboard.append([InlineKeyboardButton("⬅️ Главное меню", callback_data=f"menu:main:{user_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("👑 Админ-панель:", reply_markup=reply_markup)

async def get_creator_id() -> int:
    # В реальном коде нужно получить ID через API, здесь заглушка
    return 123456789  # замените на актуальный ID пользователя @nelinner

async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE, parts):
    query = update.callback_query
    action = parts[1]
    admin_id = query.from_user.id
    user_id = int(parts[2])
    if query.from_user.id != user_id or not await is_admin(admin_id):
        await query.answer("Недостаточно прав.", show_alert=True)
        return

    if action == "list_players":
        await admin_list_players(update, context)
    elif action == "manage_maps":
        await admin_manage_maps(update, context)
    elif action == "manage_lobby":
        await admin_manage_lobby(update, context)
    elif action == "ban":
        await admin_ban_start(update, context)
    elif action == "unban":
        await admin_unban_start(update, context)
    elif action == "grant_admin":
        if admin_id != await get_creator_id():
            await query.answer("Только создатель может выдавать админку.", show_alert=True)
            return
        await admin_grant_start(update, context)
    elif action == "revoke_admin":
        if admin_id != await get_creator_id():
            await query.answer("Только создатель может забирать админку.", show_alert=True)
            return
        await admin_revoke_start(update, context)
    elif action == "reset_lobby":
        await admin_reset_lobby(update, context, parts)
        return
    elif action == "add_map":
        await admin_add_map_start(update, context)
    elif action == "delete_map":
        await admin_delete_map_start(update, context)

async def admin_list_players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT user_id, nickname, elo, level, pro_league FROM users WHERE registered = 1")
        players = await cursor.fetchall()
    if not players:
        await query.edit_message_text("Нет зарегистрированных игроков.")
        return
    text = "Список игроков:\n\n"
    for p in players:
        pro = "👑" if p["pro_league"] else ""
        text += f"{pro} {p['nickname']} | ELO: {p['elo']} | Уровень: {p['level']} | ID: {p['user_id']}\n"
    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data=f"menu:admin:{query.from_user.id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)

async def admin_manage_maps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    global MAPS
    text = "Текущие карты:\n" + "\n".join(MAPS)
    keyboard = [
        [InlineKeyboardButton("➕ Добавить карту", callback_data=f"admin:add_map:{query.from_user.id}")],
        [InlineKeyboardButton("➖ Удалить карту", callback_data=f"admin:delete_map:{query.from_user.id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"menu:admin:{query.from_user.id}")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)

async def admin_add_map_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.message.reply_text("Введите название карты для добавления:")
    context.user_data["awaiting_map_add"] = True

async def admin_add_map_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_map_add"):
        return
    map_name = update.message.text.strip()
    if map_name in MAPS:
        await update.message.reply_text("Такая карта уже есть.")
    else:
        MAPS.append(map_name)
        await update.message.reply_text(f"Карта {map_name} добавлена.")
    context.user_data.pop("awaiting_map_add", None)

async def admin_delete_map_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.message.reply_text("Введите название карты для удаления:")
    context.user_data["awaiting_map_delete"] = True

async def admin_delete_map_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_map_delete"):
        return
    map_name = update.message.text.strip()
    if map_name in MAPS:
        MAPS.remove(map_name)
        await update.message.reply_text(f"Карта {map_name} удалена.")
    else:
        await update.message.reply_text("Карта не найдена.")
    context.user_data.pop("awaiting_map_delete", None)

async def admin_manage_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lobbies = []
    for league, mode in LOBBY_CHAT_IDS.keys():
        lobby = await get_lobby(league, mode)
        if lobby:
            lobbies.append((league, mode, lobby))
    if not lobbies:
        await query.edit_message_text("Нет активных лобби.")
        return
    keyboard = []
    for league, mode, lobby in lobbies:
        keyboard.append([
            InlineKeyboardButton(f"Сбросить {mode} {league}", callback_data=f"admin:reset_lobby:{league}:{mode}:{query.from_user.id}")
        ])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"menu:admin:{query.from_user.id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Управление лобби:", reply_markup=reply_markup)

async def admin_reset_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE, parts):
    league = parts[2]
    mode = parts[3]
    await reset_lobby(context, league, mode)
    await update.callback_query.answer("Лобби сброшено.", show_alert=True)

async def admin_ban_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.message.reply_text("Введите ID пользователя или его ник для бана:")
    context.user_data["awaiting_ban_target"] = True

async def admin_unban_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.message.reply_text("Введите ID пользователя или его ник для разбана:")
    context.user_data["awaiting_unban_target"] = True

async def admin_ban_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_ban_target"):
        return
    target = update.message.text.strip()
    user = await find_user_by_nick_or_id(target)
    if not user:
        await update.message.reply_text("Пользователь не найден.")
        return
    if user["user_id"] == await get_creator_id():
        await update.message.reply_text("Нельзя забанить создателя.")
        return
    context.user_data["ban_target_id"] = user["user_id"]
    context.user_data.pop("awaiting_ban_target", None)
    await update.message.reply_text("Введите причину бана (или отправьте '-' для перманентного бана):")
    context.user_data["awaiting_ban_reason"] = True

async def admin_unban_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_unban_target"):
        return
    target = update.message.text.strip()
    user = await find_user_by_nick_or_id(target)
    if not user:
        await update.message.reply_text("Пользователь не найден.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bans WHERE user_id = ?", (user["user_id"],))
        await db.commit()
    await update.message.reply_text("Пользователь разбанен.")
    context.user_data.pop("awaiting_unban_target", None)

async def admin_ban_reason_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_ban_reason"):
        return
    reason = update.message.text.strip()
    target_id = context.user_data.get("ban_target_id")
    if reason == "-":
        banned_until = None  # перманентный бан
    else:
        banned_until = None  # для простоты, можно добавить срок
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO bans (user_id, reason, banned_until)
            VALUES (?, ?, ?)
        """, (target_id, reason, banned_until))
        await db.commit()
    await update.message.reply_text("Пользователь забанен.")
    context.user_data.pop("awaiting_ban_reason", None)
    context.user_data.pop("ban_target_id", None)

async def find_user_by_nick_or_id(identifier: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if identifier.isdigit():
            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (int(identifier),))
            row = await cursor.fetchone()
            if row:
                return dict(row)
        cursor = await db.execute("SELECT * FROM users WHERE nickname = ?", (identifier,))
        row = await cursor.fetchone()
        if row:
            return dict(row)
    return None

async def admin_grant_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.message.reply_text("Введите ID пользователя или ник для выдачи админки:")
    context.user_data["awaiting_grant_target"] = True

async def admin_revoke_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.message.reply_text("Введите ID пользователя или ник для снятия админки:")
    context.user_data["awaiting_revoke_target"] = True

async def admin_grant_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_grant_target"):
        return
    target = update.message.text.strip()
    user = await find_user_by_nick_or_id(target)
    if not user:
        await update.message.reply_text("Пользователь не найден.")
        return
    await update_user(user["user_id"], is_admin=1)
    await update.message.reply_text("Админка выдана.")
    context.user_data.pop("awaiting_grant_target", None)

async def admin_revoke_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_revoke_target"):
        return
    target = update.message.text.strip()
    user = await find_user_by_nick_or_id(target)
    if not user:
        await update.message.reply_text("Пользователь не найден.")
        return
    if user["user_id"] == await get_creator_id():
        await update.message.reply_text("Нельзя снять админку с создателя.")
        return
    await update_user(user["user_id"], is_admin=0)
    await update.message.reply_text("Админка снята.")
    context.user_data.pop("awaiting_revoke_target", None)

# ==================== Основная функция ====================
async def main():
    await init_db()
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    # Обработчик текстовых сообщений (для всех состояний)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    # Обработчик фото (для загрузки аватарки и скриншотов)
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_messages))

    # Планируем автосброс лобби при старте
    for league_type in ["default", "pro"]:
        for mode in ["5x5", "2x2", "1x1"]:
            application.job_queue.run_once(
                reset_lobby_job,
                random.randint(600, 900),
                data=(league_type, mode)
            )

    await application.run_polling(allowed_updates=Update.ALL_TYPES)

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем состояние и вызываем соответствующую функцию
    if context.user_data.get("registration_step"):
        await handle_registration(update, context)
    elif context.user_data.get("awaiting_cancel_reason"):
        await handle_cancel_reason(update, context)
    elif context.user_data.get("awaiting_score"):
        await handle_score_input(update, context)
    elif context.user_data.get("awaiting_ticket_target") or context.user_data.get("awaiting_ticket_text"):
        await handle_ticket_input(update, context)
    elif context.user_data.get("awaiting_ticket_message"):
        await handle_ticket_message_input(update, context)
    elif context.user_data.get("awaiting_map_add"):
        await admin_add_map_input(update, context)
    elif context.user_data.get("awaiting_map_delete"):
        await admin_delete_map_input(update, context)
    elif context.user_data.get("awaiting_ban_target"):
        await admin_ban_input(update, context)
    elif context.user_data.get("awaiting_unban_target"):
        await admin_unban_input(update, context)
    elif context.user_data.get("awaiting_ban_reason"):
        await admin_ban_reason_input(update, context)
    elif context.user_data.get("awaiting_grant_target"):
        await admin_grant_input(update, context)
    elif context.user_data.get("awaiting_revoke_target"):
        await admin_revoke_input(update, context)
    else:
        # Игнорируем или отправляем подсказку
        pass

async def handle_photo_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_avatar"):
        await handle_avatar_upload(update, context)
    elif context.user_data.get("awaiting_screenshot"):
        await handle_screenshot(update, context)

if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
    asyncio.run(main())
