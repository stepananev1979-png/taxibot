import logging
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os

# Настройки
BOT_TOKEN = os.getenv("BOT_TOKEN", "8619478031:AAGf1mmtJQgtEGJ9m05hDW16ok7eDD-qijQ")
DRIVERS_CHAT_ID = int(os.getenv("DRIVERS_CHAT_ID", "-1003935717475"))
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "8557230844").split(",")]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Хранилища
orders = {}
order_counter = 0
active_chats = {}      # user_id -> order_id
driver_profiles = {}   # driver_id -> {name, car, plate}
driver_ratings = {}    # driver_id -> [list of ratings]


# ─── СОСТОЯНИЯ ───────────────────────────────

class ClientStates(StatesGroup):
    waiting_from = State()
    waiting_to = State()

class DriverStates(StatesGroup):
    waiting_price = State()
    waiting_car = State()
    waiting_plate = State()


# ─── УТИЛИТЫ ─────────────────────────────────

def driver_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Назначить цену", callback_data=f"price_{order_id}")],
        [InlineKeyboardButton(text="📍 Отправить геолокацию клиенту", callback_data=f"geo_{order_id}")],
        [InlineKeyboardButton(text="🏁 Завершить заказ", callback_data=f"done_{order_id}")]
    ])

def stars_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐1", callback_data=f"rate_{order_id}_1"),
            InlineKeyboardButton(text="⭐2", callback_data=f"rate_{order_id}_2"),
            InlineKeyboardButton(text="⭐3", callback_data=f"rate_{order_id}_3"),
            InlineKeyboardButton(text="⭐4", callback_data=f"rate_{order_id}_4"),
            InlineKeyboardButton(text="⭐5", callback_data=f"rate_{order_id}_5"),
        ]
    ])


# ─── КОМАНДА /start ───────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if message.chat.id == DRIVERS_CHAT_ID:
        return
    await state.clear()
    await message.answer(
        "🚕 *Добро пожаловать в службу такси!*\n\n"
        "Введите адрес *откуда* вас забрать:",
        parse_mode="Markdown"
    )
    await state.set_state(ClientStates.waiting_from)


# ─── КОМАНДА /profile — профиль водителя ─────

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message, state: FSMContext):
    if message.chat.id == DRIVERS_CHAT_ID:
        return
    await state.clear()
    await message.answer(
        "🚗 *Заполните профиль водителя*\n\n"
        "Введите марку и модель автомобиля:\n_(например: Toyota Camry белая)_",
        parse_mode="Markdown"
    )
    await state.set_state(DriverStates.waiting_car)

@dp.message(DriverStates.waiting_car)
async def get_car(message: types.Message, state: FSMContext):
    await state.update_data(car=message.text)
    await message.answer("Введите номер автомобиля:\n_(например: А123БВ)_", parse_mode="Markdown")
    await state.set_state(DriverStates.waiting_plate)

@dp.message(DriverStates.waiting_plate)
async def get_plate(message: types.Message, state: FSMContext):
    data = await state.get_data()
    driver_id = message.from_user.id
    driver_profiles[driver_id] = {
        "name": message.from_user.first_name or "Водитель",
        "car": data["car"],
        "plate": message.text.upper()
    }
    await state.clear()
    await message.answer(
        f"✅ *Профиль сохранён!*\n\n"
        f"🚗 Автомобиль: {data['car']}\n"
        f"🔢 Номер: {message.text.upper()}",
        parse_mode="Markdown"
    )


# ─── КОМАНДА /stats — админ статистика ───────

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return

    total = len(orders)
    done = sum(1 for o in orders.values() if o["status"] == "done")
    active = sum(1 for o in orders.values() if o["status"] == "active")
    new = sum(1 for o in orders.values() if o["status"] == "new")

    drivers_count = len(driver_profiles)

    ratings_text = ""
    for did, ratings in driver_ratings.items():
        profile = driver_profiles.get(did, {})
        name = profile.get("name", f"ID:{did}")
        avg = sum(ratings) / len(ratings)
        ratings_text += f"  👤 {name}: {'⭐' * round(avg)} ({avg:.1f})\n"

    await message.answer(
        f"📊 *Статистика*\n\n"
        f"📦 Всего заказов: {total}\n"
        f"✅ Завершено: {done}\n"
        f"🚖 В процессе: {active}\n"
        f"🆕 Новых: {new}\n\n"
        f"👨‍💼 Водителей с профилем: {drivers_count}\n\n"
        f"⭐ Рейтинги водителей:\n{ratings_text or '  Нет данных'}",
        parse_mode="Markdown"
    )


# ─── ЗАКАЗ КЛИЕНТА ────────────────────────────

