import discord
import os
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

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')

@bot.command()
async def play(ctx, url):
    if not ctx.author.voice:
        await ctx.send("¡Debes unirte a un canal de voz primero!")
        return

    channel = ctx.author.voice.channel
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

        audio_url = audio_formats[0]['url']

    print(f'Reproduciendo: {audio_url}')
    voice.play(discord.FFmpegPCMAudio(audio_url, executable=r'D:\!!!!PROGRAMACION\discord\ffmpeg-7.1.1-essentials_build\bin\ffmpeg.exe', **ffmpeg_opts))

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Desconectado del canal de voz.")
    else:
        await ctx.send("No estoy conectado a ningún canal de voz.")

bot.run(os.getenv('TOKEN'))
