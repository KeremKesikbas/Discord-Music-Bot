import asyncio
import functools
from os import name
import random
from discord.ext.commands.core import guild_only

from youtube_dl import YoutubeDL
from youtube_dl import utils

from async_timeout import timeout

from discord.channel import VoiceChannel
from discord.member import Member
from discord.voice_client import VoiceClient
from discord.utils import get
from discord.ext import commands

from discord import Embed
from discord import FFmpegPCMAudio
from discord import Status
from discord import Game
from discord import Color

## - Useless bug reports

utils.bug_reports_message = lambda: ''

class VoiceError(Exception):
    pass


class YTDLError(Exception):
    pass

## - Song datas
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'
}

async def getSongDatas(ctx: commands.Context, search: str, loop: asyncio.BaseEventLoop = None):
    ytdl = YoutubeDL(YTDL_OPTIONS)

    if loop is None:
        loop = asyncio.get_event_loop()

    partial = functools.partial(ytdl.extract_info, search, download=False, process=False)
    data = await loop.run_in_executor(None, partial)

    if data is None:
        await ctx.send(f"I couldn't find any result for \"{search}\"")

    if 'entries' not in data:
        process_info = data
    else:
        process_info = None

        for entry in data['entries']:
            if entry:
                process_info = entry
                await ctx.send(process_info)
                break

        if process_info is None:
            await ctx.send(f"I couldn't find any result for \"{search}\"")
    
    webpage_url = process_info['webpage_url']
    partial = functools.partial(ytdl.extract_info, webpage_url, download=False)
    processed_info = await loop.run_in_executor(None, partial)

    if processed_info is None:
        await ctx.send(f"I couldn't get the data from {webpage_url}")

    if 'entries' not in processed_info:
        info = processed_info
    else:
        info = None
        while info is None:
            try:
                info = processed_info['entries'].pop(0)
            except IndexError:
                await ctx.send(f"I couldn't get the data from {webpage_url}")

    return info

