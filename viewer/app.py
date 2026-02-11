from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import os
from collections import Counter
from fastapi.staticfiles import StaticFiles
import re
import httpx
from dotenv import load_dotenv

# Find root directory (where scraper and .env live) relative to this script
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
load_dotenv(os.path.join(BASE_DIR, '.env'))

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

app = FastAPI()

# Enable CORS for frontend interaction
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Use absolute path for the database to avoid "no such table" errors if started from wrong directory
DB_NAME = os.getenv('DATABASE_NAME', 'discord_data.db')
if not os.path.isabs(DB_NAME):
    DB_NAME = os.path.abspath(os.path.join(BASE_DIR, DB_NAME))

print(f"Viewer Backend: Using database at {DB_NAME}")
ATTACHMENTS_DIR = os.path.join(BASE_DIR, 'attachments')

# Ensure directory exists but technically the scraper creates it
if not os.path.exists(ATTACHMENTS_DIR):
    os.makedirs(ATTACHMENTS_DIR)

# Mount the attachments directory to serve files
app.mount("/attachments", StaticFiles(directory=ATTACHMENTS_DIR), name="attachments")

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def get_message_attachments(conn, message_id):
    attachments = conn.execute("SELECT local_path FROM attachments WHERE message_id = ?", (message_id,)).fetchall()
    return [f"http://localhost:8000/attachments/{a['local_path']}" for a in attachments]

@app.get("/messages")
def get_messages(
    keyword: str = Query(None),
    username: str = Query(None),
    limit: int = 100,
    offset: int = 0
):
    conn = get_db_connection()
    query = "SELECT * FROM messages WHERE 1=1"
    params = []
    
    if keyword:
        query += " AND content LIKE ?"
        params.append(f"%{keyword}%")
    
    if username:
        query += " AND author_name LIKE ?"
        params.append(f"%{username}%")
    
    query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    messages = conn.execute(query, params).fetchall()
    
    # Convert large IDs to strings for JS precision and add attachment links
    results = []
    for msg in messages:
        m = dict(msg)
        m['id'] = str(m['id'])
        m['channel_id'] = str(m['channel_id'])
        m['guild_id'] = str(m['guild_id'])
        m['author_id'] = str(m['author_id'])
        m['attachment_urls'] = get_message_attachments(conn, msg['id'])
        m['flag'] = m.get('flag', 'none') # Fallback to 'none' if empty
        results.append(m)
    
    conn.close()
    return results

@app.get("/stats/word-frequency")
def get_word_frequency(limit: int = 20, timeframe: str = Query('all')):
    conn = get_db_connection()
    query = "SELECT content FROM messages"
    params = []
    
    if timeframe != 'all':
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        if timeframe == '24h':
            delta = timedelta(hours=24)
        elif timeframe == '7d':
            delta = timedelta(days=7)
        elif timeframe == '30d':
            delta = timedelta(days=30)
        else:
            delta = None
            
        if delta:
            since = (now - delta).isoformat()
            query += " WHERE timestamp >= ?"
            params.append(since)
            
    messages = conn.execute(query, params).fetchall()
    conn.close()
    
    words = []
    stop_words = set(['the', 'is', 'at', 'which', 'on', 'and', 'a', 'an', 'to', 'in', 'it', 'for', 'of', 'with', 'as', 'by', 'be', 'you', 'this', 'that', 'with', 'was', 'i', 'my', 'me'])
    
    for msg in messages:
        if msg['content']:
            tokens = re.findall(r'\w+', msg['content'].lower())
            words.extend([w for w in tokens if w not in stop_words and len(w) > 2])
    
    counts = Counter(words).most_common(limit)
    return [{"word": w, "count": c} for w, c in counts]

from pydantic import BaseModel

class FlagUpdate(BaseModel):
    flag: str # 'none', 'green', 'red'

@app.put("/messages/{message_id}/flag")
def update_message_flag(message_id: int, update: FlagUpdate):
    # Support traditional flags or the new pending_react:emoji format
    valid_static_flags = ['none', 'green', 'red']
    if update.flag not in valid_static_flags and not update.flag.startswith('pending_react:'):
        return {"error": "Invalid flag format"}
    
    conn = get_db_connection()
    conn.execute("UPDATE messages SET flag = ? WHERE id = ?", (update.flag, message_id))
    conn.commit()
    conn.close()
    return {"status": "success", "flag": update.flag}

@app.get("/messages/{message_id}/context")
def get_message_context(message_id: int):
    conn = get_db_connection()
    
    # 1. Get the target message to find its timestamp and channel
    target = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if not target:
        conn.close()
        return {"error": "Message not found"}
    
    channel_id = target['channel_id']
    timestamp = target['timestamp']
    
    # 2. Get 7 messages before (ordered DESC then reversed)
    before = conn.execute(
        "SELECT * FROM messages WHERE channel_id = ? AND (timestamp < ? OR (timestamp = ? AND id < ?)) ORDER BY timestamp DESC, id DESC LIMIT 7",
        (channel_id, timestamp, timestamp, message_id)
    ).fetchall()
    
    # 3. Get 7 messages after
    after = conn.execute(
        "SELECT * FROM messages WHERE channel_id = ? AND (timestamp > ? OR (timestamp = ? AND id > ?)) ORDER BY timestamp ASC, id ASC LIMIT 7",
        (channel_id, timestamp, timestamp, message_id)
    ).fetchall()
    
    def stringify_ids_and_attach(rows):
        res = []
        for r in rows:
            m = dict(r)
            m['id'] = str(m['id'])
            m['channel_id'] = str(m['channel_id'])
            m['guild_id'] = str(m['guild_id'])
            m['author_id'] = str(m['author_id'])
            m['attachment_urls'] = get_message_attachments(conn, r['id'])
            m['flag'] = m.get('flag', 'none') # Ensure flag is present
            res.append(m)
        return res

    results = stringify_ids_and_attach(reversed(before))
    results.append(stringify_ids_and_attach([target])[0])
    results.extend(stringify_ids_and_attach(after))
    
    conn.close()
    return results

@app.get("/messages/{message_id}/reactions")
async def get_message_reactions(message_id: int):
    if not DISCORD_TOKEN:
        return {"error": "Discord token not configured"}
    
    conn = get_db_connection()
    msg = conn.execute("SELECT channel_id FROM messages WHERE id = ?", (message_id,)).fetchone()
    conn.close()
    
    if not msg:
        return {"error": "Message not found in database"}
    
    channel_id = msg['channel_id']
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}"
    headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers)
            if response.status_code != 200:
                return {"error": f"Discord API returned {response.status_code}", "detail": response.text}
            
            data = response.json()
            reactions = data.get('reactions', [])
            
            # Simplify reaction list for frontend
            results = []
            for r in reactions:
                emoji = r['emoji']
                emoji_str = emoji['name']
                if emoji.get('id'):
                    # Custom emoji format: name:id
                    emoji_str = f"{emoji['name']}:{emoji['id']}"
                
                results.append({
                    "name": emoji['name'],
                    "id": emoji.get('id'),
                    "count": r['count'],
                    "emoji_str": emoji_str
                })
            return results
        except Exception as e:
            return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
