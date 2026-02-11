import sqlite3
import discord
from discord.ext import tasks, commands
import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
DB_NAME = os.getenv('DATABASE_NAME', 'discord_data.db')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', 300))
IGNORE_FILE = 'IGNORED_CHANNELS.txt'

# Track messages pulled during this run per channel
SESSION_COUNTS = {}

def get_ignored_channels():
    if not os.path.exists(IGNORE_FILE):
        return set()
    ignored = set()
    with open(IGNORE_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Treat lines starting with "# " (hash + space) as comments
            if line.startswith('# '):
                continue
            # Otherwise, strip leading hash and whitespace to get the channel name
            name = line.lstrip('#').strip().lower()
            if name:
                ignored.add(name)
    return ignored

def delete_channel_history(channel_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('DELETE FROM messages WHERE channel_id = ?', (channel_id,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        print(f"Deleted {deleted} historical messages for ignored channel ID {channel_id}.")

def get_total_messages_count(channel_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM messages WHERE channel_id = ?', (channel_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def get_last_message_id(channel_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT MAX(id) FROM messages WHERE channel_id = ?', (channel_id,))
    last_id = c.fetchone()[0]
    conn.close()
    return last_id

# Database Setup
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Guilds table
    c.execute('''CREATE TABLE IF NOT EXISTS guilds (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL
                )''')
    
    # Channels table
    c.execute('''CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    guild_id INTEGER,
                    FOREIGN KEY (guild_id) REFERENCES guilds (id)
                )''')
    
    # Messages table
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    guild_id INTEGER,
                    author_id INTEGER,
                    author_name TEXT NOT NULL,
                    content TEXT,
                    timestamp TEXT NOT NULL,
                    attachments_count INTEGER DEFAULT 0,
                    flag TEXT DEFAULT 'none',
                    FOREIGN KEY (channel_id) REFERENCES channels (id),
                    FOREIGN KEY (guild_id) REFERENCES guilds (id)
                )''')

    # Migration: Add flag column if it doesn't exist (for existing DBs)
    try:
        c.execute('ALTER TABLE messages ADD COLUMN flag TEXT DEFAULT "none"')
    except sqlite3.OperationalError:
        pass # Column already exists

    # Attachments table
    c.execute('''CREATE TABLE IF NOT EXISTS attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER,
                    filename TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    content_type TEXT,
                    FOREIGN KEY (message_id) REFERENCES messages (id)
                )''')
    
    conn.commit()
    conn.close()

def save_guild(guild_id, name):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO guilds (id, name) VALUES (?, ?)', (guild_id, name))
    conn.commit()
    conn.close()

def save_channel(channel_id, name, guild_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO channels (id, name, guild_id) VALUES (?, ?, ?)', (channel_id, name, guild_id))
    conn.commit()
    conn.close()

def save_message(msg_id, channel_id, guild_id, author_id, author_name, content, timestamp, attachments_count, flag='none'):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''INSERT OR IGNORE INTO messages 
                 (id, channel_id, guild_id, author_id, author_name, content, timestamp, attachments_count, flag) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                 (msg_id, channel_id, guild_id, author_id, author_name, content, timestamp, attachments_count, flag))
    conn.commit()
    conn.close()

def save_attachment(message_id, filename, local_path, content_type):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''INSERT INTO attachments (message_id, filename, local_path, content_type) 
                 VALUES (?, ?, ?, ?)''', (message_id, filename, local_path, content_type))
    conn.commit()
    conn.close()

async def process_pending_reactions():
    """Checks the DB for messages marked with 'pending_react:emoji' and adds them."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, channel_id, flag FROM messages WHERE flag LIKE 'pending_react:%'")
    pending = c.fetchall()
    conn.close()

    if len(pending) > 0:
        print(f"Reaction System: Found {len(pending)} pending reactions in database.")
    else:
        # Debug: just to be sure it's running
        # print("Reaction System: No pending reactions.")
        return

    print(f"Polling: Found {len(pending)} pending reactions to process.")
    for msg_id, channel_id, flag in pending:
        # flag is 'pending_react:emoji'
        # emoji could be '✅' or 'name:id'
        emoji_data = flag.replace('pending_react:', '', 1)
        try:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
            if not channel:
                print(f"  Error: Could not find channel {channel_id} for reaction.")
                continue

            message = await channel.fetch_message(msg_id)
            if not message:
                print(f"  Error: Could not find message {msg_id} in channel {channel_id}.")
                continue

            await message.add_reaction(emoji_data)
            print(f"  Success: Reacted with {emoji_data} to message {msg_id}.")
            
            # Clear the flag
            conn = sqlite3.connect(DB_NAME)
            conn.execute("UPDATE messages SET flag = 'none' WHERE id = ?", (msg_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"  Failed to process reaction for message {msg_id}: {e}")

# Discord Bot Setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.command()
@commands.has_permissions(administrator=True)
async def react(ctx, channel_id: int, message_id: int, emoji: str):
    """Adds a reaction to a specific message. Usage: !react <channel_id> <message_id> <emoji>"""
    try:
        # Fetch the channel and message
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        if not channel:
            await ctx.send(f"Error: Could not find channel with ID {channel_id}.")
            return
            
        message = await channel.fetch_message(message_id)
        if not message:
            await ctx.send(f"Error: Could not find message with ID {message_id} in channel <#{channel_id}>.")
            return

        await message.add_reaction(emoji)
        await ctx.send(f"Success! Reacted with {emoji} to message {message_id} in <#{channel_id}>.")
        print(f"Manual Reaction: Added {emoji} to message {message_id} via command.")
    except Exception as e:
        await ctx.send(f"Failed to add reaction: {e}")
        print(f"Error in !react command: {e}")

async def scrape_channel(channel):
    print(f"Scraping channel: #{channel.name} ({channel.id})")
    save_channel(channel.id, channel.name, channel.guild.id)
    
    # Ensure attachments folder exists
    if not os.path.exists('attachments'):
        os.makedirs('attachments')

    after_message = None
    last_id = get_last_message_id(channel.id)
    if last_id:
        # Use discord.Object to start fetching after our last known message
        after_message = discord.Object(id=last_id)
    
    if channel.id not in SESSION_COUNTS:
        SESSION_COUNTS[channel.id] = 0

    total_fetched_this_scrape = 0
    
    while True:
        try:
            batch_count = 0
            # Fetch batches of 100 to allow for manual delays and error handling
            async for message in channel.history(limit=100, after=after_message, oldest_first=True):
                # Download attachments if they are images
                images_count = 0
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith('image/'):
                        local_filename = f"{message.id}_{attachment.filename}"
                        local_path = os.path.join('attachments', local_filename)
                        try:
                            await attachment.save(local_path)
                            save_attachment(message.id, attachment.filename, local_filename, attachment.content_type)
                            images_count += 1
                        except Exception as e:
                            print(f"Failed to download attachment {attachment.filename}: {e}")

                save_message(
                    message.id,
                    channel.id,
                    channel.guild.id,
                    message.author.id,
                    str(message.author),
                    message.content,
                    message.created_at.isoformat(),
                    images_count
                )
                
                SESSION_COUNTS[channel.id] += 1
                total_fetched_this_scrape += 1
                after_message = message
                batch_count += 1

            # Defensive guard: Only print progress if we actually processed messages in this run
            if batch_count > 0 and total_fetched_this_scrape > 0:
                total_in_db = get_total_messages_count(channel.id)
                print(f"[#{channel.name}] Run: {SESSION_COUNTS[channel.id]} | Total DB: {total_in_db}")

            if batch_count < 100:
                break
                
            # As requested, wait 5 seconds between batches to be conservative
            # and allow Discord's rate limits to settle.
            await asyncio.sleep(5)

        except discord.HTTPException as e:
            if e.status == 429:
                print(f"Rate limited by Discord. Waiting 10 seconds before continuing with #{channel.name}...")
                await asyncio.sleep(10)
                continue
            else:
                raise e
    
    if total_fetched_this_scrape == 0:
        total_in_db = get_total_messages_count(channel.id)
        print(f"[#{channel.name}] Up to date (Total DB: {total_in_db})")
    else:
        print(f"Finished scraping #{channel.name}: {total_fetched_this_scrape} new messages found.")

@tasks.loop(seconds=POLL_INTERVAL)
async def poll_discord():
    try:
        print(f"Starting poll at {datetime.now().isoformat()}")
        
        # Process any reactions marked in the Viewer
        await process_pending_reactions()
        
        ignored_names = get_ignored_channels()
        
        for guild in bot.guilds:
            save_guild(guild.id, guild.name)
            for channel in guild.text_channels:
                # 1. Skip if in ignore list
                if channel.name.lower() in ignored_names:
                    print(f"Ignoring channel: #{channel.name} (found in ignore list)")
                    delete_channel_history(channel.id)
                    continue

                # 2. Check if bot can read history
                permissions = channel.permissions_for(guild.me)
                if permissions.read_messages and permissions.read_message_history:
                    await scrape_channel(channel)
                else:
                    print(f"Skipping #{channel.name}: Missing permissions.")
        print("Poll completed.")
    except Exception as e:
        # Catch network issues specifically to avoid large tracebacks
        # socket.gaierror is common when internet is down
        if "getaddrinfo" in str(e) or "Connection" in str(e):
            print(f"Network error during poll: {e}. Bot will retry automatically when internet is restored.")
        else:
            print(f"An unexpected error occurred during poll: {e}")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    init_db()
    if not poll_discord.is_running():
        poll_discord.start()

import sys

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN not found in .env file.")
        sys.exit(1)
    
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("Error: Invalid Discord token. Please check your .env file.")
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred while starting the bot: {e}")
        sys.exit(1)
