# imports
import asyncio
import os
import sys
import logging
import time
from datetime import datetime, timedelta
from collections import defaultdict

import matplotlib.pyplot as plt  # для графиков
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.dispatcher.router import Router
from dotenv import load_dotenv

# --- ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")  # строка, сконвертируем в int
DEFAULT_MODE = os.getenv("DEFAULT_MODE", "forward")  # forward | file
SPAM_COOLDOWN = int(os.getenv("SPAM_COOLDOWN", "30"))  # секунды
LOG_FILE = os.getenv("LOG_FILE", "suggestions.log")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in .env")

if not ADMIN_ID:
    raise ValueError("ADMIN_ID not found in .env")

ADMIN_ID = int(ADMIN_ID)

# --- ЛОГИРОВАНИЕ В ФАЙЛ ---
logging.basicConfig(
    filename='my_bot.log',  # Логируем в файл my_bot.log
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)
logger.info("Bot file loaded. Will start soon...")

# --- ИНИЦИАЛИЗАЦИЯ DISPATCHER И ROUTER ---
dp = Dispatcher()
router = Router()
dp.include_router(router)

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
current_mode = DEFAULT_MODE

# user_last_time: для кулдауна (user_id -> float)
user_last_time = {}

# user_mapping: чтобы блокировать по username (username -> user_id)
user_mapping = {}

# black_list: set(user_id)
black_list = set()

# suggestion_data: список всех (datetime, user_id, username, text) для статистики
suggestion_data = []


# -------------------------------------------------------------
# ФУНКЦИИ ВСПОМОГАТЕЛЬНЫЕ
# -------------------------------------------------------------
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

async def ensure_admin(message: Message) -> bool:
    """Проверка, что пользователь — админ, иначе ответ."""
    if message.from_user.id != ADMIN_ID:
        await message.answer("You are not an admin.")
        return False
    return True

def is_in_cooldown(user_id: int) -> bool:
    """Проверяем, не отправлял ли пользователь сообщение недавно (SPAM_COOLDOWN)."""
    now_ts = time.time()
    last_ts = user_last_time.get(user_id, 0)
    return (now_ts - last_ts) < SPAM_COOLDOWN

def update_cooldown(user_id: int):
    """Обновляем время последнего сообщения пользователя."""
    user_last_time[user_id] = time.time()

def log_suggestion_to_file(user_id: int, username: str, text: str):
    """Запись предложения в локальный текстовый файл (LOG_FILE)."""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{ts_str}] user_id={user_id}, username={username}: {text}\n")

def add_suggestion_stat(user_id: int, username: str, text: str):
    """Сохраняем запись в suggestion_data для последующей статистики."""
    now = datetime.now()
    suggestion_data.append((now, user_id, username, text))

def generate_stats_plot():
    """
    Генерируем PNG с графиком (кол-во предложений по дням за последние 7 дней).
    Возвращаем путь к файлу.
    """
    now = datetime.now()
    days_back = 7
    counts_by_date = defaultdict(int)

    for (ts, uid, uname, txt) in suggestion_data:
        if ts >= (now - timedelta(days=days_back)):
            date_str = ts.strftime("%Y-%m-%d")
            counts_by_date[date_str] += 1

    # Сортируем по дате
    sorted_dates = sorted(counts_by_date.keys())
    x = sorted_dates
    y = [counts_by_date[d] for d in sorted_dates]

    if not x:  # Если нет данных
        x = [now.strftime("%Y-%m-%d")]
        y = [0]

    plt.figure(figsize=(6, 4))
    plt.title("Suggestions in the last 7 days")
    plt.bar(x, y, color='skyblue')
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()

    filename = "stats_plot.png"
    plt.savefig(filename)
    plt.close()
    return filename


# -------------------------------------------------------------
# КОМАНДЫ ДЛЯ АДМИНА
# -------------------------------------------------------------
@router.message(Command("shutdown"))
async def cmd_shutdown(message: Message):
    """Команда /shutdown: останавливает бота (только для админа)."""
    if not await ensure_admin(message):
        return
    await message.answer("Shutting down the bot...")
    # НЕ логируем /shutdown, чтобы не засорять логи
    sys.exit(0)


@router.message(Command("mode"))
async def cmd_mode(message: Message):
    """Управление режимом работы бота (forward|file)."""
    global current_mode  # Объявляем global до использования

    if not await ensure_admin(message):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            f"Current mode: {current_mode}\nUsage: /mode forward or /mode file"
        )
        return

    new_mode = args[1].strip().lower()
    if new_mode in ["forward", "file"]:
        current_mode = new_mode
        await message.answer(f"Mode changed to {current_mode}")
        logger.info("Admin changed mode to %s", current_mode)
    else:
        await message.answer("Unknown mode. Use 'forward' or 'file'.")


@router.message(Command("block"))
async def cmd_block(message: Message):
    """/block <username> - внести в чёрный список по username (храним user_id)."""
    if not await ensure_admin(message):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Usage: /block <username>")
        return

    username_to_block = args[1].strip().lstrip("@")
    if username_to_block in user_mapping:
        uid = user_mapping[username_to_block]
        black_list.add(uid)
        await message.answer(f"User @{username_to_block} (id={uid}) has been blocked.")
        logger.info("Admin blocked user_id=%s (username=%s)", uid, username_to_block)
    else:
        await message.answer(
            f"User @{username_to_block} not found in memory.\n"
            f"They might not have interacted with the bot yet."
        )


