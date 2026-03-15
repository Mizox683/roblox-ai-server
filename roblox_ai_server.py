from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import requests
import json
import hashlib
import os
from datetime import datetime
import urllib.parse
from bs4 import BeautifulSoup

app = Flask(__name__)
CORS(app)

@app.after_request
def add_headers(response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

# API key stored in environment variable — never hardcode it!
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DB = "roblox_ai.db"
PLANS = {
    "free":     {"messages": 500,     "price": 0},
    "starter":  {"messages": 10000,   "price": 7.50},
    "basic":    {"messages": 30000,   "price": 15.00},
    "pro":      {"messages": 100000,  "price": 37.50},
    "business": {"messages": 400000,  "price": 150.00},
}

OVERAGE_RATE = 0.00046

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key TEXT UNIQUE NOT NULL,
                game_name TEXT,
                plan TEXT DEFAULT 'free',
                messages_used INTEGER DEFAULT 0,
                messages_limit INTEGER DEFAULT 500,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key TEXT,
                player_name TEXT,
                message TEXT,
                response TEXT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key TEXT,
                player_name TEXT,
                history TEXT DEFAULT '[]',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

init_db()

def generate_key(game_name):
    raw = f"{game_name}{datetime.now().isoformat()}"
    return "rai_" + hashlib.sha256(raw.encode()).hexdigest()[:24]

def get_client(api_key):
    with get_db() as db:
        return db.execute("SELECT * FROM clients WHERE api_key=?", (api_key,)).fetchone()

def success(data=None, message="OK"):
    return jsonify({"status": "success", "message": message, "data": data})

def error(message, code=400):
    return jsonify({"status": "error", "message": message}), code

def web_search(query):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }

        results = []

        # Try Google first
        encoded = urllib.parse.quote(query)
        google_url = f"https://www.google.com/search?q={encoded}&num=5"
        google_resp = requests.get(google_url, headers=headers, timeout=6)
        soup = BeautifulSoup(google_resp.text, "html.parser")

        # Get featured snippet
        featured = soup.select_one("div.BNeawe")
        if featured:
            results.append(f"FEATURED: {featured.get_text()[:300]}")

        # Get search result snippets
        for div in soup.select("div.VwiC3b")[:5]:
            text = div.get_text(strip=True)
            if text and len(text) > 30:
                results.append(text[:200])

        # Get knowledge panel
        for span in soup.select("span.hgKElc")[:2]:
            text = span.get_text(strip=True)
            if text:
                results.append(f"FACT: {text[:200]}")

        if results:
            combined = " | ".join(results)
            print(f"✅ Google search results: {combined[:200]}...")
            return combined

        # Fallback to DuckDuckGo
        ddg_url = f"https://html.duckduckgo.com/html/?q={encoded}"
        ddg_resp = requests.get(ddg_url, headers=headers, timeout=6)
        ddg_soup = BeautifulSoup(ddg_resp.text, "html.parser")
        snippets = ddg_soup.select(".result__snippet")
        titles = ddg_soup.select(".result__title")

        for i, snippet in enumerate(snippets[:5]):
            title = titles[i].get_text(strip=True) if i < len(titles) else ""
            text = snippet.get_text(strip=True)
            results.append(f"{title}: {text}" if title else text)

        if results:
            return " | ".join(results)

        return ""

    except Exception as e:
        print(f"Search error: {e}")
        return ""

def get_conversation_history(api_key, player_name):
    with get_db() as db:
        row = db.execute(
            "SELECT history FROM conversations WHERE api_key=? AND player_name=?",
            (api_key, player_name)
        ).fetchone()
    if row:
        return json.loads(row["history"])
    return []

def save_conversation_history(api_key, player_name, history):
    if len(history) > 50:
        history = history[-50:]
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM conversations WHERE api_key=? AND player_name=?",
            (api_key, player_name)
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE conversations SET history=?, updated_at=? WHERE api_key=? AND player_name=?",
                (json.dumps(history), datetime.now().isoformat(), api_key, player_name)
            )
        else:
            db.execute(
                "INSERT INTO conversations (api_key, player_name, history) VALUES (?, ?, ?)",
                (api_key, player_name, json.dumps(history))
            )

