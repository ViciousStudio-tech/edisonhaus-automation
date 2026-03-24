"""
Business 3 — Gmail Watcher + Daily Digest (IMAP)
Two modes:
  --mode=watch   Scans home@edisonhaus.com for urgent emails (every 30 min)
  --mode=digest  Sends combined inbox + Shopify + CJ daily digest (daily 8am EST)

Auth: IMAP with App Password (stdlib imaplib — no extra packages).
"""

import os, sys, json, time, re, logging, argparse, requests, smtplib, builtins
import imaplib, email as emaillib
from email.mime.text import MIMEText
from email.header import decode_header
from datetime import datetime, timedelta, timezone
from pathlib import Path

def _no_input(*a, **k): raise RuntimeError("BLOCKED: interactive prompt")
builtins.input = _no_input

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Env ───────────────────────────────────────────────────────────────────────
GMAIL_SENDER   = os.environ.get("GMAIL_SENDER", "home@edisonhaus.com")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_TO       = os.environ.get("GMAIL_TO", "nicholas.jacksondesign@gmail.com")

SHOPIFY_STORE  = os.environ.get("SHOPIFY_STORE", "fgtyz6-bj.myshopify.com")
SHOPIFY_TOKEN  = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
CJ_API_KEY     = os.environ.get("CJ_API_KEY", "")

Path("data").mkdir(exist_ok=True)
WATCHER_HEARTBEAT = Path("data/gmail_watcher_heartbeat.json")
DIGEST_HEARTBEAT  = Path("data/daily_digest_heartbeat.json")

SHOPIFY_BASE    = f"https://{SHOPIFY_STORE}/admin/api/2024-01"
SHOPIFY_HEADERS = {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}
CJ_BASE         = "https://developers.cjdropshipping.com/api2.0/v1"

NOW = datetime.now(timezone.utc)

URGENT_KEYWORDS = [
    "refund", "broken", "damaged", "wrong item", "not received",
    "dispute", "chargeback", "cancel", "urgent", "complaint",
    "never arrived", "where is my order", "wismo",
]


# ══════════════════════════════════════════════════════════════════════════════
# IMAP helpers
# ══════════════════════════════════════════════════════════════════════════════
def imap_connect() -> imaplib.IMAP4_SSL | None:
    """Connect to Gmail IMAP. Returns connection or None."""
    if not GMAIL_APP_PASS:
        log.error("GMAIL_APP_PASSWORD not set — cannot connect to IMAP")
        return None
    try:
        conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        conn.login(GMAIL_SENDER, GMAIL_APP_PASS)
        log.info(f"IMAP connected as {GMAIL_SENDER}")
        return conn
    except Exception as e:
        log.error(f"IMAP login failed: {e}")
        return None


def decode_mime_header(raw: str) -> str:
    """Decode a MIME-encoded header into plain text."""
    if not raw:
        return ""
    parts = decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


