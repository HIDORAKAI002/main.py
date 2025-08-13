# main.py
import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import random
import os
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
intents.reactions = True

bot = commands.Bot(command_prefix='?', intents=intents, help_command=None)

# --- Database Setup ---
db = sqlite3.connect('flagbot.db')
cursor = db.cursor()

def init_db():
    """Initializes the database tables if they don't exist."""
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
    if guild_id not in active_games: return
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
                await leaderboard(channel, guild_id)


# --- Bot Events ---
@bot.event
async def on_ready():
    init_db()
    print(f'Logged in as {bot.user.name}')
    check_infections_task.start()

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

            if user_data[5] == 1: # is_infected
                update_user_data(guild_id, user.id, 'is_infected', 0)
                try:
                    await user.edit(nick=user_data[6]) # original_nickname
                    await message.channel.send(f"✨ {user.display_name} has been cured!")
                except discord.Forbidden: pass
            
            await leaderboard(message.channel, guild_id)
            await asyncio.sleep(3)
            await start_new_round(guild_id)
            return

        elif correct_answer_name: # Incorrect guess
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

# --- Helper to prevent code duplication in start command ---
async def _start_game_logic(ctx):
    active_games[ctx.guild.id] = {'channel_id': ctx.channel.id}
    settings = get_guild_settings(ctx.guild.id)
    await ctx.send(f"🎉 **Flag Quiz Started!** (Difficulty: {settings[1]}) 🎉")
    await start_new_round(ctx.guild.id)

# --- Commands ---
@bot.command(name='flagstart')
async def flag_start(ctx):
    if ctx.guild.id in active_games:
        return await ctx.send("A game is already running!")

    if ctx.author.guild_permissions.manage_guild:
        await _start_game_logic(ctx)
    else:
        VOTE_THRESHOLD = 3; VOTE_DURATION = 60.0
        embed = discord.Embed(title="Vote to Start Flag Quiz!", description=f"{ctx.author.mention} wants to start a game. We need **{VOTE_THRESHOLD}** total votes!", color=discord.Color.gold())
        embed.set_footer(text=f"React with ✅ to vote. The vote ends in {int(VOTE_DURATION)} seconds.")
        vote_msg = await ctx.send(embed=embed)
        await vote_msg.add_reaction("✅")
        voters = {ctx.author.id}
        def check(reaction, user):
            return str(reaction.emoji) == '✅' and user.id != bot.user.id and reaction.message.id == vote_msg.id
        try:
            while len(voters) < VOTE_THRESHOLD:
                time_passed = (discord.utils.utcnow() - vote_msg.created_at).total_seconds()
                remaining_time = VOTE_DURATION - time_passed
                if remaining_time <= 0: raise asyncio.TimeoutError
                reaction, user = await bot.wait_for('reaction_add', timeout=remaining_time, check=check)
                if user.id not in voters:
                    voters.add(user.id)
                    embed.description = f"{ctx.author.mention} wants to start a game!\n\n**Votes: {len(voters)}/{VOTE_THRESHOLD}**"
                    await vote_msg.edit(embed=embed)
            embed.title = "Vote Passed!"; embed.description = "Starting the game..."
            await vote_msg.edit(embed=embed); await vote_msg.clear_reactions()
            await _start_game_logic(ctx)
        except asyncio.TimeoutError:
            embed.title = "Vote Failed"; embed.description = "Not enough votes were cast in time."
            await vote_msg.edit(embed=embed); await vote_msg.clear_reactions()


@bot.command(name='flagstop')
@commands.has_permissions(manage_guild=True)
async def flag_stop(ctx):
    if ctx.guild.id not in active_games:
        return await ctx.send("There is no game running.")
    
    game_data = active_games.pop(ctx.guild.id, None)
    if game_data and game_data.get('timer_task'):
        game_data['timer_task'].cancel()
    
    await ctx.send("🏁 **Flag Quiz Ended!** 🏁")
    await leaderboard(ctx.channel, ctx.guild.id)

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

