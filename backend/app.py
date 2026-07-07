"""
WordForge — Flask Backend
AI-Assisted Password Wordlist Generator for authorized security testing.

Routes:
  GET  /             → Serve the SPA
  POST /api/generate → Accept OSINT data, call OLLAMA, return wordlist
"""

import logging
import re
from datetime import datetime

import requests
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

# ── App Setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
OLLAMA_URL      = "http://localhost:11434/api/generate"
OLLAMA_MODEL    = "dolphin-llama3:8b"
REQUEST_TIMEOUT = 300   # seconds per OLLAMA call

# Context window — dolphin-llama3:8b supports 4096 by default.
NUM_CTX = 4096

# Passwords per single OLLAMA call.
# Larger = fewer sequential calls = less total wait time.
CHUNK_SIZE = 80

# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_dob(dob_str: str) -> str:
    """Parse ISO date and return human-friendly format with derived values."""
    if not dob_str:
        return ""
    try:
        d = datetime.strptime(dob_str, "%Y-%m-%d")
        return (
            f"{d.strftime('%d/%m/%Y')} "
            f"(day={d.day}, month={d.month}, year={d.year}, "
            f"DDMM={d.strftime('%d%m')}, MMYY={d.strftime('%m%y')}, "
            f"DDMMYYYY={d.strftime('%d%m%Y')}, MMDDYYYY={d.strftime('%m%d%Y')}, "
            f"YYYYMMDD={d.strftime('%Y%m%d')})"
        )
    except ValueError:
        return dob_str


# ── FIX #3: Simplified prompt ─────────────────────────────────────────────────
# Previous prompt had 6 numbered rules + leet-speak + style instructions.
# 8B models ignore complex multi-rule prompts and output numbered lists anyway.
# Keep it short and direct: one length constraint (max only), no leet-speak,
# no style mixing — let the model do what it does naturally.
def _build_prompt(data: dict, count: int, max_length: int) -> str:
    """
    Short, direct prompt for small local LLMs.
    Only one length constraint: max_length (upper ceiling).
    No leet-speak instructions — the model applies them naturally.
    No style mixing instructions — keep prompt noise minimal.
    """
    lines = []

    def add(label: str, value: str):
        v = (value or "").strip()
        if v:
            lines.append(f"- {label}: {v}")

    add("Full Name",         data.get("full_name"))
    add("Nicknames/Aliases", data.get("nicknames"))
    add("Date of Birth",     _format_dob(data.get("dob", "")))
    add("Partner/Spouse",    data.get("partner_name"))
    add("Children Names",    data.get("children_names"))
    add("Pet Names",         data.get("pet_names"))
    add("Workplace/Company", data.get("workplace"))
    add("Hobbies/Interests", data.get("hobbies"))
    add("Favorite Things",   data.get("favorites"))
    add("Known Usernames",   data.get("known_usernames"))
    add("Important Dates",   data.get("important_dates"))
    add("Additional Notes",  data.get("context"))

    osint_block = "\n".join(lines) if lines else "(no data provided)"

    prompt = (
        f"Generate {count} password candidates for the person below.\n"
        f"Each password must be under {max_length} characters and contain no spaces.\n"
        f"Use names, dates, nicknames, and numbers from the personal data.\n"
        f"Output one password per line. No explanations.\n\n"
        f"{osint_block}\n\n"
        f"Passwords:"
    )
    return prompt


