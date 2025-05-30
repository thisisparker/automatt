#!/usr/bin/env python3

import asyncio
import io
import os
import pathlib
import shlex
import subprocess

import discord
import xword_dl
import yaml

from discord.ext import commands

CONFIG_PATH = os.getenv("CONFIG_PATH") or pathlib.Path(__file__).parent.parent / "email.yaml"

with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

TOKEN = config['discord_token']
GUILD_ID = config['discord_guild_id']

command_string = config['run_command']
command = shlex.split(command_string)

guild = discord.Object(id=GUILD_ID)

intents = discord.Intents.default()
intents.typing = False
intents.presences = False
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.tree.command(name="rerun",
                  description="Run the daily check again",
                  guild=guild)
async def rerun(interaction):
    await interaction.response.send_message("oops! re-running now. that usually takes about 10 minutes. go grab a coffee and I'll be here when you get back. ☕")

    proc = await asyncio.create_subprocess_exec(*command,
                                                stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    errors = stderr.decode().strip()

    response = "alright, i tried!"
    if errors:
        response += f" heads up, I did hit this error: {errors}"
    await interaction.followup.send(response)

@bot.tree.command(name="scrape",
                  description="try to scrape a given URL with xword-dl",
                  guild=guild)
async def scrape(interaction, url: str):
    if not url:
        await interaction.response.send_message("No URL provided")
        return

    try:
        puzzle, filename = xword_dl.by_url(url)
    except Exception as e:
        await interaction.response.send_message(f"Unable to scrape {url}, sorry! Error: {e}")
        return

    file = discord.File(io.BytesIO(puzzle.tobytes()), filename=filename)
    await interaction.response.send_message(content=f"Here's the puzzle from {url}", file=file)

@bot.command()
@commands.is_owner()
async def sync(ctx):
    await bot.tree.sync(guild=guild)
    await ctx.send('synced commands!')

@bot.command()
@commands.is_owner()
async def list_commands(ctx):
    cmds = await bot.tree.fetch_commands(guild=guild)
    for cmd in cmds:
        await ctx.send(f"/{cmd.name} - {cmd.description}")

bot.run(TOKEN)