@dp.message(ClientStates.waiting_from)
async def get_from_address(message: types.Message, state: FSMContext):
    await state.update_data(from_addr=message.text)
    await message.answer("📍 Отлично! Теперь введите адрес *куда* вас везти:", parse_mode="Markdown")
    await state.set_state(ClientStates.waiting_to)

@dp.message(ClientStates.waiting_to)
async def get_to_address(message: types.Message, state: FSMContext):
    global order_counter
    data = await state.get_data()
    from_addr = data["from_addr"]
    to_addr = message.text

    order_counter += 1
    order_id = order_counter

    orders[order_id] = {
        "client_id": message.from_user.id,
        "from_addr": from_addr,
        "to_addr": to_addr,
        "driver_id": None,
        "status": "new",
        "price": None
    }
    active_chats[message.from_user.id] = order_id
    await state.clear()

    await message.answer(
        f"✅ *Ваш заказ принят!*\n\n"
        f"📍 Откуда: {from_addr}\n"
        f"🏁 Куда: {to_addr}\n\n"
        f"⏳ Ищем водителя, ожидайте...",
        parse_mode="Markdown"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Взять заказ", callback_data=f"take_{order_id}")]
    ])

    await bot.send_message(
        DRIVERS_CHAT_ID,
        f"🚖 *НОВЫЙ ЗАКАЗ #{order_id}*\n\n"
        f"📍 Откуда: {from_addr}\n"
        f"🏁 Куда: {to_addr}\n",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


# ─── ВОДИТЕЛЬ БЕРЁТ ЗАКАЗ ────────────────────

@dp.callback_query(F.data.startswith("take_"))
async def take_order(callback: types.CallbackQuery):
    order_id = int(callback.data.split("_")[1])

    if order_id not in orders:
        await callback.answer("Заказ не найден!", show_alert=True)
        return

    order = orders[order_id]
    if order["status"] != "new":
        await callback.answer("❌ Этот заказ уже взят!", show_alert=True)
        return

    order["status"] = "active"
    order["driver_id"] = callback.from_user.id
    active_chats[callback.from_user.id] = order_id

    profile = driver_profiles.get(callback.from_user.id, {})
    driver_name = profile.get("name") or callback.from_user.first_name or "Водитель"
    car_info = f"\n🚗 {profile['car']} | 🔢 {profile['plate']}" if profile else ""

    await callback.message.edit_text(
        f"🚖 *ЗАКАЗ #{order_id}* — взят ✅\n\n"
        f"📍 Откуда: {order['from_addr']}\n"
        f"🏁 Куда: {order['to_addr']}\n\n"
        f"👤 Водитель: {driver_name}{car_info}",
        parse_mode="Markdown"
    )
    await callback.answer("Вы взяли заказ!")

    await bot.send_message(
        callback.from_user.id,
        f"✅ *Вы взяли заказ #{order_id}*\n\n"
        f"📍 Откуда: {order['from_addr']}\n"
        f"🏁 Куда: {order['to_addr']}\n\n"
        f"Назначьте цену или напишите клиенту.",
        parse_mode="Markdown",
        reply_markup=driver_keyboard(order_id)
    )

    await bot.send_message(
        order["client_id"],
        f"🎉 *Водитель найден!*\n\n"
        f"👤 Водитель: {driver_name}{car_info}\n\n"
        f"Вы можете написать водителю прямо здесь.",
        parse_mode="Markdown"
    )


# ─── ЦЕНА ────────────────────────────────────

@dp.callback_query(F.data.startswith("price_"))
async def ask_price(callback: types.CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split("_")[1])
    await state.update_data(price_order_id=order_id)
    await callback.answer()
    await bot.send_message(
        callback.from_user.id,
        "💰 Введите стоимость поездки (только цифры, например *500*):",
        parse_mode="Markdown"
    )
    await state.set_state(DriverStates.waiting_price)

@dp.message(DriverStates.waiting_price)
async def set_price(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Введите только цифры, например *500*", parse_mode="Markdown")
        return

    data = await state.get_data()
    order_id = data.get("price_order_id")
    await state.clear()

    if not order_id or order_id not in orders:
        await message.answer("Заказ не найден.")
        return

    price = int(message.text)
    orders[order_id]["price"] = price
    order = orders[order_id]

    await message.answer(
        f"✅ Цена назначена: *{price} руб.*",
        parse_mode="Markdown",
        reply_markup=driver_keyboard(order_id)
    )

    await bot.send_message(
        order["client_id"],
        f"💰 *Водитель назначил цену поездки:*\n\n"
        f"*{price} руб.*\n\n"
        f"Если согласны — просто ожидайте водителя.",
        parse_mode="Markdown"
    )


# ─── ГЕОЛОКАЦИЯ ──────────────────────────────

@dp.callback_query(F.data.startswith("geo_"))
async def send_geo_request(callback: types.CallbackQuery):
    await callback.answer()
    await bot.send_message(
        callback.from_user.id,
        "📍 Нажмите на скрепку 📎 внизу → выберите *Геолокация* → отправьте своё местоположение.\n\n"
        "Клиент сразу получит вашу геолокацию на карте!",
        parse_mode="Markdown"
    )


# ─── ПЕРЕПИСКА клиент <-> водитель ───────────

@dp.message()
async def relay_message(message: types.Message, state: FSMContext):
    if message.chat.id == DRIVERS_CHAT_ID:
        return

    user_id = message.from_user.id

    if user_id not in active_chats:
        await message.answer("Напишите /start чтобы заказать такси.")
        return

    order_id = active_chats[user_id]
    if order_id not in orders:
        return

    order = orders[order_id]

    # Клиент → водитель
    if user_id == order["client_id"] and order["driver_id"]:
        if message.location:
            await bot.send_message(order["driver_id"], "📍 *Клиент отправил геолокацию:*", parse_mode="Markdown")
            await bot.send_location(order["driver_id"], latitude=message.location.latitude, longitude=message.location.longitude)
            await message.answer("✉️ Геолокация отправлена водителю")
        elif message.text:
            await bot.send_message(order["driver_id"], f"💬 *Сообщение от клиента:*\n{message.text}", parse_mode="Markdown")
            await message.answer("✉️ Сообщение отправлено водителю")

    # Водитель → клиент
    elif user_id == order["driver_id"]:
        if message.location:
            await bot.send_message(order["client_id"], "📍 *Водитель отправил своё местоположение:*", parse_mode="Markdown")
            await bot.send_location(order["client_id"], latitude=message.location.latitude, longitude=message.location.longitude)
            await message.answer("✉️ Геолокация отправлена клиенту")
        elif message.text:
            await bot.send_message(order["client_id"], f"💬 *Сообщение от водителя:*\n{message.text}", parse_mode="Markdown")
            await message.answer("✉️ Сообщение отправлено клиенту")

    elif user_id == order["client_id"] and not order["driver_id"]:
        await message.answer("⏳ Ожидайте, ищем водителя...")


# ─── ЗАВЕРШЕНИЕ ЗАКАЗА ────────────────────────

@dp.callback_query(F.data.startswith("done_"))
async def done_order(callback: types.CallbackQuery):
    order_id = int(callback.data.split("_")[1])

    if order_id not in orders:
        await callback.answer("Заказ не найден!")
        return

    order = orders[order_id]
    order["status"] = "done"

    active_chats.pop(order["client_id"], None)
    active_chats.pop(order["driver_id"], None)

    price_text = f"\n💰 Стоимость: *{order['price']} руб.*" if order.get("price") else ""

    await callback.message.edit_text(f"✅ Заказ #{order_id} завершён!")
    await callback.answer("Заказ завершён!")

    await bot.send_message(
        order["client_id"],
        f"🏁 *Поездка завершена!*{price_text}\n\n"
        f"Пожалуйста, оцените водителя:",
        parse_mode="Markdown",
        reply_markup=stars_keyboard(order_id)
    )


# ─── ОЦЕНКА ВОДИТЕЛЯ ─────────────────────────

@dp.callback_query(F.data.startswith("rate_"))
async def rate_driver(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    order_id = int(parts[1])
    rating = int(parts[2])

    if order_id not in orders:
        await callback.answer("Заказ не найден!")
        return

    order = orders[order_id]
    driver_id = order["driver_id"]

    if driver_id not in driver_ratings:
        driver_ratings[driver_id] = []
    driver_ratings[driver_id].append(rating)

    avg = sum(driver_ratings[driver_id]) / len(driver_ratings[driver_id])
    stars = "⭐" * rating

    await callback.message.edit_text(
        f"Спасибо за оценку! {stars}\n\nДля нового заказа напишите /start"
    )
    await callback.answer("Оценка сохранена!")

    profile = driver_profiles.get(driver_id, {})
    driver_name = profile.get("name", "Водитель")

    await bot.send_message(
        driver_id,
        f"⭐ *Новая оценка!*\n\n"
        f"Клиент поставил: {stars} ({rating}/5)\n"
        f"Ваш средний рейтинг: {avg:.1f} ⭐",
        parse_mode="Markdown"
    )


# ─── ЗАПУСК ───────────────────────────────────

async def main():
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
