import os
import asyncio
import discord
from discord.ext import commands
from google import genai
from aiohttp import web
import edge_tts

# 🌐 Минимальный веб-сервер для прохождения проверки Render
async def handle_ping(request):
    return web.Response(text="GeminiBot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# 🤖 Инициализация Gemini и Discord
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

ai_client = genai.Client(api_key=GEMINI_KEY)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="\\", intents=intents)

# 🎙️ Функция озвучивания ответа в голосе
async def speak_in_vc(vc, text):
    short_text = text[:300]
    tts_file = "voice_response.mp3"
    tts = edge_tts.Communicate(short_text, "ru-RU-DmitryNeural")
    await tts.save(tts_file)

    if vc.is_playing():
        vc.stop()
    vc.play(discord.FFmpegPCMAudio(tts_file))

# 📜 Чтение истории сообщений канала (для контекста)
async def get_chat_history(channel, limit=10):
    history = []
    async for msg in channel.history(limit=limit):
        if msg.author.bot and msg.author != bot.user:
            continue
        author_name = msg.author.display_name
        history.append(f"{author_name}: {msg.content}")

    history.reverse()  # Хронологический порядок
    return "\n".join(history)

# 🔌 Команда подключения к голосовому каналу
@bot.command(name="Geminijoin")
async def gemini_join(ctx):
    if ctx.author.voice:
        await ctx.author.voice.channel.connect()
        await ctx.reply("Зашёл в голосовой канал! Теперь я могу отвечать вам голосом.")
    else:
        await ctx.reply("Сначала зайдите в голосовой канал!")

# 🔌 Команда отключения от голосового канала
@bot.command(name="Geminileave")
async def gemini_leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.reply("Вышел из голосового канала.")

# 💬 Основная команда общения \Gemini
@bot.command(name="Gemini")
async def gemini_ask(ctx, *, question: str = None):
    async with ctx.typing():
        try:
            # Собираем последние 10 сообщений чата для памяти
            chat_context = await get_chat_history(ctx.channel, limit=10)

            full_prompt = (
                f"Вот история последних сообщений в этом чате Discord:\n"
                f"{chat_context}\n\n"
                f"Ответь на последний вопрос/сообщение с учётом этой истории."
            )

            response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=full_prompt
            )
            text_reply = response.text

            # Отвечаем прямо с цитированием сообщения пользователя (reply)
            await ctx.reply(text_reply)

            # Если бот в голосовом канале — озвучиваем ответ
            vc = ctx.guild.voice_client
            if vc and vc.is_connected():
                await speak_in_vc(vc, text_reply)

        except Exception as e:
            await ctx.reply(f"Произошла ошибка API: {e}")

# 🏷️ Поддержка обычного упоминания @GeminiBot
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if bot.user.mentioned_in(message) and not message.content.startswith('\\'):
        clean_prompt = message.content.replace(f'<@{bot.user.id}>', '').strip()
        ctx = await bot.get_context(message)
        await gemini_ask(ctx, question=clean_prompt)
        return

    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f"Бот {bot.user} успешно запущен!")
    bot.loop.create_task(start_web_server())

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