# ── FIX #1 + #2: Reordered pipeline + truncation instead of drop ──────────────
def _clean_passwords(raw_text: str, max_length: int, min_length: int) -> tuple[list[str], int]:
    """
    Extract clean, unique passwords from raw LLM output.

    FIX #1 — Strip numbering/bullets BEFORE the reject filter.
    Previously reject_re contained '1.' '2.' etc. which matched numbered-list
    lines and discarded them BEFORE strip_num_re could clean them.
    Now strip runs first, so "1. rohanparmar2004" → "rohanparmar2004" → accepted.

    FIX #2 — Truncation instead of hard drop for length.
    Previously any password outside [min, max] was silently dropped.
    With a 12-15 char window, 90%+ of LLM output was discarded.
    Now:
      - Passwords longer than max_length are TRUNCATED to max_length.
      - Passwords shorter than max(4, min_length // 2) are dropped
        (genuine fragments, not real passwords).
      - Everything else is kept as-is.

    FIX #4 (bonus) — Spaces are normalised to '' instead of causing a drop.
    "Rohan Parmar" → "RohanParmar" (still OSINT-derived, still usable).
    Only lines with 4+ spaces are dropped (these are prose sentences).
    """
    seen    = set()
    result  = []
    dropped = 0

    # Strip patterns — run FIRST before any rejection logic
    strip_num_re = re.compile(r"^\d+[.)\-]\s+")   # "1. " / "42) " / "7- "
    strip_bul_re = re.compile(r"^[-*•·]\s+")       # "- " / "* " / "• "

    # Reject patterns — only genuine junk that cannot be a password after stripping.
    # NO longer contains 1\. 2\. etc. — those are handled by strip_num_re above.
    reject_re = re.compile(
        r"^(\s*$"
        r"|#{1,}\s"                                 # markdown headers
        r"|here are|these are"                      # preamble phrases
        r"|note:|sure!|okay|of course"
        r"|output:|passwords?:\s*$"                 # "Passwords:" header
        r"|based on|personal data"
        r"|[`]{1,3}"                                # code fences
        r"|={3,}"                                   # separator lines
        r"|-{3,})",                                 # horizontal rules
        re.IGNORECASE,
    )

    # Absolute minimum: drop only true fragments (< 4 chars or < half of min_length)
    min_keep = max(4, min_length // 2)

    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue

        # ── STEP 1: Strip numbering and bullets FIRST ─────────────────────
        line = strip_num_re.sub("", line).strip()
        line = strip_bul_re.sub("", line).strip()

        if not line:
            continue

        # ── STEP 2: Reject obvious junk lines ─────────────────────────────
        if reject_re.match(line):
            continue

        # ── STEP 3: Normalise spaces (don't drop — fix instead) ───────────
        # Lines with 4+ spaces are prose sentences, not passwords → drop
        if line.count(" ") >= 4:
            continue
        # Fewer spaces: remove them (e.g. "Rohan Parmar" → "RohanParmar")
        line = line.replace(" ", "")

        # ── STEP 4: Drop genuine fragments ────────────────────────────────
        if len(line) < min_keep:
            dropped += 1
            continue

        # ── STEP 5: Truncate to max_length (not drop) ─────────────────────
        if len(line) > max_length:
            line = line[:max_length]

        # ── STEP 6: Reject over-long prose that survived everything above ──
        # (shouldn't happen after truncation but guard anyway)
        if len(line) > 128:
            continue

        if line not in seen:
            seen.add(line)
            result.append(line)

    return result, dropped


def _call_ollama(prompt: str, chunk_size: int, max_length: int, min_length: int) -> tuple[list[str], int]:
    """
    Make one OLLAMA API call requesting `chunk_size` passwords.
    num_predict is capped to never exceed NUM_CTX − prompt_budget.
    """
    prompt_budget = 512
    output_budget = NUM_CTX - prompt_budget          # 4096 - 512 = 3584
    num_predict   = min(output_budget, chunk_size * 15)

    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature":    0.8,
            "top_p":          0.92,
            "repeat_penalty": 1.1,
            "num_ctx":        NUM_CTX,
            "num_predict":    num_predict,
        },
    }

    log.debug(
        "OLLAMA call: chunk_size=%d  num_ctx=%d  num_predict=%d",
        chunk_size, NUM_CTX, num_predict,
    )

    try:
        resp = requests.post(
            OLLAMA_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        raw_text = resp.json().get("response", "")
        log.info("OLLAMA chunk response: %d chars raw", len(raw_text))
        passwords, dropped = _clean_passwords(raw_text, max_length, min_length)
        if dropped:
            log.info("  └─ %d fragments dropped (too short)", dropped)
        return passwords, dropped

    except requests.exceptions.ConnectionError:
        log.error("OLLAMA connection refused at %s", OLLAMA_URL)
        return [], 0
    except requests.exceptions.Timeout:
        log.error("OLLAMA call timed out after %ds", REQUEST_TIMEOUT)
        return [], 0
    except Exception as exc:
        log.error("OLLAMA call failed: %s", exc)
        return [], 0


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/generate", methods=["GET", "POST"])
def generate():
    if request.method == "GET":
        return jsonify(
            error="This endpoint only accepts POST requests with JSON body.",
            usage="POST /api/generate with Content-Type: application/json",
        ), 405

    # ── Parse input ──────────────────────────────────────────────────────────
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify(success=False, error="Invalid JSON body."), 400

    if not data:
        return jsonify(success=False, error="Empty request body."), 400

    full_name = (data.get("full_name") or "").strip()
    if not full_name:
        return jsonify(success=False, error="Full Name is required."), 400

    try:
        count = max(10, min(1000, int(data.get("count", 100))))
    except (TypeError, ValueError):
        count = 100

    try:
        min_length = max(1, min(64, int(data.get("min_length", 8))))
    except (TypeError, ValueError):
        min_length = 8

    try:
        raw_max    = data.get("max_length", min_length)
        max_length = max(min_length, min(128, int(raw_max)))
    except (TypeError, ValueError):
        max_length = min_length

    log.info(
        "Generating wordlist for: %s | count=%d | max_length=%d | "
        "chunk_size=%d | num_ctx=%d",
        full_name[:30], count, max_length, CHUNK_SIZE, NUM_CTX,
    )

    # ── Chunked generation ────────────────────────────────────────────────────
    seen_passwords: set[str]  = set()
    all_passwords:  list[str] = []
    total_dropped = 0
    remaining     = count
    chunk_num     = 0
    empty_chunks  = 0

    while remaining > 0 and len(all_passwords) < count:
        chunk_size = min(CHUNK_SIZE, remaining)
        chunk_num += 1
        total_chunks = -(-count // CHUNK_SIZE)   # ceiling division

        log.info(
            "Chunk %d/%d — requesting %d passwords (have %d / %d)",
            chunk_num, total_chunks, chunk_size, len(all_passwords), count,
        )

        prompt                   = _build_prompt(data, chunk_size, max_length)
        chunk_results, n_dropped = _call_ollama(prompt, chunk_size, max_length, min_length)
        total_dropped           += n_dropped

        if not chunk_results:
            empty_chunks += 1
            log.warning(
                "Chunk %d returned no passwords (empty_chunks=%d)",
                chunk_num, empty_chunks,
            )
            if empty_chunks >= 3:
                log.error("3 consecutive empty chunks — stopping early")
                break
            continue

        empty_chunks = 0
        added = 0
        for pw in chunk_results:
            if pw not in seen_passwords:
                seen_passwords.add(pw)
                all_passwords.append(pw)
                added += 1

        log.info("Chunk %d added %d unique passwords", chunk_num, added)
        remaining -= chunk_size

    passwords = all_passwords
    log.info(
        "Final wordlist: %d unique passwords (requested %d, %d chunks, %d fragments dropped)",
        len(passwords), count, chunk_num, total_dropped,
    )

    if not passwords:
        return jsonify(
            success=False,
            error=(
                "AI returned no usable passwords. "
                "Try adding more OSINT data or reducing the count."
            ),
        ), 200

    return jsonify(
        success=True,
        wordcount=len(passwords),
        passwords=passwords,
        min_length=min_length,
        max_length=max_length,
        requested_count=count,
        model=OLLAMA_MODEL,
    )


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    banner = r"""
  __        __            _ _____
  \ \      / /__  _ __ __| |  ___|__  _ __ __ _  ___
   \ \ /\ / / _ \| '__/ _` | |_ / _ \| '__/ _` |/ _ \
    \ V  V / (_) | | | (_| |  _| (_) | | | (_| |  __/
     \_/\_/ \___/|_|  \__,_|_|  \___/|_|  \__, |\___|
                                            |___/
"""
    print("=" * 55 + banner + "=" * 55)
    print(f"  AI Wordlist Generator  |  Model: {OLLAMA_MODEL}")
    print(f"  OLLAMA endpoint: {OLLAMA_URL}")
    print(f"  Context window:  {NUM_CTX} tokens  |  Chunk size: {CHUNK_SIZE}")
    print("  Server: http://localhost:5000")
    print("  For AUTHORIZED SECURITY TESTING ONLY")
    print("=" * 55 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=True)
