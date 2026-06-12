import asyncio
import logging

logging.basicConfig(level=logging.INFO)

async def main():
    from bot import dp, bot
    import max_bot

    await asyncio.gather(
        dp.start_polling(bot, handle_signals=False),
        max_bot.polling()
    )

if __name__ == "__main__":
    asyncio.run(main())