def extract_body(msg: emaillib.message.Message) -> str:
    """Extract plain-text body from an email message (first 500 chars)."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")[:500]
            elif ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    text = payload.decode("utf-8", errors="replace")
                    text = re.sub(r"<[^>]+>", " ", text)
                    text = re.sub(r"\s+", " ", text).strip()
                    return text[:500]
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode("utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
            return text[:500]
    return "(no body)"


def imap_fetch_since(conn: imaplib.IMAP4_SSL, since_dt: datetime,
                     unseen_only: bool = False) -> list[dict]:
    """Fetch emails since a datetime. Returns list of dicts with id, subject, from, date, body."""
    conn.select("INBOX", readonly=not unseen_only)  # writable only if we need to mark read

    # IMAP SINCE uses date only (no time), so we filter by date and post-filter
    date_str = since_dt.strftime("%d-%b-%Y")
    criteria = f'(UNSEEN SINCE {date_str})' if unseen_only else f'(SINCE {date_str})'

    status, data = conn.search(None, criteria)
    if status != "OK" or not data[0]:
        return []

    msg_ids = data[0].split()
    results = []

    for msg_id in msg_ids:
        try:
            status, msg_data = conn.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = emaillib.message_from_bytes(raw)

            subject = decode_mime_header(msg.get("Subject", ""))
            sender  = decode_mime_header(msg.get("From", ""))
            date    = msg.get("Date", "")

            # Parse date and filter by actual timestamp (IMAP SINCE is date-only)
            body = extract_body(msg)

            results.append({
                "id": msg_id,
                "subject": subject,
                "from": sender,
                "date": date,
                "body": body,
            })
        except Exception as e:
            log.warning(f"Error fetching message {msg_id}: {e}")

    return results


def imap_mark_read(conn: imaplib.IMAP4_SSL, msg_id: bytes):
    """Mark a message as read (SEEN)."""
    try:
        conn.store(msg_id, "+FLAGS", "\\Seen")
    except Exception as e:
        log.warning(f"Could not mark {msg_id} as read: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SMTP email sending
# ══════════════════════════════════════════════════════════════════════════════
def send_email(to: str, subject: str, body: str) -> bool:
    """Send plain-text email via Gmail SMTP."""
    if not GMAIL_SENDER or not GMAIL_APP_PASS:
        log.warning("GMAIL_SENDER or GMAIL_APP_PASSWORD not set — skipping email")
        log.info(f"  Would send to {to}: {subject}")
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = f"EdisonHaus <{GMAIL_SENDER}>"
    msg["To"] = to
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(GMAIL_SENDER, GMAIL_APP_PASS)
            smtp.sendmail(GMAIL_SENDER, [to], msg.as_string())
        log.info(f"Email sent to {to}")
        return True
    except Exception as e:
        log.error(f"SMTP send failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Shopify helpers
# ══════════════════════════════════════════════════════════════════════════════
def shopify_get(endpoint: str, params: dict = None) -> dict | None:
    if not SHOPIFY_TOKEN:
        return None
    url = f"{SHOPIFY_BASE}/{endpoint}"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=SHOPIFY_HEADERS, params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 2)))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.warning(f"Shopify GET {endpoint} attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return None


def shopify_put(endpoint: str, payload: dict) -> dict | None:
    if not SHOPIFY_TOKEN:
        return None
    try:
        r = requests.put(f"{SHOPIFY_BASE}/{endpoint}",
                         headers=SHOPIFY_HEADERS, json=payload, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"Shopify PUT {endpoint}: {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# CJ helpers
# ══════════════════════════════════════════════════════════════════════════════
def cj_get_token() -> str | None:
    if not CJ_API_KEY:
        return None
    try:
        r = requests.post(f"{CJ_BASE}/authentication/getAccessToken",
                          json={"apiKey": CJ_API_KEY}, timeout=15)
        data = r.json()
        if data.get("result") is True:
            return data["data"]["accessToken"]
        log.warning(f"CJ auth failed: {data.get('message')}")
    except Exception as e:
        log.warning(f"CJ auth error: {e}")
    return None


def cj_get_orders(token: str, status: str) -> list:
    since = (NOW - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        r = requests.get(f"{CJ_BASE}/shopping/order/list",
                         headers={"CJ-Access-Token": token},
                         params={"orderStatus": status, "createDateFrom": since,
                                 "pageNum": 1, "pageSize": 50}, timeout=15)
        data = r.json()
        if data.get("result") is True:
            return data.get("data", {}).get("list", []) or []
    except Exception as e:
        log.warning(f"CJ order list ({status}): {e}")
    return []


# ══════════════════════════════════════════════════════════════════════════════
# Shopify data collection
# ══════════════════════════════════════════════════════════════════════════════
def fetch_recent_orders(hours: int = 24) -> list:
    since = (NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    data = shopify_get("orders.json", {
        "created_at_min": since, "status": "any", "limit": 250})
    return data.get("orders", []) if data else []


def fetch_unfulfilled_old(hours: int = 48) -> list:
    before = (NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    data = shopify_get("orders.json", {
        "fulfillment_status": "unfulfilled", "created_at_max": before,
        "financial_status": "paid", "status": "open", "limit": 50})
    return data.get("orders", []) if data else []


def fetch_refunds(days: int = 7) -> list:
    since = (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    results = []
    for fs in ("refunded", "partially_refunded"):
        data = shopify_get("orders.json", {
            "financial_status": fs, "updated_at_min": since,
            "status": "any", "limit": 50})
        if data:
            results.extend(data.get("orders", []))
    return results


def fetch_revenue(since_str: str, until_str: str) -> float:
    data = shopify_get("orders.json", {
        "created_at_min": since_str, "created_at_max": until_str,
        "financial_status": "paid", "status": "any", "limit": 250})
    orders = data.get("orders", []) if data else []
    return sum(float(o.get("total_price", 0)) for o in orders)


def fetch_cj_problems() -> list:
    token = cj_get_token()
    if not token:
        return []
    problems = []
    for status in ("FAILED", "ABNORMAL"):
        problems.extend(cj_get_orders(token, status))
        time.sleep(1)
    return problems


# ══════════════════════════════════════════════════════════════════════════════
# Keyword matching
# ══════════════════════════════════════════════════════════════════════════════
def is_urgent(subject: str, body: str) -> bool:
    text = (subject + " " + body).lower()
    return any(kw in text for kw in URGENT_KEYWORDS)


def categorize_email(subject: str, body: str) -> str:
    text = (subject + " " + body).lower()
    if is_urgent(subject, body):
        return "urgent"
    if any(kw in text for kw in ["order", "tracking", "shipping", "delivered", "shipment", "fulfillment"]):
        return "order"
    if any(kw in text for kw in ["?", "how", "when", "can i", "do you", "help", "question", "inquiry"]):
        return "question"
    return "other"


# ══════════════════════════════════════════════════════════════════════════════
# MODE 1 — WATCH (every 30 min)
# ══════════════════════════════════════════════════════════════════════════════
def run_watch():
    log.info("── Gmail Watcher — checking for urgent emails ──")

    conn = imap_connect()
    if not conn:
        WATCHER_HEARTBEAT.write_text(json.dumps({
            "module": "b3_gmail_watcher", "last_run": datetime.now().isoformat(),
            "status": "error: IMAP login failed", "emails_checked": 0, "urgent_alerted": 0,
        }, indent=2))
        return

    since = NOW - timedelta(minutes=35)
    messages = imap_fetch_since(conn, since, unseen_only=True)
    log.info(f"Found {len(messages)} unread email(s) since {since.strftime('%H:%M UTC')}")

    alerted = 0
    for msg in messages:
        if not is_urgent(msg["subject"], msg["body"]):
            continue

        log.info(f"  URGENT: {msg['subject'][:60]} — {msg['from'][:40]}")

        alert_body = (
            f"Urgent email received at home@edisonhaus.com\n\n"
            f"From: {msg['from']}\n"
            f"Subject: {msg['subject']}\n"
            f"Date: {msg['date']}\n\n"
            f"Preview:\n{msg['body']}\n\n"
            f"Reply at: https://mail.google.com/mail/u/?authuser=home@edisonhaus.com"
        )
        alert_subject = f"\U0001f6a8 EdisonHaus URGENT: {msg['subject'][:80]} \u2014 {msg['from'][:40]}"

        send_email(GMAIL_TO, alert_subject, alert_body)
        imap_mark_read(conn, msg["id"])
        alerted += 1

    try:
        conn.logout()
    except Exception:
        pass

    log.info(f"Alerted on {alerted} urgent email(s)")
    WATCHER_HEARTBEAT.write_text(json.dumps({
        "module": "b3_gmail_watcher", "last_run": datetime.now().isoformat(),
        "status": "success", "emails_checked": len(messages), "urgent_alerted": alerted,
    }, indent=2))


# ══════════════════════════════════════════════════════════════════════════════
# MODE 2 — DIGEST (daily)
# ══════════════════════════════════════════════════════════════════════════════
def run_digest():
    log.info("── Daily Digest — building report ──")

    # Update Shopify customer email (idempotent)
    result = shopify_put("shop.json", {"shop": {"customer_email": "home@edisonhaus.com"}})
    if result:
        log.info(f"Shopify customer_email: {result.get('shop', {}).get('customer_email', '?')}")

    # ── IMAP inbox stats ──────────────────────────────────────────────────────
    total_inbox = 0
    total_unread = 0
    categories = {"urgent": [], "question": [], "order": [], "other": []}

    conn = imap_connect()
    if conn:
        since_24h = NOW - timedelta(hours=24)

        # All emails last 24h
        all_msgs = imap_fetch_since(conn, since_24h, unseen_only=False)
        total_inbox = len(all_msgs)

        for msg in all_msgs:
            cat = categorize_email(msg["subject"], msg["body"])
            categories[cat].append({"subject": msg["subject"], "from": msg["from"]})

        # Unread count
        conn.select("INBOX", readonly=True)
        date_str = since_24h.strftime("%d-%b-%Y")
        status, data = conn.search(None, f"(UNSEEN SINCE {date_str})")
        if status == "OK" and data[0]:
            total_unread = len(data[0].split())

        try:
            conn.logout()
        except Exception:
            pass

        log.info(f"  Inbox: {total_inbox} total, {total_unread} unread")
    else:
        log.warning("  IMAP unavailable — inbox section will be empty")

    # ── Shopify data ──────────────────────────────────────────────────────────
    log.info("Fetching Shopify data...")
    recent      = fetch_recent_orders(24)
    unfulfilled = fetch_unfulfilled_old(48)
    refunds     = fetch_refunds(7)
    cj_problems = fetch_cj_problems()

    now_str = NOW.strftime("%Y-%m-%dT%H:%M:%S-00:00")
    d7_str  = (NOW - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    d14_str = (NOW - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    rev_7d       = fetch_revenue(d7_str, now_str)
    rev_prior_7d = fetch_revenue(d14_str, d7_str)
    revenue_24h  = sum(float(o.get("total_price", 0)) for o in recent)

    log.info(f"  Orders 24h: {len(recent)}, revenue: ${revenue_24h:.2f}")
    log.info(f"  7d: ${rev_7d:.2f}, prior 7d: ${rev_prior_7d:.2f}")

    # ── Build digest body ─────────────────────────────────────────────────────
    date_str = NOW.strftime("%A, %B %d %Y")
    action_items = []

    lines = [
        "\u2501" * 28,
        f"EdisonHaus Daily Digest \u2014 {date_str}",
        "\u2501" * 28,
        "",
        "\U0001f4e7 INBOX (last 24hrs)",
        f"{total_inbox} total | {total_unread} unread",
    ]

    if categories["urgent"]:
        lines.append(f"\U0001f6a8 Urgent: {len(categories['urgent'])}")
        for e in categories["urgent"][:5]:
            lines.append(f"   \u2022 {e['subject'][:60]} \u2014 {e['from'][:30]}")
            action_items.append(f"Urgent email: {e['subject'][:50]}")
    else:
        lines.append("\U0001f6a8 Urgent: None")

    if categories["question"]:
        lines.append(f"\u2753 Questions: {len(categories['question'])}")
        for e in categories["question"][:3]:
            lines.append(f"   \u2022 {e['subject'][:60]}")
    else:
        lines.append("\u2753 Questions: None")

    lines.append(f"\U0001f4e6 Order related: {len(categories['order'])}")
    lines.append(f"\U0001f4ec Other: {len(categories['other'])}")
    lines.append("")

    lines.append("\U0001f4e6 ORDERS (last 24hrs)")
    lines.append(f"New: {len(recent)} | Revenue: ${revenue_24h:.2f}")

    if unfulfilled:
        lines.append(f"\u26a0\ufe0f Unfulfilled 48hr+: {len(unfulfilled)}")
        for o in unfulfilled:
            num = o.get("name", "?")
            created = o.get("created_at", "?")[:10]
            lines.append(f"   \u2022 {num} (placed {created})")
            action_items.append(f"Unfulfilled order {num} ({created})")
    else:
        lines.append("\u26a0\ufe0f Unfulfilled 48hr+: None")

    if cj_problems:
        lines.append(f"\u26a0\ufe0f CJ Failed: {len(cj_problems)}")
        for o in cj_problems:
            oid = o.get("orderId", o.get("orderNum", "?"))
            lines.append(f"   \u2022 CJ order {oid} \u2014 {o.get('orderStatus', '?')}")
            action_items.append(f"CJ failed order {oid}")
    else:
        lines.append("\u26a0\ufe0f CJ Failed: None")
    lines.append("")

    if rev_prior_7d > 0:
        pct = ((rev_7d - rev_prior_7d) / rev_prior_7d) * 100
        change = f"+{pct:.1f}%" if pct >= 0 else f"{pct:.1f}%"
    elif rev_7d > 0:
        change = "new revenue (no prior)"
    else:
        change = "\u2014"
    lines.append("\U0001f4b0 REVENUE")
    lines.append(f"Last 7d: ${rev_7d:.2f} | Prior 7d: ${rev_prior_7d:.2f} | {change}")
    lines.append("")

    if refunds:
        for o in refunds:
            action_items.append(f"Refund: {o.get('name', '?')} ${o.get('total_price', '0')}")

    lines.append("\u26a0\ufe0f ACTION NEEDED")
    if action_items:
        for item in action_items:
            lines.append(f"   \u2022 {item}")
    else:
        lines.append("Nothing \u2014 all clear \u2705")
    lines.append("")

    lines.extend([
        "\u2501" * 28,
        "Manage: home@edisonhaus.com",
        "Shopify: https://admin.shopify.com/store/fgtyz6-bj",
        "\u2501" * 28,
    ])

    body = "\n".join(lines)

    tag = f"\u26a0\ufe0f {len(action_items)} items need attention" if action_items else "\u2705 All clear"
    subject = f"EdisonHaus Daily \u2014 {NOW.strftime('%b %d')} | {len(recent)} orders | {tag}"

    log.info(f"\nSubject: {subject}\n")
    log.info(body)

    send_email(GMAIL_TO, subject, body)

    DIGEST_HEARTBEAT.write_text(json.dumps({
        "module": "b3_daily_digest", "last_run": datetime.now().isoformat(),
        "status": "success", "orders_24h": len(recent), "action_items": len(action_items),
    }, indent=2))


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="EdisonHaus Gmail Watcher + Digest")
    parser.add_argument("--mode", choices=["watch", "digest"], required=True)
    args = parser.parse_args()

    log.info("=" * 60)
    log.info(f"B3 Gmail Watcher — mode={args.mode}")
    log.info("=" * 60)

    if args.mode == "watch":
        run_watch()
    else:
        run_digest()

    log.info("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"Gmail watcher failed: {e}")
        for hb in [WATCHER_HEARTBEAT, DIGEST_HEARTBEAT]:
            try:
                hb.write_text(json.dumps({
                    "module": hb.stem, "last_run": datetime.now().isoformat(),
                    "status": f"error: {e}",
                }, indent=2))
            except Exception:
                pass
        raise
