# bot.py
import logging
import os
import json
from datetime import datetime
from threading import Lock

import requests
from aiogram import Bot, Dispatcher, types, executor
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# ========== НастройКА ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise SystemExit("Установите переменную окружения TELEGRAM_TOKEN")

# Координаты Тулы
LAT, LON = 54.1920, 37.6175
TIMEZONE = "Europe/Moscow"

# Файл для хранения последних прогнозов по чату
STORE_FILE = "last_forecasts.json"
_store_lock = Lock()

# Логи
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(bot)

# ========== Клавиатуры ==========
btn_weather = KeyboardButton("🌤 Погода в Туле")
btn_refresh = KeyboardButton("🔁 Обновить")
btn_last = KeyboardButton("🕘 Последнее")
main_kb = ReplyKeyboardMarkup(resize_keyboard=True).add(btn_weather).add(btn_refresh, btn_last)

def make_inline_kb():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("Сегодня", callback_data="day:0"),
        InlineKeyboardButton("Завтра", callback_data="day:1")
    )
    kb.add(InlineKeyboardButton("🔁 Обновить", callback_data="refresh"))
    return kb

# ========== Расшифровка weathercode ==========
WEATHER_CODES = {
    0: "Ясно", 1: "Преимущественно ясно", 2: "Переменная облачность", 3: "Пасмурно",
    45: "Туман", 48: "Изморозь", 51: "Морось лёгкая", 53: "Морось", 55: "Морось интенсивная",
    61: "Дождь лёгкий", 63: "Дождь", 65: "Дождь сильный", 71: "Снег лёгкий", 73: "Снег",
    75: "Снег сильный", 80: "Ливни лёгкие", 81: "Ливни", 82: "Ливни сильные",
    95: "Гроза", 96: "Гроза с градом", 99: "Гроза с сильным градом"
}

