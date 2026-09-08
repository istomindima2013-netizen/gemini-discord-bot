import os
import asyncio
import discord
from discord.ext import commands
from google import genai
from aiohttp import web
import edge_tts

# Веб-сервер для Render (Keep-Alive)
async def handle_ping(request):
    return web.Response(text="GeminiBot is active!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

ai_client = genai.Client(api_key=GEMINI_KEY)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="\\", intents=intents)

# Хранилище диалогов (истории контекста) для каждого текстового канала
channel_chats = {}

def get_chat_session(channel_id):
    """Создает или возвращает существующий чат с памятью контекста"""
    if channel_id not in channel_chats:
        channel_chats[channel_id] = ai_client.chats.create(model='gemini-2.5-flash')
    return channel_chats[channel_id]

async def speak_in_vc(vc, text):
    short_text = text[:300]
    tts_file = "voice_response.mp3"
    tts = edge_tts.Communicate(short_text, "ru-RU-DmitryNeural")
    await tts.save(tts_file)
    
    if vc.is_playing():
        vc.stop()
    vc.play(discord.FFmpegPCMAudio(tts_file))

@bot.command(name="Geminijoin")
async def gemini_join(ctx):
    if ctx.author.voice:
        await ctx.author.voice.channel.connect()
        await ctx.send("Зашёл в голосовой канал!")
    else:
        await ctx.send("Сначала зайди в голосовой канал!")

@bot.command(name="Geminileave")
async def gemini_leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Вышел из голосового канала.")

@bot.command(name="Geminireset")
async def gemini_reset(ctx):
    """Очистить память диалога в текущем канале"""
    channel_id = ctx.channel.id
    if channel_id in channel_chats:
        del channel_chats[channel_id]
    await ctx.send("История диалога в этом канале сброшена!")

@bot.command(name="Gemini")
async def gemini_ask(ctx, *, question: str = None):
    if not question:
        await ctx.send("Напиши вопрос после команды!")
        return

    async with ctx.typing():
        try:
            # Берем чат канала — он помнит все предыдущие сообщения!
            chat = get_chat_session(ctx.channel.id)
            response = chat.send_message(question)
            text_reply = response.text

            await ctx.send(text_reply)

            # Озвучка, если бот в голосовом канале
            vc = ctx.guild.voice_client
            if vc and vc.is_connected():
                await speak_in_vc(vc, text_reply)

        except Exception as e:
            await ctx.send(f"Ошибка API: {e}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Обработка упоминания @GeminiBot
    if bot.user.mentioned_in(message) and not message.content.startswith('\\'):
        clean_prompt = message.content.replace(f'<@{bot.user.id}>', '').strip()
        if clean_prompt:
            ctx = await bot.get_context(message)
            await gemini_ask(ctx, question=clean_prompt)
            return

    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f"Бот {bot.user} запущен и готов к работе!")
    bot.loop.create_task(start_web_server())

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
