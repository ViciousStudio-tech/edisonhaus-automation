"""
Business 3 — Gmail Watcher + Daily Digest (Service Account)
Two modes:
  --mode=watch   Scans home@edisonhaus.com for urgent emails (every 30 min)
  --mode=digest  Sends combined inbox + Shopify + CJ daily digest (daily 8am EST)

Auth: Google Service Account with domain-wide delegation impersonating
home@edisonhaus.com. No OAuth flow, no app password, no 2FA needed.
Env: GMAIL_SERVICE_ACCOUNT_JSON (full JSON key), GMAIL_TO, SHOPIFY_ACCESS_TOKEN, CJ_API_KEY
"""

import os, sys, json, time, re, logging, argparse, requests, builtins, base64
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from pathlib import Path

def _no_input(*a, **k): raise RuntimeError("BLOCKED: interactive prompt")
builtins.input = _no_input

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Env ───────────────────────────────────────────────────────────────────────
GMAIL_SA_JSON  = os.environ.get("GMAIL_SERVICE_ACCOUNT_JSON", "")
GMAIL_USER     = "home@edisonhaus.com"
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

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


# ══════════════════════════════════════════════════════════════════════════════
# Gmail Service Account
# ══════════════════════════════════════════════════════════════════════════════
_gmail_service = None

def get_gmail_service():
    """Build Gmail API service using service account with domain-wide delegation."""
    global _gmail_service
    if _gmail_service:
        return _gmail_service
    if not GMAIL_SA_JSON:
        log.error("GMAIL_SERVICE_ACCOUNT_JSON not set")
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        sa_info = json.loads(GMAIL_SA_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            sa_info, scopes=SCOPES
        )
        delegated = credentials.with_subject(GMAIL_USER)
        _gmail_service = build("gmail", "v1", credentials=delegated,
                               cache_discovery=False)
        log.info(f"Gmail API connected (impersonating {GMAIL_USER})")
        return _gmail_service
    except Exception as e:
        log.error(f"Gmail service account init failed: {e}")
        return None


def gmail_list_messages(query: str, max_results: int = 100) -> list[dict]:
    """List messages matching a Gmail search query."""
    svc = get_gmail_service()
    if not svc:
        return []
    try:
        resp = svc.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
        return resp.get("messages", [])
    except Exception as e:
        log.warning(f"Gmail list error: {e}")
        return []


def gmail_get_message(msg_id: str, fmt: str = "metadata") -> dict | None:
    """Fetch a single message. fmt: metadata, full, raw."""
    svc = get_gmail_service()
    if not svc:
        return None
    try:
        return svc.users().messages().get(
            userId="me", id=msg_id, format=fmt
        ).execute()
    except Exception as e:
        log.warning(f"Gmail get message {msg_id}: {e}")
        return None


def gmail_get_header(msg: dict, name: str) -> str:
    """Extract a header value from message metadata."""
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def gmail_get_body(msg_id: str) -> str:
    """Fetch plain-text body (first 500 chars)."""
    msg = gmail_get_message(msg_id, fmt="full")
    if not msg:
        return "(no body)"
    payload = msg.get("payload", {})
    parts = [payload] + (payload.get("parts") or [])
    for part in parts:
        if part.get("mimeType") in ("text/plain", "text/html"):
            b64 = part.get("body", {}).get("data", "")
            if b64:
                text = base64.urlsafe_b64decode(b64).decode("utf-8", errors="replace")
                if part["mimeType"] == "text/html":
                    text = re.sub(r"<[^>]+>", " ", text)
                    text = re.sub(r"\s+", " ", text).strip()
                return text[:500]
    return "(no body)"