def parse_duration(duration: int):
    minutes, seconds = divmod(duration, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    duration = []
    if days > 0:
        duration.append('{} days'.format(days))
    if hours > 0:
        duration.append('{} hours'.format(hours))
    if minutes > 0:
        duration.append('{} minutes'.format(minutes))
    if seconds > 0:
        duration.append('{} seconds'.format(seconds))

    return ', '.join(duration)

####################

class Song():
    def __init__(self, info, requester: Member, channel: VoiceChannel):
        self.requester = requester
        self.channel = channel
        self.info = info

        self.uploader = info.get('uploader')
        self.uploader_url = info.get('uploader_url')

        date = info.get('upload_date')
        self.upload_date = date[6:8] + '.' + date[4:6] + '.' + date[0:4]

        self.title = info.get('title')
        self.thumbnail = info.get('thumbnail')
        self.description = info.get('description')
        self.duration = parse_duration(int(info.get('duration')))
        self.seconds = info.get('duration')

        self.webpage_url = info.get('webpage_url')
        self.stream_url = info.get('url')

    def createEmbed(self, embedTitle: str):
        embed: Embed = Embed(title=embedTitle, 
                            description=f"{self.title} [by {self.uploader}]({self.uploader_url})", 
                            color=Color.dark_grey())

        embed.add_field(name="Duration", value=self.duration)
        embed.add_field(name="Requested by", value=self.requester.mention)
        embed.add_field(name="Webpage", value=f"[Link]({self.webpage_url})")
        embed.set_thumbnail(url=self.thumbnail)

        return embed

    def __str__(self):
        return f"{self.title}"

####################

class SongQueue(asyncio.Queue):
    def __iter__(self):
        return self._queue.__iter__()

    def __len__(self):
        return self.qsize()

    def clear(self):
        self._queue.clear()

    def getItem(self, index):
        return self._queue[index]

    def shuffle(self):
        random.shuffle(self._queue)

    def remove(self, index: int):
        del self._queue[index]

class Queue():
    def __init__(self, client: commands.Bot):
        self.client = client

        self.voice: VoiceClient = None

        self.currentSong: Song = None

        self.songList = SongQueue()
        self.songHistory = []

        self.loop = False # loops the current song

        self.next = asyncio.Event()

        self.audioPlayer = None

    async def startAudio(self):
        while True:
            self.next.clear()

            if not self.loop:
                try:
                    async with timeout(1):
                        self.currentSong = await self.songList.get()

                        self.songHistory.append(self.currentSong)

                except asyncio.TimeoutError:
                    self.client.loop.create_task(self.stop())

                    return
                    
            self.voice.play(FFmpegPCMAudio(self.currentSong.stream_url, **FFMPEG_OPTIONS), after=self.playNext)

            await self.next.wait()

    def playNext(self, error=None):
        if error:
            raise VoiceError(str(error))

        self.next.set()

    async def stop(self):
        self.songList.clear()

        if self.voice:
            await self.voice.disconnect()
            self.voice = None
            self.loop = False
            self.audioPlayer = None

    async def pause(self):
        if self.voice:
            await self.voice.pause()

    async def resume(self):
        if self.voice:
            await self.voice.resume()

####################

class CommandData:
    def __init__(self, name: str, aliases: str, description: str, *argNames: str):
        self.name = name
        self.aliases = aliases
        self.description = description
        self.args = argNames

    def createForHelp(self):
        result: str = f"**-{self.name}{self.aliases}"

        for arg in self.args:
            result += f"[{arg}]"

        result += f"**   -> {self.description}\n"

        return result

    def createEmbed(self):
        return Embed(title=f"-{self.name}{self.aliases}", description=self.description, color=Color.dark_grey())

    def __str__(self) -> str:
        return self.name

####################

class MusicBot(commands.Cog):
    
    def __init__(self, client: commands.Bot):
        self.client: commands.Bot = client
        self.voice: VoiceClient = None

        self.queue: Queue = Queue(client)

        self.commandList = []

        self.commandList.append(CommandData("help", "", "Command help and usage", "command"))
        self.commandList.append(CommandData("ping", "", "Shows the ping"))
        self.commandList.append(CommandData("queue", "(q)", "Shows the playlist"))
        self.commandList.append(CommandData("join", "", "Joins the voice channel"))
        self.commandList.append(CommandData("leave", "(disconnect)", "Exits voice channel and clears playlist"))
        self.commandList.append(CommandData("pause", "", "Pauses the current playing song"))
        self.commandList.append(CommandData("resume", "", "Resumes the current playing song"))
        self.commandList.append(CommandData("skip", "(s, fs)", "Skips the current playing song"))
        self.commandList.append(CommandData("now", "(np, playing)", "Shows the current playing song"))
        self.commandList.append(CommandData("shuffle", "", "Shuffles the playist"))
        self.commandList.append(CommandData("loop", "", "Loops the current playing song"))
        self.commandList.append(CommandData("unloop", "", "Unloops the current playing song"))
        self.commandList.append(CommandData("remove", "", "Removes specified song from playlist", "song"))
        self.commandList.append(CommandData("play", "(p)", "Plays the specified Youtube video", "search"))

    # Events

    @commands.Cog.listener()
    async def on_ready(self):
        await self.client.change_presence(status=Status.online, activity=Game("a song"))

        print(f"{self.client.user} is active")
            
    # Commands

    @commands.command(name="help")
    async def help(self, ctx: commands.Context, command: str = ""):

        c: CommandData = None

        for cmd in self.commandList:
            if str(cmd) == command:
                c = cmd

        if c != None:
            embed: Embed = c.createEmbed()

        else:
            embed: Embed = Embed(title="Music Bot", color=Color.dark_grey())

            commands: str = ""

            for command in self.commandList:
                commands += command.createForHelp()

            embed.add_field(name="Commands", value=commands, inline=False)

        await ctx.send(embed=embed)

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context):
        await ctx.send(f"{round(self.client.latency * 1000)}ms")

    @commands.command(name="queue", aliases=["q"])
    async def queue(self, ctx: commands.Context):
        if not self.voice.is_playing():
            return await ctx.send("Nothing playing right now")

        qEmbed: Embed = Embed(title="Playlist", color= Color.dark_grey())

        qEmbed.add_field(name="Now Playing", value=f"{self.queue.currentSong.title}\n")

        if not self.queue.songList.empty():
            result: str = ""

            for i in range(len(self.queue.songList)):
                result += f"\n{str(i + 1)}. " + self.queue.songList.getItem(i).title

            qEmbed.add_field(name="Queue", value=result, inline=False)

        await ctx.send(embed=qEmbed)

    @commands.command(name="join")
    async def join(self, ctx: commands.Context):
        author: Member = ctx.message.author

        if author.voice is not None:
            channel: VoiceChannel = author.voice.channel

            self.voice = get(self.client.voice_clients, guild=ctx.guild)

            if self.voice and self.voice.is_connected():
                await self.voice.move_to(channel)

            else:
                self.voice = await channel.connect()

            self.queue.voice = self.voice

        else:
            await ctx.send("u are not in a voice channel")

    @commands.command(name="leave", aliases=["disconnect"])
    async def leave(self, ctx: commands.Context):
        if not self.queue.voice:
            return await ctx.send("Im not connected to any voice channel")

        await self.queue.stop()

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context):
        if self.queue.voice.is_playing():
            await self.queue.pause()
            await ctx.message.add_reaction('⏹')

    @commands.command(name="resume")
    async def resume(self, ctx: commands.Context):
        if self.queue.voice.is_paused():
            await self.queue.voice.resume()
            await ctx.message.add_reaction('⏯')

    @commands.command(name="skip", aliases=["s", "fs"])
    async def skip(self, ctx: commands.Context):
        if not self.voice.is_playing():
            return await ctx.send("Nothing playing right now")
        
        self.queue.voice.stop()

        await ctx.message.add_reaction('✅')

    @commands.command(name="now", aliases=["np", "playing"])
    async def np(self, ctx: commands.Context):
        if not self.voice.is_playing():
            return await ctx.send("Nothing playing right now")

        await ctx.send(embed=self.queue.currentSong.createEmbed("Now Playing"))

    @commands.command(name="shuffle")
    async def shuffle(self, ctx: commands.Context):
        if len(self.queue.songList) == 0:
            return await ctx.send("Song queue is empty")

        self.queue.songList.shuffle()

        await ctx.message.add_reaction('✅')

    @commands.command(name="loop")
    async def loop(self, ctx: commands.Context):
        if not self.voice.is_playing():
            return await ctx.send("Nothing playing right now")

        self.queue.loop = True
        await ctx.message.add_reaction('✅')
    
    @commands.command(name="unloop")
    async def unloop(self, ctx: commands.Context):
        if not self.voice.is_playing():
            return await ctx.send("Nothing playing right now")

        self.queue.loop = False
        await ctx.message.add_reaction('✅')

    @commands.command(name="remove")
    async def remove(self, ctx: commands.Command, *song: str):
        if len(self.queue.songList) == 0:
            return await ctx.send("Song queue is empty")

        result = ""

        for s in song:
            result += s + " "

        try:
            result = int(result)

        except:
            result = str(result)

        if type(result) == int:
            self.queue.songList.remove(result - 1)

        elif type(result) == str:
            for i in range(len(self.queue.songList)):
                if (self.queue.songList.getItem(i).title == result):
                    self.queue.songList.remove(i)

        else:
            await ctx.send("Invalid argument")

        await ctx.message.add_reaction('✅')

    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *search: str):

        await ctx.invoke(self.join)

        result = ""

        for s in search:
            result += s + " "

        if result == "":
            await ctx.send("i can't play nothing.")

        info = await getSongDatas(ctx, result, self.client.loop)

        await self.queue.songList.put(Song(info, ctx.message.author, ctx.message.author.voice.channel))

        if self.queue.audioPlayer:
            self.queue.audioPlayer = self.client.loop.create_task(self.queue.startAudio())

        await ctx.message.add_reaction('✅')

def setup(client):
    client.add_cog(MusicBot(client))