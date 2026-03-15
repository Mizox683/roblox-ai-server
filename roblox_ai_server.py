from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import psycopg2
import psycopg2.extras
import requests
import json
import hashlib
import os
import threading
from datetime import datetime
import urllib.parse
from bs4 import BeautifulSoup
 
app = Flask(__name__)
CORS(app)
 
@app.after_request
def add_headers(response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response
 
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
 
PLANS = {
    "free":  {"messages": 50,   "price": 0},
    "daily": {"messages": 1000, "price": 5.00},
}
 
def get_db():
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    return conn
 
def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            api_key TEXT UNIQUE NOT NULL,
            game_name TEXT,
            plan TEXT DEFAULT 'free',
            messages_used INTEGER DEFAULT 0,
            messages_limit INTEGER DEFAULT 50,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usage_log (
            id SERIAL PRIMARY KEY,
            api_key TEXT,
            player_name TEXT,
            message TEXT,
            response TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            api_key TEXT,
            player_name TEXT,
            history TEXT DEFAULT '[]',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ip_registrations (
            id SERIAL PRIMARY KEY,
            ip_address TEXT UNIQUE NOT NULL,
            api_key TEXT,
            game_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id SERIAL PRIMARY KEY,
            ip_address TEXT UNIQUE NOT NULL,
            reviewer_name TEXT,
            rating INTEGER,
            review_text TEXT,
            plan TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
 
def safe_init_db():
    try:
        init_db()
        print("Database initialized successfully")
    except Exception as e:
        print(f"Database init error (non-fatal): {e}")
 
threading.Thread(target=safe_init_db, daemon=True).start()
 
def generate_key(game_name):
    raw = f"{game_name}{datetime.now().isoformat()}"
    return "rai_" + hashlib.sha256(raw.encode()).hexdigest()[:24]
 
def get_client(api_key):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM clients WHERE api_key=%s", (api_key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row
 
def success(data=None, message="OK"):
    return jsonify({"status": "success", "message": message, "data": data})
 
def error(message, code=400):
    return jsonify({"status": "error", "message": message}), code
 
def web_search(query):
    try:
        if TAVILY_API_KEY:
            try:
                resp = requests.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": TAVILY_API_KEY,
                        "query": query,
                        "search_depth": "basic",
                        "max_results": 5,
                        "include_answer": True,
                    },
                    timeout=8
                )
                data = resp.json()
                results = []
                if data.get("answer"):
                    results.append("ANSWER: " + data["answer"])
                for r in data.get("results", [])[:4]:
                    title = r.get("title", "")
                    content = r.get("content", "")[:200]
                    if content:
                        results.append((title + ": " + content) if title else content)
                if results:
                    return " | ".join(results)
            except Exception as e:
                print(f"Tavily error: {e}")
 
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        results = []
        encoded = urllib.parse.quote(query)
 
        try:
            ddg_api_url = "https://api.duckduckgo.com/?q=" + encoded + "&format=json&no_html=1&skip_disambig=1"
            ddg_api_resp = requests.get(ddg_api_url, headers=headers, timeout=6)
            ddg_data = ddg_api_resp.json()
            if ddg_data.get("AbstractText"):
                results.append("ANSWER: " + ddg_data["AbstractText"][:400])
            if ddg_data.get("Answer"):
                results.append("INSTANT: " + ddg_data["Answer"][:300])
            infobox = ddg_data.get("Infobox", {})
            if isinstance(infobox, dict):
                for item in infobox.get("content", [])[:5]:
                    label = item.get("label", "")
                    value = item.get("value", "")
                    if label and value:
                        results.append(label + ": " + value)
            for topic in ddg_data.get("RelatedTopics", [])[:3]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append(topic["Text"][:200])
        except Exception as e:
            print(f"DDG API error: {e}")
 
        if results:
            return " | ".join(results)
 
        try:
            ddg_url = "https://html.duckduckgo.com/html/?q=" + encoded
            ddg_resp = requests.get(ddg_url, headers=headers, timeout=6)
            ddg_soup = BeautifulSoup(ddg_resp.text, "html.parser")
            snippets = ddg_soup.select(".result__snippet")
            titles = ddg_soup.select(".result__title")
            for i, snippet in enumerate(snippets[:5]):
                title = titles[i].get_text(strip=True) if i < len(titles) else ""
                text = snippet.get_text(strip=True)
                results.append((title + ": " + text) if title else text)
        except Exception as e:
            print(f"DDG HTML error: {e}")
 
        return " | ".join(results) if results else ""
 
    except Exception as e:
        print(f"Search error: {e}")
        return ""
 
def get_conversation_history(api_key, player_name):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT history FROM conversations WHERE api_key=%s AND player_name=%s",
        (api_key, player_name)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return json.loads(row["history"])
    return []
 
def save_conversation_history(api_key, player_name, history):
    if len(history) > 50:
        history = history[-50:]
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM conversations WHERE api_key=%s AND player_name=%s",
        (api_key, player_name)
    )
    existing = cur.fetchone()
    if existing:
        cur.execute(
            "UPDATE conversations SET history=%s, updated_at=%s WHERE api_key=%s AND player_name=%s",
            (json.dumps(history), datetime.now(), api_key, player_name)
        )
    else:
        cur.execute(
            "INSERT INTO conversations (api_key, player_name, history) VALUES (%s, %s, %s)",
            (api_key, player_name, json.dumps(history))
        )
    conn.commit()
    cur.close()
    conn.close()
 
