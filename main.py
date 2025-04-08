import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Select
import asyncio
import random
import datetime
from collections import defaultdict, Counter
import os
bot = commands.Bot(command_prefix="!", intents=intents)

# Dictionnaire des queues par salon
queues = {}  # {channel_id: {user_id: timestamp}}

QUEUE_TIMEOUT = 3600  # 1h

@bot.command()
async def join(ctx):
    channel_id = ctx.channel.id
    if channel_id not in queues:
        queues[channel_id] = {}

    queue = queues[channel_id]

    if ctx.author.id in queue:
        await ctx.send("❌ Tu es déjà dans la queue.")
        return

    queue[ctx.author.id] = datetime.datetime.utcnow()
    players = list(queue.keys())

    embed = discord.Embed(title="✅ Nouveau joueur", color=discord.Color.green())
    embed.description = f"{ctx.author.mention} a rejoint la queue ({len(players)}/6)"
    embed.add_field(name="Joueurs", value="\n".join([f"<@{p}>" for p in players]), inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def leave(ctx):
    channel_id = ctx.channel.id
    if channel_id not in queues or ctx.author.id not in queues[channel_id]:
        await ctx.send("❌ Tu n'es pas dans la queue.")
        return
    del queues[channel_id][ctx.author.id]
    await ctx.send(f"↩️ {ctx.author.mention} a quitté la queue. ({len(queues[channel_id])}/6)")

@bot.command()
async def status(ctx):
    channel_id = ctx.channel.id
    if channel_id not in queues or not queues[channel_id]:
        await ctx.send("💤 La queue est vide.")
        return
    queue = queues[channel_id]
    embed = discord.Embed(title="⏳ Joueurs dans la queue", color=discord.Color.blurple())
    embed.description = "\n".join([f"<@{uid}>" for uid in queue.keys()])
    await ctx.send(embed=embed)

@tasks.loop(minutes=10)
async def clean_timeout():
    now = datetime.datetime.utcnow()
    for channel_id in list(queues.keys()):
        queue = queues[channel_id]
        to_remove = [uid for uid, ts in queue.items() if (now - ts).total_seconds() > QUEUE_TIMEOUT]
        for uid in to_remove:
            del queue[uid]
