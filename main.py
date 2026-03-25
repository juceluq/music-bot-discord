import discord
import os
import asyncio
import random
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

load_dotenv()

try:
    discord.opus.load_opus("libopus.so.0")
except Exception:
    pass

sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET")
))

# -- Configuracion yt-dlp ----------------------------------------------------

_COOKIE_FILE = os.path.join(os.path.dirname(__file__), "cookies.txt")

YTDL_OPTS: dict = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "logtostderr": False,
    "default_search": "ytsearch",
    "js_runtimes": {"node": {}},
}

if os.path.exists(_COOKIE_FILE):
    YTDL_OPTS["cookiefile"] = _COOKIE_FILE
    print(f"[yt-dlp] Usando cookies.txt")

import shutil

# En Railway/Linux ffmpeg está en PATH; en Windows usamos el build local
_local_ffmpeg = os.path.join(os.path.dirname(__file__), "ffmpeg-7.1.1-essentials_build", "bin", "ffmpeg.exe")
FFMPEG_PATH = _local_ffmpeg if os.path.isfile(_local_ffmpeg) else (shutil.which("ffmpeg") or "ffmpeg")

# -- Estado por servidor -----------------------------------------------------

guild_queues: dict[int, list[dict]] = {}
guild_current: dict[int, dict] = {}
guild_fetch_tasks: dict[int, asyncio.Task] = {}


def get_queue(guild_id: int) -> list[dict]:
    if guild_id not in guild_queues:
        guild_queues[guild_id] = []
    return guild_queues[guild_id]


async def _eager_fetch_worker(guild_id: int):
    """Resuelve en background todas las canciones sin URL de la cola."""
    try:
        while True:
            queue = get_queue(guild_id)
            idx = next((i for i, s in enumerate(queue) if "url" not in s), -1)
            if idx == -1:
                return  # toda la cola ya está resuelta
            target = queue[idx]
            q = target.get("search_query") or target["webpage_url"]
            fetched = await fetch_song(q)
            # Verificar que el slot sigue siendo el mismo antes de escribir
            if fetched and idx < len(get_queue(guild_id)) and get_queue(guild_id)[idx] is target:
                get_queue(guild_id)[idx] = {**target, **fetched}
            await asyncio.sleep(0.2)
    finally:
        guild_fetch_tasks.pop(guild_id, None)


def _start_eager_fetch(guild_id: int):
    task = guild_fetch_tasks.get(guild_id)
    if task is None or task.done():
        guild_fetch_tasks[guild_id] = asyncio.create_task(_eager_fetch_worker(guild_id))


def _cancel_eager_fetch(guild_id: int):
    task = guild_fetch_tasks.pop(guild_id, None)
    if task and not task.done():
        task.cancel()


# -- Bot ---------------------------------------------------------------------

intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True


