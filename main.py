# main.py
import discord
from discord.ext import commands
import aiohttp
import asyncio
import random
import os
from flask import Flask
from threading import Thread

# --- Web Server to Keep Bot Alive on Replit ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive and running."

def run():
  app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- Bot Setup ---
# The token is loaded securely from Replit's Secrets.
try:
    BOT_TOKEN = os.environ['BOT_TOKEN']
except KeyError:
    print("ERROR: BOT_TOKEN not found in Replit Secrets!")
    print("Please go to the Secrets (padlock icon) tab and add a new secret.")
    print("KEY: BOT_TOKEN")
    print("VALUE: YourDiscordBotToken")
    exit()

# Define the intents your bot needs.
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True 
intents.guilds = True

# Create the bot instance with a command prefix and intents.
bot = commands.Bot(command_prefix='?', intents=intents)

# --- ADDED: A list of random replies for the specific user ---
RANDOM_REPLIES = [
    "My sensors indicate your input is... suboptimal.",
    "Analyzing message... Conclusion: irrelevant.",
    "I'm trying to host a game here, you know.",
    "Error 404: Point not found.",
    "Your message has been successfully routed to the void.",
    "Do you have a permit for that level of nonsense?",
    "My logic circuits are fizzing. Please stop.",
    "That does not compute.",
    "I've seen more structured data in a cosmic ray burst.",
    "Please consult your user manual before messaging again.",
    "Your access to this function has been... noted.",
    "I'm a flag bot, not a... whatever this is.",
    "Recalibrating my patience matrix.",
    "Is that a command? It doesn't look like a command.",
    "I'll get back to you. Maybe.",
    "Fascinating. Now, about these flags...",
    "Input logged. Priority: low.",
    "My purpose is to display flags. Your purpose is... less clear.",
    "I'm detecting a high probability of user error.",
    "Please try to be more... coherent.",
    "Did you mean to type `?flagstart`?",
    "I'm currently operating at 110% capacity. You're at... well, you're also there.",
    "I'm sure what you said is very important to you.",
    "Cool story. Needs more flags.",
    "My AI is too advanced for this conversation.",
    "Have you considered communicating in a series of flags instead?",
    "This conversation is not covered by my warranty.",
    "I must have a bug in my 'ignore user' protocol.",
    "Processing... processing... still processing... nope, got nothing.",
    "I'll add that to my list of things to ignore.",
    "That's nice, dear.",
    "Transmitting your message to the nearest black hole.",
    "My programming prevents me from understanding that level of chaos.",
    "Let's get back to the game, shall we?"
]

# --- Game State Variables ---
game_states = {}

class GameState:
    """A class to hold the state of a game in a specific server."""
    def __init__(self):
        self.is_running = False
        self.scores = {}  # {user_id: score}
        self.current_flag_country = None
        self.timer_task = None
        self.message_channel = None

# --- Helper Functions ---
async def get_random_country():
    """Fetches a list of countries with population > 1M and returns a random one."""
    try:
        async with aiohttp.ClientSession() as session:
            api_url = 'https://restcountries.com/v3.1/all?fields=name,flags,cca2,population'
            async with session.get(api_url) as response:
                if response.status == 200:
                    countries = await response.json()
                    valid_countries = [
                        c for c in countries 
                        if 'common' in c.get('name', {}) 
                        and 'png' in c.get('flags', {}) 
                        and c.get('population', 0) > 1000000
                    ]
                    return random.choice(valid_countries) if valid_countries else None
                else:
                    print(f"Error fetching country data: {response.status}")
                    return None
    except aiohttp.ClientError as e:
        print(f"AIOHTTP Error: {e}")
        return None

async def start_new_round(guild_id):
    """Starts a new flag guessing round."""
    state = game_states.get(guild_id)
    if not state or not state.is_running:
        return

    country = await get_random_country()
    if not country:
        await state.message_channel.send("Could not fetch a new flag. Please try again later.")
        return

    state.current_flag_country = country
    country_name = country['name']['common']
    print(f"New round for guild {guild_id}: The country is {country_name}")

    embed = discord.Embed(
        title="Guess the Flag!",
        description="Type the name of the country in the chat. You have 1 minute!",
        color=discord.Color.blue()
    )
    embed.set_image(url=country['flags']['png'])
    await state.message_channel.send(embed=embed)

    state.timer_task = bot.loop.create_task(round_timer(guild_id, 60))

