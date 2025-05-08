import discord
import os
import asyncio
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

load_dotenv()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

ytdl_opts = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioquality': 1,
    'outtmpl': 'downloads/%(id)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'quiet': True,
    'logtostderr': False,
    'default_search': 'auto',
}

ffmpeg_opts = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# Cola de reproducción por servidor (guild)
queues = {}

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')

@bot.command()
async def play(ctx, url):
    if not ctx.author.voice:
        await ctx.send("¡Debes unirte a un canal de voz primero!")
        return

    channel = ctx.author.voice.channel
    voice = ctx.voice_client

    if voice and voice.is_connected():
        if voice.channel != channel:
            await voice.move_to(channel)
    else:
        voice = await channel.connect()

    with yt_dlp.YoutubeDL(ytdl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if 'entries' in info:
            info = info['entries'][0]
        audio_formats = [
            f for f in info['formats']
            if f['ext'] in ['mp3', 'm4a', 'opus'] and 'acodec' in f and f['acodec'] != 'none'
        ]
        if not audio_formats:
            await ctx.send("No se encontró un formato de audio válido.")
            return

        title = info.get('title', 'Desconocido')
        audio_url = audio_formats[0]['url']

    if ctx.guild.id not in queues:
        queues[ctx.guild.id] = []

    if not voice.is_playing():
        await ctx.send(f"😈 **Reproduciendo:** {title}")
        voice.play(discord.FFmpegPCMAudio(audio_url, executable=r'D:\!!!!PROGRAMACION\discord\ffmpeg-7.1.1-essentials_build\bin\ffmpeg.exe', **ffmpeg_opts), after=lambda e: check_queue(ctx))
    else:
        queues[ctx.guild.id].append({'title': title, 'url': audio_url})
        await ctx.send(f"🎶 **Añadido a la cola:** {title}")

def check_queue(ctx):
    if queues[ctx.guild.id]:
        next_song = queues[ctx.guild.id].pop(0)
        voice = ctx.voice_client
        voice.play(discord.FFmpegPCMAudio(next_song['url'], executable=r'D:\!!!!PROGRAMACION\discord\ffmpeg-7.1.1-essentials_build\bin\ffmpeg.exe', **ffmpeg_opts), after=lambda e: check_queue(ctx))
        asyncio.run_coroutine_threadsafe(ctx.send(f"😈 **Reproduciendo siguiente canción:** {next_song['title']}"), bot.loop)
    else:
        asyncio.run_coroutine_threadsafe(ctx.send("No hay más canciones en la cola."), bot.loop)

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        ctx.voice_client.stop()
        await ctx.voice_client.disconnect()
        queues[ctx.guild.id] = []  # Limpiar la cola
        await ctx.send("Desconectado del canal de voz y cola vacía.")
    else:
        await ctx.send("No estoy conectado a ningún canal de voz.")

bot.run(os.getenv('TOKEN'))