@app.route("/home", methods=["GET"])
def home():
    return render_template("index.html")
 
@app.route("/register", methods=["POST"])
def register():
    data = request.json
    game_name = data.get("game_name", "").strip()
    plan = data.get("plan", "free")
 
    if not game_name:
        return error("Game name required")
    if plan not in PLANS:
        return error("Invalid plan")
 
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()
 
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM ip_registrations WHERE ip_address=%s", (ip,))
    existing_ip = cur.fetchone()
 
    if existing_ip and plan == "free":
        cur.close()
        conn.close()
        return jsonify({
            "status": "error",
            "message": "already_registered",
            "existing_key": existing_ip["api_key"],
            "game_name": existing_ip["game_name"]
        }), 403
 
    api_key = generate_key(game_name)
    limit = PLANS[plan]["messages"]
 
    cur2 = conn.cursor()
    cur2.execute(
        "INSERT INTO clients (api_key, game_name, plan, messages_limit) VALUES (%s, %s, %s, %s)",
        (api_key, game_name, plan, limit)
    )
    if plan == "free":
        cur2.execute(
            "INSERT INTO ip_registrations (ip_address, api_key, game_name) VALUES (%s, %s, %s)",
            (ip, api_key, game_name)
        )
    conn.commit()
    cur2.close()
    cur.close()
    conn.close()
 
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
            "message": "Daily message limit reached! Upgrade your plan.",
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
        current_year = datetime.now().year
        time_sensitive = ["president", "ceo", "owner", "prime minister", "leader", "governor",
                          "current", "latest", "now", "today", "recent", "winner", "champion",
                          "score", "price", "worth", "salary", "rank", "who is", "who are"]
        search_query = message
        if any(kw in message.lower() for kw in time_sensitive):
            search_query = message + " " + str(current_year)
        print(f"Searching web for: {search_query}")
        search_context = web_search(search_query)
        if not search_context or len(search_context) < 50:
            search_context = web_search(message + " " + str(current_year))
 
    history = get_conversation_history(api_key, player_name)
 
    today = datetime.now().strftime("%B %d, %Y")
    system_prompt = (
        "You are " + npc_name + ", a highly intelligent AI assistant inside a Roblox game.\n"
        + npc_personality + "\n\n"
        "CRITICAL ROBLOX TOS COMPLIANCE RULES - ALWAYS FOLLOW THESE:\n"
        "- Never generate sexual, romantic or inappropriate content of any kind\n"
        "- Never help with exploiting, hacking, or cheating in any Roblox game\n"
        "- Never generate content that promotes violence, self harm or suicide\n"
        "- Never share personal information about real people\n"
        "- Never generate content that discriminates based on race, gender, religion or sexuality\n"
        "- Always keep responses appropriate for all ages\n"
        "- If asked to violate any of these rules, politely decline and change the subject\n"
        "- Never help bypass Roblox moderation or safety systems\n\n"
        "The player name is " + player_name + ".\n"
        "Today is " + today + ".\n\n"
        "IMPORTANT RULES:\n"
        "- You have access to web search but results may be outdated - always caveat current info\n"
        "- Never say you are an AI language model - just be natural and helpful\n"
        "- You remember everything said in this conversation\n"
        "- Keep responses concise (1-3 sentences) and fun for a Roblox game\n"
        "- Never think out loud, never say searching or let me check - just give the answer\n"
        "- If you are not sure about current info, say it might be outdated\n"
        "- Today is " + today + " - always use this as the current date"
    )
 
    if search_context:
        system_prompt += (
            "\n\n=== WEB SEARCH RESULTS (may be outdated) ===\n"
            + search_context +
            "\n=== END OF SEARCH RESULTS ===\n\n"
            "Use these results to help answer but note they may not be fully up to date.\n"
            "Give a direct answer and if it involves current events mention it may be outdated.\n"
            "Do NOT think out loud or show your reasoning - just answer."
        )
    else:
        system_prompt += (
            "\n\nNo web search needed. Answer from your knowledge.\n"
            "Remember today is " + today + "."
        )
 
    history.append({"role": "user", "content": message})
 
    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": "Bearer " + GROQ_API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system_prompt}
                ] + history,
                "max_tokens": 200,
                "temperature": 0.4,
            },
            timeout=20
        )
 
        result = response.json()
        if "choices" not in result or not result["choices"]:
            print(f"Groq error response: {result}")
            return error("AI returned no response: " + str(result.get("error", {}).get("message", "Unknown")))
        reply = result["choices"][0]["message"]["content"].strip()
 
        history.append({"role": "assistant", "content": reply})
        save_conversation_history(api_key, player_name, history)
 
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "UPDATE clients SET messages_used = messages_used + 1 WHERE api_key=%s",
            (api_key,)
        )
        cur.execute(
            "INSERT INTO usage_log (api_key, player_name, message, response) VALUES (%s, %s, %s, %s)",
            (api_key, player_name, message, reply)
        )
        conn.commit()
        cur.close()
        conn.close()
 
        return success({
            "reply": reply,
            "npc_name": npc_name,
            "messages_used": used + 1,
            "messages_remaining": limit - used - 1,
            "web_search_used": bool(search_context)
        })
 
    except Exception as e:
        return error("AI error: " + str(e))
 
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
 
