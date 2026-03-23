"""
Business 3 — Daily Support Digest
Pulls Shopify orders, CJ fulfillment status, and revenue metrics.
Sends a plain-text digest email via Gmail SMTP.
Runs daily at 8 AM EST via GitHub Actions.
"""

import os, json, time, logging, requests, smtplib, builtins
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.mime.text import MIMEText

def _no_input(*a, **k): raise RuntimeError("BLOCKED: interactive prompt")
builtins.input = _no_input

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Env ───────────────────────────────────────────────────────────────────────
SHOPIFY_STORE  = os.environ.get("SHOPIFY_STORE", "fgtyz6-bj.myshopify.com")
SHOPIFY_TOKEN  = os.environ["SHOPIFY_ACCESS_TOKEN"]
CJ_API_KEY     = os.environ.get("CJ_API_KEY", "")
GMAIL_SENDER   = os.environ.get("GMAIL_SENDER", "")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_TO       = os.environ.get("GMAIL_TO", GMAIL_SENDER)

Path("data").mkdir(exist_ok=True)
HEARTBEAT = Path("data/support_digest_heartbeat.json")

SHOPIFY_BASE    = f"https://{SHOPIFY_STORE}/admin/api/2024-01"
SHOPIFY_HEADERS = {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}

CJ_BASE = "https://developers.cjdropshipping.com/api2.0/v1"

NOW = datetime.now(timezone.utc)

