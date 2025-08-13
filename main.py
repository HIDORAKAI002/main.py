# main.py
import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import random
import os
import json
import sqlite3
from datetime import datetime, timedelta

# --- Bot Setup ---
try:
    BOT_TOKEN = os.environ['BOT_TOKEN']
except KeyError:
    print("ERROR: BOT_TOKEN environment variable not found!")
    exit()

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.members = True
intents.bans = True

bot = commands.Bot(command_prefix='?', intents=intents, help_command=None)

# --- Database Setup ---
db = sqlite3.connect('flagbot.db')
cursor = db.cursor()

def init_db():
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        guild_id TEXT, user_id TEXT, score INTEGER DEFAULT 0, xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 0, is_infected INTEGER DEFAULT 0, original_nickname TEXT,
        infection_expiry TIMESTAMP, PRIMARY KEY (guild_id, user_id)
    )''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS guilds (
        guild_id TEXT PRIMARY KEY, difficulty TEXT DEFAULT 'normal', log_channel TEXT
    )''')
    db.commit()
    print("Database initialized.")

# --- Database Helper Functions ---
def get_user_data(guild_id, user_id):
    cursor.execute("SELECT * FROM users WHERE guild_id = ? AND user_id = ?", (str(guild_id), str(user_id)))
    data = cursor.fetchone()
    if data is None:
        cursor.execute("INSERT INTO users (guild_id, user_id) VALUES (?, ?)", (str(guild_id), str(user_id)))
        db.commit()
        return (str(guild_id), str(user_id), 0, 0, 0, 0, None, None)
    return data

def update_user_data(guild_id, user_id, column, value):
    cursor.execute(f"UPDATE users SET {column} = ? WHERE guild_id = ? AND user_id = ?", (value, str(guild_id), str(user_id)))
    db.commit()

def get_guild_settings(guild_id):
    cursor.execute("SELECT * FROM guilds WHERE guild_id = ?", (str(guild_id),))
    data = cursor.fetchone()
    if data is None:
        cursor.execute("INSERT INTO guilds (guild_id) VALUES (?)", (str(guild_id),))
        db.commit()
        return (str(guild_id), 'normal', None)
    return data

def update_guild_settings(guild_id, column, value):
    cursor.execute(f"UPDATE guilds SET {column} = ? WHERE guild_id = ?", (value, str(guild_id)))
    db.commit()

# --- Game State & Helpers ---
active_games = {} # {guild_id: {'answer': str, 'channel_id': int, 'timer_task': asyncio.Task}}
RANDOM_REPLIES = ["My sensors indicate your input is... suboptimal.", "Analyzing message... Conclusion: irrelevant."]

async def get_random_country(difficulty="normal"):
    population_filter = 0
    if difficulty == "easy": population_filter = 15000000
    elif difficulty == "normal": population_filter = 1000000
    try:
        async with aiohttp.ClientSession() as session:
            api_url = 'https://restcountries.com/v3.1/all?fields=name,flags,population'
            async with session.get(api_url) as response:
                if response.status == 200:
                    countries = await response.json()
                    valid_countries = [c for c in countries if 'common' in c.get('name', {}) and 'png' in c.get('flags', {}) and c.get('population', 0) > population_filter]
                    return random.choice(valid_countries) if valid_countries else None
    except Exception as e:
        print(f"Error fetching country: {e}")
        return None

async def start_new_round(guild_id):
    channel_id = active_games[guild_id]['channel_id']
    channel = bot.get_channel(channel_id)
    if not channel:
        active_games.pop(guild_id, None)
        return

    settings = get_guild_settings(guild_id)
    difficulty = settings[1]

    country = await get_random_country(difficulty)
    if not country:
        return await channel.send(f"Could not fetch a new flag. Please try again later.")

    active_games[guild_id]['answer'] = country['name']['common']
    print(f"New round for guild {guild_id}: The country is {country['name']['common']}")

    embed = discord.Embed(title="Guess the Flag!", description="Type the name of the country! You have 60 seconds.", color=discord.Color.blue())
    embed.set_image(url=country['flags']['png'])
    await channel.send(embed=embed)

    active_games[guild_id]['timer_task'] = bot.loop.create_task(round_timer(guild_id, 60))

async def round_timer(guild_id, seconds):
    await asyncio.sleep(seconds)
    if guild_id in active_games:
        game = active_games.pop(guild_id, None)
        if game:
            channel = bot.get_channel(game['channel_id'])
            if channel:
                await channel.send(f"Time's up! The answer was **{game['answer']}**. Game has ended.")
                await show_leaderboard(channel, guild_id)

async def show_leaderboard(channel, guild_id):
    cursor.execute("SELECT user_id, score FROM users WHERE guild_id = ? AND score > 0 ORDER BY score DESC LIMIT 10", (str(guild_id),))
    sorted_scores = cursor.fetchall()
    if not sorted_scores: return

    embed = discord.Embed(title="Leaderboard", color=discord.Color.gold())
    description = ""
    for i, (user_id, score) in enumerate(sorted_scores):
        try:
            user = await bot.fetch_user(int(user_id))
            user_name = user.display_name
        except:
            user_name = f"Unknown User"
        emoji = ["🥇", "🥈", "🥉"][i] if i < 3 else ""
        description += f"{emoji} **{user_name}**: {score} points\n"
    embed.description = description
    await channel.send(embed=embed)


# --- Bot Events ---
@bot.event
async def on_ready():
    init_db()
    print(f'Logged in as {bot.user.name}')
    check_infections_task.start()
    try:
        channel = bot.get_channel(1347134723549302867)
        if channel and channel.permissions_for(channel.guild.me).send_messages:
            await channel.send("Bot systems reloaded. Database is now persistent.")
    except Exception as e:
        print(f"An error occurred sending update message: {e}")

@bot.event
async def on_message(message):
    if message.author.bot: return
    guild_id = message.guild.id

    if message.author.id == 1342499092739391538 and guild_id in active_games:
        await message.reply(random.choice(RANDOM_REPLIES))

    if guild_id in active_games and active_games[guild_id].get('channel_id') == message.channel.id:
        if message.content.startswith(bot.command_prefix):
            await bot.process_commands(message)
            return

        guess = message.content.lower().strip()
        correct_answer_name = active_games[guild_id].get('answer', '').lower()

        if correct_answer_name and guess == correct_answer_name:
            game_data = active_games[guild_id]
            if game_data.get('timer_task'): game_data['timer_task'].cancel()
            
            active_games[guild_id]['answer'] = None
            user = message.author

            await message.channel.send(f"**{user.display_name}** guessed it right! The country was **{correct_answer_name.title()}**.")
            
            user_data = get_user_data(guild_id, user.id)
            old_level, current_xp, current_score = user_data[4], user_data[3], user_data[2]
            xp_gain = random.randint(15, 25)
            new_xp = current_xp + xp_gain
            new_level = int(new_xp**0.5 // 4)

            update_user_data(guild_id, user.id, 'score', current_score + 1)
            update_user_data(guild_id, user.id, 'xp', new_xp)
            if new_level > old_level:
                update_user_data(guild_id, user.id, 'level', new_level)
                await message.channel.send(f"**LEVEL UP!** {user.display_name} has reached **Level {new_level}**!")

            if user_data[5] == 1:
                update_user_data(guild_id, user.id, 'is_infected', 0)
                try:
                    await user.edit(nick=user_data[6])
                    await message.channel.send(f"✨ {user.display_name} has been cured!")
                except discord.Forbidden: pass
            
            await show_leaderboard(message.channel, guild_id)
            await asyncio.sleep(3)
            await start_new_round(guild_id)
            return

        elif correct_answer_name:
            user = message.author
            user_data = get_user_data(guild_id, user.id)
            if user_data[5] == 0:
                try:
                    original_nick = message.author.nick
                    await message.author.edit(nick=f"{message.author.display_name} 🦠")
                    update_user_data(guild_id, user.id, 'is_infected', 1)
                    update_user_data(guild_id, user.id, 'original_nickname', original_nick)
                    update_user_data(guild_id, user.id, 'infection_expiry', datetime.utcnow() + timedelta(minutes=30))
                    await message.add_reaction('🦠')
                except discord.Forbidden:
                    await message.channel.send(f"**Permissions Error!** I can't apply infection because I'm missing the `Manage Nicknames` permission.")

    await bot.process_commands(message)

@tasks.loop(minutes=1)
async def check_infections_task():
    now = datetime.utcnow()
    cursor.execute("SELECT guild_id, user_id, original_nickname FROM users WHERE is_infected = 1 AND infection_expiry < ?", (now,))
    for guild_id, user_id, original_nickname in cursor.fetchall():
        try:
            guild = bot.get_guild(int(guild_id))
            if not guild: continue
            member = await guild.fetch_member(int(user_id))
            await member.edit(nick=original_nickname)
            update_user_data(guild_id, user_id, 'is_infected', 0)
            print(f"Cured {member.display_name} via timeout.")
        except Exception as e:
            print(f"Error during infection cure: {e}")

# --- Commands ---
@bot.command(name='flagstart')
@commands.has_permissions(manage_guild=True)
async def flag_start(ctx):
    if ctx.guild.id in active_games:
        return await ctx.send("A game is already running!")
    
    active_games[ctx.guild.id] = {'channel_id': ctx.channel.id}
    settings = get_guild_settings(ctx.guild.id)
    await ctx.send(f"🎉 **Flag Quiz Started!** (Difficulty: {settings[1]}) 🎉")
    await start_new_round(ctx.guild.id)

@bot.command(name='flagstop')
@commands.has_permissions(manage_guild=True)
async def flag_stop(ctx):
    if ctx.guild.id not in active_games:
        return await ctx.send("There is no game running.")
    
    game_data = active_games.pop(ctx.guild.id, None)
    if game_data and game_data.get('timer_task'):
        game_data['timer_task'].cancel()
    
    await ctx.send("🏁 **Flag Quiz Ended!** 🏁")
    await show_leaderboard(ctx.channel, ctx.guild.id)

@bot.command(name='flagskip')
@commands.has_permissions(manage_guild=True)
async def flag_skip(ctx):
    if ctx.guild.id not in active_games:
        return await ctx.send("There is no game to skip.")
    
    game_data = active_games[ctx.guild.id]
    if game_data.get('timer_task'): game_data['timer_task'].cancel()
    
    correct_answer = game_data['answer']
    await ctx.send(f"The flag was skipped. The answer was **{correct_answer}**. Loading next flag...")
    await start_new_round(ctx.guild.id)

@bot.command(name='difficulty')
@commands.has_permissions(manage_guild=True)
async def difficulty(ctx, level: str.lower):
    if level not in ['easy', 'normal', 'hard']:
        return await ctx.send("Invalid difficulty. Choose `easy`, `normal`, or `hard`.")
    update_guild_settings(ctx.guild.id, 'difficulty', level)
    await ctx.send(f"Game difficulty set to **{level}**.")

@bot.command(name='leaderboard', aliases=['lb'])
async def leaderboard_command(ctx):
    await show_leaderboard(ctx.channel, ctx.guild.id)

@bot.command(name="profile", aliases=["stats", "level"])
async def profile(ctx, member: discord.Member = None):
    member = member or ctx.author
    user_data = get_user_data(ctx.guild.id, member.id)
    embed = discord.Embed(title=f"{member.display_name}'s Profile", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=f"**{user_data[4]}**")
    embed.add_field(name="XP", value=f"**{user_data[3]}**")
    embed.add_field(name="Flags Guessed", value=f"**{user_data[2]}**")
    if user_data[5] == 1: embed.set_footer(text="Status: Currently Infected 🦠")
    await ctx.send(embed=embed)

@bot.command(name="height")
async def height(ctx, member: discord.Member = None):
    member = member or ctx.author
    random.seed(member.id)
    height_val = round(random.uniform(1.1, 19.9), 1)
    units = ["raccoons", "slices of pizza", "RTX 4090s", "stacked cats"]
    unit = random.choice(units)
    random.seed()
    await ctx.send(f"📏 **{member.display_name}** is **{height_val} {unit}** tall.")

@bot.command(name="serverlore")
async def server_lore(ctx):
    user_data = get_user_data(ctx.guild.id, ctx.author.id)
    if user_data[4] < 3:
        return await ctx.send("You must reach **Level 3** to access server lore!")
    
    valid_members = [m for m in ctx.guild.members if not m.bot]
    if len(valid_members) < 2: return await ctx.send("We need at least two humans for a good story!")
    
    user1, user2 = random.sample(valid_members, 2)
    events = ["The Great Emoji War", "The Day of a Thousand Pings"]
    outcomes = ["which led to the creation of #memes", "and things were never the same"]
    lore = f"In ancient server history, **{random.choice(events)}** between **{user1.display_name}** and **{user2.display_name}** concluded, {random.choice(outcomes)}."
    await ctx.send(f"📜 A page from the archives reveals...\n\n{lore}")

@bot.command(name='flaglog')
@commands.has_permissions(manage_guild=True)
async def flaglog(ctx, channel: discord.TextChannel = None):
    if channel:
        update_guild_settings(ctx.guild.id, 'log_channel', str(channel.id))
        await ctx.send(f"✅ **Log Channel Set!** Announcements will now be sent to {channel.mention}.")
    else:
        update_guild_settings(ctx.guild.id, 'log_channel', None)
        await ctx.send("🗑️ **Log Channel Cleared!**")

@bot.command(name='flaghelp')
async def flag_help(ctx):
    embed = discord.Embed(title="🚩 Flag Quiz Help 🚩", color=discord.Color.blurple())
    embed.add_field(name="Game Commands", value="`?flagstart`\n`?flagstop`\n`?flagskip`\n`?leaderboard`", inline=True)
    embed.add_field(name="Fun Commands", value="`?profile [@user]`\n`?height [@user]`\n`?serverlore` (Lvl 3+)", inline=True)
    embed.add_field(name="Settings", value="`?difficulty <level>`\n`?flaglog #channel`", inline=False)
    embed.set_footer(text="Admin commands (?gban, ?gannounce) are restricted and hidden.")
    await ctx.send(embed=embed)

# --- Admin Commands ---
# ... (forceupdate, gban, gunban, gannounce commands are complex and long, but are included here without changes)
@bot.command(name='forceupdate', aliases=['fupdate'])
@commands.is_owner()
async def force_update(ctx):
    old_version, new_version = f"v{random.randint(1,3)}.{random.randint(0,9)}.{random.randint(0,9)}", f"v{random.randint(3,5)}.{random.randint(0,9)}.{random.randint(0,9)}-beta"
    embed = discord.Embed(title="SYSTEM UPDATE IN PROGRESS", description=f"```ini\n[INFO] Remote update initiated by [{ctx.author.name}].\n[INFO] Current version: {old_version}```", color=discord.Color.blue())
    msg = await ctx.send(embed=embed)
    await asyncio.sleep(2); embed.description = f"```ini\n[INFO] Fetching update manifest for [{new_version}]...\n[NET] Secure connection established.```"; await msg.edit(embed=embed)
    await asyncio.sleep(2); embed.color = discord.Color.orange()
    for i in range(11):
        progress, bar, size = i * 10, '█' * i + '░' * (10 - i), f"{(i/10) * 24.7:.1f}"
        embed.description = f"```ini\n[NET] Downloading package [core-geodata.pkg]...\n\n[{bar}] {progress}% ({size}/24.7 MB)```"; await msg.edit(embed=embed); await asyncio.sleep(0.4)
    await asyncio.sleep(1.5); embed.description = f"```ini\n[SYS] Download complete. Decompressing assets...```"; await msg.edit(embed=embed)
    await asyncio.sleep(2.5); embed.color = discord.Color.green(); embed.description = f"```ini\n[DB] Verifying data integrity... OK.\n[SYS] Restarting core services...```"; await msg.edit(embed=embed)
    await asyncio.sleep(2); embed.title = "SYSTEM UPDATE COMPLETE"; embed.description = f"```ini\n[SUCCESS] All systems updated to [{new_version}].\n[INFO] Bot is fully operational.```"; await msg.edit(embed=embed)

@bot.command(name='gban')
@commands.is_owner()
async def gban(ctx, member: discord.Member, *, reason: str = "No reason provided."):
    if member.id == ctx.author.id or member.id == bot.user.id: return await ctx.send("Cannot target self.")
    embed = discord.Embed(title="GLOBAL BANISHMENT PROTOCOL", color=discord.Color.dark_red()); embed.set_author(name="SYSTEM ALERT: THREAT DETECTED"); embed.add_field(name="Status", value="`Initializing...`", inline=False); msg = await ctx.send(embed=embed)
    await asyncio.sleep(2); embed.clear_fields(); embed.add_field(name="Status", value="`Acquiring target...`"); embed.add_field(name="Target Locked", value=f"{member.mention}"); embed.add_field(name="Reason", value=f"`{reason}`"); await msg.edit(embed=embed)
    await asyncio.sleep(2.5); success_guilds, failed_guilds, total_guilds = [], [], len(bot.guilds)
    for i, guild in enumerate(bot.guilds):
        embed.clear_fields(); embed.add_field(name="Status", value=f"`Propagating ban... Guild {i+1}/{total_guilds}`"); embed.add_field(name="Current Node", value=f"**{guild.name}**"); await msg.edit(embed=embed); await asyncio.sleep(0.5)
        try: await guild.ban(member, reason=f"Global Ban by {ctx.author} | Reason: {reason}"); success_guilds.append(f"**{guild.name}**")
        except Exception as e: failed_guilds.append(f"**{guild.name}**: Failed - {type(e).__name__}")
    embed.title="GLOBAL BANISHMENT COMPLETE"; embed.set_author(name="SYSTEM REPORT"); embed.clear_fields(); embed.add_field(name="Target", value=f"{member.mention}"); embed.color=discord.Color.green() if not failed_guilds else discord.Color.orange()
    if success_guilds: embed.add_field(name="✅ Banned In", value="\n".join(success_guilds) or "None", inline=False)
    if failed_guilds: embed.add_field(name="❌ Failed In", value="\n".join(failed_guilds) or "None", inline=False)
    await msg.edit(embed=embed)

@bot.command(name='gunban')
@commands.is_owner()
async def gunban(ctx, user_id: int, *, reason: str = "No reason provided."):
    try: user_to_unban = await bot.fetch_user(user_id)
    except discord.NotFound: return await ctx.send("Could not find a user with that ID.")
    success_guilds, failed_guilds = [], []
    await ctx.send(f"Initiating global unban for **{user_to_unban.name}**...")
    for guild in bot.guilds:
        try: await guild.unban(user_to_unban, reason=f"Global Unban by {ctx.author} | Reason: {reason}"); success_guilds.append(f"**{guild.name}**")
        except Exception as e: failed_guilds.append(f"**{guild.name}**: Failed")
    embed = discord.Embed(title="Global Unban Report", color=discord.Color.green()); embed.add_field(name="Target", value=f"{user_to_unban.name}");
    if success_guilds: embed.add_field(name="✅ Unbanned In", value="\n".join(success_guilds), inline=False)
    if failed_guilds: embed.add_field(name="❌ Failed In", value="\n".join(failed_guilds), inline=False)
    await ctx.send(embed=embed)

@bot.command(name='gannounce')
@commands.is_owner()
async def global_announce(ctx, *, message: str):
    success_count, fail_count, unconfigured_guilds = 0, 0, []
    embed = discord.Embed(title="Global Announcement", description=message, color=discord.Color.red())
    embed.set_author(name=f"Announcement from Bot Developer: {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(f"📡 Starting global announcement to {len(bot.guilds)} servers...")
    for guild in bot.guilds:
        settings = get_guild_settings(guild.id)
        log_channel_id = settings[2]
        if not log_channel_id:
            unconfigured_guilds.append(guild.name)
            continue
        target_channel = guild.get_channel(int(log_channel_id))
        if not target_channel or not target_channel.permissions_for(guild.me).send_messages:
            fail_count += 1
            continue
        mods_to_ping = " ".join([m.mention for m in guild.members if not m.bot and m.guild_permissions.manage_messages])
        try:
            await target_channel.send(content=mods_to_ping or "Attention Moderators,", embed=embed)
            success_count += 1
        except Exception as e:
            print(f"Failed to send to '{guild.name}': {e}"); fail_count += 1
        await asyncio.sleep(1)
    report_embed = discord.Embed(title="Global Announcement Report", color=discord.Color.green())
    report_embed.add_field(name="✅ Success", value=f"{success_count} servers", inline=False)
    report_embed.add_field(name="❌ Failures", value=f"{fail_count} servers", inline=False)
    await ctx.send(embed=report_embed)
    if unconfigured_guilds:
        dm_message = "The following servers were not configured with `?flaglog`:\n- " + "\n- ".join(unconfigured_guilds)
        try: await ctx.author.send(dm_message)
        except discord.Forbidden: await ctx.send("Could not DM you the list of unconfigured servers.")

# --- Error Handling ---
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound): return
    if isinstance(error, commands.MissingPermissions): await ctx.send("You don't have permission for that.")
    elif isinstance(error, commands.NotOwner): await ctx.send("`[ACCESS DENIED]`")
    elif isinstance(error, commands.MissingRequiredArgument): await ctx.send(f"You're missing an argument. Usage: `?{ctx.command.name} {ctx.command.signature}`")
    elif isinstance(error, (commands.MemberNotFound, commands.UserNotFound)): await ctx.send(f"Could not find user '{error.argument}'.")
    else:
        print(f"Unhandled error in '{ctx.command}': {error}")
        await ctx.send("An unexpected error occurred.")

# --- Run the Bot ---
if __name__ == "__main__":
    bot.run(BOT_TOKEN)
