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

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Хранилище заказов в памяти
# orders[order_id] = {client_id, from_addr, to_addr, driver_id, status}
orders = {}
order_counter = 0

# Связь клиент <-> водитель для переписки
# active_chats[user_id] = order_id
active_chats = {}


# Состояния для клиента
class ClientStates(StatesGroup):
    waiting_from = State()
    waiting_to = State()


# ─────────────────────────────────────────────
# КЛИЕНТ
# ─────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    # Если сообщение из группы водителей — игнорируем
    if message.chat.id == DRIVERS_CHAT_ID:
        return

    await message.answer(
        "🚕 *Добро пожаловать в службу такси!*\n\n"
        "Я помогу вам вызвать автомобиль.\n"
        "Введите адрес *откуда* вас забрать:",
        parse_mode="Markdown"
    )
    await state.set_state(ClientStates.waiting_from)


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

    # Сохраняем заказ
    orders[order_id] = {
        "client_id": message.from_user.id,
        "from_addr": from_addr,
        "to_addr": to_addr,
        "driver_id": None,
        "status": "new"
    }
    active_chats[message.from_user.id] = order_id

    await state.clear()

    # Подтверждение клиенту
    await message.answer(
        f"✅ *Ваш заказ принят!*\n\n"
        f"📍 Откуда: {from_addr}\n"
        f"🏁 Куда: {to_addr}\n\n"
        f"⏳ Ищем водителя, ожидайте...",
        parse_mode="Markdown"
    )

    # Кнопка для водителей
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Взять заказ", callback_data=f"take_{order_id}")]
    ])

    # Отправляем заказ в чат водителей
    await bot.send_message(
        DRIVERS_CHAT_ID,
        f"🚖 *НОВЫЙ ЗАКАЗ #{order_id}*\n\n"
        f"📍 Откуда: {from_addr}\n"
        f"🏁 Куда: {to_addr}\n",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


# ─────────────────────────────────────────────
# ВОДИТЕЛЬ — берёт заказ
# ─────────────────────────────────────────────

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

    # Помечаем заказ как взятый
    order["status"] = "active"
    order["driver_id"] = callback.from_user.id
    active_chats[callback.from_user.id] = order_id

    driver_name = callback.from_user.first_name or "Водитель"

    # Обновляем сообщение в чате водителей
    await callback.message.edit_text(
        f"🚖 *ЗАКАЗ #{order_id}* — взят ✅\n\n"
        f"📍 Откуда: {order['from_addr']}\n"
        f"🏁 Куда: {order['to_addr']}\n\n"
        f"👤 Водитель: {driver_name}",
        parse_mode="Markdown"
    )

    await callback.answer("Вы взяли заказ!")

    # Кнопка завершить для водителя — в личку
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏁 Завершить заказ", callback_data=f"done_{order_id}")]
    ])

    await bot.send_message(
        callback.from_user.id,
        f"✅ Вы взяли заказ #{order_id}\n\n"
        f"📍 Откуда: {order['from_addr']}\n"
        f"🏁 Куда: {order['to_addr']}\n\n"
        f"Вы можете написать сообщение клиенту прямо здесь.\n"
        f"Когда довезёте — нажмите кнопку ниже.",
        reply_markup=keyboard
    )

    # Уведомляем клиента
    await bot.send_message(
        order["client_id"],
        f"🎉 *Водитель найден!*\n\n"
        f"👤 Водитель: {driver_name}\n\n"
        f"Вы можете написать водителю прямо здесь — он получит ваше сообщение.",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
# ПЕРЕПИСКА клиент <-> водитель через бота
# ─────────────────────────────────────────────

@dp.message()
async def relay_message(message: types.Message):
    # Игнорируем сообщения из чата водителей
    if message.chat.id == DRIVERS_CHAT_ID:
        return

    user_id = message.from_user.id

    if user_id not in active_chats:
        await message.answer(
            "Напишите /start чтобы заказать такси."
        )
        return

    order_id = active_chats[user_id]
    if order_id not in orders:
        return

    order = orders[order_id]

    # Клиент пишет водителю
    if user_id == order["client_id"] and order["driver_id"]:
        await bot.send_message(
            order["driver_id"],
            f"💬 *Сообщение от клиента:*\n{message.text}",
            parse_mode="Markdown"
        )
        await message.answer("✉️ Сообщение отправлено водителю")

    # Водитель пишет клиенту
    elif user_id == order["driver_id"]:
        await bot.send_message(
            order["client_id"],
            f"💬 *Сообщение от водителя:*\n{message.text}",
            parse_mode="Markdown"
        )
        await message.answer("✉️ Сообщение отправлено клиенту")

    else:
        await message.answer("⏳ Ожидайте, ищем водителя...")


# ─────────────────────────────────────────────
# ЗАВЕРШЕНИЕ ЗАКАЗА
# ─────────────────────────────────────────────

@dp.callback_query(F.data.startswith("done_"))
async def done_order(callback: types.CallbackQuery):
    order_id = int(callback.data.split("_")[1])

    if order_id not in orders:
        await callback.answer("Заказ не найден!")
        return

    order = orders[order_id]
    order["status"] = "done"

    # Убираем из активных чатов
    active_chats.pop(order["client_id"], None)
    active_chats.pop(order["driver_id"], None)

    await callback.message.edit_text(
        f"✅ Заказ #{order_id} завершён!"
    )
    await callback.answer("Заказ завершён!")

    await bot.send_message(
        order["client_id"],
        "🏁 *Поездка завершена!*\n\nСпасибо что воспользовались нашим такси!\n\nДля нового заказа напишите /start",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────

async def main():
    logger.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
