#!/usr/bin/env python3
import os
import sys
import smtplib
from pathlib import Path
from email.message import EmailMessage
from datetime import datetime


ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "data" / "results"

# We’ll search for candidates anywhere under data/results
# (works whether your outputs are in data/results/global/ or a timestamped subfolder)
PATTERNS = [
    "candidates_A*.csv",
    "candidates_B*.csv",
    "candidates_C*.csv",
]


def require(name: str) -> str:
    v = os.getenv(name)
    if not v:
        print(f"Missing env var: {name}", file=sys.stderr)
        sys.exit(2)
    return v


def main() -> None:
    sender = require("EMAIL_SENDER")
    recipients_raw = require("EMAIL_RECIPIENT")
    password = require("EMAIL_PASSWORD")  # Gmail App Password

    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    # Normalize recipients: allow comma/newline separated
    recipients = [r.strip() for r in recipients_raw.replace("\n", ",").split(",") if r.strip()]
    if not recipients:
        print("EMAIL_RECIPIENT parsed to empty list.", file=sys.stderr)
        sys.exit(2)

    if not RESULTS_DIR.exists():
        print(f"Results dir not found: {RESULTS_DIR}", file=sys.stderr)
        sys.exit(3)

    # Find candidate files
    found = []
    for pat in PATTERNS:
        found.extend(RESULTS_DIR.rglob(pat))

    # Keep only non-empty files
    found = [p for p in found if p.is_file() and p.stat().st_size > 0]

    # If multiple runs exist, prefer the newest 1 file per category (A/B/C)
    def newest_for(prefix: str):
        files = [p for p in found if p.name.startswith(prefix)]
        if not files:
            return None
        return max(files, key=lambda p: p.stat().st_mtime)

    a = newest_for("candidates_A")
    b = newest_for("candidates_B")
    c = newest_for("candidates_C")

    files_to_attach = [p for p in [a, b, c] if p is not None]

    if not files_to_attach:
        print(
            "No non-empty candidates_[A|B|C]*.csv found under data/results.\n"
            "Tip: check artifact paths and where run_all_universes.py writes outputs.",
            file=sys.stderr,
        )
        sys.exit(3)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    msg = EmailMessage()
    msg["Subject"] = f"Global Screener — Candidates A/B/C ({now})"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    body = [
        "Attached: candidates A/B/C from the latest GitHub Actions run.",
        "",
        f"Repo root: {ROOT}",
        f"Results dir: {RESULTS_DIR}",
        "",
        "Attachments:",
        *[f"- {p.relative_to(ROOT)}" for p in files_to_attach],
    ]
    msg.set_content("\n".join(body))

    for p in files_to_attach:
        msg.add_attachment(p.read_bytes(), maintype="text", subtype="csv", filename=p.name)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=60) as server:
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)

    print(f"✅ Sent {len(files_to_attach)} attachment(s) to {', '.join(recipients)}")


if __name__ == "__main__":
    main()