# --- Leaderboard Commands ---
async def leaderboard(channel, guild_id):
    """Helper function to show the server-specific leaderboard."""
    cursor.execute("SELECT user_id, score FROM users WHERE guild_id = ? AND score > 0 ORDER BY score DESC LIMIT 10", (str(guild_id),))
    server_top_users = cursor.fetchall()
    if not server_top_users:
        return await channel.send("The leaderboard for this server is empty.")
    
    embed = discord.Embed(title=f"Leaderboard for {channel.guild.name}", color=discord.Color.gold())
    description = ""
    for i, (user_id, score) in enumerate(server_top_users):
        try: user_name = (await bot.fetch_user(int(user_id))).display_name
        except: user_name = f"Unknown User"
        emoji = ["🥇", "🥈", "🥉"][i] if i < 3 else "🔹"
        description += f"{emoji} **{user_name}**: {score} points\n"
    embed.description = description
    await channel.send(embed=embed)

@bot.command(name='leaderboard', aliases=['lb'])
async def leaderboard_command(ctx):
    """Shows the leaderboard for the current server."""
    await leaderboard(ctx.channel, ctx.guild.id)

@bot.command(name='gleaderboard', aliases=['glb'])
async def global_leaderboard(ctx):
    """Displays a leaderboard of the top players across all servers."""
    cursor.execute("SELECT user_id, SUM(score) as total_score FROM users WHERE score > 0 GROUP BY user_id ORDER BY total_score DESC LIMIT 10")
    global_top_users = cursor.fetchall()
    if not global_top_users:
        return await ctx.send("The global leaderboard is empty!")

    embed = discord.Embed(title="🏆 Global Leaderboard 🏆", description="Top 10 players across all servers!", color=discord.Color.purple())
    for i, (user_id, total_score) in enumerate(global_top_users):
        try: user_name = (await bot.fetch_user(int(user_id))).display_name
        except: user_name = f"Unknown User"
        emoji = ["🥇", "🥈", "🥉"][i] if i < 3 else "🔹"
        embed.add_field(name=f"{emoji} {i+1}. {user_name}", value=f"**{total_score}** total points", inline=False)
    await ctx.send(embed=embed)


# --- Fun Commands ---
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
    embed.add_field(name="Game Commands", value="`?flagstart` - Starts a game (or a vote).\n`?flagstop`\n`?flagskip`", inline=False)
    embed.add_field(name="Leaderboards", value="`?leaderboard` (This Server)\n`?gleaderboard` (Global)", inline=False)
    embed.add_field(name="Fun Commands", value="`?profile [@user]`\n`?height [@user]`\n`?serverlore` (Lvl 3+)", inline=False)
    embed.add_field(name="Settings", value="`?difficulty <level>`\n`?flaglog #channel`", inline=False)
    embed.set_footer(text="Admin commands are restricted and hidden.")
    await ctx.send(embed=embed)

@bot.command(name='gannounce')
@commands.is_owner()
async def global_announce(ctx, *, message: str):
    success_count, fail_count, unconfigured_guilds = 0, 0, []
    embed = discord.Embed(title="Global Announcement", description=message, color=discord.Color.red())
    embed.set_author(name=f"From Bot Developer: {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
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
            print(f"Failed in '{guild.name}': {e}"); fail_count += 1
        await asyncio.sleep(1)
    report_embed = discord.Embed(title="Global Announcement Report", color=discord.Color.green())
    report_embed.add_field(name="✅ Success", value=f"{success_count} servers")
    report_embed.add_field(name="❌ Failures", value=f"{fail_count} servers")
    await ctx.send(embed=report_embed)
    if unconfigured_guilds:
        dm_message = "The following servers have no log channel set:\n- " + "\n- ".join(unconfigured_guilds)
        try: await ctx.author.send(dm_message)
        except: await ctx.send("Could not DM you the list of unconfigured servers.")

# --- Error Handling ---
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound): return
    if isinstance(error, commands.MissingPermissions): await ctx.send("You don't have permission for that.")
    elif isinstance(error, commands.NotOwner): await ctx.send("`[ACCESS DENIED]`")
    elif isinstance(error, commands.MissingRequiredArgument): await ctx.send(f"You're missing an argument. Usage: `?{ctx.command.name} {ctx.command.signature}`")
    elif isinstance(error, (commands.MemberNotFound, commands.UserNotFound)): await ctx.send(f"Could not find that user.")
    else:
        print(f"Unhandled error in '{ctx.command}': {error}")
        await ctx.send("An unexpected error occurred.")

# --- Run the Bot ---
if __name__ == "__main__":
    bot.run(BOT_TOKEN)