@app.route("/register", methods=["POST"])
def register():
    data = request.json
    game_name = data.get("game_name", "").strip()
    plan = data.get("plan", "free")
    if not game_name:
        return error("Game name required")
    if plan not in PLANS:
        return error("Invalid plan")
    api_key = generate_key(game_name)
    limit = PLANS[plan]["messages"]
    with get_db() as db:
        db.execute(
            "INSERT INTO clients (api_key, game_name, plan, messages_limit) VALUES (?, ?, ?, ?)",
            (api_key, game_name, plan, limit)
        )
    return success({
        "api_key": api_key,
        "game_name": game_name,
        "plan": plan,
        "messages_limit": limit
    }, "Game registered!")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    api_key = data.get("api_key")
    player_name = data.get("player_name", "Player")
    message = data.get("message", "")
    npc_name = data.get("npc_name", "Assistant")
    npc_personality = data.get("npc_personality", "You are a helpful assistant in a Roblox game.")

    if not api_key or not message:
        return error("api_key and message required")

    client = get_client(api_key)
    if not client:
        return error("Invalid API key", 401)

    used = client["messages_used"]
    limit = client["messages_limit"]

    if used >= limit:
        return jsonify({
            "status": "limit_reached",
            "message": "Monthly message limit reached! Upgrade your plan.",
            "messages_used": used,
            "messages_limit": limit
        })

    has_exact_search = '"' in message
    search_keywords = [
        "what is", "who is", "when is", "where is", "how much",
        "latest", "news", "current", "today", "price", "weather",
        "score", "when did", "how many", "tell me about", "search",
        "find", "look up", "what happened", "recent", "who won",
        "when was", "how old", "where does", "what are", "why is",
        "how does", "what does", "when will", "who made", "who created",
        "what year", "how tall", "how big", "population", "capital",
        "president", "ceo", "owner", "release date", "update",
        "new", "best", "top", "ranked", "worth", "salary",
        "define", "meaning", "explain", "describe", "compare"
    ]

    needs_search = has_exact_search or any(kw in message.lower() for kw in search_keywords)
    search_context = ""

    if needs_search:
        print(f"🔍 Searching web for: {message}")
        search_context = web_search(message)

    history = get_conversation_history(api_key, player_name)

    system_prompt = f"""You are {npc_name}, a highly intelligent AI assistant inside a Roblox game.
{npc_personality}

The player's name is {player_name}.
Today's date is {datetime.now().strftime("%B %d, %Y")}.

IMPORTANT RULES:
- You have FULL access to the internet through a web search tool
- You ALWAYS have up to date information — never say you are stuck in a training cutoff
- Never say "as of my last update" or "I don't have access to the internet" — you DO have access
- Never say you are an AI language model — just be natural and helpful
- You remember everything said in this conversation
- Keep responses concise (2-4 sentences) and fun for a Roblox game
- If the player uses "quotes" around words they want an exact search for that phrase
- Today is {datetime.now().strftime("%B %d, %Y")} — always use this as the current date"""

    if search_context:
        system_prompt += f"""

LIVE WEB SEARCH RESULTS (use these to answer accurately):
{search_context}

Use these results to give an accurate and up to date answer.
Never say you cannot access the internet — you just searched and got these results."""
    else:
        system_prompt += f"""

No web search was needed for this message. Answer from your knowledge.
Remember today is {datetime.now().strftime("%B %d, %Y")}."""

    history.append({"role": "user", "content": message})

    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system_prompt}
                ] + history,
                "max_tokens": 250,
                "temperature": 0.7,
            },
            timeout=15
        )

        reply = response.json()["choices"][0]["message"]["content"]

        history.append({"role": "assistant", "content": reply})
        save_conversation_history(api_key, player_name, history)

        with get_db() as db:
            db.execute(
                "UPDATE clients SET messages_used = messages_used + 1 WHERE api_key=?",
                (api_key,)
            )
            db.execute(
                "INSERT INTO usage_log (api_key, player_name, message, response) VALUES (?, ?, ?, ?)",
                (api_key, player_name, message, reply)
            )

        return success({
            "reply": reply,
            "npc_name": npc_name,
            "messages_used": used + 1,
            "messages_remaining": limit - used - 1,
            "web_search_used": bool(search_context)
        })

    except Exception as e:
        return error(f"AI error: {str(e)}")

