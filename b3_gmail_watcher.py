"""
Business 3 — Gmail Watcher + Daily Digest
Two modes:
  --mode=watch   Scans home@edisonhaus.com for urgent emails (every 30 min)
  --mode=digest  Sends combined inbox + Shopify + CJ daily digest (daily 8am EST)

Authentication:
  Gmail API OAuth2: GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
  SMTP sending:     GMAIL_SENDER (home@edisonhaus.com) + GMAIL_APP_PASSWORD
"""

import os, sys, json, time, re, logging, argparse, requests, smtplib, builtins, base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.mime.text import MIMEText

def _no_input(*a, **k): raise RuntimeError("BLOCKED: interactive prompt")
builtins.input = _no_input

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Env ───────────────────────────────────────────────────────────────────────
GMAIL_CLIENT_ID     = os.environ.get("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
GMAIL_REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")
GMAIL_SENDER        = os.environ.get("GMAIL_SENDER", "home@edisonhaus.com")
GMAIL_APP_PASS      = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_TO            = os.environ.get("GMAIL_TO", "nicholas.jacksondesign@gmail.com")

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

# ── Gmail OAuth2 ──────────────────────────────────────────────────────────────
_gmail_access_token: str = ""

def gmail_auth() -> str:
    """Exchange refresh token for a fresh Gmail API access token."""
    global _gmail_access_token
    if _gmail_access_token:
        return _gmail_access_token
    if not all([GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN]):
        log.error("Gmail OAuth2 env vars not set (GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN)")
        return ""
    try:
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": GMAIL_CLIENT_ID,
            "client_secret": GMAIL_CLIENT_SECRET,
            "refresh_token": GMAIL_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        }, timeout=15)
        r.raise_for_status()
        _gmail_access_token = r.json()["access_token"]
        log.info("Gmail OAuth2 token obtained")
        return _gmail_access_token
    except Exception as e:
        log.error(f"Gmail OAuth2 failed: {e}")
        return ""


def gmail_api(method: str, endpoint: str, **kwargs) -> dict | None:
    """Call Gmail API. Returns parsed JSON or None."""
    token = gmail_auth()
    if not token:
        return None
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.request(method, url, headers=headers, timeout=15, **kwargs)
        if r.status_code == 429:
            time.sleep(2)
            r = requests.request(method, url, headers=headers, timeout=15, **kwargs)
        r.raise_for_status()
        return r.json() if r.text else {}
    except Exception as e:
        log.warning(f"Gmail API {endpoint}: {e}")
        return None


def gmail_get_messages(query: str, max_results: int = 100) -> list:
    """Search Gmail and return list of message metadata."""
    data = gmail_api("GET", "messages", params={"q": query, "maxResults": max_results})
    if not data or "messages" not in data:
        return []
    messages = []
    for msg_stub in data["messages"]:
        msg = gmail_api("GET", f"messages/{msg_stub['id']}", params={"format": "metadata",
            "metadataHeaders": ["From", "Subject", "Date"]})
        if msg:
            messages.append(msg)
    return messages


def gmail_get_body(msg_id: str) -> str:
    """Fetch plain-text body of a message (first 500 chars)."""
    msg = gmail_api("GET", f"messages/{msg_id}", params={"format": "full"})
    if not msg:
        return ""
    payload = msg.get("payload", {})
    # Try top-level body first, then parts
    parts = [payload] + (payload.get("parts") or [])
    for part in parts:
        if part.get("mimeType") in ("text/plain", "text/html"):
            b64 = part.get("body", {}).get("data", "")
            if b64:
                text = base64.urlsafe_b64decode(b64).decode("utf-8", errors="replace")
                # Strip HTML tags if needed
                if part["mimeType"] == "text/html":
                    text = re.sub(r"<[^>]+>", " ", text)
                    text = re.sub(r"\s+", " ", text).strip()
                return text[:500]
    return "(no body)"


def gmail_get_header(msg: dict, name: str) -> str:
    """Extract a header value from a Gmail message metadata response."""
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def gmail_ensure_label(label_name: str) -> str | None:
    """Get or create a Gmail label by name. Returns label ID."""
    data = gmail_api("GET", "labels")
    if data:
        for label in data.get("labels", []):
            if label["name"] == label_name:
                return label["id"]
    # Create it
    result = gmail_api("POST", "labels", json={"name": label_name,
        "labelListVisibility": "labelShow", "messageListVisibility": "show"})
    if result:
        log.info(f"Created Gmail label: {label_name}")
        return result["id"]
    return None


