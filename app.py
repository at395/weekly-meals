"""
Recipe Agent v2 - Flask backend
Handles meal planning, feedback, shopping list generation.
"""

import os, json, re
from flask import Flask, request, jsonify, send_from_directory
from anthropic import Anthropic

app = Flask(__name__, static_folder="static")
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MEMORY_FILE = "memory.json"

PLAN_SYSTEM = """You are a warm, practical weekly meal planner. Propose a 7-day plan (lunch + dinner each day).

Rules:
- Respect all allergies and dietary restrictions absolutely
- Vary cuisines — never repeat the same cuisine on consecutive days
- Balance nutrition across the week
- Keep meals realistic: 30-60 min to cook at home
- Reuse ingredients across days to reduce waste
- Meal names should be appetising and specific (e.g. "Lemon herb roast chicken", not "Chicken dish")

Always respond with valid JSON only, no markdown, no commentary:
{
  "Monday":    { "lunch": "...", "dinner": "..." },
  "Tuesday":   { "lunch": "...", "dinner": "..." },
  "Wednesday": { "lunch": "...", "dinner": "..." },
  "Thursday":  { "lunch": "...", "dinner": "..." },
  "Friday":    { "lunch": "...", "dinner": "..." },
  "Saturday":  { "lunch": "...", "dinner": "..." },
  "Sunday":    { "lunch": "...", "dinner": "..." }
}
When applying feedback, only change what needs changing."""

SHOPPING_SYSTEM = """Extract a consolidated grocery shopping list from a weekly meal plan.

Rules:
- Merge duplicate ingredients with combined quantities
- Group by: Produce, Meat & Fish, Dairy & Eggs, Bakery, Pantry, Frozen
- Skip staples people always have: salt, pepper, oil, basic dried spices
- Include quantities where meaningful (e.g. "600g chicken breast", "3 lemons")
- JSON only, no commentary: { "Produce": ["item"], "Meat & Fish": ["item"], ... }"""


def load_mem():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE) as f: return json.load(f)
        except: pass
    return {"preferences": {}, "disliked_meals": [], "liked_meals": [], "conversation": [], "current_plan": None}

def save_mem(m):
    with open(MEMORY_FILE, "w") as f: json.dump(m, f, indent=2)

def parse_json(raw):
    raw = re.sub(r"```json\s*|```\s*", "", raw).strip()
    return json.loads(raw)

def build_profile(mem):
    p = mem.get("preferences", {})
    d = mem.get("disliked_meals", [])
    lines = []
    if p: lines.append("Preferences: " + ", ".join(f"{k}: {v}" for k,v in p.items()))
    if d: lines.append("Always avoid: " + ", ".join(d))
    return "\n".join(lines) or "No specific preferences."


@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/memory", methods=["GET"])
def get_memory():
    return jsonify(load_mem())

@app.route("/api/memory", methods=["POST"])
def update_memory():
    mem = load_mem()
    data = request.json
    if "preferences" in data:
        mem["preferences"].update(data["preferences"])
    save_mem(mem)
    return jsonify({"ok": True})

@app.route("/api/plan/generate", methods=["POST"])
def generate_plan():
    mem = load_mem()
    msg = f"Generate a weekly meal plan.\n\n{build_profile(mem)}\n\nJSON only."
    conversation = [{"role": "user", "content": msg}]
    r = client.messages.create(model="claude-3-5-haiku-20241022", max_tokens=2000,
                                system=PLAN_SYSTEM, messages=conversation)
    raw = r.content[0].text.strip()
    conversation.append({"role": "assistant", "content": raw})
    plan = parse_json(raw)
    mem["conversation"] = conversation
    mem["current_plan"] = plan
    save_mem(mem)
    return jsonify({"plan": plan})

