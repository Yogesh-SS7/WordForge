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

# Context window to use.  Must be ≤ what the model can handle in RAM.
# dolphin-llama3:8b ships with a 4096-token rope by default.
NUM_CTX = 4096

# Passwords per single OLLAMA call.  Keeping this low (≤40) ensures
# prompt + output always fits inside NUM_CTX without overflow.
CHUNK_SIZE = 40

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


def _build_prompt(data: dict, count: int, min_length: int) -> str:
    """
    Concise prompt optimised for small local LLMs (≤8B parameters).
    Verbose "CRITICAL RULES" framing causes 8B models to echo the rules
    back instead of generating passwords, so we keep instructions short
    and direct.
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
        f"Generate exactly {count} password candidates for the person described below.\n\n"
        f"Rules:\n"
        f"1. Every password must be at least {min_length} characters long.\n"
        f"2. No spaces — passwords are single unbroken strings (no spaces ever).\n"
        f"3. Use only information from the personal data to build each password.\n"
        f"4. Mix styles: lowercase, FirstCap, CamelCase, leet (a=4, e=3, i=1, o=0, s=5).\n"
        f"5. Output exactly {count} lines. Each line is one password. Nothing else.\n"
        f"6. No numbering, no bullets, no explanations, no blank lines.\n\n"
        f"Personal data:\n{osint_block}\n\n"
        f"Passwords:"
    )
    return prompt


def _clean_passwords(raw_text: str) -> list[str]:
    """
    Strip all formatting junk from LLM output and return clean, unique passwords.
    Zero-tolerance on spaces — any line containing a space is rejected.
    """
    seen   = set()
    result = []

    # Patterns whose matching lines are always junk (instructions echoed back, headers, etc.)
    reject_re = re.compile(
        r"^(\s*$"
        r"|#+\s"
        r"|[-*]\s{0,2}(pattern|technique|example|note|rule|output|here|begin"
        r"|sure|okay|of course|critical|follow|every|apply|build|use|mix"
        r"|no |password|generate)[:\s]"
        r"|(here are|these are|note:|sure!|okay|of course|begin|output:|passwords?:"
        r"|based on|personal data|rules?:|1\.|2\.|3\.|4\.|5\.|6\.)"
        r"|[`]{1,3}"
        r"|={3,}"
        r"|-{3,})",
        re.IGNORECASE,
    )
    strip_num_re = re.compile(r"^\d+[.)\-]\s+")   # "1. " / "1) " / "1- "
    strip_bul_re = re.compile(r"^[-*•·]\s+")

    for line in raw_text.splitlines():
        line = line.strip()
        if not line or reject_re.match(line):
            continue

        # Strip numbering / bullet prefixes the LLM sometimes adds
        line = strip_num_re.sub("", line)
        line = strip_bul_re.sub("", line)
        line = line.strip()

        if not line:
            continue

        # Reject over-long lines (prose paragraphs)
        if len(line) > 128:
            continue

        # ── FIX #1: zero-tolerance space filter ──────────────────────────
        # Passwords must be single unbroken strings.  Any space means the
        # LLM output a phrase / sentence instead of a password.
        if " " in line:
            continue

        if line not in seen:
            seen.add(line)
            result.append(line)

    return result


def _call_ollama(prompt: str, chunk_size: int) -> list[str]:
    """
    Make one OLLAMA API call requesting `chunk_size` passwords.

    FIX #2 — num_predict is capped so it never exceeds NUM_CTX.
    When num_predict > num_ctx, OLLAMA silently resets it to its
    internal default (~128 tokens), which is why we were getting only
    12–16 passwords regardless of the requested count.

    Safe formula:
        prompt_budget  = 512 tokens  (generous upper bound for our prompt)
        output_budget  = NUM_CTX - prompt_budget
        num_predict    = min(output_budget, chunk_size * 18)
    """
    prompt_budget  = 512
    output_budget  = NUM_CTX - prompt_budget                 # e.g. 4096-512 = 3584
    num_predict    = min(output_budget, chunk_size * 18)     # e.g. 40*18=720 ≪ 3584

    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature":    0.75,
            "top_p":          0.9,
            "repeat_penalty": 1.15,
            "num_ctx":        NUM_CTX,     # explicit context window
            "num_predict":    num_predict, # always ≤ NUM_CTX − prompt_budget
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
        return _clean_passwords(raw_text)

    except requests.exceptions.ConnectionError:
        log.error("OLLAMA connection refused at %s", OLLAMA_URL)
        return []
    except requests.exceptions.Timeout:
        log.error("OLLAMA call timed out after %ds", REQUEST_TIMEOUT)
        return []
    except Exception as exc:
        log.error("OLLAMA call failed: %s", exc)
        return []


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

    log.info(
        "Generating wordlist for: %s | count=%d | min_length=%d | "
        "chunk_size=%d | num_ctx=%d",
        full_name[:30], count, min_length, CHUNK_SIZE, NUM_CTX,
    )

    # ── FIX #3: Chunked generation ────────────────────────────────────────────
    # We split the total request into chunks of CHUNK_SIZE so that
    # prompt + output always fits well within NUM_CTX.
    # Results from each chunk are merged and deduplicated.
    seen_passwords: set[str]  = set()
    all_passwords:  list[str] = []
    remaining = count
    chunk_num = 0
    empty_chunks = 0           # consecutive empty chunk counter — safety stop

    while remaining > 0 and len(all_passwords) < count:
        chunk_size = min(CHUNK_SIZE, remaining)
        chunk_num += 1

        log.info(
            "Chunk %d/%d — requesting %d passwords (have %d / %d so far)",
            chunk_num,
            -(-count // CHUNK_SIZE),   # ceiling division = total expected chunks
            chunk_size,
            len(all_passwords),
            count,
        )

        prompt        = _build_prompt(data, chunk_size, min_length)
        chunk_results = _call_ollama(prompt, chunk_size)

        if not chunk_results:
            empty_chunks += 1
            log.warning("Chunk %d returned no passwords (empty_chunks=%d)", chunk_num, empty_chunks)
            if empty_chunks >= 3:
                log.error("3 consecutive empty chunks — stopping early to avoid infinite loop")
                break
            # Don't break immediately — retry the same remaining count
            continue

        empty_chunks = 0   # reset counter on success
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
        "Final wordlist: %d unique passwords (requested %d, ran %d chunks)",
        len(passwords), count, chunk_num,
    )

    if not passwords:
        return jsonify(
            success=False,
            error="AI returned no usable passwords. Try adding more OSINT data or reducing the count.",
        ), 200

    return jsonify(
        success=True,
        wordcount=len(passwords),
        passwords=passwords,
        min_length=min_length,
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