def gmail_add_label(msg_id: str, label_id: str):
    """Add a label to a message."""
    gmail_api("POST", f"messages/{msg_id}/modify",
              json={"addLabelIds": [label_id]})


# ── SMTP email sending ───────────────────────────────────────────────────────
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


# ── Shopify helpers ───────────────────────────────────────────────────────────
def shopify_get(endpoint: str, params: dict = None) -> dict | None:
    """GET from Shopify Admin API with retry."""
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
    """PUT to Shopify Admin API."""
    if not SHOPIFY_TOKEN:
        return None
    url = f"{SHOPIFY_BASE}/{endpoint}"
    try:
        r = requests.put(url, headers=SHOPIFY_HEADERS, json=payload, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"Shopify PUT {endpoint}: {e}")
    return None


# ── CJ helpers ────────────────────────────────────────────────────────────────
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
                                 "pageNum": 1, "pageSize": 50},
                         timeout=15)
        data = r.json()
        if data.get("result") is True:
            return data.get("data", {}).get("list", []) or []
    except Exception as e:
        log.warning(f"CJ order list ({status}): {e}")
    return []


# ── Shopify data collection ──────────────────────────────────────────────────
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
# MODE 1 — WATCH (every 30 min)
# ══════════════════════════════════════════════════════════════════════════════
def is_urgent(subject: str, body: str) -> bool:
    """Check if email matches urgent keywords."""
    text = (subject + " " + body).lower()
    return any(kw in text for kw in URGENT_KEYWORDS)


def run_watch():
    log.info("── Gmail Watcher — checking for urgent emails ──")

    # Get or create "Auto-Alerted" label
    label_id = gmail_ensure_label("Auto-Alerted")

    # Fetch unread emails from last 35 minutes, excluding already-alerted
    query = "is:unread newer_than:35m -label:Auto-Alerted"
    messages = gmail_get_messages(query, max_results=50)
    log.info(f"Found {len(messages)} unread email(s) in last 35 min")

    alerted = 0
    for msg in messages:
        msg_id = msg["id"]
        subject = gmail_get_header(msg, "Subject")
        sender  = gmail_get_header(msg, "From")
        date    = gmail_get_header(msg, "Date")
        body    = gmail_get_body(msg_id)

        if not is_urgent(subject, body):
            continue

        log.info(f"  URGENT: {subject[:60]} — {sender[:40]}")

        alert_body = (
            f"Urgent email received at home@edisonhaus.com\n\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n"
            f"Date: {date}\n\n"
            f"Preview:\n{body}\n\n"
            f"Reply at: https://mail.google.com/mail/u/?authuser=home@edisonhaus.com"
        )
        alert_subject = f"\U0001f6a8 EdisonHaus URGENT: {subject[:80]} \u2014 {sender[:40]}"

        send_email(GMAIL_TO, alert_subject, alert_body)

        if label_id:
            gmail_add_label(msg_id, label_id)

        alerted += 1

    log.info(f"Alerted on {alerted} urgent email(s)")

    WATCHER_HEARTBEAT.write_text(json.dumps({
        "module": "b3_gmail_watcher",
        "last_run": datetime.now().isoformat(),
        "status": "success",
        "emails_checked": len(messages),
        "urgent_alerted": alerted,
    }, indent=2))


# ══════════════════════════════════════════════════════════════════════════════
# MODE 2 — DIGEST (daily)
# ══════════════════════════════════════════════════════════════════════════════
def categorize_email(subject: str, body: str) -> str:
    """Return category emoji+label for an email."""
    text = (subject + " " + body).lower()
    if is_urgent(subject, body):
        return "urgent"
    order_kw = ["order", "tracking", "shipping", "delivered", "shipment", "fulfillment"]
    if any(kw in text for kw in order_kw):
        return "order"
    question_kw = ["?", "how", "when", "can i", "do you", "help", "question", "inquiry"]
    if any(kw in text for kw in question_kw):
        return "question"
    return "other"


