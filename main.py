import os
import asyncio
import re
import discord
from discord.ext import commands
from google import genai
from google.genai import types
from groq import Groq
from aiohttp import web
import edge_tts

# 🌐 Web-сервер для Render Health Check
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

# 🤖 Инициализация
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GROQ_KEY = os.getenv("GROQ_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

ai_client = genai.Client(api_key=GEMINI_KEY)
groq_client = Groq(api_key=GROQ_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="\\", intents=intents)

active_dialogs = {}
session_chats = {}  # Временная память голосовой сессии

# 🎙️ Функция озвучивания
async def speak_in_vc(vc, text):
    short_text = text[:350]
    filename = "response.mp3"
    tts = edge_tts.Communicate(short_text, "ru-RU-DmitryNeural")
    await tts.save(filename)
    
    if vc.is_playing():
        vc.stop()
        
    vc.play(discord.FFmpegPCMAudio(filename))
    while vc.is_playing():
        await asyncio.sleep(0.1)

# 📜 Чтение истории текстового чата (для контекста текстовых команд)
async def get_chat_history(channel, limit=10):
    history = []
    async for msg in channel.history(limit=limit):
        if msg.author.bot and msg.author != bot.user:
            continue
        history.append(f"{msg.author.display_name}: {msg.content}")
    history.reverse()
    return "\n".join(history)

# Обработка записи голоса
async def process_user_audio(sink, channel, vc):
    if not sink.audio_data:
        return

    guild_id = vc.guild.id

    for user_id, audio in sink.audio_data.items():
        audio_filename = f"temp_{user_id}.wav"
        with open(audio_filename, "wb") as f:
            f.write(audio.file.read())

        try:
            # Расшифровка голоса через Groq
            with open(audio_filename, "rb") as file:
                transcription = groq_client.audio.transcriptions.create(
                    file=(audio_filename, file.read()),
                    model="whisper-large-v3",
                    language="ru",
                    response_format="text"
                )

            user_text = str(transcription).strip()
            user_text_lower = user_text.lower()

            # Проверка активации по слову «Гемини» / «Gemini»
            if "гемини" in user_text_lower or "gemini" in user_text_lower:
                clean_prompt = re.sub(r'(?i)\b(гемини|gemini)\b', '', user_text).strip(" ,.!?")
                if not clean_prompt:
                    clean_prompt = "Привет!"

                await channel.send(f"🗣️ **Вы:** {user_text}")

                # Создание чат-сессии, если не создана
                if guild_id not in session_chats:
                    session_chats[guild_id] = ai_client.chats.create(
                        model='gemini-2.5-flash',
                        config=types.GenerateContentConfig(
                            system_instruction="Ты собеседник в голосовом чате Discord. Отвечай кратко и емко (1-3 предложения)."
                        )
                    )

                chat_session = session_chats[guild_id]
                response = chat_session.send_message(clean_prompt)
                bot_reply = response.text

                await channel.send(f"🤖 **Gemini:** {bot_reply}")
                await speak_in_vc(vc, bot_reply)

        except Exception as e:
            print(f"Ошибка обработки голоса: {e}")
        finally:
            if os.path.exists(audio_filename):
                os.remove(audio_filename)

    if active_dialogs.get(guild_id, False):
        await start_listening_cycle(vc, channel)

async def start_listening_cycle(vc, channel):
    if not vc.is_connected() or not active_dialogs.get(vc.guild.id, False):
        return

    sink = discord.sinks.WaveSink()
    vc.start_record(sink, lambda s, *args: asyncio.run_coroutine_threadsafe(process_user_audio(s, channel, vc), bot.loop))
    
    await asyncio.sleep(5)
    
    if vc.is_recording:
        vc.stop_record()

# ================= КОМАНДЫ =================

# 1️⃣ Зайти в ГК + включить голос + начать память
@bot.command(name="Geminijoin")
async def gemini_join(ctx):
    if not ctx.author.voice:
        await ctx.reply("Сначала зайди в голосовой канал!")
        return

    vc = ctx.guild.voice_client
    if not vc:
        vc = await ctx.author.voice.channel.connect()

    guild_id = ctx.guild.id
    active_dialogs[guild_id] = True

    # Инициализация чистой памяти для этого разговора
    session_chats[guild_id] = ai_client.chats.create(
        model='gemini-2.5-flash',
        config=types.GenerateContentConfig(
            system_instruction="Ты собеседник в голосовом чате Discord. Отвечай кратко и емко (1-3 предложения)."
        )
    )

    await ctx.reply("🎙️ Зашёл в голосовой канал! Говори: *«Гемини, [твой вопрос]»*.")
    await start_listening_cycle(vc, ctx.channel)

# 2️⃣ Выйти из ГК + сбросить память
@bot.command(name="Geminileave")
async def gemini_leave(ctx):
    guild_id = ctx.guild.id
    active_dialogs[guild_id] = False

    # Стираем память разговора
    if guild_id in session_chats:
        del session_chats[guild_id]

    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.reply("Вышел из голосового канала. Память разговора очищена.")

# 3️⃣ Текстовый вопрос / общение
@bot.command(name="Gemini")
async def gemini_ask(ctx, *, question: str = None):
    if not question:
        await ctx.reply("Задай вопрос после команды, например: `\\Gemini Какая погода?`")
        return

    async with ctx.typing():
        try:
            chat_context = await get_chat_history(ctx.channel, limit=10)
            
            full_prompt = (
                f"История чата Discord:\n{chat_context}\n\n"
                f"Ответь на вопрос пользователя: {question}"
            )

            response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=full_prompt
            )
            text_reply = response.text

            await ctx.reply(text_reply)

            # Если бот сидит в ГК — продублировать ответ голосом
            vc = ctx.guild.voice_client
            if vc and vc.is_connected():
                await speak_in_vc(vc, text_reply)

        except Exception as e:
            await ctx.reply(f"Ошибка API: {e}")

# Обработка упоминаний @GeminiBot
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
    print(f"Бот {bot.user} запущен!")
    bot.loop.create_task(start_web_server())

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
