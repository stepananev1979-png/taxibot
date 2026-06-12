import asyncio
import logging
import os
import aiohttp
from datetime import datetime

# Настройки
MAX_TOKEN = os.getenv("MAX_TOKEN", "f9LHodD0cOIaJNGoUYKSYuCQvJBYTkPKvf-Apng6RHg-XRcfRq78dAdalAzv2A0LlofhyQSq3OcESloHr9O_")
TG_BOT_TOKEN = os.getenv("BOT_TOKEN", "8619478031:AAGf1mmtJQgtEGJ9m05hDW16ok7eDD-qijQ")
DRIVERS_CHAT_ID = int(os.getenv("DRIVERS_CHAT_ID", "-1003935717475"))

MAX_API = "https://botapi.max.ru"
TG_API = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Хранилища
max_orders = {}        # order_id -> данные заказа
max_order_counter = 0
max_active_chats = {}  # user_id -> order_id
max_marker = 0         # маркер для получения новых сообщений


# ─── ОТПРАВКА В МАКС ─────────────────────────

async def send_max_message(session, user_id, text):
    url = f"{MAX_API}/messages"
    headers = {"Authorization": MAX_TOKEN, "Content-Type": "application/json"}
    payload = {
        "recipient": {"user_id": user_id},
        "body": {"text": text}
    }
    try:
        async with session.post(url, json=payload, headers=headers) as r:
            return await r.json()
    except Exception as e:
        logger.error(f"Ошибка отправки в Макс: {e}")


# ─── ОТПРАВКА В TELEGRAM ──────────────────────

async def send_tg_message(session, chat_id, text, reply_markup=None):
    url = f"{TG_API}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with session.post(url, json=payload) as r:
            return await r.json()
    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {e}")


# ─── ПОЛУЧЕНИЕ ЦЕНЫ ──────────────────────────

def get_price_max(from_addr, to_addr):
    try:
        from openpyxl import load_workbook
        import re

        def normalize(addr):
            addr = addr.lower().strip()
            addr = re.sub(r'\s+\d+[а-яa-z]?$', '', addr)
            addr = re.sub(r',.*', '', addr)
            addr = re.sub(r'^(ул\.|улица|пер\.|переулок)\s+', '', addr)
            return addr.strip()

        wb = load_workbook("price.xlsx", read_only=True)
        ws = wb.active
        prices = {}
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i < 3:
                continue
            if row[0] and row[2] and row[4]:
                f = str(row[0]).strip().lower()
                t = str(row[2]).strip().lower()
                prices[(f, t)] = (int(row[4]), int(row[5]) if row[5] else int(row[4]) + 50)

        fn = normalize(from_addr)
        tn = normalize(to_addr)

        now = datetime.now()
        is_night = now.hour < 6 or (now.hour == 6 and now.minute < 30)

        if (fn, tn) in prices:
            d, n = prices[(fn, tn)]
            price = n if is_night else d
            night = " (ночной тариф 🌙)" if is_night else ""
            return f"💰 Предварительная стоимость: {price} руб.{night}\nТочную стоимость укажет водитель."

        for (f, t), (d, n) in prices.items():
            if (fn in f or f in fn) and (tn in t or t in tn):
                price = n if is_night else d
                night = " (ночной тариф 🌙)" if is_night else ""
                return f"💰 Предварительная стоимость: {price} руб.{night}\nТочную стоимость укажет водитель."

    except Exception as e:
        logger.error(f"Ошибка прайса: {e}")

    return "💰 Предварительная стоимость: от 150 руб.\nТочную стоимость укажет водитель."


# ─── ОБРАБОТКА СООБЩЕНИЙ ─────────────────────