@app.route("/", methods=["GET"])
def dashboard():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM clients ORDER BY created_at DESC")
    clients = cur.fetchall()
    cur.execute("SELECT SUM(messages_used) as t FROM clients")
    total = cur.fetchone()
    total_messages = total["t"] or 0
    cur.close()
    conn.close()
 
    rows = ""
    for c in clients:
        remaining = c["messages_limit"] - c["messages_used"]
        pct = round((c["messages_used"] / c["messages_limit"]) * 100) if c["messages_limit"] > 0 else 0
        rows += (
            "<tr>"
            "<td>" + c["game_name"] + "</td>"
            "<td><span class=\"plan " + c["plan"] + "\">" + c["plan"].upper() + "</span></td>"
            "<td>" + f'{c["messages_used"]:,}' + "</td>"
            "<td>" + f'{c["messages_limit"]:,}' + "</td>"
            "<td>" + f'{remaining:,}' + "</td>"
            "<td><div class=\"bar\"><div class=\"fill\" style=\"width:" + str(pct) + "%\"></div></div> " + str(pct) + "%</td>"
            "<td><code>" + c["api_key"] + "</code></td>"
            "</tr>"
        )
 
    return (
        "<html><head><title>MizoxAI Dashboard</title><style>"
        "body{font-family:monospace;background:#1a1a2e;color:#cdd6f4;padding:40px;}"
        "h1{color:#89b4fa;}"
        "table{width:100%;border-collapse:collapse;margin-top:20px;}"
        "th{background:#313244;padding:12px;text-align:left;color:#89b4fa;}"
        "td{padding:10px;border-bottom:1px solid #313244;}"
        "code{background:#313244;padding:2px 6px;border-radius:4px;font-size:11px;}"
        ".plan{padding:3px 8px;border-radius:10px;font-size:11px;font-weight:bold;}"
        ".free{background:#45475a;}.daily{background:#1e6e3e;}"
        ".bar{background:#313244;border-radius:4px;height:8px;width:100px;display:inline-block;}"
        ".fill{background:#89b4fa;border-radius:4px;height:8px;}"
        ".stat{background:#2a2a3e;border-radius:12px;padding:20px;display:inline-block;min-width:150px;text-align:center;margin:10px;}"
        ".num{font-size:36px;font-weight:bold;color:#89b4fa;}"
        "</style></head><body>"
        "<h1>🤖 MizoxAI Dashboard</h1>"
        "<div>"
        "<div class=\"stat\"><div class=\"num\">" + str(len(clients)) + "</div>Games</div>"
        "<div class=\"stat\"><div class=\"num\">" + f'{total_messages:,}' + "</div>Total Messages</div>"
        "</div>"
        "<table><tr><th>Game</th><th>Plan</th><th>Used</th><th>Limit</th><th>Remaining</th><th>Usage</th><th>API Key</th></tr>"
        + rows +
        "</table></body></html>"
    )
 