def run_digest():
    log.info("── Daily Digest — building report ──")

    # ── Update Shopify customer email (idempotent) ────────────────────────────
    result = shopify_put("shop.json", {"shop": {"customer_email": "home@edisonhaus.com"}})
    if result:
        log.info(f"Shopify customer_email: {result.get('shop', {}).get('customer_email', '?')}")

    # ── Gmail inbox stats ─────────────────────────────────────────────────────
    log.info("Fetching inbox (24hr)...")
    all_msgs = gmail_get_messages("newer_than:1d", max_results=100)
    unread_msgs = gmail_get_messages("is:unread newer_than:1d", max_results=100)
    total_inbox = len(all_msgs)
    total_unread = len(unread_msgs)

    categories = {"urgent": [], "question": [], "order": [], "other": []}
    for msg in all_msgs:
        subject = gmail_get_header(msg, "Subject")
        sender  = gmail_get_header(msg, "From")
        body    = gmail_get_body(msg["id"])
        cat     = categorize_email(subject, body)
        categories[cat].append({"subject": subject, "sender": sender})

    log.info(f"  {total_inbox} total, {total_unread} unread")

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

    revenue_24h = sum(float(o.get("total_price", 0)) for o in recent)

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
        f"\U0001f4e7 INBOX (last 24hrs)",
        f"{total_inbox} total | {total_unread} unread",
    ]

    # Urgent
    if categories["urgent"]:
        lines.append(f"\U0001f6a8 Urgent: {len(categories['urgent'])}")
        for e in categories["urgent"][:5]:
            lines.append(f"   \u2022 {e['subject'][:60]} \u2014 {e['sender'][:30]}")
            action_items.append(f"Urgent email: {e['subject'][:50]}")
    else:
        lines.append("\U0001f6a8 Urgent: None")

    # Questions
    if categories["question"]:
        lines.append(f"\u2753 Questions: {len(categories['question'])}")
        for e in categories["question"][:3]:
            lines.append(f"   \u2022 {e['subject'][:60]}")
    else:
        lines.append("\u2753 Questions: None")

    # Order related
    lines.append(f"\U0001f4e6 Order related: {len(categories['order'])}")
    lines.append(f"\U0001f4ec Other: {len(categories['other'])}")
    lines.append("")

    # Orders
    lines.append(f"\U0001f4e6 ORDERS (last 24hrs)")
    lines.append(f"New orders: {len(recent)} | Revenue: ${revenue_24h:.2f}")

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

    # Revenue
    if rev_prior_7d > 0:
        pct = ((rev_7d - rev_prior_7d) / rev_prior_7d) * 100
        change = f"+{pct:.1f}%" if pct >= 0 else f"{pct:.1f}%"
    elif rev_7d > 0:
        change = "new revenue (no prior)"
    else:
        change = "\u2014"
    lines.append(f"\U0001f4b0 REVENUE")
    lines.append(f"Last 7 days: ${rev_7d:.2f} | Prior 7 days: ${rev_prior_7d:.2f} | {change}")
    lines.append("")

    # Refunds
    if refunds:
        for o in refunds:
            action_items.append(f"Refund: {o.get('name', '?')} ${o.get('total_price', '0')}")

    # Action needed
    lines.append(f"\u26a0\ufe0f ACTION NEEDED")
    if action_items:
        for item in action_items:
            lines.append(f"   \u2022 {item}")
    else:
        lines.append(f"Nothing \u2014 all clear \u2705")
    lines.append("")

    lines.extend([
        "\u2501" * 28,
        "Manage inbox: home@edisonhaus.com",
        "Shopify: https://admin.shopify.com/store/fgtyz6-bj",
        "\u2501" * 28,
    ])

    body = "\n".join(lines)

    # Subject
    tag = f"\u26a0\ufe0f {len(action_items)} items need attention" if action_items else "\u2705 All clear"
    subject = f"EdisonHaus Daily \u2014 {NOW.strftime('%b %d')} | {len(recent)} orders | {tag}"

    log.info(f"\nSubject: {subject}\n")
    log.info(body)

    send_email(GMAIL_TO, subject, body)

    DIGEST_HEARTBEAT.write_text(json.dumps({
        "module": "b3_daily_digest",
        "last_run": datetime.now().isoformat(),
        "status": "success",
        "orders_24h": len(recent),
        "action_items": len(action_items),
    }, indent=2))


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="EdisonHaus Gmail Watcher + Digest")
    parser.add_argument("--mode", choices=["watch", "digest"], required=True,
                        help="watch = urgent email alerts, digest = daily summary")
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
        # Write heartbeat even on failure
        for hb in [WATCHER_HEARTBEAT, DIGEST_HEARTBEAT]:
            try:
                hb.write_text(json.dumps({
                    "module": hb.stem,
                    "last_run": datetime.now().isoformat(),
                    "status": f"error: {e}",
                }, indent=2))
            except Exception:
                pass
        raise
