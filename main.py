import discord
import os
import asyncio
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

load_dotenv()

sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET")
))

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='cojons', intents=intents)

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

queues = {}

@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user.name}')

def get_spotify_title(url):
    try:
        if "track" in url:
            track_id = url.split("/")[-1].split("?")[0]
            track = sp.track(track_id)
            return f"{track['name']} {track['artists'][0]['name']}"
    except Exception as e:
        print(f"Error al obtener datos de Spotify: {e}")
    return None

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

    if "open.spotify.com" in url:
        search_query = get_spotify_title(url)
        if not search_query:
            await ctx.send("❌ No se pudo obtener información de la canción de Spotify.")
            return
        url = f"ytsearch:{search_query}"

    with yt_dlp.YoutubeDL(ytdl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if 'entries' in info:
            info = info['entries'][0]
        audio_formats = [
            f for f in info['formats']
            if f['ext'] in ['mp3', 'm4a', 'opus'] and 'acodec' in f and f['acodec'] != 'none'
        ]
        if not audio_formats:
            await ctx.send("❌ No se encontró un formato de audio válido.")
            return

        title = info.get('title', 'Desconocido')
        audio_url = audio_formats[0]['url']

    if ctx.guild.id not in queues:
        queues[ctx.guild.id] = []

    if not voice.is_playing():
        await ctx.send(f"😈 **Reproduciendo:** {title}")
        ffmpeg_path = os.path.join(os.getcwd(), 'ffmpeg-7.1.1-essentials_build', 'bin', 'ffmpeg.exe')
        voice.play(discord.FFmpegPCMAudio(audio_url, **ffmpeg_opts))
        # voice.play(discord.FFmpegPCMAudio(audio_url, executable=ffmpeg_path, **ffmpeg_opts), after=lambda e: check_queue(ctx))
    else:
        queues[ctx.guild.id].append({'title': title, 'url': audio_url})
        await ctx.send(f"🎶 **Añadido a la cola:** {title}")

def check_queue(ctx):
    if queues[ctx.guild.id]:
        next_song = queues[ctx.guild.id].pop(0)
        voice = ctx.voice_client
        ffmpeg_path = os.path.join(os.getcwd(), 'ffmpeg-7.1.1-essentials_build', 'bin', 'ffmpeg.exe')
        voice.play(discord.FFmpegPCMAudio(next_song['url'], **ffmpeg_opts))
        # voice.play(discord.FFmpegPCMAudio(next_song['url'], executable=ffmpeg_path, **ffmpeg_opts), after=lambda e: check_queue(ctx))
        asyncio.run_coroutine_threadsafe(ctx.send(f"😈 **Reproduciendo siguiente canción:** {next_song['title']}"), bot.loop)
    else:
        asyncio.run_coroutine_threadsafe(ctx.send("No hay más canciones en la cola."), bot.loop)

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        ctx.voice_client.stop()
        await ctx.voice_client.disconnect()
        queues[ctx.guild.id] = []
        await ctx.send("🛑 Desconectado del canal de voz y cola vacía.")
    else:
        await ctx.send("❌ No estoy conectado a ningún canal de voz.")

@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Saltando a la siguiente canción...")
    else:
        await ctx.send("❌ No se está reproduciendo ninguna canción.")

bot.run(os.getenv('TOKEN'))