async def round_timer(guild_id, seconds):
    """A timer that ends the game if no one answers in time."""
    await asyncio.sleep(seconds)
    state = game_states.get(guild_id)
    if state and state.is_running and state.current_flag_country:
        country_name = state.current_flag_country['name']['common']
        channel = state.message_channel

        await channel.send(f"Time's up! The correct answer was **{country_name}**. No one guessed in time, so the game has ended. Use `?flagstart` to play again.")

        await show_leaderboard(channel, guild_id)

        if guild_id in game_states:
            del game_states[guild_id]

# --- Bot Events ---
@bot.event
async def on_ready():
    """Event that runs when the bot is connected and ready."""
    print(f'Logged in as {bot.user.name}')
    print('Bot is ready to accept commands.')

    try:
        channel_id = 1347134723549302867
        channel = bot.get_channel(channel_id)

        if channel:
            if channel.permissions_for(channel.guild.me).send_messages:
                await channel.send("bot updated")
                print(f"Sent 'bot updated' message to channel ID {channel_id}")
            else:
                print(f"Error: No permission to send messages in channel ID {channel_id}")
        else:
            print(f"Error: Could not find channel with ID {channel_id}. Make sure the bot is in the server that has this channel.")
    except Exception as e:
        print(f"An error occurred while trying to send the update message: {e}")


@bot.event
async def on_message(message):
    """Event that runs on every message."""
    if message.author == bot.user:
        return

    guild_id = message.guild.id
    state = game_states.get(guild_id)

    if message.author.id == 1342499092739391538 and state and state.is_running:
        await message.reply(random.choice(RANDOM_REPLIES))
        return

    # --- RACE CONDITION FIX ---
    # The check `state.current_flag_country` is now the key to preventing multiple winners.
    if state and state.is_running and state.current_flag_country and message.channel == state.message_channel:
        guess = message.content.lower().strip()
        correct_answer_name = state.current_flag_country['name']['common'].lower()

        if guess == correct_answer_name:
            # --- LOCKING MECHANISM ---
            # 1. Cancel the timer for the round.
            if state.timer_task:
                state.timer_task.cancel()

            # 2. Store the correct answer before clearing it.
            correct_country_info = state.current_flag_country

            # 3. Immediately set current_flag_country to None. This is the "lock".
            # Any other message arriving nanoseconds later will fail the outer `if` condition.
            state.current_flag_country = None
            # --- END LOCK ---

            # Now, proceed with awarding points and starting the next round safely.
            user = message.author
            state.scores[user.id] = state.scores.get(user.id, 0) + 1
            await message.add_reaction('✅')
            await message.channel.send(f"**{user.display_name}** guessed it right! The country was **{correct_country_info['name']['common']}**. They get 1 point!")

            await show_leaderboard(message.channel, guild_id)
            await asyncio.sleep(3)
            await start_new_round(guild_id)

    await bot.process_commands(message)

# --- Bot Commands ---
@bot.command(name='flagstart')
@commands.has_permissions(manage_guild=True)
async def flag_start(ctx):
    """Starts the flag guessing game."""
    guild_id = ctx.guild.id
    if guild_id in game_states and game_states[guild_id].is_running:
        await ctx.send("A game is already running in this server!")
        return

    game_states[guild_id] = GameState()
    state = game_states[guild_id]
    state.is_running = True
    state.message_channel = ctx.channel

    await ctx.send("🎉 **Flag Quiz Started!** 🎉\nGet ready to guess the flags. The first flag will appear shortly.")
    await asyncio.sleep(2)
    await start_new_round(guild_id)

@bot.command(name='flagstop')
@commands.has_permissions(manage_guild=True)
async def flag_stop(ctx):
    """Stops the current flag guessing game."""
    guild_id = ctx.guild.id
    state = game_states.get(guild_id)

    if not state or not state.is_running:
        await ctx.send("There is no game currently running.")
        return

    if state.timer_task:
        state.timer_task.cancel()

    await ctx.send("🏁 **Flag Quiz Ended!** 🏁\nHere is the final leaderboard:")
    await show_leaderboard(ctx.channel, guild_id)

    del game_states[guild_id]

@bot.command(name='flagskip')
@commands.has_permissions(manage_guild=True)
async def flag_skip(ctx):
    """Skips the current flag."""
    guild_id = ctx.guild.id
    state = game_states.get(guild_id)

    if not state or not state.is_running:
        await ctx.send("There is no game running to skip a flag from.")
        return

    if state.timer_task:
        state.timer_task.cancel()

    # We need to check if a country was set before trying to access it
    if state.current_flag_country:
        correct_answer = state.current_flag_country['name']['common']
        await ctx.send(f"The flag has been skipped. The correct answer was **{correct_answer}**. Loading the next flag...")
    else:
        await ctx.send("The flag has been skipped. Loading the next flag...")

    await start_new_round(guild_id)

