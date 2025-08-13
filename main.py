# main.py
import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import random
import os
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

# We need to define the owner ID for the new command to work
# It will default to the person who owns the bot application on the Discord Developer Portal
bot = commands.Bot(command_prefix='?', intents=intents, help_command=None)


# --- List of random replies for the specific user ---
RANDOM_REPLIES = [
    "My sensors indicate your input is... suboptimal.", "Analyzing message... Conclusion: irrelevant.",
    "I'm trying to host a game here, you know.", "Error 404: Point not found.",
    "Your message has been successfully routed to the void.", "Do you have a permit for that level of nonsense?",
    "My logic circuits are fizzing. Please stop.", "That does not compute."
]

# --- Game State Variables ---
game_states = {}

class GameState:
    """A class to hold the state of a game in a specific server."""
    def __init__(self):
        self.is_running = False
        self.player_stats = {}  # {user_id: {'score': 0, 'xp': 0, 'level': 0}}
        self.current_flag_country = None
        self.timer_task = None
        self.message_channel = None
        self.difficulty = "normal"
        self.infected_users = {} # {user_id: expiry_timestamp}

# --- Helper Functions ---
async def get_random_country(difficulty="normal"):
    population_filter = 0
    if difficulty == "easy": population_filter = 15000000
    elif difficulty == "normal": population_filter = 1000000
    try:
        async with aiohttp.ClientSession() as session:
            api_url = 'https://restcountries.com/v3.1/all?fields=name,flags,cca2,population'
            async with session.get(api_url) as response:
                if response.status == 200:
                    countries = await response.json()
                    valid_countries = [c for c in countries if 'common' in c.get('name', {}) and 'png' in c.get('flags', {}) and c.get('population', 0) > population_filter]
                    return random.choice(valid_countries) if valid_countries else None
                else:
                    print(f"Error fetching country data: {response.status}")
                    return None
    except aiohttp.ClientError as e:
        print(f"AIOHTTP Error: {e}")
        return None

async def start_new_round(guild_id):
    state = game_states.get(guild_id)
    if not state or not state.is_running: return

    country = await get_random_country(state.difficulty)
    if not country:
        await state.message_channel.send(f"Could not fetch a new flag. Please try again later.")
        return

    state.current_flag_country = country
    country_name = country['name']['common']
    print(f"New round for guild {guild_id}: The country is {country_name}")

    embed = discord.Embed(title="Guess the Flag!", description="Type the name of the country in the chat. You have 60 seconds!", color=discord.Color.blue())
    embed.set_image(url=country['flags']['png'])
    await state.message_channel.send(embed=embed)

    state.timer_task = bot.loop.create_task(round_timer(guild_id, 60))

async def round_timer(guild_id, seconds):
    await asyncio.sleep(seconds)
    state = game_states.get(guild_id)
    if state and state.is_running and state.current_flag_country:
        country_name = state.current_flag_country['name']['common']
        channel = state.message_channel
        await channel.send(f"Time's up! The correct answer was **{country_name}**. No one guessed in time, so the game has ended. Use `?flagstart` to play again.")
        if guild_id in game_states:
            del game_states[guild_id]

# --- Bot Events ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    check_infections_task.start()
    try:
        channel = bot.get_channel(1347134723549302867)
        if channel and channel.permissions_for(channel.guild.me).send_messages:
            await channel.send("Bot systems reloaded. All features active.")
    except Exception as e:
        print(f"An error occurred while trying to send the update message: {e}")


