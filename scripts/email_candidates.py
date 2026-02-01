#!/usr/bin/env python3
import os, sys, smtplib
from pathlib import Path
from email.message import EmailMessage
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "data" / "results" / "global"

FILES = [
    RESULTS / "candidates_A_global.csv",
    RESULTS / "candidates_B_global.csv",
    RESULTS / "candidates_C_global.csv",
]

def require(name: str) -> str:
    v = os.getenv(name)
    if not v:
        print(f"Missing env var: {name}", file=sys.stderr)
        sys.exit(2)
    return v

def main():
    sender = require("EMAIL_SENDER")
    recipient = require("EMAIL_RECIPIENT")
    password = require("EMAIL_PASSWORD")  # Gmail App Password recommended

    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    missing = [p for p in FILES if not p.exists() or p.stat().st_size == 0]
    if missing:
        print("Missing/empty candidate files:", *map(str, missing), sep="\n", file=sys.stderr)
        sys.exit(3)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    msg = EmailMessage()
    msg["Subject"] = f"Global Screener — Candidates A/B/C ({now})"
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content("Attached: candidates A/B/C from the latest GitHub Actions run.")

    for p in FILES:
        msg.add_attachment(p.read_bytes(), maintype="text", subtype="csv", filename=p.name)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=60) as server:
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)

    print(f"✅ Sent {len(FILES)} attachments to {recipient}")

if __name__ == "__main__":
    main()
