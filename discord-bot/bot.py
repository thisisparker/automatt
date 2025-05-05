#!/usr/bin/env python3

import shlex
import subprocess

import discord
import yaml

from discord.ext import commands

with open('../email.yaml') as f:
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

    result = subprocess.run(command, capture_output=True, text=True)
    response = "alright, i tried!"
    if result.stderr:
        response += " heads up, I did hit this error: {result.stderr}" 
    await interaction.followup.send(response)

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
        print(f"/{cmd.name} - {cmd.description}")

bot.run(TOKEN)