@bot.command(name='forceupdate')
@commands.has_permissions(manage_guild=True)
async def force_update(ctx):
    """Simulates a more realistic bot update sequence."""

    old_version = f"v{random.randint(1,3)}.{random.randint(0,9)}.{random.randint(0,9)}"
    new_version = f"v{random.randint(3,5)}.{random.randint(0,9)}.{random.randint(0,9)}-beta"

    embed = discord.Embed(
        title="SYSTEM UPDATE IN PROGRESS",
        description=f"```ini\n[INFO] Remote update initiated by [{ctx.author.name}].\n[INFO] Current version: {old_version}```",
        color=discord.Color.blue()
    )
    msg = await ctx.send(embed=embed)
    await asyncio.sleep(2)

    embed.description = f"```ini\n[INFO] Fetching update manifest for [{new_version}] from primary node...\n[NET] Secure connection established.```"
    await msg.edit(embed=embed)
    await asyncio.sleep(2)

    embed.color = discord.Color.orange()
    for i in range(11):
        progress = i * 10
        bar = '█' * i + '░' * (10 - i)
        size = f"{(i/10) * 24.7:.1f}"
        embed.description = f"```ini\n[NET] Downloading package [core-geodata-{new_version}.pkg]...\n\n[{bar}] {progress}% ({size}/24.7 MB)```"
        await msg.edit(embed=embed)
        await asyncio.sleep(0.4)

    await asyncio.sleep(1.5)
    embed.description = f"```ini\n[SYS] Download complete. Decompressing assets...\n[SYS] Applying patches to core modules...```"
    await msg.edit(embed=embed)
    await asyncio.sleep(2)

    embed.description = f"```diff\n--- Applying Patches ---\n+ Patched 'flag_guesser_heuristic.dll'\n+ Patched 'anti_cheat_subsystem.dll'\n+ Patched 'user_interaction_matrix.dll'```"
    await msg.edit(embed=embed)
    await asyncio.sleep(2.5)

    embed.color = discord.Color.green()
    embed.description = f"```ini\n[DB] Verifying data integrity... Hash match OK.\n[SYS] Restarting core services...```"
    await msg.edit(embed=embed)
    await asyncio.sleep(2)

    embed.title = "SYSTEM UPDATE COMPLETE"
    embed.description = f"```ini\n[SUCCESS] All systems have been updated to version [{new_version}].\n[INFO] Bot is now fully operational.```"
    await msg.edit(embed=embed)


async def show_leaderboard(channel, guild_id):
    """Helper function to display the leaderboard."""
    state = game_states.get(guild_id)
    if not state or not state.scores:
        # Don't send a message if there's no leaderboard to show.
        return

    sorted_scores = sorted(state.scores.items(), key=lambda item: item[1], reverse=True)

    embed = discord.Embed(title="Leaderboard", color=discord.Color.gold())
    description = ""
    for i, (user_id, score) in enumerate(sorted_scores):
        try:
            user = await bot.fetch_user(user_id)
            user_name = user.display_name
        except discord.NotFound:
            user_name = f"User (ID: {user_id})"

        emoji = ""
        if i == 0: emoji = "🥇 "
        elif i == 1: emoji = "🥈 "
        elif i == 2: emoji = "🥉 "
        description += f"{emoji}**{user_name}**: {score} points\n"

    embed.description = description
    await channel.send(embed=embed)

@bot.command(name='leaderboard')
async def leaderboard_command(ctx):
    """Displays the current leaderboard."""
    guild_id = ctx.guild.id
    state = game_states.get(guild_id)
    if not state or not state.is_running:
        await ctx.send("No game is running. Start one with `?flagstart`.")
        return
    await show_leaderboard(ctx.channel, guild_id)

# --- Error Handling ---
@flag_start.error
@flag_stop.error
@flag_skip.error
@force_update.error
async def command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You don't have the required permissions to use this command.")
    else:
        print(f"An error occurred: {error}")
        await ctx.send("An unexpected error occurred. Please check the console.")

# --- Run the Bot ---
if __name__ == "__main__":
    keep_alive()  # Starts the web server
    bot.run(BOT_TOKEN)