@bot.event
async def on_message(message):
    if message.author.bot: return
    guild_id = message.guild.id
    state = game_states.get(guild_id)

    if message.author.id == 1342499092739391538 and state and state.is_running:
        await message.reply(random.choice(RANDOM_REPLIES))
        return

    if state and state.is_running and state.current_flag_country and message.channel == state.message_channel:
        guess = message.content.lower().strip()
        correct_answer_name = state.current_flag_country['name']['common'].lower()
        user = message.author

        if guess == correct_answer_name:
            if state.timer_task: state.timer_task.cancel()
            
            correct_country_info = state.current_flag_country
            state.current_flag_country = None

            if user.id not in state.player_stats:
                state.player_stats[user.id] = {'score': 0, 'xp': 0, 'level': 0}
            
            player_data = state.player_stats[user.id]
            old_level = player_data['level']
            xp_gain = random.randint(15, 25)

            player_data['score'] += 1
            player_data['xp'] += xp_gain
            player_data['level'] = int(player_data['xp']**0.5 // 4)

            await message.add_reaction('✅')
            await message.channel.send(f"**{user.display_name}** guessed it right! The country was **{correct_country_info['name']['common']}**. They get 1 point and **{xp_gain} XP**!")
            
            if player_data['level'] > old_level:
                await message.channel.send(f"**LEVEL UP!** {user.display_name} has reached **Level {player_data['level']}**!")

            if user.id in state.infected_users:
                del state.infected_users[user.id]
                try:
                    await user.edit(nick=None) 
                    await message.channel.send(f"✨ {user.display_name} has been cured of the flag infection!")
                except discord.Forbidden: pass
            
            await show_leaderboard(message.channel, guild_id)
            await asyncio.sleep(3)
            await start_new_round(guild_id)

        else:
            if user.id not in state.infected_users:
                try:
                    await user.edit(nick=f"{user.display_name} 🦠")
                    state.infected_users[user.id] = datetime.utcnow() + timedelta(minutes=30)
                    await message.add_reaction('🦠')
                except discord.Forbidden:
                    await message.channel.send(f"**Permissions Error!** I can't apply the infection because I'm missing the `Manage Nicknames` permission.")
                    state.infected_users[user.id] = datetime.utcnow() + timedelta(minutes=30)
                except Exception as e:
                    print(f"An unexpected error occurred during infection: {e}")

    await bot.process_commands(message)

@tasks.loop(minutes=1)
async def check_infections_task():
    now = datetime.utcnow()
    for guild_id, state in list(game_states.items()):
        if not state.is_running: continue
        expired = [uid for uid, expiry in list(state.infected_users.items()) if now > expiry]
        for user_id in expired:
            del state.infected_users[user_id]
            try:
                guild = bot.get_guild(guild_id)
                if guild:
                    member = await guild.fetch_member(user_id)
                    await member.edit(nick=None)
                    print(f"Cured {member.display_name} in {guild.name} via timeout.")
            except Exception as e:
                print(f"Error during infection cure: {e}")


# --- Commands ---

@bot.command(name='flagstart')
@commands.has_permissions(manage_guild=True)
async def flag_start(ctx):
    guild_id = ctx.guild.id
    if guild_id in game_states and game_states[guild_id].is_running:
        return await ctx.send("A game is already running in this server!")
    if guild_id not in game_states:
        game_states[guild_id] = GameState()
    state = game_states[guild_id]
    state.is_running = True
    state.message_channel = ctx.channel
    await ctx.send(f"🎉 **Flag Quiz Started!** (Difficulty: {state.difficulty}) 🎉\nGet ready!")
    await asyncio.sleep(2)
    await start_new_round(guild_id)

@bot.command(name='flagstop')
@commands.has_permissions(manage_guild=True)
async def flag_stop(ctx):
    guild_id = ctx.guild.id
    state = game_states.get(guild_id)
    if not state or not state.is_running:
        return await ctx.send("There is no game currently running.")
    if state.timer_task: state.timer_task.cancel()
    await ctx.send("🏁 **Flag Quiz Ended!** 🏁\nHere is the final leaderboard:")
    await show_leaderboard(ctx.channel, guild_id)
    del game_states[guild_id]

@bot.command(name='flagskip')
@commands.has_permissions(manage_guild=True)
async def flag_skip(ctx):
    guild_id = ctx.guild.id
    state = game_states.get(guild_id)
    if not state or not state.is_running:
        return await ctx.send("There is no game running to skip a flag from.")
    if state.timer_task: state.timer_task.cancel()
    if state.current_flag_country:
        correct_answer = state.current_flag_country['name']['common']
        await ctx.send(f"The flag has been skipped. The correct answer was **{correct_answer}**. Loading the next flag...")
    else:
        await ctx.send("The flag has been skipped. Loading the next flag...")
    await start_new_round(guild_id)

@bot.command(name='difficulty')
@commands.has_permissions(manage_guild=True)
async def difficulty(ctx, level: str):
    level = level.lower()
    if level not in ['easy', 'normal', 'hard']:
        return await ctx.send("Invalid difficulty. Please choose from `easy`, `normal`, or `hard`.")
    guild_id = ctx.guild.id
    if guild_id not in game_states:
        game_states[guild_id] = GameState()
    game_states[guild_id].difficulty = level
    await ctx.send(f"Game difficulty has been set to **{level}**.")


@bot.command(name='leaderboard')
async def leaderboard_command(ctx):
    guild_id = ctx.guild.id
    state = game_states.get(guild_id)
    if not state or not state.is_running:
        return await ctx.send("No game is running. Start one with `?flagstart`.")
    await show_leaderboard(ctx.channel, guild_id)

@bot.command(name="profile", aliases=["stats", "level"])
async def profile(ctx, member: discord.Member = None):
    member = member or ctx.author
    state = game_states.get(ctx.guild.id)
    if not state or member.id not in state.player_stats:
        return await ctx.send(f"{member.display_name} hasn't played yet!")
    
    player_data = state.player_stats[member.id]
    embed = discord.Embed(title=f"{member.display_name}'s Profile", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=f"**{player_data['level']}**", inline=True)
    embed.add_field(name="XP", value=f"**{player_data['xp']}**", inline=True)
    embed.add_field(name="Flags Guessed", value=f"**{player_data['score']}**", inline=True)
    if member.id in state.infected_users:
        embed.set_footer(text="Status: Currently Infected 🦠")
    await ctx.send(embed=embed)

@bot.command(name="height")
async def height(ctx, member: discord.Member = None):
    member = member or ctx.author
    random.seed(member.id)
    height_val = round(random.uniform(1.1, 19.9), 1)
    units = ["raccoons", "slices of pizza", "RTX 4090s", "stacked cats"]
    unit = random.choice(units)
    random.seed()
    await ctx.send(f"📏 After careful measurement, **{member.display_name}** is **{height_val} {unit}** tall.")

@bot.command(name="serverlore")
async def server_lore(ctx):
    state = game_states.get(ctx.guild.id)
    if not state or ctx.author.id not in state.player_stats:
        return await ctx.send("You need to play the game first to access server lore!")
    
    player_level = state.player_stats[ctx.author.id]['level']
    if player_level < 3:
        return await ctx.send("You must reach **Level 3** to access the server's ancient lore!")
    
    valid_members = [m for m in ctx.guild.members if not m.bot]
    if len(valid_members) < 2: return await ctx.send("We need at least two humans for a good story!")
    
    user1, user2 = random.sample(valid_members, 2)
    events = ["The Great Emoji War", "The Day of a Thousand Pings", "The Prophecy of the Lost Meme"]
    outcomes = ["which led to the creation of #memes", "and things were never the same", "which is why we can't have nice things"]
    lore = f"In ancient server history, **{random.choice(events)}** between **{user1.display_name}** and **{user2.display_name}** concluded, {random.choice(outcomes)}."
    await ctx.send(f"📜 A page from the archives reveals...\n\n{lore}")

@bot.command(name='flaghelp')
async def flag_help(ctx):
    embed = discord.Embed(title="🚩 Flag Quiz Help 🚩", description="Here are the available commands for the flag game:", color=discord.Color.blurple())
    embed.add_field(name="Game Commands", value="`?flagstart`\n`?flagstop`\n`?flagskip`\n`?leaderboard`", inline=True)
    embed.add_field(name="Fun Commands", value="`?profile [@user]`\n`?height [@user]`\n`?serverlore` (Lvl 3+)", inline=True)
    embed.add_field(name="Settings", value="`?difficulty <level>` (easy, normal, hard)", inline=False)
    embed.set_footer(text="Admin commands (?gban, ?gannounce) are restricted and hidden.")
    await ctx.send(embed=embed)

# --- Admin Commands ---
@bot.command(name='forceupdate')
@commands.has_permissions(manage_guild=True)
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
async def gban(ctx, member: discord.Member, *, reason: str = "No reason provided."):
    if ctx.author.id != 794610250375364629: return await ctx.send("`[ACCESS DENIED]`")
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
async def gunban(ctx, user_id: int, *, reason: str = "No reason provided."):
    if ctx.author.id != 794610250375364629: return await ctx.send("`[ACCESS DENIED]`")
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

#==============================================================#
#--- NEW FEATURE ADDED HERE ---
#==============================================================#
@bot.command(name='gannounce')
@commands.is_owner() # This ensures only the bot owner can use this command
async def global_announce(ctx, *, message: str):
    """Sends an announcement to every server the bot is in, pinging mods."""
    
    success_count = 0
    fail_count = 0
    
    embed = discord.Embed(title="Global Announcement", description=message, color=discord.Color.blue())
    embed.set_author(name=f"Announcement from {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
    embed.set_footer(text="This is a global message sent to all servers.")

    await ctx.send(f"Starting global announcement to {len(bot.guilds)} servers. This may take a moment...")

    for guild in bot.guilds:
        # Find a suitable channel to post in
        target_channel = None
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                target_channel = channel
                break # Found a valid channel, stop looking

        if not target_channel:
            print(f"Failed to send to '{guild.name}': No channel with send permissions.")
            fail_count += 1
            continue

        # Find all members with 'Manage Messages' permission
        mods_to_ping = [
            member.mention for member in guild.members 
            if not member.bot and member.guild_permissions.manage_messages
        ]
        
        ping_string = " ".join(mods_to_ping) if mods_to_ping else "Attention Moderators,"

        try:
            await target_channel.send(content=ping_string, embed=embed)
            success_count += 1
            print(f"Successfully sent announcement to '{guild.name}' in #{target_channel.name}")
        except Exception as e:
            print(f"Failed to send to '{guild.name}' in #{target_channel.name}: {e}")
            fail_count += 1
        
        await asyncio.sleep(1) # Small delay to avoid rate limits

    await ctx.send(f"**Global Announcement Complete!**\n✅ Sent successfully to **{success_count}** servers.\n❌ Failed in **{fail_count}** servers.")


# --- Helper function for leaderboard ---
async def show_leaderboard(channel, guild_id):
    state = game_states.get(guild_id)
    if not state or not state.player_stats: return
    sorted_scores = sorted(state.player_stats.items(), key=lambda item: item[1]['score'], reverse=True)
    embed = discord.Embed(title="Leaderboard", color=discord.Color.gold())
    description = ""
    for i, (user_id, data) in enumerate(sorted_scores[:10]):
        try: user = await bot.fetch_user(user_id); user_name = user.display_name
        except discord.NotFound: user_name = f"User (ID: {user_id})"
        emoji = ["🥇", "🥈", "🥉"][i] if i < 3 else ""
        description += f"{emoji}**{user_name}**: {data['score']} points\n"
    embed.description = description
    await channel.send(embed=embed)

# --- Error Handling ---
@gban.error
@gunban.error
@global_announce.error # Added error handler for the new command
async def admin_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send(f"Missing argument.")
    elif isinstance(error, (commands.MemberNotFound, commands.UserNotFound)): await ctx.send(f"Could not find user.")
    elif isinstance(error, commands.BadArgument): await ctx.send("Invalid user ID.")
    elif isinstance(error, commands.NotOwner): await ctx.send("`[ACCESS DENIED]` This command is for the bot owner only.")
    else: await ctx.send("An unexpected error occurred."); print(f"Error: {error}")

@flag_start.error
@flag_stop.error
@flag_skip.error
@force_update.error
@difficulty.error
async def command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions): await ctx.send("You don't have permission.")
    else: print(f"An error occurred: {error}"); await ctx.send("An unexpected error occurred.")

# --- Run the Bot ---
if __name__ == "__main__":
    bot.run(BOT_TOKEN)
