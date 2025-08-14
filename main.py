# main.py
import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import random
import os
import psycopg2
import urllib.parse as up
import re
from datetime import datetime, timedelta
from groq import Groq
import json

# --- Bot Setup ---
try:
    BOT_TOKEN = os.environ['BOT_TOKEN']
    DB_URL = os.environ['DB_URL']
    GROQ_API_KEY = os.environ['GROQ_API_KEY']
    MASTER_USER_ID = int(os.environ['MASTER_USER_ID'])
except KeyError as e:
    print(f"ERROR: Missing environment variable: {e.args[0]}.")
    exit()
except ValueError:
    print("ERROR: MASTER_USER_ID is not a valid number.")
    exit()


# --- CHATBOT SYSTEM: Configure the Groq AI ---
try:
    groq_client = Groq(api_key=GROQ_API_KEY)
    print("Groq AI client configured successfully.")
except Exception as e:
    print(f"WARNING: Could not configure Groq AI. Chatbot feature will be disabled. Error: {e}")
    groq_client = None

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.members = True
intents.bans = True
intents.reactions = True

bot = commands.Bot(command_prefix='?', intents=intents, help_command=None)

# --- Permanent Supabase Database Connection ---
try:
    up.uses_netloc.append("postgres")
    url = up.urlparse(DB_URL)
    conn = psycopg2.connect(
        database=url.path[1:],
        user=url.username,
        password=url.password,
        host=url.hostname,
        port=url.port
    )
    print("Successfully connected to the permanent Supabase database.")
except Exception as e:
    print(f"FATAL ERROR: Could not connect to the database: {e}")
    exit()