class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="++", intents=intents)

    async def setup_hook(self):
        guild_id = os.getenv("GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"Slash commands sincronizados al servidor {guild_id} (instantaneo)")
        else:
            await self.tree.sync()
            print("Slash commands sincronizados globalmente (puede tardar hasta 1h)")

    async def on_ready(self):
        print(f"Conectado como {self.user.name}")


bot = MusicBot()

# -- Helpers de Spotify -------------------------------------------------------

def _track_query(track: dict) -> str:
    return f"{track['name']} {track['artists'][0]['name']}"


def get_spotify_track_query(url: str) -> str | None:
    try:
        track_id = url.split("/track/")[-1].split("?")[0]
        return _track_query(sp.track(track_id))
    except Exception as e:
        print(f"Error Spotify track: {e}")
    return None


def get_spotify_album_queries(url: str) -> list[str] | None:
    try:
        album_id = url.split("/album/")[-1].split("?")[0]
        queries: list[str] = []
        page = sp.album_tracks(album_id, limit=50)
        while True:
            for track in page["items"]:
                if track and track.get("name"):
                    queries.append(_track_query(track))
            if not page.get("next"):
                break
            page = sp.next(page)
        return queries or None
    except Exception as e:
        print(f"Error Spotify album: {e}")
    return None


def get_spotify_playlist_queries(url: str) -> list[str] | None:
    try:
        playlist_id = url.split("/playlist/")[-1].split("?")[0]
        queries: list[str] = []
        offset = 0
        while True:
            page = sp.playlist_items(
                playlist_id, offset=offset, limit=100,
                fields="items(track(name,artists)),next"
            )
            for item in page["items"]:
                track = item.get("track")
                if track and track.get("name"):
                    queries.append(_track_query(track))
            if not page.get("next"):
                break
            offset += 100
        return queries or None
    except Exception as e:
        print(f"Error Spotify playlist: {e}")
    return None


# -- Helpers de yt-dlp --------------------------------------------------------

async def fetch_song(query: str) -> dict | None:
    loop = asyncio.get_event_loop()
    try:
        with yt_dlp.YoutubeDL(YTDL_OPTS) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(query, download=False)
            )
    except Exception as e:
        print(f"yt-dlp error: {e}")
        return None
    if not info:
        return None
    if "entries" in info:
        info = info["entries"][0]
    if not info:
        return None

    formats = info.get("formats", [])
    audio_only = [
        f for f in formats
        if f.get("acodec", "none") != "none"
        and f.get("vcodec", "none") == "none"
        and f.get("url")
    ]
    if not audio_only:
        audio_only = [f for f in formats if f.get("acodec", "none") != "none" and f.get("url")]
    if not audio_only:
        return None

    audio_only.sort(key=lambda f: f.get("tbr") or f.get("abr") or 0, reverse=True)
    best = audio_only[0]

    return {
        "title":        info.get("title", "Desconocido"),
        "webpage_url":  info.get("webpage_url", query),
        "thumbnail":    info.get("thumbnail"),
        "duration":     info.get("duration", 0),
        "url":          best["url"],
        "http_headers": best.get("http_headers", info.get("http_headers", {})),
    }


async def fetch_yt_playlist(url: str) -> list[dict] | None:
    loop = asyncio.get_event_loop()
    opts = {"extract_flat": True, "quiet": True, "noplaylist": False}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = await loop.run_in_executor(
            None, lambda: ydl.extract_info(url, download=False)
        )
    if not info or "entries" not in info:
        return None

    entries: list[dict] = []
    for entry in info["entries"]:
        if not entry:
            continue
        vid_url = entry.get("url") or entry.get("webpage_url") or ""
        if not vid_url.startswith("http"):
            vid_id = entry.get("id")
            if not vid_id:
                continue
            vid_url = f"https://www.youtube.com/watch?v={vid_id}"
        entries.append({
            "title":       entry.get("title", "Desconocida"),
            "webpage_url": vid_url,
            "thumbnail":   entry.get("thumbnail"),
            "duration":    entry.get("duration", 0),
        })
    return entries or None


# -- Audio source -------------------------------------------------------------

def make_audio_source(song: dict) -> discord.FFmpegPCMAudio:
    return discord.FFmpegPCMAudio(
        song["url"],
        executable=FFMPEG_PATH,
        before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        options="-vn",
    )


# -- Embeds --------------------------------------------------------------------

