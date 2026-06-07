import logging
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
from datetime import datetime

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
active_chats = {}       # user_id -> order_id
driver_profiles = {}    # driver_id -> {name, car, plate}
driver_ratings = {}     # driver_id -> [list of ratings]
client_history = {}     # client_id -> [list of orders]
drivers_on_shift = set()  # множество ID водителей на смене
all_drivers = {}          # driver_id -> имя (все кто писал боту)


# ─── СОСТОЯНИЯ ───────────────────────────────

class ClientStates(StatesGroup):
    waiting_from = State()
    waiting_to = State()

class DriverStates(StatesGroup):
    waiting_price = State()
    waiting_car = State()
    waiting_plate = State()


# ─── КЛАВИАТУРЫ ──────────────────────────────

def driver_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Назначить цену", callback_data=f"price_{order_id}")],
        [InlineKeyboardButton(text="📍 Отправить геолокацию клиенту", callback_data=f"geo_{order_id}")],
        [InlineKeyboardButton(text="🏁 Завершить заказ", callback_data=f"done_{order_id}")]
    ])

def client_active_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Отправить мою геолокацию водителю", callback_data=f"cgeo_{order_id}")]
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


# ─── УВЕДОМЛЕНИЕ ЕСЛИ ЗАКАЗ НЕ ВЗЯЛИ ────────

async def notify_no_driver(order_id: int):
    await asyncio.sleep(300)  # 5 минут
    if order_id not in orders:
        return
    order = orders[order_id]
    if order["status"] != "new":
        return

    await bot.send_message(
        order["client_id"],
        "⚠️ *Пока никто не взял ваш заказ.*\n\n"
        "Попробуйте:\n"
        "— подождать ещё немного\n"
        "— написать /start и сделать новый заказ\n\n"
        "Приносим извинения за ожидание! 🙏",
        parse_mode="Markdown"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"⚠️ *Заказ #{order_id} никто не взял 5 минут!*\n\n"
                f"📍 Откуда: {order['from_addr']}\n"
                f"🏁 Куда: {order['to_addr']}\n\n"
                f"Требуется внимание!",
                parse_mode="Markdown"
            )
        except:
            pass


# ─── /start — для клиентов ───────────────────

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


# ─── /driver — для водителей ─────────────────

@dp.message(Command("driver"))
async def cmd_driver(message: types.Message, state: FSMContext):
    if message.chat.id == DRIVERS_CHAT_ID:
        return
    await state.clear()

    user_id = message.from_user.id
    name = message.from_user.first_name or f"ID:{user_id}"

    # Добавляем в список водителей
    all_drivers[user_id] = name

    await message.answer(
        "🚗 *Вы зарегистрированы как водитель!*\n\n"
        "Диспетчер поставит вас на смену.\n"
        "После этого вы сможете брать заказы.\n\n"
        "Заполните профиль: /profile",
        parse_mode="Markdown"
    )

    # Уведомляем админа
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🚗 *Новый водитель написал боту:*\n\n"
                f"Имя: {name}\n"
                f"ID: `{user_id}`\n\n"
                f"Поставить на смену:\n"
                f"`/on {user_id}`",
                parse_mode="Markdown"
            )
        except:
            pass


# ─── КОМАНДЫ СМЕН (только для админа) ────────

@dp.message(Command("on"))
async def shift_on(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "⚠️ Укажите ID водителя.\n"
            "Пример: `/on 123456789`\n\n"
            "Список водителей: /drivers",
            parse_mode="Markdown"
        )
        return

    try:
        driver_id = int(parts[1])
    except ValueError:
        await message.answer("⚠️ ID должен быть числом.")
        return

    drivers_on_shift.add(driver_id)
    name = all_drivers.get(driver_id, f"ID:{driver_id}")

    await message.answer(f"✅ Водитель *{name}* поставлен на смену!", parse_mode="Markdown")

    try:
        await bot.send_message(
            driver_id,
            "✅ *Вы поставлены на смену!*\n\n"
            "Теперь вы можете брать заказы.\n"
            "Следите за чатом водителей.",
            parse_mode="Markdown"
        )
    except:
        await message.answer("⚠️ Не удалось уведомить водителя — он не писал боту.")


@dp.message(Command("off"))
async def shift_off(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "⚠️ Укажите ID водителя.\n"
            "Пример: `/off 123456789`\n\n"
            "Список водителей: /drivers",
            parse_mode="Markdown"
        )
        return

    try:
        driver_id = int(parts[1])
    except ValueError:
        await message.answer("⚠️ ID должен быть числом.")
        return

    drivers_on_shift.discard(driver_id)
    name = all_drivers.get(driver_id, f"ID:{driver_id}")

    await message.answer(f"🔴 Водитель *{name}* снят со смены!", parse_mode="Markdown")

    try:
        await bot.send_message(
            driver_id,
            "🔴 *Ваша смена завершена.*\n\n"
            "Вы больше не можете брать заказы.\n"
            "До следующей смены!",
            parse_mode="Markdown"
        )
    except:
        pass


