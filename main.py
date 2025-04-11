
import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Select
import asyncio
import random
import datetime
from collections import defaultdict, Counter
from keep_alive import keep_alive

keep_alive()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command('help')

QUEUE_TIMEOUT = 3600  # 1h
queue = {}  # {user_id: timestamp}
match_id_counter = random.randint(100, 999)
matches = {}  # match_id: {team1: [], team2: [], reported: False}
mmr = defaultdict(lambda: {"elo": 0, "wins": 0, "losses": 0})
duo_wins = Counter()
cancelled_matches = set()

def generate_match_id():
    global match_id_counter
    match_id_counter += 1
    return match_id_counter

def get_display_name(guild, user_id):
    member = guild.get_member(user_id)
    return member.display_name if member else f"<@{user_id}>"

class CaptainSelect(Select):
    def __init__(self, label, options, on_select, allowed_user_id):
        self.callback_func = on_select
        self.allowed_user_id = allowed_user_id
        super().__init__(placeholder=label, options=[
            discord.SelectOption(label=opt.display_name, value=str(opt.id)) for opt in options
        ])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.allowed_user_id:
            await interaction.response.send_message("❌ Tu n'es pas autorisé à faire ce choix.", ephemeral=True)
            return
        await interaction.response.defer()
        await self.callback_func(interaction, int(self.values[0]))
        self.disabled = True
        await interaction.message.edit(view=self.view)

class CaptainView(View):
    def __init__(self, label, options, on_select, allowed_user_id):
        super().__init__(timeout=None)
        self.add_item(CaptainSelect(label, options, on_select, allowed_user_id))