# ── Helpers ───────────────────────────────────────────────────────────────────
def shopify_get(endpoint: str, params: dict = None) -> dict | list | None:
    """GET from Shopify Admin API with basic retry."""
    url = f"{SHOPIFY_BASE}/{endpoint}"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=SHOPIFY_HEADERS, params=params, timeout=15)
            if r.status_code == 429:
                retry = int(r.headers.get("Retry-After", 2))
                log.warning(f"Shopify rate limit, sleeping {retry}s")
                time.sleep(retry)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.warning(f"Shopify GET {endpoint} attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return None


def shopify_put(endpoint: str, payload: dict) -> dict | None:
    """PUT to Shopify Admin API."""
    url = f"{SHOPIFY_BASE}/{endpoint}"
    try:
        r = requests.put(url, headers=SHOPIFY_HEADERS, json=payload, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"Shopify PUT {endpoint}: {e}")
    return None


def cj_get_token() -> str | None:
    """Authenticate with CJ and return access token."""
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
    """Fetch CJ orders by status from last 48hrs."""
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


# ── Data collection ───────────────────────────────────────────────────────────
def fetch_recent_orders(hours: int = 24) -> list:
    """Fetch orders placed in the last N hours."""
    since = (NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    data = shopify_get("orders.json", {
        "created_at_min": since, "status": "any", "limit": 250
    })
    return data.get("orders", []) if data else []


def fetch_unfulfilled_old(hours: int = 48) -> list:
    """Orders with no fulfillment older than N hours."""
    before = (NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    data = shopify_get("orders.json", {
        "fulfillment_status": "unfulfilled",
        "created_at_max": before,
        "financial_status": "paid",
        "status": "open",
        "limit": 50
    })
    return data.get("orders", []) if data else []


def fetch_refunds(days: int = 7) -> list:
    """Orders refunded (fully or partially) in last N days."""
    since = (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    results = []
    for fs in ("refunded", "partially_refunded"):
        data = shopify_get("orders.json", {
            "financial_status": fs, "updated_at_min": since,
            "status": "any", "limit": 50
        })
        if data:
            results.extend(data.get("orders", []))
    return results


def fetch_revenue(since_str: str, until_str: str) -> float:
    """Sum total_price for paid orders between two ISO timestamps."""
    data = shopify_get("orders.json", {
        "created_at_min": since_str, "created_at_max": until_str,
        "financial_status": "paid", "status": "any", "limit": 250
    })
    orders = data.get("orders", []) if data else []
    return sum(float(o.get("total_price", 0)) for o in orders)


def fetch_out_of_stock() -> list:
    """Products with zero inventory."""
    products = []
    data = shopify_get("products.json", {"limit": 250, "status": "active"})
    if not data:
        return []
    for p in data.get("products", []):
        for v in p.get("variants", []):
            inv = v.get("inventory_quantity")
            # inventory_management=None means unlimited — skip those
            if v.get("inventory_management") and inv is not None and inv <= 0:
                products.append(p["title"])
                break
    return products


def fetch_cj_problem_orders() -> list:
    """CJ orders with FAILED or ABNORMAL status in last 48hrs."""
    token = cj_get_token()
    if not token:
        return []
    problems = []
    for status in ("FAILED", "ABNORMAL"):
        problems.extend(cj_get_orders(token, status))
        time.sleep(1)
    return problems


# ── Email composition ─────────────────────────────────────────────────────────
def compose_digest() -> tuple[str, str]:
    """Build digest subject and body. Returns (subject, body)."""
    log.info("Fetching Shopify orders (24hr)...")
    recent = fetch_recent_orders(24)
    log.info(f"  {len(recent)} orders in last 24hrs")

    log.info("Fetching unfulfilled orders (48hr+)...")
    unfulfilled = fetch_unfulfilled_old(48)
    log.info(f"  {len(unfulfilled)} unfulfilled 48hr+ orders")

    log.info("Fetching refunds (7d)...")
    refunds = fetch_refunds(7)
    log.info(f"  {len(refunds)} refunds")

    log.info("Calculating revenue...")
    now_str = NOW.strftime("%Y-%m-%dT%H:%M:%S-00:00")
    d7_str  = (NOW - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    d14_str = (NOW - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    rev_7d = fetch_revenue(d7_str, now_str)
    rev_prior_7d = fetch_revenue(d14_str, d7_str)
    log.info(f"  7d: ${rev_7d:.2f} | prior 7d: ${rev_prior_7d:.2f}")

    log.info("Checking out-of-stock...")
    oos = fetch_out_of_stock()
    log.info(f"  {len(oos)} out-of-stock products")

    log.info("Checking CJ problem orders...")
    cj_problems = fetch_cj_problem_orders()
    log.info(f"  {len(cj_problems)} CJ problem orders")

    # ── Build body ────────────────────────────────────────────────────────────
    date_str = NOW.strftime("%A, %B %d %Y")
    lines = [
        f"── EdisonHaus Daily Digest ──────────────────",
        f"Date: {date_str}",
        "",
    ]

    # Orders section
    lines.append("📦 ORDERS (last 24hrs)")
    if recent:
        for o in recent:
            num = o.get("name", o.get("order_number", "?"))
            total = o.get("total_price", "0.00")
            fs = o.get("financial_status", "?")
            ff = o.get("fulfillment_status") or "unfulfilled"
            lines.append(f"  {num}  ${total}  {fs}/{ff}")
        lines.append(f"  Total: {len(recent)} order(s)")
    else:
        lines.append("  No new orders")
    lines.append("")

    # Needs action section
    action_items = []
    if unfulfilled:
        action_items.append(f"  🔶 {len(unfulfilled)} unfulfilled order(s) older than 48hrs:")
        for o in unfulfilled:
            num = o.get("name", "?")
            created = o.get("created_at", "?")[:10]
            action_items.append(f"     {num} (placed {created})")
    if cj_problems:
        action_items.append(f"  🔶 {len(cj_problems)} CJ order(s) FAILED/ABNORMAL:")
        for o in cj_problems:
            oid = o.get("orderId", o.get("orderNum", "?"))
            status = o.get("orderStatus", "?")
            action_items.append(f"     CJ order {oid} — {status}")
    if refunds:
        action_items.append(f"  🔶 {len(refunds)} refund(s) in last 7 days:")
        for o in refunds:
            num = o.get("name", "?")
            total = o.get("total_price", "0.00")
            action_items.append(f"     {num}  ${total}")

    lines.append("⚠️  NEEDS ACTION")
    if action_items:
        lines.extend(action_items)
    else:
        lines.append("  All clear — nothing needs attention")
    lines.append("")

    # Revenue section
    if rev_prior_7d > 0:
        pct = ((rev_7d - rev_prior_7d) / rev_prior_7d) * 100
        change_str = f"+{pct:.1f}%" if pct >= 0 else f"{pct:.1f}%"
    elif rev_7d > 0:
        change_str = "+∞% (no prior revenue)"
    else:
        change_str = "—"
    lines.append("📊 REVENUE")
    lines.append(f"  Last 7 days: ${rev_7d:.2f} | Prior 7 days: ${rev_prior_7d:.2f} | Change: {change_str}")
    lines.append("")

    # Out of stock section
    lines.append("🔴 OUT OF STOCK")
    if oos:
        for name in oos[:20]:
            lines.append(f"  • {name}")
        if len(oos) > 20:
            lines.append(f"  ... and {len(oos) - 20} more")
    else:
        lines.append("  None")
    lines.append("")
    lines.append("────────────────────────────────────────────")
    lines.append("Sent by EdisonHaus Automation • github.com/ViciousStudio-tech/edisonhaus-automation")

    body = "\n".join(lines)

    # Subject
    action_tag = "action needed" if (unfulfilled or cj_problems or refunds) else "all clear"
    subject = f"EdisonHaus Daily Digest — {NOW.strftime('%b %d')} | {len(recent)} order(s) | {action_tag}"

    return subject, body


# ── Email sending ─────────────────────────────────────────────────────────────
def send_email(subject: str, body: str):
    """Send plain-text email via Gmail SMTP."""
    if not GMAIL_SENDER or not GMAIL_APP_PASS:
        log.warning("GMAIL_SENDER or GMAIL_APP_PASSWORD not set — skipping email")
        log.info(f"Subject: {subject}")
        log.info(f"Body:\n{body}")
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = f"EdisonHaus Support <{GMAIL_SENDER}>"
    msg["To"] = GMAIL_TO

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(GMAIL_SENDER, GMAIL_APP_PASS)
            smtp.sendmail(GMAIL_SENDER, [GMAIL_TO], msg.as_string())
        log.info(f"Digest email sent to {GMAIL_TO}")
        return True
    except Exception as e:
        log.error(f"Email send failed: {e}")
        return False


# ── Shopify email config (one-time setup) ────────────────────────────────────
def update_shopify_customer_email():
    """Set Shopify customer-facing email to support@edisonhaus.com."""
    result = shopify_put("shop.json", {
        "shop": {"customer_email": "support@edisonhaus.com"}
    })
    if result:
        email = result.get("shop", {}).get("customer_email", "?")
        log.info(f"Shopify customer_email set to: {email}")
    else:
        log.warning("Could not update Shopify customer_email")
    return result


def check_notification_templates():
    """List Shopify notification templates."""
    data = shopify_get("notifications.json")
    if not data:
        log.warning("Could not fetch notification templates")
        return []
    templates = data.get("notifications", [])
    log.info(f"Found {len(templates)} notification templates")
    for t in templates:
        log.info(f"  • {t.get('name', '?')} (subject: {t.get('subject', '?')[:50]})")
    return templates


# ── Heartbeat ─────────────────────────────────────────────────────────────────
def write_heartbeat(status: str, orders_24h: int = 0, action_needed: bool = False):
    HEARTBEAT.write_text(json.dumps({
        "module": "b3_support_digest",
        "last_run": datetime.now().isoformat(),
        "status": status,
        "orders_24h": orders_24h,
        "action_needed": action_needed,
    }, indent=2))


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("B3 Support Digest — EdisonHaus")
    log.info("=" * 60)

    # One-time Shopify email config — safe to re-run (idempotent)
    update_shopify_customer_email()
    check_notification_templates()

    # Build and send digest
    subject, body = compose_digest()
    log.info(f"\nSubject: {subject}\n")
    log.info(body)

    sent = send_email(subject, body)
    status = "success" if sent else "success_no_email"
    write_heartbeat(status)
    log.info("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"Support digest failed: {e}")
        write_heartbeat(f"error: {e}")
        raise
