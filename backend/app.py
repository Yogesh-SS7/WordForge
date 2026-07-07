"""
WordForge — Flask Backend
AI-Assisted Password Wordlist Generator for authorized security testing.

Routes:
  GET  /             → Serve the SPA
  POST /api/generate → Accept OSINT data, call OLLAMA, return wordlist
"""

import json
import logging
import re
from datetime import datetime

import requests
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

# ── App Setup ────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "dolphin-llama3:8b"
REQUEST_TIMEOUT = 300              # seconds — LLM can be slow

# ── Helpers ───────────────────────────────────────────────────

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
    """Build a clean, natural prompt focused on relevant combinations at the target length."""
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
        f"Based on the personal information below, generate {count} unique password guesses "
        f"that this person might realistically use.\n\n"
        f"Every password must be exactly {min_length} characters long — not shorter, not longer.\n\n"
        f"Build each password by combining pieces of the information given: "
        f"names, dates, nicknames, pet names, years, workplaces, hobbies, and favourite things. "
        f"If a combination comes out shorter than {min_length} characters, extend it by adding "
        f"birth year digits, anniversary digits, or other numbers from the data — "
        f"not random symbols or unrelated characters.\n\n"
        f"Do not add random junk. Every character in each password must come from or relate to "
        f"the personal information provided.\n\n"
        f"Output rules:\n"
        f"- One password per line, nothing else\n"
        f"- No numbering, no bullet points, no headers, no explanations\n"
        f"- Every password is exactly {min_length} characters\n"
        f"- Generate exactly {count} passwords\n\n"
        f"Personal information:\n{osint_block}\n\n"
        f"Passwords:"
    )
    return prompt


def _clean_passwords(raw_text: str) -> list[str]:
    """
    Strip formatting junk from LLM output and deduplicate.
    Length enforcement is handled entirely by the prompt — no post-filter here.
    """
    lines = raw_text.splitlines()
    seen   = set()
    result = []

    reject_patterns = [
        r"^\s*$",
        r"^#+\s",
        r"^[-*]\s{0,2}(pattern|technique|example|note|rule|output|here|begin|sure|okay|of course)[:\s]",
        r"^(here are|these are|note:|sure!|okay|of course|begin|output:|passwords?:)",
        r"^[`]{1,3}",
        r"={3,}",       # separator lines like ======
    ]
    reject_re    = re.compile("|".join(reject_patterns), re.IGNORECASE)
    strip_num_re = re.compile(r"^\d+[.)\-]\s+")   # "1. " or "1) " or "1- "
    strip_bul_re = re.compile(r"^[-*•·]\s+")

    for line in lines:
        line = line.strip()
        if not line or reject_re.match(line):
            continue

        line = strip_num_re.sub("", line)
        line = strip_bul_re.sub("", line)
        line = line.strip()

        # Skip empty after stripping, or over-long lines (prose)
        if not line or len(line) > 128:
            continue
        # Skip lines that look like sentences (too many spaces)
        if line.count(" ") > 2:
            continue

        if line not in seen:
            seen.add(line)
            result.append(line)

    return result


# ── Routes ────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/generate", methods=["GET", "POST"])
def generate():
    # Browser visited the endpoint directly (GET) — give a helpful message
    if request.method == "GET":
        return jsonify(
            error="This endpoint only accepts POST requests with JSON body.",
            usage="POST /api/generate with Content-Type: application/json",
        ), 405

    # ── Parse input ──────────────────────────────────────────
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify(success=False, error="Invalid JSON body."), 400

    if not data:
        return jsonify(success=False, error="Empty request body."), 400

    full_name = (data.get("full_name") or "").strip()
    if not full_name:
        return jsonify(success=False, error="Full Name is required."), 400

    # Validate and clamp generation parameters
    try:
        count = max(10, min(1000, int(data.get("count", 100))))
    except (TypeError, ValueError):
        count = 100

    try:
        min_length = max(1, min(64, int(data.get("min_length", 8))))
    except (TypeError, ValueError):
        min_length = 8

    log.info(
        "Generating wordlist for: %s | count=%d | min_length=%d",
        full_name[:30], count, min_length
    )

    # ── Build prompt ─────────────────────────────────────────
    prompt = _build_prompt(data, count, min_length)
    log.debug("Prompt length: %d chars", len(prompt))

    # ── Call OLLAMA ──────────────────────────────────────────
    ollama_payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature":    0.7,    # lower = more obedient to instructions
            "top_p":          0.9,
            "repeat_penalty": 1.15,
            # Each password ~10 tokens on average; add 50% buffer
            "num_predict":    max(8192, count * 15),
        },
    }

    try:
        resp = requests.post(
            OLLAMA_URL,
            json=ollama_payload,
            timeout=REQUEST_TIMEOUT,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        log.error("OLLAMA connection refused at %s", OLLAMA_URL)
        return jsonify(
            success=False,
            error=(
                "OLLAMA service not detected. "
                "Make sure it's running on localhost:11434 "
                f"with model '{OLLAMA_MODEL}' loaded."
            ),
        ), 503
    except requests.exceptions.Timeout:
        log.error("OLLAMA request timed out after %ds", REQUEST_TIMEOUT)
        return jsonify(
            success=False,
            error="OLLAMA request timed out. The model may be busy or not loaded.",
        ), 504
    except requests.exceptions.HTTPError as exc:
        log.error("OLLAMA HTTP error: %s", exc)
        return jsonify(
            success=False,
            error=f"OLLAMA returned HTTP {resp.status_code}. Check that the model is available.",
        ), 502

    # ── Parse OLLAMA response ─────────────────────────────────
    try:
        ollama_data = resp.json()
    except ValueError:
        log.error("OLLAMA returned non-JSON response")
        return jsonify(success=False, error="OLLAMA returned an invalid response."), 502

    raw_text = ollama_data.get("response", "")
    log.info("OLLAMA raw response length: %d chars", len(raw_text))

    if not raw_text.strip():
        return jsonify(
            success=False,
            error="AI returned no results. Try adjusting your input data.",
        ), 200

    # ── Clean & deduplicate ───────────────────────────────────
    passwords = _clean_passwords(raw_text)
    log.info("Cleaned wordlist: %d unique passwords (requested %d)", len(passwords), count)

    if not passwords:
        return jsonify(
            success=False,
            error="AI returned no usable passwords. Try adding more OSINT data or reducing the requested count.",
        ), 200

    return jsonify(
        success=True,
        wordcount=len(passwords),
        passwords=passwords,
        min_length=min_length,
        requested_count=count,
        model=OLLAMA_MODEL,
    )


# ── Entry Point ───────────────────────────────────────────────

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
    print("  Server: http://localhost:5000")
    print("  For AUTHORIZED SECURITY TESTING ONLY")
    print("=" * 55 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=True)