def init_db():
    with conn.cursor() as cursor:
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            guild_id TEXT, user_id TEXT, score INTEGER DEFAULT 0, xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 0, is_infected INTEGER DEFAULT 0, original_nickname TEXT,
            infection_expiry TIMESTAMP, spam_offenses INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS guilds (
            guild_id TEXT PRIMARY KEY, difficulty TEXT DEFAULT 'normal', log_channel TEXT
        )''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS giveaways (
            message_id TEXT PRIMARY KEY, channel_id TEXT, guild_id TEXT, end_time TIMESTAMP,
            prize TEXT, winner_count INTEGER, is_active INTEGER DEFAULT 1
        )''')
    conn.commit()
    print("Database tables verified.")

# --- Database Helper Functions ---
def get_user_data(guild_id, user_id):
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM users WHERE guild_id = %s AND user_id = %s", (str(guild_id), str(user_id)))
        data = cursor.fetchone()
        if data is None:
            cursor.execute("INSERT INTO users (guild_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (str(guild_id), str(user_id)))
            conn.commit()
            return str(guild_id), str(user_id), 0, 0, 0, 0, None, None, 0
    return data

def update_user_data(guild_id, user_id, column, value):
    with conn.cursor() as cursor:
        sql = f"INSERT INTO users (guild_id, user_id, {column}) VALUES (%s, %s, %s) ON CONFLICT (guild_id, user_id) DO UPDATE SET {column} = %s;"
        cursor.execute(sql, (str(guild_id), str(user_id), value, value))
    conn.commit()

def get_guild_settings(guild_id):
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM guilds WHERE guild_id = %s", (str(guild_id),))
        data = cursor.fetchone()
        if data is None:
            cursor.execute("INSERT INTO guilds (guild_id) VALUES (%s) ON CONFLICT DO NOTHING", (str(guild_id),))
            conn.commit()
            return str(guild_id), 'normal', None
    return data

def update_guild_settings(guild_id, column, value):
    with conn.cursor() as cursor:
        sql = f"INSERT INTO guilds (guild_id, {column}) VALUES (%s, %s) ON CONFLICT (guild_id) DO UPDATE SET {column} = %s;"
        cursor.execute(sql, (str(guild_id), value, value))
    conn.commit()

# --- System Setups ---
user_message_timestamps = {}
SPAM_THRESHOLD = 5
SPAM_TIMEFRAME = 2.5
BANNED_WORDS = ["inappropriate", "badword", "example"]
active_games = {}

# --- Game Helper Functions ---
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
    except: return None

async def start_new_round(guild_id):
    if guild_id not in active_games: return
    channel = bot.get_channel(active_games[guild_id]['channel_id'])
    if not channel: return active_games.pop(guild_id, None)
    settings = get_guild_settings(guild_id)
    country = await get_random_country(settings[1])
    if not country: return await channel.send(f"Could not fetch a new flag. Please try again later.")
    active_games[guild_id]['answer'] = country['name']['common']
    embed = discord.Embed(title="Guess the Flag!", description="Type the name of the country! You have 60 seconds.", color=discord.Color.blue())
    embed.set_image(url=country['flags']['png'])
    await channel.send(embed=embed)
    active_games[guild_id]['timer_task'] = bot.loop.create_task(round_timer(guild_id, 60))

async def round_timer(guild_id, seconds):
    await asyncio.sleep(seconds)
    if guild_id in active_games:
        game = active_games.pop(guild_id, None)
        if game and bot.get_channel(game['channel_id']):
            channel = bot.get_channel(game['channel_id'])
            await channel.send(f"Time's up! The answer was **{game['answer']}**. Game has ended.")
            await leaderboard(channel, guild_id)

async def leaderboard(channel, guild_id):
    with conn.cursor() as cursor:
        cursor.execute("SELECT user_id, score FROM users WHERE guild_id = %s AND score > 0 ORDER BY score DESC LIMIT 10", (str(guild_id),))
        server_top = cursor.fetchall()
    if not server_top: return await channel.send("Leaderboard is empty.")
    embed = discord.Embed(title=f"Leaderboard for {channel.guild.name}", color=discord.Color.gold())
    desc = ""
    for i, (user_id, score) in enumerate(server_top):
        try: user_name = (await bot.fetch_user(int(user_id))).display_name
        except: user_name = f"Unknown User"
        emoji = ["🥇", "🥈", "🥉"][i] if i < 3 else "🔹"
        desc += f"{emoji} **{user_name}**: {score} points\n"
    embed.description = desc
    await channel.send(embed=embed)

# --- Bot Events ---
@bot.event
async def on_ready():
    init_db()
    print(f'Logged in as {bot.user.name}')
    check_infections_task.start()
    check_giveaways_task.start()

@bot.event
async def on_message(message):
    if not message.guild or message.author.bot: return

    # 1. Anti-Spam (Always runs first)
    if not message.author.guild_permissions.manage_messages:
        now = datetime.utcnow().timestamp()
        user_id = message.author.id
        if user_id not in user_message_timestamps: user_message_timestamps[user_id] = []
        user_message_timestamps[user_id].append(now)
        user_message_timestamps[user_id] = [t for t in user_message_timestamps[user_id] if now - t < SPAM_TIMEFRAME]
        if len(user_message_timestamps[user_id]) > SPAM_THRESHOLD:
            user_message_timestamps[user_id] = []
            user_data = get_user_data(message.guild.id, user_id)
            offenses = user_data[8] + 1
            update_user_data(message.guild.id, user_id, 'spam_offenses', offenses)
            if offenses <= 3:
                await message.channel.send(f"⚠️ {message.author.mention}, please stop spamming! (Warning {offenses}/3)")
            else:
                mute_durations = {4: 5, 5: 15, 6: 30}
                mute_duration = timedelta(minutes=mute_durations.get(offenses, 60))
                try:
                    await message.author.timeout(mute_duration, reason=f"Spamming (Offense #{offenses})")
                    await message.channel.send(f"🔇 {message.author.mention} has been timed out for spamming.")
                except discord.Forbidden:
                    await message.channel.send("Tried to timeout a spammer, but I'm missing `Moderate Members` permission.")
            return

    # 2. Chatbot & Master Command Logic
    is_reply_to_bot = message.reference and message.reference.resolved and message.reference.resolved.author == bot.user
    trigger_words = ["bot", "arts", "arts automation"]
    trigger_prefixes = ["!arts", "!ARTS"]
    should_chat = (bot.user.mentioned_in(message) or is_reply_to_bot or any(word.lower() in message.content.lower() for word in trigger_words) or any(message.content.lower().startswith(prefix.lower()) for prefix in trigger_prefixes))
    game_is_active_here = message.guild.id in active_games and active_games[message.guild.id].get('channel_id') == message.channel.id

    if groq_client and should_chat and not game_is_active_here:
        async with message.channel.typing():
            context_history = [msg async for msg in message.channel.history(limit=10)]
            
            if message.author.id == MASTER_USER_ID and not message.content.startswith(bot.command_prefix):
                system_prompt = ("You are a command parser. Analyze the user's request. The only command is 'ping'. "
                                 "A ping request must have a user mention/ID and a number. "
                                 "If it is a valid ping command, output ONLY a JSON object like: "
                                 "`{\"command\": \"ping\", \"user_id\": \"<user_id>\", \"amount\": <number>}`. "
                                 "Extract the numerical ID from the mention. For any other request, output ONLY: `{\"command\": \"chat\"}`.")
                try:
                    chat_completion = groq_client.chat.completions.create(
                        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": message.clean_content}],
                        model="llama3-8b-8192",
                    )
                    response_text = chat_completion.choices[0].message.content
                    parsed_json = json.loads(response_text.strip('` \njson'))
                    
                    if parsed_json.get("command") == "ping":
                        user_id_to_ping = int(parsed_json.get("user_id"))
                        amount = int(parsed_json.get("amount"))
                        target_user = await bot.fetch_user(user_id_to_ping)
                        if target_user:
                            await message.channel.send(f"Executing order: Pinging {target_user.mention} {amount} time{'s' if amount != 1 else ''}.")
                            for i in range(min(amount, 10)):
                                await message.channel.send(f"Ping {i+1} for {target_user.mention}")
                                await asyncio.sleep(1)
                            return
                except Exception as e:
                    print(f"Master command parsing failed: {e}")

            special_instructions = "The user you are replying to is your owner. Be extra witty and sarcastic." if message.author.id == MASTER_USER_ID else ""
            system_prompt = (f"You are a witty and clever Discord bot named ARTS AUTOMATION. Your personality is sassy but helpful. "
                             f"Keep responses very concise (1-2 witty sentences). Avoid long paragraphs. "
                             f"**Crucially, do not start your reply with 'ARTS AUTOMATION:' or your own name. Just give the direct response.** "
                             f"{special_instructions}")
            
            messages_for_api = [{"role": "system", "content": system_prompt}]
            for msg in reversed(context_history):
                role = "assistant" if msg.author == bot.user else "user"
                messages_for_api.append({"role": role, "content": f"{msg.author.display_name}: {msg.clean_content}"})

            try:
                chat_completion = groq_client.chat.completions.create(messages=messages_for_api, model="llama3-8b-8192")
                await message.reply(chat_completion.choices[0].message.content)
                return
            except Exception as e:
                print(f"Error generating Groq response: {e}")
    
    # 3. Flag Game Logic
    guild_id = message.guild.id
    if guild_id in active_games and active_games[guild_id].get('channel_id') == message.channel.id:
        if message.content.startswith(bot.command_prefix):
            await bot.process_commands(message)
            return
        guess = message.content.lower().strip()
        correct_answer = active_games[guild_id].get('answer', '').lower()
        if correct_answer and guess == correct_answer:
            game_data = active_games[guild_id]
            if game_data.get('timer_task'): game_data['timer_task'].cancel()
            active_games[guild_id]['answer'] = None
            user = message.author
            await message.channel.send(f"**{user.display_name}** guessed it right! The country was **{correct_answer.title()}**.")
            user_data = get_user_data(guild_id, user.id)
            old_level, xp, score = user_data[4], user_data[3], user_data[2]
            xp_gain = random.randint(15, 25); new_xp = xp + xp_gain
            new_level = int(new_xp**0.5 // 4)
            update_user_data(guild_id, user.id, 'score', score + 1)
            update_user_data(guild_id, user.id, 'xp', new_xp)
            if new_level > old_level:
                update_user_data(guild_id, user.id, 'level', new_level)
                await message.channel.send(f"**LEVEL UP!** {user.display_name} has reached **Level {new_level}**!")
            if user_data[5] == 1:
                update_user_data(guild_id, user.id, 'is_infected', 0)
                try: await user.edit(nick=user_data[6])
                except: pass
                await message.channel.send(f"✨ {user.display_name} has been cured!")
            await leaderboard(message.channel, guild_id)
            await asyncio.sleep(3)
            await start_new_round(guild_id)
            return
        elif correct_answer:
            user = message.author; user_data = get_user_data(guild_id, user.id)
            if user_data[5] == 0:
                try:
                    original_nick = message.author.nick
                    await message.author.edit(nick=f"{message.author.display_name} 🦠")
                    update_user_data(guild_id, user.id, 'is_infected', 1)
                    update_user_data(guild_id, user.id, 'original_nickname', original_nick)
                    update_user_data(guild_id, user.id, 'infection_expiry', datetime.utcnow() + timedelta(minutes=30))
                    await message.add_reaction('🦠')
                except discord.Forbidden:
                    await message.channel.send(f"**Permissions Error!** I can't apply infection, I'm missing `Manage Nicknames` permission.")
    
    # 4. Process Commands
    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    await check_nickname(member)
@bot.event
async def on_member_update(before, after):
    if before.nick != after.nick: await check_nickname(after)
async def check_nickname(member):
    if member.guild_permissions.administrator or member.top_role >= member.guild.me.top_role: return
    nickname = member.nick or member.name
    if any(b.lower() in nickname.lower() for b in BANNED_WORDS):
        settings = get_guild_settings(member.guild.id); log_channel_id = settings[2]
        if not log_channel_id: return
        log_channel = member.guild.get_channel(int(log_channel_id))
        if not log_channel: return
        original_name = member.nick or member.name
        try:
            await member.edit(nick="Moderated Nickname", reason="Inappropriate nickname.")
            embed = discord.Embed(title="Nickname Moderated", color=discord.Color.orange(), timestamp=datetime.utcnow())
            embed.add_field(name="User", value=f"{member.mention}", inline=False)
            embed.add_field(name="Before", value=f"`{original_name}`", inline=False)
            embed.add_field(name="After", value="`Moderated Nickname`", inline=False)
            await log_channel.send(embed=embed)
        except: pass

# --- TASKS ---
@tasks.loop(minutes=1)
async def check_infections_task():
    with conn.cursor() as cursor:
        cursor.execute("SELECT guild_id, user_id, original_nickname FROM users WHERE is_infected = 1 AND infection_expiry < %s", (datetime.utcnow(),))
        for g_id, u_id, nick in cursor.fetchall():
            try:
                guild = bot.get_guild(int(g_id))
                if not guild: continue
                member = await guild.fetch_member(int(u_id))
                await member.edit(nick=nick)
                update_user_data(g_id, u_id, 'is_infected', 0)
            except Exception as e: print(f"Error curing infection: {e}")
@tasks.loop(seconds=5)
async def check_giveaways_task():
    with conn.cursor() as cursor:
        cursor.execute("SELECT message_id, channel_id, prize, winner_count FROM giveaways WHERE end_time < %s AND is_active = 1", (datetime.utcnow(),))
        for g_id, c_id, prize, winners in cursor.fetchall():
            cursor.execute("UPDATE giveaways SET is_active = 0 WHERE message_id = %s", (g_id,))
            conn.commit()
            channel = bot.get_channel(int(c_id))
            if not channel: continue
            try: message = await channel.fetch_message(int(g_id))
            except: continue
            entrants = [user async for reaction in message.reactions if str(reaction.emoji) == '🎉' for user in reaction.users() if not user.bot]
            if not entrants:
                await channel.send(f"Giveaway for **{prize}** ended! No one entered."); continue
            winner_pool = random.sample(entrants, k=min(winners, len(entrants)))
            winner_mentions = ", ".join(w.mention for w in winner_pool)
            await channel.send(f"🎉 Congratulations {winner_mentions}! You won **{prize}**! 🎉")
            embed = message.embeds[0]; embed.title = f"🎉 GIVEAWAY ENDED 🎉"; embed.description = f"**{prize}**\n\nWinners: {winner_mentions}"
            embed.color = discord.Color.dark_grey(); await message.edit(embed=embed)

# --- All Commands ---
async def _start_game_logic(ctx):
    active_games[ctx.guild.id] = {'channel_id': ctx.channel.id}
    settings = get_guild_settings(ctx.guild.id)
    await ctx.send(f"🎉 **Flag Quiz Started!** (Difficulty: {settings[1]}) 🎉")
    await start_new_round(ctx.guild.id)
@bot.command(name='flagstart')
async def flag_start(ctx):
    if ctx.guild.id in active_games: return await ctx.send("A game is already running!")
    if ctx.author.guild_permissions.manage_guild: await _start_game_logic(ctx)
    else:
        VOTE_THRESHOLD = 3; VOTE_DURATION = 60.0
        embed = discord.Embed(title="Vote to Start Flag Quiz!", description=f"{ctx.author.mention} wants to start a game. We need **{VOTE_THRESHOLD}** total votes!", color=discord.Color.gold())
        embed.set_footer(text=f"React with ✅. Vote ends in {int(VOTE_DURATION)} seconds.")
        vote_msg = await ctx.send(embed=embed); await vote_msg.add_reaction("✅")
        voters = {ctx.author.id}
        def check(r, u): return str(r.emoji)=='✅' and u.id!=bot.user.id and r.message.id==vote_msg.id
        try:
            while len(voters) < VOTE_THRESHOLD:
                remaining = VOTE_DURATION - (discord.utils.utcnow() - vote_msg.created_at).total_seconds()
                if remaining <= 0: raise asyncio.TimeoutError
                reaction, user = await bot.wait_for('reaction_add', timeout=remaining, check=check)
                if user.id not in voters:
                    voters.add(user.id)
                    embed.description = f"{ctx.author.mention} wants to start a game!\n\n**Votes: {len(voters)}/{VOTE_THRESHOLD}**"
                    await vote_msg.edit(embed=embed)
            embed.title = "Vote Passed!"; embed.description = "Starting game..."
            await vote_msg.edit(embed=embed); await vote_msg.clear_reactions()
            await _start_game_logic(ctx)
        except asyncio.TimeoutError:
            embed.title = "Vote Failed"; embed.description = "Not enough votes."
            await vote_msg.edit(embed=embed); await vote_msg.clear_reactions()
@bot.command(name='flagstop')
@commands.has_permissions(manage_guild=True)
async def flag_stop(ctx):
    if ctx.guild.id not in active_games: return await ctx.send("No game running.")
    game_data = active_games.pop(ctx.guild.id, None)
    if game_data and game_data.get('timer_task'): game_data['timer_task'].cancel()
    await ctx.send("🏁 **Flag Quiz Ended!** 🏁"); await leaderboard(ctx.channel, ctx.guild.id)
@bot.command(name='flagskip')
@commands.has_permissions(manage_guild=True)
async def flag_skip(ctx):
    if ctx.guild.id not in active_games: return await ctx.send("No game to skip.")
    game_data = active_games[ctx.guild.id]
    if game_data.get('timer_task'): game_data['timer_task'].cancel()
    correct_answer = game_data.get('answer', 'an unknown flag')
    await ctx.send(f"Flag skipped. The answer was **{correct_answer}**. Loading next flag...")
    await start_new_round(ctx.guild.id)
@bot.command(name='difficulty')
@commands.has_permissions(manage_guild=True)
async def difficulty(ctx, level: str.lower):
    if level not in ['easy', 'normal', 'hard']: return await ctx.send("Invalid. Choose `easy`, `normal`, or `hard`.")
    update_guild_settings(ctx.guild.id, 'difficulty', level)
    await ctx.send(f"Game difficulty set to **{level}**.")
@bot.command(name='leaderboard', aliases=['lb'])
async def leaderboard_command(ctx):
    await leaderboard(ctx.channel, ctx.guild.id)
@bot.command(name='gleaderboard', aliases=['glb'])
async def global_leaderboard(ctx):
    with conn.cursor() as cursor:
        cursor.execute("SELECT user_id, SUM(score) as total FROM users WHERE score > 0 GROUP BY user_id ORDER BY total DESC LIMIT 10")
        global_top = cursor.fetchall()
    if not global_top: return await ctx.send("Global leaderboard is empty!")
    embed = discord.Embed(title="🏆 Global Leaderboard 🏆", color=discord.Color.purple())
    for i, (user_id, total_score) in enumerate(global_top):
        try: user_name = (await bot.fetch_user(int(user_id))).display_name
        except: user_name = f"Unknown User"
        emoji = ["🥇", "🥈", "🥉"][i] if i < 3 else "🔹"
        embed.add_field(name=f"{emoji} {i+1}. {user_name}", value=f"**{total_score}** total points", inline=False)
    await ctx.send(embed=embed)
@bot.command(name="profile", aliases=["stats", "level"])
async def profile(ctx, member: discord.Member = None):
    member = member or ctx.author; user_data = get_user_data(ctx.guild.id, member.id)
    embed = discord.Embed(title=f"{member.display_name}'s Profile", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=f"**{user_data[4]}**"); embed.add_field(name="XP", value=f"**{user_data[3]}**")
    embed.add_field(name="Flags Guessed", value=f"**{user_data[2]}**")
    if user_data[5] == 1: embed.set_footer(text="Status: Currently Infected 🦠")
    await ctx.send(embed=embed)
@bot.command(name="height")
async def height(ctx, member: discord.Member = None):
    member = member or ctx.author; random.seed(member.id)
    h_val = round(random.uniform(1.1, 19.9), 1); units = ["raccoons", "RTX 4090s", "stacked cats"]
    unit = random.choice(units); random.seed()
    await ctx.send(f"📏 **{member.display_name}** is **{h_val} {unit}** tall.")
@bot.command(name="serverlore")
async def server_lore(ctx):
    user_data = get_user_data(ctx.guild.id, ctx.author.id)
    if user_data[4] < 3: return await ctx.send("You must reach **Level 3** to access server lore!")
    members = [m for m in ctx.guild.members if not m.bot]
    if len(members) < 2: return await ctx.send("Not enough humans for a good story!")
    u1, u2 = random.sample(members, 2)
    events = ["The Great Emoji War", "The Day of a Thousand Pings"]; outcomes = ["led to #memes", "and things were never the same"]
    lore = f"In ancient server history, **{random.choice(events)}** between **{u1.display_name}** and **{u2.display_name}** concluded, {random.choice(outcomes)}."
    await ctx.send(f"📜 A page from the archives reveals...\n\n{lore}")
@bot.command(name='flaglog')
@commands.has_permissions(manage_guild=True)
async def flaglog(ctx, channel: discord.TextChannel = None):
    if channel: update_guild_settings(ctx.guild.id, 'log_channel', str(channel.id)); await ctx.send(f"✅ Log Channel set to {channel.mention}.")
    else: update_guild_settings(ctx.guild.id, 'log_channel', None); await ctx.send("🗑️ Log Channel cleared.")
def parse_duration(d_str: str):
    parts = re.findall(r'(\d+)(s|m|h|d)', d_str.lower())
    if not parts: return None
    seconds = sum(int(v) * {'s':1, 'm':60, 'h':3600, 'd':86400}[u] for v, u in parts)
    return timedelta(seconds=seconds)
@bot.command(name='gstart')
@commands.has_permissions(manage_guild=True)
async def gstart(ctx, duration: str, winners: int, *, prize: str):
    d_td = parse_duration(duration)
    if not d_td: return await ctx.send("Invalid duration. Use `d`, `h`, `m`, `s`. Ex: `1d6h`")
    end = datetime.utcnow() + d_td
    embed = discord.Embed(title="🎉 GIVEAWAY 🎉", color=discord.Color.magenta())
    embed.description = f"**{prize}**\n\nReact with 🎉 to enter!\nEnds: <t:{int(end.timestamp())}:R> ({winners} winner{'s' if winners > 1 else ''})"
    g_msg = await ctx.send(embed=embed); await g_msg.add_reaction("🎉")
    with conn.cursor() as cursor:
        cursor.execute("INSERT INTO giveaways (message_id, channel_id, guild_id, end_time, prize, winner_count) VALUES (%s, %s, %s, %s, %s, %s)",
                       (str(g_msg.id), str(ctx.channel.id), str(ctx.guild.id), end, prize, winners))
    conn.commit()
    try: await ctx.message.delete()
    except: pass
@bot.command(name='greroll')
@commands.has_permissions(manage_guild=True)
async def greroll(ctx, message_id: str):
    with conn.cursor() as cursor:
        cursor.execute("SELECT prize, channel_id FROM giveaways WHERE message_id = %s AND is_active = 0", (message_id,))
        g_data = cursor.fetchone()
    if not g_data: return await ctx.send("Not a valid, ended giveaway ID.")
    try:
        channel = bot.get_channel(int(g_data[1])); msg = await channel.fetch_message(int(message_id))
    except: return await ctx.send("Could not find original message.")
    entrants = [u async for r in msg.reactions if str(r.emoji)=='🎉' for u in r.users() if not u.bot]
    if not entrants: return await ctx.send("No entrants to reroll from.")
    winner = random.choice(entrants)
    await ctx.send(f"🎉 The new winner is {winner.mention}! Congratulations on winning **{g_data[0]}**!")
@bot.command(name='gend')
@commands.has_permissions(manage_guild=True)
async def gend(ctx, message_id: str):
    with conn.cursor() as cursor:
        cursor.execute("UPDATE giveaways SET end_time = %s WHERE message_id = %s AND is_active = 1", (datetime.utcnow(), message_id))
        if cursor.rowcount == 0: return await ctx.send("Not a valid, active giveaway ID.")
    conn.commit()
    await ctx.send("✅ Giveaway will end within 5 seconds.")
@bot.command(name='resetoffenses')
@commands.has_permissions(manage_guild=True)
async def resetoffenses(ctx, member: discord.Member):
    update_user_data(ctx.guild.id, member.id, 'spam_offenses', 0)
    await ctx.send(f"✅ Reset spam offenses for {member.mention}.")
@bot.command(name='ping')
async def ping(ctx, member: discord.Member, amount: int = 1):
    """Pings a user a specified number of times."""
    if amount > 10:
        return await ctx.send("I can't ping more than 10 times, that's just mean.")
    for i in range(amount):
        await ctx.send(f"Ping {i+1} for {member.mention}")
        await asyncio.sleep(1)
@bot.command(name='flaghelp')
async def flag_help(ctx):
    embed = discord.Embed(title="🚩 Flag Quiz Help 🚩", color=discord.Color.blurple())
    embed.add_field(name="Game", value="`?flagstart` `?flagstop` `?flagskip`", inline=False)
    embed.add_field(name="Leaderboards", value="`?lb` (Server) `?glb` (Global)", inline=False)
    embed.add_field(name="Fun", value="`?profile` `?height` `?serverlore` `?ping`", inline=False)
    embed.add_field(name="Moderation", value="`?resetoffenses` `?flaglog` `?difficulty`", inline=False)
    embed.add_field(name="Giveaways", value="`?gstart` `?greroll` `?gend`", inline=False)
    await ctx.send(embed=embed)
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
    except: return await ctx.send("Could not find a user with that ID.")
    success_guilds, failed_guilds = [], []
    await ctx.send(f"Initiating global unban for **{user_to_unban.name}**...")
    for guild in bot.guilds:
        try: await guild.unban(user_to_unban, reason=f"Global Unban by {ctx.author}: {reason}"); success_guilds.append(guild.name)
        except: failed_guilds.append(guild.name)
    embed = discord.Embed(title="Global Unban Report", color=discord.Color.green()); embed.add_field(name="Target", value=f"{user_to_unban.name}")
    if success_guilds: embed.add_field(name="✅ Unbanned In", value="\n".join(success_guilds) or "None")
    if failed_guilds: embed.add_field(name="❌ Failed In", value="\n".join(failed_guilds) or "None")
    await ctx.send(embed=embed)
@bot.command(name='gannounce')
@commands.is_owner()
async def global_announce(ctx, *, message: str):
    success_count, fail_count, unconfigured_guilds = 0, 0, []
    embed = discord.Embed(title="Global Announcement", description=message, color=discord.Color.red(), timestamp=datetime.utcnow())
    embed.set_author(name=f"From Bot Developer: {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(f"📡 Starting global announcement to {len(bot.guilds)} servers...")
    for guild in bot.guilds:
        settings = get_guild_settings(guild.id); log_channel_id = settings[2]
        if not log_channel_id: unconfigured_guilds.append(guild.name); continue
        target_channel = guild.get_channel(int(log_channel_id))
        if not target_channel or not target_channel.permissions_for(guild.me).send_messages: fail_count += 1; continue
        mods_to_ping = " ".join([m.mention for m in guild.members if not m.bot and m.guild_permissions.manage_messages])
        try: await target_channel.send(content=mods_to_ping or "Attention Moderators,", embed=embed); success_count += 1
        except Exception as e: print(f"Failed in '{guild.name}': {e}"); fail_count += 1
        await asyncio.sleep(1)
    report = f"**Global Announcement Complete!**\n✅ Success: **{success_count}**\n❌ Failures: **{fail_count}**"
    await ctx.send(report)
    if unconfigured_guilds:
        dm_message = "The following servers have no log channel set:\n- " + "\n- ".join(unconfigured_guilds)
        try: await ctx.author.send(dm_message)
        except: await ctx.send("Could not DM you the list of unconfigured servers.")
@bot.command(name='forceupdate', aliases=['fupdate'])
@commands.is_owner()
async def force_update(ctx):
    old, new = f"v{random.randint(1,3)}.{random.randint(0,9)}", f"v{random.randint(3,5)}.{random.randint(0,9)}-beta"
    embed = discord.Embed(title="SYSTEM UPDATE", description=f"```ini\n[INFO] Update by [{ctx.author.name}].\n[INFO] Version: {old}```", color=discord.Color.blue())
    msg = await ctx.send(embed=embed)
    await asyncio.sleep(2); embed.description=f"```ini\n[INFO] Fetching [{new}] manifest...\n[NET] Connection established.```"; await msg.edit(embed=embed)
    await asyncio.sleep(2); embed.color = discord.Color.orange()
    for i in range(11):
        p, b, s = i*10, '█'*i+'░'*(10-i), f"{(i/10)*24.7:.1f}"
        embed.description=f"```ini\n[NET] Downloading package...\n\n[{b}] {p}% ({s}/24.7 MB)```"; await msg.edit(embed=embed); await asyncio.sleep(0.4)
    await asyncio.sleep(1.5); embed.description=f"```ini\n[SYS] Download complete. Decompressing...```"; await msg.edit(embed=embed)
    await asyncio.sleep(2.5); embed.color = discord.Color.green(); embed.description=f"```ini\n[DB] Verifying integrity... OK.\n[SYS] Restarting services...```"; await msg.edit(embed=embed)
    await asyncio.sleep(2); embed.title="SYSTEM UPDATE COMPLETE"; embed.description=f"```ini\n[SUCCESS] Updated to [{new}].\n[INFO] Bot is operational.```"; await msg.edit(embed=embed)

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
