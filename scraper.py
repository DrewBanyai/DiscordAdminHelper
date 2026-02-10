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

# Discord Bot Setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

async def scrape_channel(channel):
    print(f"Scraping channel: #{channel.name} ({channel.id})")
    save_channel(channel.id, channel.name, channel.guild.id)
    
    # Ensure attachments folder exists
    if not os.path.exists('attachments'):
        os.makedirs('attachments')

    count = 0
    after_message = None
    
    while True:
        try:
            batch_count = 0
            # Fetch batches of 100 to allow for manual delays and error handling
            async for message in channel.history(limit=100, after=after_message, oldest_first=True):
                # Check if message exists to avoid re-downloading attachments
                conn = sqlite3.connect(DB_NAME)
                exists = conn.execute("SELECT 1 FROM messages WHERE id = ?", (message.id,)).fetchone()
                conn.close()

                if not exists:
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
                    count += 1
                
                after_message = message
                batch_count += 1

            if batch_count == 0:
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
    
    if count > 0:
        print(f"Finished scraping #{channel.name}: {count} new messages found.")

@tasks.loop(seconds=POLL_INTERVAL)
async def poll_discord():
    try:
        print(f"Starting poll at {datetime.now().isoformat()}")
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

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN not found in .env file.")
    else:
        bot.run(TOKEN)