def gmail_send(to: str, subject: str, body: str) -> bool:
    """Send an email via Gmail API."""
    svc = get_gmail_service()
    if not svc:
        log.warning("Gmail service unavailable — cannot send email")
        log.info(f"  Would send to {to}: {subject}")
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["To"] = to
        msg["From"] = f"EdisonHaus <{GMAIL_USER}>"
        msg["Subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        svc.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        log.info(f"Email sent to {to}")
        return True
    except Exception as e:
        log.error(f"Gmail send failed: {e}")
        return False


def gmail_mark_read(msg_id: str):
    """Remove UNREAD label from a message."""
    svc = get_gmail_service()
    if not svc:
        return
    try:
        svc.users().messages().modify(
            userId="me", id=msg_id,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
    except Exception as e:
        log.warning(f"Could not mark {msg_id} as read: {e}")


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
    if is_urgent(subject, body):
        return "urgent"
    text = (subject + " " + body).lower()
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

    since = NOW - timedelta(minutes=35)
    epoch = int(since.timestamp())
    query = f"is:unread after:{epoch}"

    stubs = gmail_list_messages(query, max_results=50)
    log.info(f"Found {len(stubs)} unread email(s) in last 35 min")

    alerted = 0
    for stub in stubs:
        msg = gmail_get_message(stub["id"], fmt="metadata")
        if not msg:
            continue

        subject = gmail_get_header(msg, "Subject")
        sender  = gmail_get_header(msg, "From")
        date    = gmail_get_header(msg, "Date")
        body    = gmail_get_body(stub["id"])

        if not is_urgent(subject, body):
            continue

        log.info(f"  URGENT: {subject[:60]} — {sender[:40]}")

        alert_body = (
            f"Urgent email received at {GMAIL_USER}\n\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n"
            f"Date: {date}\n\n"
            f"Preview:\n{body}\n\n"
            f"Reply at: https://mail.google.com/mail/u/?authuser={GMAIL_USER}"
        )
        alert_subject = f"\U0001f6a8 EdisonHaus URGENT: {subject[:80]} \u2014 {sender[:40]}"

        gmail_send(GMAIL_TO, alert_subject, alert_body)
        gmail_mark_read(stub["id"])
        alerted += 1

    log.info(f"Alerted on {alerted} urgent email(s)")
    WATCHER_HEARTBEAT.write_text(json.dumps({
        "module": "b3_gmail_watcher", "last_run": datetime.now().isoformat(),
        "status": "success", "emails_checked": len(stubs), "urgent_alerted": alerted,
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

    # ── Gmail inbox stats ─────────────────────────────────────────────────────
    since_24h = NOW - timedelta(hours=24)
    epoch_24h = int(since_24h.timestamp())

    all_stubs    = gmail_list_messages(f"after:{epoch_24h}", max_results=200)
    unread_stubs = gmail_list_messages(f"is:unread after:{epoch_24h}", max_results=200)
    total_inbox  = len(all_stubs)
    total_unread = len(unread_stubs)

    categories = {"urgent": [], "question": [], "order": [], "other": []}
    for stub in all_stubs:
        msg = gmail_get_message(stub["id"], fmt="metadata")
        if not msg:
            continue
        subject = gmail_get_header(msg, "Subject")
        sender  = gmail_get_header(msg, "From")
        body    = gmail_get_body(stub["id"])
        cat     = categorize_email(subject, body)
        categories[cat].append({"subject": subject, "from": sender})

    log.info(f"  Inbox: {total_inbox} total, {total_unread} unread")

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

    # ── Build digest ──────────────────────────────────────────────────────────
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
    # ── Product health ─────────────────────────────────────────────────────
    health_hb = Path("data/product_health_heartbeat.json")
    if health_hb.exists():
        try:
            hdata = json.loads(health_hb.read_text())
            h_total = hdata.get("total_checked", 0)
            h_removed = hdata.get("removed", 0)
            h_updated = hdata.get("price_updated", 0)
            lines.append(f"\U0001f50d PRODUCT HEALTH: {h_total} checked | {h_removed} removed | {h_updated} price updated")
            if h_removed > 0:
                titles = hdata.get("removed_titles", [])
                lines.append(f"\U0001f6a8 REMOVED PRODUCTS:")
                for t in titles[:10]:
                    lines.append(f"   \u2022 {t[:60]}")
                action_items.append(f"{h_removed} products removed by health check")
            if h_updated > 0:
                titles = hdata.get("price_updated_titles", [])
                lines.append(f"\U0001f4b2 PRICE UPDATES:")
                for t in titles[:10]:
                    lines.append(f"   \u2022 {t[:80]}")
        except Exception as e:
            lines.append(f"\U0001f50d PRODUCT HEALTH: error reading heartbeat ({e})")
    else:
        lines.append("\U0001f50d PRODUCT HEALTH: no data yet")
    lines.append("")

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
        f"Manage: {GMAIL_USER}",
        "Shopify: https://admin.shopify.com/store/fgtyz6-bj",
        "\u2501" * 28,
    ])

    body = "\n".join(lines)
    tag = f"\u26a0\ufe0f {len(action_items)} items need attention" if action_items else "\u2705 All clear"
    subject = f"EdisonHaus Daily \u2014 {NOW.strftime('%b %d')} | {len(recent)} orders | {tag}"

    log.info(f"\nSubject: {subject}\n")
    log.info(body)

    gmail_send(GMAIL_TO, subject, body)

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