@router.message(Command("unblock"))
async def cmd_unblock(message: Message):
    """/unblock <username> - убрать из чёрного списка."""
    if not await ensure_admin(message):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Usage: /unblock <username>")
        return

    username_to_unblock = args[1].strip().lstrip("@")
    if username_to_unblock in user_mapping:
        uid = user_mapping[username_to_unblock]
        if uid in black_list:
            black_list.remove(uid)
            await message.answer(f"User @{username_to_unblock} (id={uid}) has been unblocked.")
            logger.info("Admin unblocked user_id=%s (username=%s)", uid, username_to_unblock)
        else:
            await message.answer(f"User @{username_to_unblock} is not blocked.")
    else:
        await message.answer(f"User @{username_to_unblock} not found in memory.")


@router.message(Command("blocked"))
async def cmd_blocked(message: Message):
    """Показать всех заблокированных пользователей."""
    if not await ensure_admin(message):
        return

    if not black_list:
        await message.answer("No blocked users.")
        return

    # Инвертируем user_mapping, чтобы user_id -> username
    rev_map = {v: k for k, v in user_mapping.items()}

    lines = ["Blocked users:"]
    for uid in black_list:
        uname = rev_map.get(uid, None)
        if uname:
            lines.append(f" - @{uname} (id={uid})")
        else:
            lines.append(f" - (id={uid}) (unknown username)")
    await message.answer("\n".join(lines))


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Показать статистику + график (после отправки картинку удаляем)."""
    if not await ensure_admin(message):
        return

    total = len(suggestion_data)
    unique_users = len(set(d[1] for d in suggestion_data))

    now = datetime.now()
    last_24h = [d for d in suggestion_data if (now - d[0]) < timedelta(hours=24)]
    last_7d = [d for d in suggestion_data if (now - d[0]) < timedelta(days=7)]
    last_30d = [d for d in suggestion_data if (now - d[0]) < timedelta(days=30)]

    text_stats = (
        f"**Stats**:\n"
        f"Total suggestions: {total}\n"
        f"Unique users: {unique_users}\n"
        f"Last 24 hours: {len(last_24h)}\n"
        f"Last 7 days: {len(last_7d)}\n"
        f"Last 30 days: {len(last_30d)}"
    )

    await message.answer(text_stats, parse_mode="Markdown")

    plot_file = generate_stats_plot()
    try:
        # Отправим картинку
        await message.answer_photo(photo=FSInputFile(plot_file))
    except Exception as e:
        logger.error("Failed to send stats plot: %s", e)
        await message.answer("Error sending stats plot.")
    finally:
        # Удаляем файл после отправки, если существует
        if os.path.exists(plot_file):
            os.remove(plot_file)


# -------------------------------------------------------------
# ОСНОВНОЙ ХЕНДЛЕР (ПРЕДЛОЖЕНИЯ)
# -------------------------------------------------------------
@router.message(Command("start"))
async def cmd_start(message: Message):
    """
    /start — приветствие и сохранение username->user_id.
    Полезно, чтобы block/unblock работал, даже если пользователь не отправляет текст.
    """
    user_id = message.from_user.id
    username = message.from_user.username or f"user{user_id}"
    user_mapping[username] = user_id  # Сохраняем в память

    await message.answer(
        "Hello! Send me your suggestion, and I'll handle it.\n"
        "Admins can use: /mode, /shutdown, /block, /unblock, /blocked, /stats."
    )


@router.message()
async def handle_suggestion(message: Message):
    user_id = message.from_user.id
    # Если есть username, используем его (например, "someuser"), иначе "user12345"
    username = message.from_user.username or f"user{user_id}"

    # Обновляем словарь user_mapping (если есть)
    user_mapping[username] = user_id

    # Блокировка, кулдаун, добавление в статистику — как и раньше
    ...

    if current_mode == "forward":
        # Формируем имя для показа админу
        if message.from_user.username:
            display_name = f"@{message.from_user.username}"
        else:
            # Если нет username, показываем имя + фамилию
            # full_name = "FirstName LastName" (aiogram в 3.x это message.from_user.full_name)
            display_name = message.from_user.full_name  

        # Создаём текст, который отправим админу
        text_for_admin = (
            f"From {display_name} (id={user_id}):\n"
            f"{message.text}"
        )
        
        try:
            # Отправляем админу уже готовый текст
            await message.bot.send_message(ADMIN_ID, text_for_admin)
            logger.info("Forwarded suggestion from %s (id=%s) to admin", username, user_id)
        except Exception as e:
            logger.error("Failed to forward suggestion: %s", e)
    else:
        # Режим записи в файл (file)
        log_suggestion_to_file(user_id, username, message.text)
        logger.info("Added suggestion from %s (id=%s) to file", username, user_id)
    
    # Ответ пользователю
    await message.answer("Your suggestion has been received. Thank you!")

# -------------------------------------------------------------
# MAIN
# -------------------------------------------------------------
async def main():
    logger.info("=== SUGGESTION BOT STARTING ===")
    bot = Bot(token=BOT_TOKEN)
    await dp.start_polling(bot)
    logger.info("=== SUGGESTION BOT STOPPED ===")


if __name__ == "__main__":
    asyncio.run(main())
