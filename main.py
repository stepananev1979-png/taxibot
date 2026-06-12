import asyncio
import logging

logging.basicConfig(level=logging.INFO)

async def main():
    # Импортируем оба бота
    from bot import dp, bot
    from max_bot import polling as max_polling

    # Запускаем оба в одном event loop
    await asyncio.gather(
        dp.start_polling(bot),
        max_polling()
    )

if __name__ == "__main__":
    asyncio.run(main())