@app.route("/api/plan/feedback", methods=["POST"])
def feedback():
    mem = load_mem()
    text = request.json.get("feedback", "")
    plan = mem.get("current_plan")
    if not plan:
        return jsonify({"error": "No plan yet"}), 400

    # Extract dislikes quietly
    try:
        r = client.messages.create(model="claude-3-5-haiku-20241022", max_tokens=150,
            messages=[{"role":"user","content":f'Extract dislikes from: "{text}". JSON only: {{"dislikes":[],"likes":[]}}'}])
        ex = json.loads(r.content[0].text.strip())
        if ex.get("dislikes"):
            mem["disliked_meals"] = sorted(set(mem.get("disliked_meals",[]) + ex["dislikes"]))
        if ex.get("likes"):
            mem["liked_meals"] = sorted(set(mem.get("liked_meals",[]) + ex["likes"]))
    except: pass

    conv = mem.get("conversation", [])
    conv.append({"role":"user","content":f"Current plan:\n{json.dumps(plan,indent=2)}\n\nFeedback: {text}\n\nUpdate plan. JSON only."})
    r = client.messages.create(model="claude-3-5-haiku-20241022", max_tokens=2000,
                                system=PLAN_SYSTEM, messages=conv)
    raw = r.content[0].text.strip()
    conv.append({"role":"assistant","content":raw})
    updated = parse_json(raw)
    mem["conversation"] = conv
    mem["current_plan"] = updated
    save_mem(mem)
    return jsonify({"plan": updated})

@app.route("/api/plan/voice", methods=["POST"])
def voice_feedback():
    """Like feedback but returns a spoken confirmation sentence too."""
    mem = load_mem()
    text = request.json.get("feedback", "")
    plan = mem.get("current_plan")

    if not plan:
        # Generate fresh plan from voice request
        msg = f"{text}\n\n{build_profile(mem)}\n\nJSON only."
        conversation = [{"role": "user", "content": msg}]
        r = client.messages.create(model="claude-3-5-haiku-20241022", max_tokens=2000,
                                    system=PLAN_SYSTEM, messages=conversation)
        raw = r.content[0].text.strip()
        conversation.append({"role": "assistant", "content": raw})
        plan = parse_json(raw)
        mem["conversation"] = conversation
        mem["current_plan"] = plan
        save_mem(mem)
        spoken = "Done! I've created your weekly meal plan. Take a look and let me know if you'd like any changes."
        return jsonify({"plan": plan, "spoken": spoken})

    # Apply feedback to existing plan
    conv = mem.get("conversation", [])
    conv.append({"role":"user","content":f"Current plan:\n{json.dumps(plan,indent=2)}\n\nVoice feedback: {text}\n\nUpdate plan. JSON only."})
    r = client.messages.create(model="claude-3-5-haiku-20241022", max_tokens=2000,
                                system=PLAN_SYSTEM, messages=conv)
    raw = r.content[0].text.strip()
    conv.append({"role":"assistant","content":raw})
    updated = parse_json(raw)
    mem["conversation"] = conv
    mem["current_plan"] = updated
    save_mem(mem)

    # Generate a natural spoken summary of what changed
    try:
        sr = client.messages.create(model="claude-3-5-haiku-20241022", max_tokens=100,
            messages=[{"role":"user","content":f'The user said: "{text}". The plan was updated. Write ONE friendly sentence (max 25 words) confirming what changed. No JSON.'}])
        spoken = sr.content[0].text.strip().strip('"')
    except:
        spoken = "Done! Your plan has been updated."

    return jsonify({"plan": updated, "spoken": spoken})

@app.route("/api/shopping", methods=["POST"])
def shopping():
    mem = load_mem()
    plan = mem.get("current_plan")
    if not plan:
        return jsonify({"error": "No plan yet"}), 400
    r = client.messages.create(model="claude-3-5-haiku-20241022", max_tokens=1500,
        messages=[{"role":"user","content":f"{SHOPPING_SYSTEM}\n\nPlan:\n{json.dumps(plan,indent=2)}"}])
    raw = r.content[0].text.strip()
    shopping_list = parse_json(raw)
    return jsonify({"shopping": shopping_list})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