class VoteView(View):
    def __init__(self, players, channel):
        super().__init__(timeout=None)
        self.votes = {"CAPITAINES": [], "ALÉATOIRE": []}
        self.players = players
        self.channel = channel
        self.vote_message = None

    async def update_embed(self):
        embed = discord.Embed(title="🗳️ 6 joueurs en queue ! Choisissez un mode de match :", color=discord.Color.blue())
        embed.add_field(name="Joueurs", value="\n".join([f"<@{p}>" for p in self.players]), inline=False)
        embed.add_field(name="Vote CAPITAINES", value=f"{len(self.votes['CAPITAINES'])} / 6", inline=True)
        embed.add_field(name="Vote ALÉATOIRE", value=f"{len(self.votes['ALÉATOIRE'])} / 6", inline=True)
        await self.vote_message.edit(embed=embed, view=self)

    async def disable_buttons(self):
        for child in self.children:
            child.disabled = True
        await self.vote_message.edit(view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in self.players:
            await interaction.response.send_message("Tu ne fais pas partie de cette file d'attente.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="CAPITAINES", style=discord.ButtonStyle.primary)
    async def vote_captains(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid in self.votes["CAPITAINES"] or uid in self.votes["ALÉATOIRE"]:
            await interaction.response.send_message("Tu as déjà voté.", ephemeral=True)
            return
        self.votes["CAPITAINES"].append(uid)
        await interaction.response.send_message("Vote enregistré pour **CAPITAINES**", ephemeral=True)
        await self.update_embed()
        if len(self.votes["CAPITAINES"]) >= 3:
            await self.disable_buttons()
            await self.channel.send(embed=discord.Embed(title="🏆 Mode CAPITAINES sélectionné !", color=discord.Color.gold()))
            await start_captain_mode(self.players, self.channel)

    @discord.ui.button(label="ALÉATOIRE", style=discord.ButtonStyle.success)
    async def vote_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid in self.votes["CAPITAINES"] or uid in self.votes["ALÉATOIRE"]:
            await interaction.response.send_message("Tu as déjà voté.", ephemeral=True)
            return
        self.votes["ALÉATOIRE"].append(uid)
        await interaction.response.send_message("Vote enregistré pour **ALÉATOIRE**", ephemeral=True)
        await self.update_embed()
        if len(self.votes["ALÉATOIRE"]) >= 3:
            await self.disable_buttons()
            await self.channel.send(embed=discord.Embed(title="🎲 Mode Aléatoire sélectionné !", color=discord.Color.green()))
            await start_random_mode(self.players, self.channel)
async def start_captain_mode(players, channel):
    guild = channel.guild
    ids = players.copy()
    random.shuffle(ids)
    cap1, cap2 = ids[0], ids[1]
    remaining = [guild.get_member(uid) for uid in ids[2:]]

    match_id = generate_match_id()
    matches[match_id] = {"team1": [cap1], "team2": [cap2], "reported": False}

    await channel.send(embed=discord.Embed(
        title="🏆 Match prêt - Mode Capitaines",
        description=f"ID du match : {match_id}",
        color=discord.Color.gold()
    ))
    await channel.send(f"Les capitaines désignés sont :\nTeam 1 : <@{cap1}>\nTeam 2 : <@{cap2}>")

    async def step1(interaction, selected_id):
        matches[match_id]["team1"].append(selected_id)
        remaining1 = [m for m in remaining if m.id != selected_id]

        async def step2a(interaction2, selected_id2):
            matches[match_id]["team2"].append(selected_id2)
            remaining2 = [m for m in remaining1 if m.id != selected_id2]

            async def step2b(interaction3, selected_id3):
                matches[match_id]["team2"].append(selected_id3)
                last = [m.id for m in remaining2 if m.id != selected_id3]
                if last:
                    matches[match_id]["team1"].append(last[0])

                t1 = matches[match_id]["team1"]
                t2 = matches[match_id]["team2"]
                embed = discord.Embed(title="✅ Équipes finales", color=discord.Color.green())
                embed.add_field(name="Team 1", value="\n".join([get_display_name(guild, u) for u in t1]), inline=True)
                embed.add_field(name="Team 2", value="\n".join([get_display_name(guild, u) for u in t2]), inline=True)
                embed.set_footer(text=f"ID du match : {match_id}")
                await channel.send(embed=embed)

            await channel.send(f"<@{cap2}>, choisis ton 2e joueur :")
            await channel.send(view=CaptainView("Choisis un joueur", remaining2, step2b, cap2))

        await channel.send(f"<@{cap2}>, choisis ton 1er joueur :")
        await channel.send(view=CaptainView("Choisis un joueur", remaining1, step2a, cap2))

    await channel.send(f"<@{cap1}>, choisis ton joueur :")
    await channel.send(view=CaptainView("Choisis un joueur", remaining, step1, cap1))

async def start_random_mode(players, channel):
    random.shuffle(players)
    team1, team2 = players[:3], players[3:]
    match_id = generate_match_id()
    matches[match_id] = {"team1": team1, "team2": team2, "reported": False}

    embed = discord.Embed(title="✅ Match prêt - Équipes (aléatoires)", color=discord.Color.green())
    embed.add_field(name="Team 1", value="\n".join([f"<@{u}>" for u in team1]), inline=True)
    embed.add_field(name="Team 2", value="\n".join([f"<@{u}>" for u in team2]), inline=True)
    embed.set_footer(text=f"ID du match : {match_id}")
    await channel.send(embed=embed)

@bot.command()
async def join(ctx):
    if ctx.author.id in queue:
        await ctx.send("❌ Tu es déjà dans la queue.")
        return
    queue[ctx.author.id] = {"timestamp": datetime.datetime.utcnow(), "channel": ctx.channel}
    players = list(queue.keys())

    embed = discord.Embed(title="✅ Nouveau joueur", color=discord.Color.green())
    embed.description = f"{ctx.author.mention} a rejoint la queue ({len(players)}/6)"
    embed.add_field(name="Joueurs", value="\n".join([f"<@{p}>" for p in players]), inline=False)

    # Envoyer dans le même salon uniquement
    await ctx.channel.send(embed=embed)

    if len(players) == 6:
        view = VoteView(players, ctx.channel)
        vote_embed = await ctx.channel.send(
            embed=discord.Embed(title="✅ 6 joueurs réunis !", description="Début du vote.", color=discord.Color.green()),
            view=view
        )
        view.vote_message = vote_embed

@bot.command()
async def leave(ctx):
    if ctx.author.id not in queue:
        await ctx.send("❌ Tu n'es pas dans la queue.")
        return
    del queue[ctx.author.id]
    await ctx.channel.send(f"↩️ {ctx.author.mention} a quitté la queue. ({len(queue)}/6)")


@tasks.loop(minutes=10)
async def clean_timeout():
    now = datetime.datetime.utcnow()
    to_remove = []

    for uid, data in queue.items():
        if (now - data["timestamp"]).total_seconds() > QUEUE_TIMEOUT:
            to_remove.append(uid)
            channel = data["channel"]
            await channel.send(f"⏳ <@{uid}> a été retiré de la queue pour inactivité.")

    for uid in to_remove:
        del queue[uid]



@bot.command()
async def win(ctx, match_id: int):
    await report_result(ctx, match_id, winner=ctx.author.id)

@bot.command()
async def loose(ctx, match_id: int):
    await report_result(ctx, match_id, winner=None, loser=ctx.author.id)

@bot.command()
async def cancel(ctx, match_id: int):
    cancelled_matches.add(match_id)
    await ctx.send(f"❌ Le match {match_id} a été annulé.")

async def report_result(ctx, match_id: int, winner=None, loser=None):
    match = matches.get(match_id)
    if not match or match["reported"] or match_id in cancelled_matches:
        await ctx.send("❌ Match invalide ou déjà terminé.")
        return

    t1, t2 = match["team1"], match["team2"]
    if winner in t1:
        w, l = t1, t2
    elif winner in t2:
        w, l = t2, t1
    elif loser in t1:
        w, l = t2, t1
    elif loser in t2:
        w, l = t1, t2
    else:
        await ctx.send("❌ Tu ne fais pas partie de ce match.")
        return

    avg_w_elo = sum(mmr[u]["elo"] for u in w) / 3
    avg_l_elo = sum(mmr[u]["elo"] for u in l) / 3
    k = 25
    prob = 1 / (1 + 10 ** ((avg_l_elo - avg_w_elo) / 400))
    gain = round(k * (1 - prob))
    loss = round(gain)

    embed = discord.Embed(title=f"🏁 Match {match_id} terminé !", color=discord.Color.purple())
    embed.add_field(name="Gagnants", value="\n".join([get_display_name(ctx.guild, u) for u in w]), inline=True)
    embed.add_field(name="Perdants", value="\n".join([get_display_name(ctx.guild, u) for u in l]), inline=True)

    for u in w:
        mmr[u]["elo"] += gain
        mmr[u]["wins"] += 1
    for u in l:
        mmr[u]["elo"] -= loss
        mmr[u]["losses"] += 1

    for a in w:
        for b in w:
            if a != b:
                duo_wins[tuple(sorted((a, b)))] += 1

    match["reported"] = True
    await ctx.send(embed=embed)
@bot.command()
async def undo(ctx, match_id: int):
    match = match_history.get(match_id)
    if not match:
        await ctx.send("❌ Aucun match trouvé avec cet ID.")
        return

    # Vérifie si le score a déjà été annulé
    if match.get("undone"):
        await ctx.send("⚠️ Ce match a déjà été annulé.")
        return

    winner = match["winner"]
    loser = 2 if winner == 1 else 1
    mmr_change = match["mmr_change"]

    for player_id in match[f"team{winner}"]:
        player_stats[player_id]["mmr"] -= mmr_change
        player_stats[player_id]["wins"] -= 1

    for player_id in match[f"team{loser}"]:
        player_stats[player_id]["mmr"] += mmr_change
        player_stats[player_id]["losses"] -= 1

    match["undone"] = True  # Marquer comme annulé

    embed = discord.Embed(
        title="↩️ Match annulé",
        description=f"Les modifications de MMR pour le match ID {match_id} ont été annulées.",
        color=discord.Color.red()
    )
    await ctx.send(embed=embed)

@bot.command()
async def leaderboard(ctx):
    classement = sorted(mmr.items(), key=lambda x: x[1]["elo"], reverse=True)
    desc = ""
    for i, (uid, stats) in enumerate(classement, 1):
        total = stats["wins"] + stats["losses"]
        winrate = (stats["wins"] / total * 100) if total else 0
        desc += f"**#{i}** {get_display_name(ctx.guild, uid)} - 🧠 {stats['elo']} ELO | ✅ {stats['wins']} | ❌ {stats['losses']} | 🎯 {winrate:.1f}%\n"

    embed = discord.Embed(title="🏆 Leaderboard solo", description=desc or "Aucun joueur encore classé.", color=discord.Color.blue())
    await ctx.send(embed=embed)

@bot.command()
async def leadersboard(ctx):
    classement = duo_wins.most_common(10)
    desc = ""
    for i, ((a, b), wins) in enumerate(classement, 1):
        desc += f"**#{i}** {get_display_name(ctx.guild, a)} + {get_display_name(ctx.guild, b)} — 🥇 {wins} victoires ensemble\n"

    embed = discord.Embed(title="🤝 Leaderboard duo", description=desc or "Aucun duo classé.", color=discord.Color.teal())
    await ctx.send(embed=embed)


@bot.command(name="commands")
async def show_commands(ctx):
    embed = discord.Embed(title="📘 Commandes disponibles", color=discord.Color.blue())
    embed.add_field(name="🎮 !join", value="Rejoindre la queue.", inline=False)
    embed.add_field(name="🏃 !leave", value="Quitter la queue.", inline=False)
    embed.add_field(name="⏳ !status", value="Voir les joueurs actuellement en queue.", inline=False)
    embed.add_field(name="🏆 !win <ID>", value="Déclarer la victoire de ton équipe.", inline=False)
    embed.add_field(name="🔻 !loose <ID>", value="Déclarer la défaite de ton équipe.", inline=False)
    embed.add_field(name="↩️ !undo <ID>", value="Annuler un match déjà reporté (rembourse MMR).", inline=False)
    embed.add_field(name="⛔ !cancel <ID>", value="Annuler un match (admin uniquement).", inline=False)
    embed.add_field(name="📊 !leaderboard", value="Afficher le classement solo.", inline=False)
    embed.add_field(name="👥 !leadersboard", value="Afficher les meilleurs duos.", inline=False)
    await ctx.send(embed=embed)


@bot.command()
async def status(ctx):
    if not queue:
        await ctx.send("💤 La queue est vide.")
        return

    embed = discord.Embed(title="⏳ Joueurs dans la queue", color=discord.Color.blurple())
    embed.description = "\n".join([f"<@{uid}>" for uid in queue.keys()])
    await ctx.send(embed=embed)

@bot.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {bot.user}")
    clean_timeout.start()


if __name__ == "__main__":
    import os
    bot.run(os.getenv("TOKEN"))