@dp.message(Command("drivers"))
async def list_drivers(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return

    if not all_drivers:
        await message.answer("👤 Пока никто не писал боту.")
        return

    text = "👨‍💼 *Список водителей:*\n\n"
    for driver_id, name in all_drivers.items():
        status = "🟢 на смене" if driver_id in drivers_on_shift else "🔴 не на смене"
        profile = driver_profiles.get(driver_id, {})
        car_info = f" | 🚗 {profile['car']}" if profile.get("car") else ""
        ratings = driver_ratings.get(driver_id, [])
        rating_info = f" | ⭐{sum(ratings)/len(ratings):.1f}" if ratings else ""
        text += f"👤 *{name}*{car_info}{rating_info}\n"
        text += f"   ID: `{driver_id}` — {status}\n"
        text += f"   /on {driver_id} | /off {driver_id}\n\n"

    await message.answer(text, parse_mode="Markdown")


# ─── /history — история заказов клиента ──────

@dp.message(Command("history"))
async def cmd_history(message: types.Message):
    if message.chat.id == DRIVERS_CHAT_ID:
        return

    client_id = message.from_user.id
    history = client_history.get(client_id, [])

    if not history:
        await message.answer(
            "📋 У вас пока нет завершённых поездок.\n\n"
            "Напишите /start чтобы заказать такси!"
        )
        return

    text = "📋 *Ваши последние поездки:*\n\n"
    for i, ride in enumerate(reversed(history[-10:]), 1):
        price_text = f"💰 {ride['price']} руб." if ride.get("price") else "💰 цена не указана"
        rating_text = f"⭐ {ride['rating']}/5" if ride.get("rating") else "⭐ без оценки"
        text += (
            f"*{i}. Заказ #{ride['order_id']}*\n"
            f"📍 {ride['from_addr']} → {ride['to_addr']}\n"
            f"{price_text} | {rating_text}\n"
            f"🕐 {ride['date']}\n\n"
        )

    await message.answer(text, parse_mode="Markdown")


# ─── /profile — профиль водителя ─────────────

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


# ─── /stats — статистика админа ──────────────

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return

    total = len(orders)
    done = sum(1 for o in orders.values() if o["status"] == "done")
    active = sum(1 for o in orders.values() if o["status"] == "active")
    new = sum(1 for o in orders.values() if o["status"] == "new")
    on_shift = len(drivers_on_shift)

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
        f"🟢 Водителей на смене: {on_shift}\n"
        f"👨‍💼 Всего водителей: {len(all_drivers)}\n\n"
        f"⭐ Рейтинги:\n{ratings_text or '  Нет данных'}",
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
        "price": None,
        "date": datetime.now().strftime("%d.%m.%Y %H:%M")
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

    asyncio.create_task(notify_no_driver(order_id))


# ─── ВОДИТЕЛЬ БЕРЁТ ЗАКАЗ ────────────────────

@dp.callback_query(F.data.startswith("take_"))
async def take_order(callback: types.CallbackQuery):
    order_id = int(callback.data.split("_")[1])
    driver_id = callback.from_user.id

    # Проверка смены
    if driver_id not in drivers_on_shift:
        await callback.answer(
            "⛔ Вы не на смене!\nОбратитесь к диспетчеру.",
            show_alert=True
        )
        return

    if order_id not in orders:
        await callback.answer("Заказ не найден!", show_alert=True)
        return

    order = orders[order_id]
    if order["status"] != "new":
        await callback.answer("❌ Этот заказ уже взят!", show_alert=True)
        return

    order["status"] = "active"
    order["driver_id"] = driver_id
    active_chats[driver_id] = order_id

    profile = driver_profiles.get(driver_id, {})
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
        driver_id,
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
        f"Вы можете написать водителю или отправить геолокацию.",
        parse_mode="Markdown",
        reply_markup=client_active_keyboard(order_id)
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
        f"*{price} руб.*",
        parse_mode="Markdown"
    )


# ─── ГЕОЛОКАЦИЯ ВОДИТЕЛЯ → КЛИЕНТУ ──────────

@dp.callback_query(F.data.startswith("geo_"))
async def send_geo_request(callback: types.CallbackQuery):
    await callback.answer()
    await bot.send_message(
        callback.from_user.id,
        "📍 Нажмите на скрепку 📎 внизу → выберите *Геолокация* → отправьте своё местоположение.\n\n"
        "Клиент сразу получит вашу геолокацию на карте!",
        parse_mode="Markdown"
    )


# ─── ГЕОЛОКАЦИЯ КЛИЕНТА → ВОДИТЕЛЮ ──────────

@dp.callback_query(F.data.startswith("cgeo_"))
async def client_geo_request(callback: types.CallbackQuery):
    await callback.answer()
    await bot.send_message(
        callback.from_user.id,
        "📍 Нажмите на скрепку 📎 внизу → выберите *Геолокация* → отправьте своё местоположение.\n\n"
        "Водитель получит вашу геолокацию на карте!",
        parse_mode="Markdown"
    )


# ─── ПЕРЕПИСКА клиент <-> водитель ───────────

@dp.message()
async def relay_message(message: types.Message, state: FSMContext):
    if message.chat.id == DRIVERS_CHAT_ID:
        return

    user_id = message.from_user.id

    if user_id not in active_chats:
        await message.answer(
            "Напишите /start чтобы заказать такси.\n"
            "Напишите /history чтобы посмотреть историю поездок."
        )
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

    client_id = order["client_id"]
    if client_id not in client_history:
        client_history[client_id] = []
    client_history[client_id].append({
        "order_id": order_id,
        "from_addr": order["from_addr"],
        "to_addr": order["to_addr"],
        "price": order.get("price"),
        "rating": rating,
        "date": order.get("date", "—")
    })

    await callback.message.edit_text(
        f"Спасибо за оценку! {stars}\n\n"
        f"Для новой поездки — /start\n"
        f"История поездок — /history"
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