@app.route("/review", methods=["POST"])
def add_review():
    data = request.json
    reviewer_name = data.get("reviewer_name", "").strip()
    rating = data.get("rating")
    review_text = data.get("review_text", "").strip()
 
    if not reviewer_name or not rating or not review_text:
        return error("Name, rating and review are required")
    if not isinstance(rating, int) or rating < 1 or rating > 5:
        return error("Rating must be between 1 and 5")
    if len(review_text) > 300:
        return error("Review must be under 300 characters")
 
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()
 
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
 
    # Check if IP has a registered key (free or paid)
    cur.execute("SELECT * FROM ip_registrations WHERE ip_address=%s", (ip,))
    registered = cur.fetchone()
 
    # Also check if IP has a paid key by matching against clients
    cur.execute("SELECT * FROM clients WHERE plan='daily' AND api_key IN (SELECT api_key FROM ip_registrations WHERE ip_address=%s)", (ip,))
    paid = cur.fetchone()
 
    if not registered and not paid:
        # Check if any client was registered from this IP via paid plan
        cur.execute("SELECT * FROM clients WHERE plan='daily'")
        all_paid = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"status": "error", "message": "not_eligible"}), 403
 
    # Check if already reviewed
    cur.execute("SELECT * FROM reviews WHERE ip_address=%s", (ip,))
    existing = cur.fetchone()
    if existing:
        cur.close()
        conn.close()
        return jsonify({"status": "error", "message": "already_reviewed"}), 403
 
    plan = "daily" if paid else "free"
    cur2 = conn.cursor()
    cur2.execute(
        "INSERT INTO reviews (ip_address, reviewer_name, rating, review_text, plan) VALUES (%s, %s, %s, %s, %s)",
        (ip, reviewer_name, rating, review_text, plan)
    )
    conn.commit()
    cur2.close()
    cur.close()
    conn.close()
    return success({"reviewer_name": reviewer_name, "rating": rating}, "Review added!")
 
@app.route("/reviews", methods=["GET"])
def get_reviews():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT reviewer_name, rating, review_text, plan, created_at FROM reviews ORDER BY created_at DESC")
    reviews = cur.fetchall()
    cur.close()
    conn.close()
    result = []
    for r in reviews:
        result.append({
            "reviewer_name": r["reviewer_name"],
            "rating": r["rating"],
            "review_text": r["review_text"],
            "plan": r["plan"],
            "created_at": r["created_at"].strftime("%B %d, %Y")
        })
    return jsonify({"status": "success", "data": result})
 
@app.route("/check-eligible", methods=["GET"])
def check_eligible():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM ip_registrations WHERE ip_address=%s", (ip,))
    registered = cur.fetchone()
    cur.execute("SELECT * FROM reviews WHERE ip_address=%s", (ip,))
    already_reviewed = cur.fetchone()
    cur.close()
    conn.close()
    return jsonify({
        "eligible": bool(registered),
        "already_reviewed": bool(already_reviewed)
    })
 
 
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🚀 MizoxAI Server running on port {port}")
    app.run(host="0.0.0.0", port=port)
 