async def handle_message(session, message):
    global max_order_counter

    user_id = message.get("sender", {}).get("user_id")
    text = message.get("body", {}).get("text", "").strip()

    if not user_id or not text:
        return

    # Команда /start
    if text.lower() in ["/start", "start"]:
        max_active_chats.pop(user_id, None)
        await send_max_message(session, user_id,
            "🚕 Добро пожаловать в службу такси!\n\n"
            "Введите адрес ОТКУДА вас забрать:"
        )
        max_active_chats[user_id] = {"step": "waiting_from"}
        return

    # Шаг 1 — откуда
    if user_id in max_active_chats and max_active_chats[user_id].get("step") == "waiting_from":
        max_active_chats[user_id] = {"step": "waiting_to", "from": text}
        await send_max_message(session, user_id,
            f"📍 Откуда: {text}\n\nТеперь введите адрес КУДА вас везти:"
        )
        return

    # Шаг 2 — куда
    if user_id in max_active_chats and max_active_chats[user_id].get("step") == "waiting_to":
        from_addr = max_active_chats[user_id]["from"]
        to_addr = text

        max_order_counter += 1
        order_id = f"M{max_order_counter}"  # M = из Макса

        max_orders[order_id] = {
            "client_id": user_id,
            "from_addr": from_addr,
            "to_addr": to_addr,
            "driver_id": None,
            "status": "new",
            "source": "max",
            "date": datetime.now().strftime("%d.%m.%Y %H:%M")
        }
        max_active_chats[user_id] = {"step": "active", "order_id": order_id}

        price_text = get_price_max(from_addr, to_addr)

        await send_max_message(session, user_id,
            f"✅ Ваш заказ принят!\n\n"
            f"📍 Откуда: {from_addr}\n"
            f"🏁 Куда: {to_addr}\n\n"
            f"{price_text}\n\n"
            f"⏳ Ищем водителя, ожидайте..."
        )

        # Отправляем заказ в чат водителей Telegram
        keyboard = {
            "inline_keyboard": [[
                {"text": f"✅ Взять заказ (MAX)", "callback_data": f"takemax_{order_id}_{user_id}_{from_addr[:20]}_{to_addr[:20]}"}
            ]]
        }
        await send_tg_message(
            session,
            DRIVERS_CHAT_ID,
            f"🚖 *НОВЫЙ ЗАКАЗ {order_id}* (из Макса 📱)\n\n"
            f"📍 Откуда: {from_addr}\n"
            f"🏁 Куда: {to_addr}\n",
            reply_markup=keyboard
        )

        # Таймер 5 минут
        asyncio.create_task(notify_no_driver_max(session, order_id, user_id))
        return

    # Переписка с водителем
    if user_id in max_active_chats and max_active_chats[user_id].get("step") == "active":
        order_id = max_active_chats[user_id].get("order_id")
        if order_id and order_id in max_orders:
            order = max_orders[order_id]
            if order.get("driver_tg_id"):
                # Отправляем сообщение водителю в Telegram
                await send_tg_message(
                    session,
                    order["driver_tg_id"],
                    f"💬 *Сообщение от клиента (MAX):*\n{text}"
                )
            else:
                await send_max_message(session, user_id, "⏳ Ожидайте, водитель ещё не найден...")
        return

    # Если ничего не подошло
    await send_max_message(session, user_id,
        "Напишите /start чтобы заказать такси 🚕"
    )


# ─── УВЕДОМЛЕНИЕ ЕСЛИ НЕ ВЗЯЛИ 5 МИНУТ ──────

async def notify_no_driver_max(session, order_id, user_id):
    await asyncio.sleep(300)
    if order_id not in max_orders:
        return
    if max_orders[order_id]["status"] != "new":
        return
    await send_max_message(session, user_id,
        "⚠️ Пока никто не взял ваш заказ.\n\n"
        "Попробуйте написать /start и сделать новый заказ.\n"
        "Приносим извинения за ожидание! 🙏"
    )


# ─── ОСНОВНОЙ ЦИКЛ — POLLING ─────────────────

async def polling():
    global max_marker
    headers = {"Authorization": MAX_TOKEN}

    async with aiohttp.ClientSession() as session:
        logger.info("MAX бот запущен!")

        while True:
            try:
                params = {"timeout": 30}
                if max_marker:
                    params["marker"] = max_marker

                url = f"{MAX_API}/updates"
                async with session.get(url, headers=headers, params=params) as r:
                    data = await r.json()

                updates = data.get("updates", [])
                if data.get("marker"):
                    max_marker = data["marker"]

                for update in updates:
                    if update.get("update_type") == "message_created":
                        await handle_message(session, update.get("message", {}))

            except Exception as e:
                logger.error(f"Ошибка polling: {e}")
                await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(polling())