@app.route("/usage/<api_key>", methods=["GET"])
def usage(api_key):
    client = get_client(api_key)
    if not client:
        return error("Invalid API key", 401)
    return success({
        "game_name": client["game_name"],
        "plan": client["plan"],
        "messages_used": client["messages_used"],
        "messages_limit": client["messages_limit"],
        "messages_remaining": client["messages_limit"] - client["messages_used"],
    })

@app.route("/upgrade", methods=["POST"])
def upgrade():
    data = request.json
    api_key = data.get("api_key")
    new_plan = data.get("plan")
    if not api_key or not new_plan:
        return error("api_key and plan required")
    if new_plan not in PLANS:
        return error("Invalid plan")
    client = get_client(api_key)
    if not client:
        return error("Invalid API key", 401)
    new_limit = PLANS[new_plan]["messages"]
    with get_db() as db:
        db.execute(
            "UPDATE clients SET plan=?, messages_limit=? WHERE api_key=?",
            (new_plan, new_limit, api_key)
        )
    return success({"plan": new_plan, "messages_limit": new_limit}, "Plan upgraded!")

@app.route("/", methods=["GET"])
def dashboard():
    with get_db() as db:
        clients = db.execute("SELECT * FROM clients ORDER BY created_at DESC").fetchall()
        total_messages = db.execute("SELECT SUM(messages_used) as t FROM clients").fetchone()["t"] or 0

    rows = ""
    for c in clients:
        remaining = c["messages_limit"] - c["messages_used"]
        pct = round((c["messages_used"] / c["messages_limit"]) * 100) if c["messages_limit"] > 0 else 0
        rows += f"""
        <tr>
            <td>{c["game_name"]}</td>
            <td><span class="plan {c['plan']}">{c['plan'].upper()}</span></td>
            <td>{c["messages_used"]:,}</td>
            <td>{c["messages_limit"]:,}</td>
            <td>{remaining:,}</td>
            <td>
                <div class="bar"><div class="fill" style="width:{pct}%"></div></div>
                {pct}%
            </td>
            <td><code>{c["api_key"]}</code></td>
        </tr>"""

    return f"""
    <html>
    <head><title>RobloxAI Dashboard</title>
    <style>
        body {{ font-family: monospace; background: #1a1a2e; color: #cdd6f4; padding: 40px; }}
        h1 {{ color: #89b4fa; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th {{ background: #313244; padding: 12px; text-align: left; color: #89b4fa; }}
        td {{ padding: 10px; border-bottom: 1px solid #313244; }}
        code {{ background: #313244; padding: 2px 6px; border-radius: 4px; font-size: 11px; }}
        .plan {{ padding: 3px 8px; border-radius: 10px; font-size: 11px; font-weight: bold; }}
        .free {{ background: #45475a; }} .starter {{ background: #1e6e3e; }}
        .basic {{ background: #1e4e8e; }} .pro {{ background: #6e3e8e; }}
        .business {{ background: #8e3e1e; }}
        .bar {{ background: #313244; border-radius: 4px; height: 8px; width: 100px; display: inline-block; }}
        .fill {{ background: #89b4fa; border-radius: 4px; height: 8px; }}
        .stat {{ background: #2a2a3e; border-radius: 12px; padding: 20px; display: inline-block; min-width: 150px; text-align: center; margin: 10px; }}
        .num {{ font-size: 36px; font-weight: bold; color: #89b4fa; }}
    </style>
    </head>
    <body>
    <h1>🤖 RobloxAI Dashboard</h1>
    <div>
        <div class="stat"><div class="num">{len(clients)}</div>Games</div>
        <div class="stat"><div class="num">{total_messages:,}</div>Total Messages</div>
    </div>
    <table>
        <tr>
            <th>Game</th><th>Plan</th><th>Used</th>
            <th>Limit</th><th>Remaining</th><th>Usage</th><th>API Key</th>
        </tr>
        {rows}
    </table>
    </body></html>
    """

if __name__ == "__main__":
    print("\n🚀 RobloxAI Server running at http://localhost:5000")
    print("📊 Open http://localhost:5000 in your browser\n")
    app.run(debug=True, port=5000)