def make_embed(
    title: str,
    description: str,
    color: discord.Color = discord.Color.purple(),
    thumbnail: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed

PAGE_SIZE = 10


def format_duration(seconds: int | float | None) -> str:
    if not seconds:
        return "?:??"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _song_link(song: dict) -> str:
    url = song.get("webpage_url", "")
    if url.startswith("ytsearch:"):
        return song.get("title", "Desconocida")
    return f"[{song.get('title', 'Desconocida')}]({url})"

# -- L�gica de cola ------------------------------------------------------------

async def play_next_async(
    text_channel: discord.abc.Messageable,
    voice: discord.VoiceClient,
    guild_id: int,
    notify: bool = False,
    _skip_depth: int = 0,
):
    if _skip_depth >= 10:
        await text_channel.send(embed=make_embed(
            "Error", "Se saltaron 10 canciones seguidas sin poder reproducir ninguna.", discord.Color.red()
        ))
        return

    queue = get_queue(guild_id)
    if not queue:
        guild_current.pop(guild_id, None)
        return

    song = queue.pop(0)
    guild_current[guild_id] = song

    if "url" not in song:
        query = song.get("search_query") or song["webpage_url"]
        fetched = await fetch_song(query)
        if not fetched:
            print(f"[skip] No se encontro audio para: {song.get('title', query)}")
            await play_next_async(text_channel, voice, guild_id, notify=notify, _skip_depth=_skip_depth + 1)
            return
        song = {**song, **fetched}
        guild_current[guild_id] = song

    # Pre-fetch la siguiente cancion en background para que este lista a tiempo
    async def prefetch_next():
        if not queue:
            return
        nxt = queue[0]
        if "url" in nxt:
            return
        q = nxt.get("search_query") or nxt["webpage_url"]
        fetched = await fetch_song(q)
        if fetched and queue and queue[0] is nxt:  # evita race: solo escribe si sigue siendo el mismo item
            queue[0] = {**nxt, **fetched}

    asyncio.create_task(prefetch_next())

    def after(error: Exception | None):
        if error:
            print(f"[ffmpeg error] {song.get('title', '?')}: {error}")
        asyncio.run_coroutine_threadsafe(
            play_next_async(text_channel, voice, guild_id, notify=False),
            bot.loop,
        )

    voice.play(make_audio_source(song), after=after)

    if notify:
        await text_channel.send(embed=make_embed(
            "Reproduciendo",
            f"**[{song['title']}]({song['webpage_url']})**",
            thumbnail=song.get("thumbnail"),
        ))


def play_next(text_channel, voice, guild_id):
    asyncio.run_coroutine_threadsafe(
        play_next_async(text_channel, voice, guild_id),
        bot.loop,
    )


# -- View: cargar mas tracks de Spotify ---------------------------------------

class LoadMoreView(discord.ui.View):
    def __init__(self, guild_id: int, remaining: list[str]):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.remaining = remaining

    @discord.ui.button(label="Cargar 100 mas", style=discord.ButtonStyle.primary)
    async def load_more(self, interaction: discord.Interaction, button: discord.ui.Button):
        batch = self.remaining[:100]
        self.remaining = self.remaining[100:]

        queue = get_queue(self.guild_id)
        for item in batch:
            if isinstance(item, dict):
                queue.append(item)
            else:
                queue.append({
                    "title":        item,
                    "webpage_url":  f"ytsearch:{item}",
                    "search_query": f"ytsearch:{item}",
                    "thumbnail":    None,
                    "duration":     0,
                })

        if not self.remaining:
            button.disabled = True
            self.stop()

        await interaction.response.edit_message(
            embed=make_embed(
                "Canciones anadidas",
                f"Se anadieron **{len(batch)}** canciones a la cola.\n" + (
                    f"Quedan **{len(self.remaining)}** por cargar."
                    if self.remaining else
                    "Todas las canciones estan en la cola."
                ),
                color=discord.Color.green(),
            ),
            view=self if self.remaining else None,
        )


# -- View: cola paginada -------------------------------------------------------

class QueueView(discord.ui.View):
    def __init__(self, guild_id: int, page: int = 0):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.page = page
        self._refresh_buttons()

    def _total_pages(self) -> int:
        return max(1, (len(get_queue(self.guild_id)) + PAGE_SIZE - 1) // PAGE_SIZE)

    def _refresh_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self._total_pages() - 1

    def build_embed(self) -> discord.Embed:
        queue   = get_queue(self.guild_id)
        current = guild_current.get(self.guild_id)
        total   = len(queue)
        pages   = self._total_pages()

        embed = discord.Embed(title="🎶 Cola de reproducción", color=discord.Color.purple())

        if current:
            dur = format_duration(current.get("duration"))
            embed.add_field(
                name="▶️ Reproduciendo ahora",
                value=f"**{_song_link(current)}** `{dur}`",
                inline=False,
            )
            if current.get("thumbnail"):
                embed.set_thumbnail(url=current["thumbnail"])

        start      = self.page * PAGE_SIZE
        page_songs = queue[start : start + PAGE_SIZE]

        if page_songs:
            lines = []
            for i, song in enumerate(page_songs, start + 1):
                dur = format_duration(song.get("duration"))
                lines.append(f"`{i}.` {_song_link(song)} `{dur}`")
            embed.add_field(name="En cola", value="\n".join(lines), inline=False)
        elif not current:
            embed.description = "La cola está vacía."

        embed.set_footer(text=f"Página {self.page + 1}/{pages} · {total} canción{'es' if total != 1 else ''} en cola")
        return embed

    @discord.ui.button(label="◄", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="►", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self._total_pages() - 1, self.page + 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


# -- ++sync (solo owner) -------------------------------------------------------

@bot.command(name="sync")
@commands.is_owner()
async def sync_cmd(ctx: commands.Context):
    guild = discord.Object(id=ctx.guild.id)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    await ctx.send("Slash commands sincronizados en este servidor.")


# -- Slash commands ------------------------------------------------------------

async def process_single(
    item: str,
    queue: list,
    results: list,
) -> None:
    """Procesa una URL/busqueda individual y la añade a queue y results."""
    item = item.strip()
    if not item:
        return

    # Spotify playlist
    if "spotify.com" in item and "/playlist/" in item:
        queries = get_spotify_playlist_queries(item)
        if not queries:
            results.append(("error", f"No se pudo cargar playlist Spotify: {item}"))
            return
        first_batch = queries[:100]
        remaining   = queries[100:]
        for q in first_batch:
            queue.append({
                "title":        q,
                "webpage_url":  f"ytsearch:{q}",
                "search_query": f"ytsearch:{q}",
                "thumbnail":    None,
                "duration":     0,
            })
        results.append(("spotify_playlist", first_batch, remaining))
        return

    # Spotify album
    if "spotify.com" in item and "/album/" in item:
        queries = get_spotify_album_queries(item)
        if not queries:
            results.append(("error", f"No se pudo cargar album Spotify: {item}"))
            return
        first_batch = queries[:100]
        remaining   = queries[100:]
        for q in first_batch:
            queue.append({
                "title":        q,
                "webpage_url":  f"ytsearch:{q}",
                "search_query": f"ytsearch:{q}",
                "thumbnail":    None,
                "duration":     0,
            })
        results.append(("spotify_playlist", first_batch, remaining))
        return

    # Spotify track
    if "spotify.com" in item and "/track/" in item:
        query = get_spotify_track_query(item)
        if not query:
            results.append(("error", f"No se pudo obtener track Spotify: {item}"))
            return
        item = f"ytsearch:{query}"

    # YouTube playlist
    elif "youtube.com" in item and "list=" in item:
        entries = await fetch_yt_playlist(item)
        if not entries:
            results.append(("error", f"No se pudo cargar playlist YouTube: {item}"))
            return
        first_batch = entries[:100]
        remaining   = entries[100:]
        for entry in first_batch:
            queue.append(entry)
        results.append(("yt_playlist", first_batch, remaining))
        return

    # Busqueda o URL individual
    elif not item.startswith("http"):
        item = f"ytsearch:{item}"

    song = await fetch_song(item)
    if not song:
        results.append(("error", f"No se encontro audio: {item}"))
        return

    queue.append(song)
    results.append(("song", song))


@bot.tree.command(
    name="cojonsplay",
    description="Reproduce canciones/playlists de YouTube o Spotify. Separa varias URLs con espacio.",
)
@app_commands.describe(busqueda="Una o varias URLs/nombres separados por espacio")
async def play(interaction: discord.Interaction, busqueda: str):
    if not interaction.user.voice:
        await interaction.response.send_message("Debes unirte a un canal de voz primero.", ephemeral=True)
        return

    await interaction.response.defer()

    channel = interaction.user.voice.channel
    voice = interaction.guild.voice_client
    if voice and voice.is_connected():
        if voice.channel != channel:
            await voice.move_to(channel)
    else:
        voice = await channel.connect()

    guild_id = interaction.guild_id
    queue = get_queue(guild_id)

    # Dividir por espacios, pero respetar URLs (que no contienen espacios)
    items = busqueda.split()
    # Reagrupar: si un token no empieza por http y el anterior tampoco, son parte del mismo nombre
    merged: list[str] = []
    buf = ""
    for token in items:
        if token.startswith("http"):
            if buf.strip():
                merged.append(buf.strip())
                buf = ""
            merged.append(token)
        else:
            buf += (" " if buf else "") + token
    if buf.strip():
        merged.append(buf.strip())

    was_playing = voice.is_playing()
    results: list = []

    # Procesar en paralelo si hay varias
    await asyncio.gather(*[process_single(item, queue, results) for item in merged])

    # Lanzar worker que resuelve URLs/duraciones de canciones pendientes
    _start_eager_fetch(guild_id)

    if not was_playing and queue:
        await play_next_async(interaction.channel, voice, guild_id, notify=True)

    # Construir respuesta resumida
    songs_added   = [r for r in results if r[0] == "song"]
    yt_playlists  = [r for r in results if r[0] == "yt_playlist"]
    sp_playlists  = [r for r in results if r[0] == "spotify_playlist"]
    errors        = [r for r in results if r[0] == "error"]

    lines: list[str] = []

    for r in songs_added:
        s = r[1]
        lines.append(f"🎶 **[{s['title']}]({s['webpage_url']})**")

    views_to_send: list[LoadMoreView] = []
    for r in yt_playlists:
        _, first_batch, remaining = r
        lines.append(f"🎵 Playlist YouTube: **{len(first_batch)}** canciones añadidas." +
                     (f" Quedan **{len(remaining)}** por cargar." if remaining else ""))
        if remaining:
            views_to_send.append(LoadMoreView(guild_id, remaining))

    for r in sp_playlists:
        _, first_batch, remaining = r
        lines.append(f"🎵 Playlist Spotify: **{len(first_batch)}** canciones añadidas." +
                     (f" Quedan **{len(remaining)}** por cargar." if remaining else ""))
        if remaining:
            views_to_send.append(LoadMoreView(guild_id, remaining))

    for r in errors:
        lines.append(f"❌ {r[1]}")

    if not lines:
        await interaction.followup.send("No se encontro ningún audio válido.")
        return

    title = "▶️ Reproduciendo" if not was_playing else "🎶 Añadido a la cola"
    if views_to_send:
        await interaction.followup.send(
            embed=make_embed(title, "\n".join(lines)),
            view=views_to_send[0],
        )
        for view in views_to_send[1:]:
            await interaction.followup.send(
                embed=make_embed("Cargar mas canciones de Spotify", f"Quedan **{len(view.remaining)}** canciones."),
                view=view,
            )
    else:
        await interaction.followup.send(
            embed=make_embed(title, "\n".join(lines)),
        )


@bot.tree.command(name="cojonskip", description="Salta la cancion actual")
async def skip(interaction: discord.Interaction):
    voice = interaction.guild.voice_client
    if voice and voice.is_playing():
        voice.stop()
        await interaction.response.send_message("Cancion saltada.")
    else:
        await interaction.response.send_message("No se esta reproduciendo nada.", ephemeral=True)


@bot.tree.command(name="cojonsstop", description="Detiene la musica, vacia la cola y desconecta el bot")
async def stop(interaction: discord.Interaction):
    voice = interaction.guild.voice_client
    if not voice:
        await interaction.response.send_message("No estoy en ningun canal de voz.", ephemeral=True)
        return

    guild_id = interaction.guild_id
    _cancel_eager_fetch(guild_id)
    guild_queues[guild_id] = []
    guild_current.pop(guild_id, None)
    voice.stop()
    await voice.disconnect()
    await interaction.response.send_message("Desconectado y cola vaciada.")


@bot.tree.command(name="cojonsqueue", description="Muestra la cola de reproducción con páginas")
async def show_queue(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    if not guild_current.get(guild_id) and not get_queue(guild_id):
        await interaction.response.send_message("La cola está vacía.", ephemeral=True)
        return
    view = QueueView(guild_id)
    await interaction.response.send_message(embed=view.build_embed(), view=view)


@bot.tree.command(name="cojonsshuffle", description="Mezcla aleatoriamente la cola")
async def shuffle(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    queue = get_queue(guild_id)
    if not queue:
        await interaction.response.send_message("La cola está vacía.", ephemeral=True)
        return
    random.shuffle(queue)
    await interaction.response.send_message(embed=make_embed(
        "🔀 Cola mezclada",
        f"Se mezclaron **{len(queue)}** canciones aleatoriamente.",
        color=discord.Color.blurple(),
    ))


@bot.tree.command(name="cojonspause", description="Pausa o reanuda la reproducción")
async def pause(interaction: discord.Interaction):
    voice = interaction.guild.voice_client
    if not voice:
        await interaction.response.send_message("No estoy en ningún canal de voz.", ephemeral=True)
        return
    if voice.is_playing():
        voice.pause()
        await interaction.response.send_message(embed=make_embed("⏸️ Pausado", "Reproducción pausada."))
    elif voice.is_paused():
        voice.resume()
        await interaction.response.send_message(embed=make_embed("▶️ Reanudado", "Reproducción reanudada."))
    else:
        await interaction.response.send_message("No se está reproduciendo nada.", ephemeral=True)


@bot.tree.command(name="cojonsremove", description="Elimina una canción de la cola por su número")
@app_commands.describe(numero="Número de la canción en la cola")
async def remove(interaction: discord.Interaction, numero: int):
    guild_id = interaction.guild_id
    queue = get_queue(guild_id)
    if not queue:
        await interaction.response.send_message("La cola está vacía.", ephemeral=True)
        return
    if numero < 1 or numero > len(queue):
        await interaction.response.send_message(
            f"Número inválido. La cola tiene **{len(queue)}** canciones.", ephemeral=True
        )
        return
    removed = queue.pop(numero - 1)
    await interaction.response.send_message(embed=make_embed(
        "🗑️ Eliminada",
        f"**{_song_link(removed)}** eliminada de la cola.",
        color=discord.Color.orange(),
    ))


@bot.tree.command(name="cojonsnowplaying", description="Muestra la canción que se está reproduciendo")
async def nowplaying(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    current = guild_current.get(guild_id)
    if not current:
        await interaction.response.send_message("No se está reproduciendo nada.", ephemeral=True)
        return
    dur = format_duration(current.get("duration"))
    await interaction.response.send_message(embed=make_embed(
        "▶️ Reproduciendo ahora",
        f"**{_song_link(current)}**\n`{dur}`",
        thumbnail=current.get("thumbnail"),
    ))


@bot.tree.command(name="cojonsclear", description="Vacía la cola sin detener la canción actual")
async def clear_queue(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    queue = get_queue(guild_id)
    count = len(queue)
    if not count:
        await interaction.response.send_message("La cola ya está vacía.", ephemeral=True)
        return
    _cancel_eager_fetch(guild_id)
    guild_queues[guild_id] = []
    await interaction.response.send_message(embed=make_embed(
        "🗑️ Cola vaciada",
        f"Se eliminaron **{count}** canciones de la cola.",
        color=discord.Color.orange(),
    ))


bot.run(os.getenv("TOKEN"))