# ========== Работа с Open-Meteo ==========
def get_weather():
    """Запрашивает ежедневный прогноз (daily) у open-meteo."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode,windspeed_10m_max",
        "timezone": TIMEZONE
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def _format_temp(val):
    try:
        return f"{val:+.0f}°C"
    except Exception:
        return "—"

def _build_day_block_from_daily(daily, idx, label):
    """
    daily — это dict как из open-meteo daily
    idx — индекс дня (0 для сегодня, 1 для завтра)
    """
    dates = daily.get("time", [])
    tmax = daily.get("temperature_2m_max", [])
    tmin = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_sum", [])
    wcode = daily.get("weathercode", [])
    wind = daily.get("windspeed_10m_max", [])

    if idx >= len(dates):
        return f"*{label}*\nНет данных.\n"

    # дата
    try:
        date_str = datetime.strptime(dates[idx], "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        date_str = dates[idx]

    desc = WEATHER_CODES.get(wcode[idx], "—") if idx < len(wcode) else "—"
    tmax_val = _format_temp(tmax[idx]) if idx < len(tmax) else "—"
    tmin_val = _format_temp(tmin[idx]) if idx < len(tmin) else "—"
    precip_val = f"{precip[idx]} мм" if idx < len(precip) else "—"
    wind_val = f"{wind[idx]} м/с" if idx < len(wind) else "—"

    return (
        f"*{label} — {date_str}*\n"
        f"{desc}\n"
        f"Макс: {tmax_val}, мин: {tmin_val}\n"
        f"Осадки: {precip_val}\n"
        f"Ветер: {wind_val}\n"
    )

def build_full_forecast_message(api_data):
    """Собирает полный текст (сегодня + завтра) из ответа API."""
    daily = api_data.get("daily", {})
    today = _build_day_block_from_daily(daily, 0, "Сегодня")
    tomorrow = _build_day_block_from_daily(daily, 1, "Завтра")
    return today + "\n" + tomorrow + f"\n\nДанные: Open-Meteo.com"

# ========== Простой персист (JSON) ==========
def _load_store():
    if not os.path.exists(STORE_FILE):
        return {}
    try:
        with open(STORE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("Не удалось загрузить файл store, вернём пустой словарь.")
        return {}

def _save_store(store):
    try:
        with open(STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Не удалось сохранить файл store.")

def save_last_forecast(chat_id: int, api_data: dict):
    """Сохраняет последний прогноз (по chat_id) — сохраняем сырые daily и текст."""
    with _store_lock:
        store = _load_store()
        daily = api_data.get("daily", {})
        text = build_full_forecast_message(api_data)
        store[str(chat_id)] = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "daily": daily,
            "text": text
        }
        _save_store(store)

def get_last_forecast(chat_id: int):
    with _store_lock:
        store = _load_store()
        return store.get(str(chat_id))

# ========== Обработчики ==========
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    text = (
        "Привет! Я присылаю прогноз погоды в Туле на сегодня и завтра.\n\n"
        "Используй кнопки или напиши /weather."
    )
    await message.answer(text, reply_markup=main_kb)

@dp.message_handler(commands=["weather"])
async def cmd_weather(message: types.Message):
    await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
    try:
        api_data = get_weather()
        save_last_forecast(message.chat.id, api_data)
        text = build_full_forecast_message(api_data)
        await message.answer(text, parse_mode="Markdown", reply_markup=main_kb, reply_markup_inline := None)
        # отправляем inline-кнопки отдельным сообщением (чтобы клавиатура осталась видимой)
        await message.answer("Выберите:", reply_markup=make_inline_kb())
    except Exception:
        logger.exception("Ошибка получения прогноза (команда /weather)")
        await message.answer("Не удалось получить прогноз. Попробуйте позже.", reply_markup=main_kb)

@dp.message_handler(lambda m: m.text == "🌤 Погода в Туле")
async def btn_weather_handler(message: types.Message):
    # полностью аналогично /weather
    await cmd_weather(message)

@dp.message_handler(lambda m: m.text == "🔁 Обновить")
async def btn_refresh_handler(message: types.Message):
    await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
    try:
        api_data = get_weather()
        save_last_forecast(message.chat.id, api_data)
        text = build_full_forecast_message(api_data)
        await message.answer("Обновлено:\n\n" + text, parse_mode="Markdown", reply_markup=main_kb)
        await message.answer("Выберите:", reply_markup=make_inline_kb())
    except Exception:
        logger.exception("Ошибка при обновлении (кнопка Обновить)")
        await message.answer("Не удалось обновить прогноз. Попробуйте позже.", reply_markup=main_kb)

@dp.message_handler(lambda m: m.text == "🕘 Последнее")
async def btn_last_handler(message: types.Message):
    last = get_last_forecast(message.chat.id)
    if last:
        ts = last.get("ts", "—")
        text = last.get("text", "—")
        await message.answer(f"Последнее сохранённое: {ts}\n\n" + text, parse_mode="Markdown", reply_markup=main_kb)
        await message.answer("Выберите:", reply_markup=make_inline_kb())
    else:
        await message.answer("Нет сохранённого прогноза для этого чата. Нажми «🌤 Погода в Туле» чтобы получить текущий прогноз.", reply_markup=main_kb)

# ========== Callback queries (inline buttons) ==========
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("day:"))
async def cb_day_callback(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)  # убираем кружочек ожидания
    chat_id = callback_query.message.chat.id
    _, idx_str = callback_query.data.split(":", 1)
    try:
        idx = int(idx_str)
    except Exception:
        await bot.send_message(chat_id, "Некорректная кнопка.")
        return

    last = get_last_forecast(chat_id)
    if last:
        daily = last.get("daily", {})
        # отправляем только нужный блок
        block = _build_day_block_from_daily(daily, idx, "Сегодня" if idx == 0 else "Завтра")
        await bot.send_message(chat_id, block, parse_mode="Markdown", reply_markup=main_kb)
    else:
        # если нет сохранённого — получаем новый
        try:
            api_data = get_weather()
            save_last_forecast(chat_id, api_data)
            daily = api_data.get("daily", {})
            block = _build_day_block_from_daily(daily, idx, "Сегодня" if idx == 0 else "Завтра")
            await bot.send_message(chat_id, block, parse_mode="Markdown", reply_markup=main_kb)
        except Exception:
            logger.exception("Ошибка получения прогноза для callback day")
            await bot.send_message(chat_id, "Не удалось получить прогноз.", reply_markup=main_kb)

@dp.callback_query_handler(lambda c: c.data == "refresh")
async def cb_refresh(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    chat_id = callback_query.message.chat.id
    try:
        api_data = get_weather()
        save_last_forecast(chat_id, api_data)
        text = build_full_forecast_message(api_data)
        await bot.send_message(chat_id, "Обновлено:\n\n" + text, parse_mode="Markdown", reply_markup=main_kb)
    except Exception:
        logger.exception("Ошибка получения прогноза для callback refresh")
        await bot.send_message(chat_id, "Не удалось обновить прогноз.", reply_markup=main_kb)

@dp.message_handler()
async def fallback(message: types.Message):
    """Фоллбек для любых других сообщений."""
    await message.answer("Напиши /weather или используй кнопки.", reply_markup=main_kb)

# ========== Запуск ==========
if __name__ == "__main__":
    logger.info("Bot started")
    executor.start_polling(dp, skip_updates=True)
