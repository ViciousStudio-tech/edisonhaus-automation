"""
EdisonHaus Watchdog — Self-monitoring & alerting system
Checks all B2/B3 components, sends alerts, generates dashboard JSON.
Runs every 30 minutes via GitHub Actions.
"""

import os, json, sqlite3, logging, smtplib, requests, re, time, xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from email.mime.text import MIMEText

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SHOPIFY_STORE        = os.environ.get("SHOPIFY_STORE", "fgtyz6-bj.myshopify.com")
SHOPIFY_ACCESS_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
GMAIL_SENDER         = os.environ.get("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD   = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_TO             = os.environ.get("GMAIL_TO", GMAIL_SENDER)
DB_PATH              = os.environ.get("DB_PATH", "data/dropship.db")

SHOPIFY_HEADERS = {
    "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
    "Content-Type": "application/json"
}
SHOPIFY_BASE = f"https://{SHOPIFY_STORE}/admin/api/2024-10"

HEARTBEAT_FILES = {
    "product_finder":  "b3_product_heartbeat.json",
    "store_manager":   "b3_store_heartbeat.json",
    "order_fulfiller": "b3_fulfillment_heartbeat.json",
}

STALE_THRESHOLDS = {
    "product_finder":  timedelta(days=4),
    "store_manager":   timedelta(days=4),
    "order_fulfiller": timedelta(hours=6),
}


def check_heartbeats() -> dict:
    results = {}
    now = datetime.now()
    for name, filepath in HEARTBEAT_FILES.items():
        p = Path(filepath)
        if not p.exists():
            results[name] = {"status": "missing", "last_run": None, "details": "heartbeat file not found"}
            continue
        try:
            data = json.loads(p.read_text())
            last_run = datetime.fromisoformat(data.get("last_run", "2000-01-01"))
            age = now - last_run
            threshold = STALE_THRESHOLDS.get(name, timedelta(days=7))
            status_from_hb = data.get("status", "unknown")
            if "error" in status_from_hb:
                status = "error"
            elif age > threshold:
                status = "stale"
            else:
                status = "ok"
            results[name] = {
                "status": status,
                "last_run": data.get("last_run"),
                "age_hours": round(age.total_seconds() / 3600, 1),
                "details": status_from_hb
            }
        except Exception as e:
            results[name] = {"status": "error", "last_run": None, "details": str(e)}
    return results


def check_shopify() -> dict:
    if not SHOPIFY_ACCESS_TOKEN:
        return {"status": "no_token", "product_count": 0, "order_count": 0}
    try:
        p_resp = requests.get(f"{SHOPIFY_BASE}/products/count.json", headers=SHOPIFY_HEADERS, timeout=10)
        o_resp = requests.get(f"{SHOPIFY_BASE}/orders/count.json?financial_status=paid", headers=SHOPIFY_HEADERS, timeout=10)
        product_count = p_resp.json().get("count", 0) if p_resp.ok else -1
        order_count   = o_resp.json().get("count", 0) if o_resp.ok else -1
        return {"status": "ok", "product_count": product_count, "order_count": order_count}
    except Exception as e:
        return {"status": "error", "product_count": -1, "order_count": -1, "error": str(e)}


def check_db() -> dict:
    try:
        conn = sqlite3.connect(DB_PATH)
        total    = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        pending  = conn.execute("SELECT COUNT(*) FROM products WHERE status='pending'").fetchone()[0]
        listed   = conn.execute("SELECT COUNT(*) FROM products WHERE status='listed'").fetchone()[0]
        conn.close()
        return {"status": "ok", "total": total, "pending": pending, "listed": listed}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def send_alert(subject: str, body: str, urgent: bool = False):
    """Send an alert email.

    urgent=True  → sends immediately regardless of time (pipeline errors,
                   order failures, Shopify down, etc.)
    urgent=False → only sends at 9am EST (14:00 UTC) or 6pm EST (23:00 UTC)
                   to avoid inbox flooding from routine status updates.
    """
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        log.warning("No Gmail credentials — skipping alert email")
        return
    from datetime import timezone
    current_hour_utc = datetime.now(timezone.utc).hour
    scheduled_hours = {14, 23}  # 9am EST, 6pm EST
    if not urgent and current_hour_utc not in scheduled_hours:
        log.info(f"Routine email suppressed — UTC hour {current_hour_utc} not in window {scheduled_hours}")
        return
    if urgent:
        subject = f"🚨 URGENT — {subject}"
    try:
        msg = MIMEText(body)
        msg["Subject"] = f"[EdisonHaus Alert] {subject}"
        msg["From"]    = GMAIL_SENDER
        msg["To"]      = GMAIL_TO
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        log.info(f"Alert sent: {subject}")
    except Exception as e:
        log.error(f"Failed to send alert: {e}")


def write_dashboard(report: dict):
    Path("data").mkdir(exist_ok=True)
    Path("data/dashboard.json").write_text(json.dumps(report, indent=2))
    log.info("Dashboard JSON written to data/dashboard.json")


# ── Theme Drift Check (auto-generated by phase4_pipeline_lockdown) ──
THEME = "Warm Ambient Home Lighting & Decor"

def check_theme_drift() -> dict:
    """Check all live products for theme compliance. Auto-delete drifters."""
    if not SHOPIFY_ACCESS_TOKEN:
        return {"status": "no_token", "checked": 0, "removed": 0}

    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return {"status": "no_anthropic_key", "checked": 0, "removed": 0}
        client = anthropic.Anthropic(api_key=api_key)
    except Exception:
        return {"status": "no_anthropic", "checked": 0, "removed": 0}

    try:
        resp = requests.get(
            f"{SHOPIFY_BASE}/products.json?limit=250",
            headers=SHOPIFY_HEADERS, timeout=15
        )
        products = resp.json().get("products", [])
    except Exception as e:
        return {"status": "error", "error": str(e), "checked": 0, "removed": 0}

    checked = 0
    removed = 0
    removed_list = []

    for product in products:
        title = product.get("title", "")
        body = product.get("body_html", "")
        body_text = re.sub(r"<[^>]+>", " ", body)[:300] if body else ""

        try:
            msg = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=50,
                messages=[{"role": "user", "content": f'Does this product fit a "{THEME}" store? Product: {title}. Description: {body_text[:200]}. Answer YES or NO only.'}]
            )
            answer = msg.content[0].text.strip().upper()
            checked += 1

            if "NO" in answer:
                pid = product["id"]
                del_resp = requests.delete(
                    f"{SHOPIFY_BASE}/products/{pid}.json",
                    headers=SHOPIFY_HEADERS, timeout=10
                )
                if del_resp.status_code in (200, 204):
                    removed += 1
                    removed_list.append({"id": str(pid), "title": title})
                    log.info(f"Theme drift: deleted '{title[:50]}'")

            time.sleep(1)
        except Exception as e:
            log.warning(f"Theme check failed for '{title[:40]}': {e}")

    return {"status": "ok", "checked": checked, "removed": removed, "removed_products": removed_list}


FEED_URL = "https://viciousstudio-tech.github.io/edisonhaus-automation/feeds/google_feed.xml"


def check_feed_health() -> dict:
    """Fetch the Google Merchant feed and check for common issues."""
    try:
        resp = requests.get(FEED_URL, timeout=30)
        if resp.status_code != 200:
            return {"status": "error", "total_items": 0, "ns0_count": 0,
                    "empty_descriptions": 0, "bad_gpc": 0,
                    "error": f"HTTP {resp.status_code}"}

        raw = resp.text
        ns0_count = raw.count("ns0:")

        # Parse XML
        root = ET.fromstring(resp.content)
        # Handle default namespace
        ns = {}
        if root.tag.startswith("{"):
            default_ns = root.tag.split("}")[0] + "}"
            ns["atom"] = default_ns.strip("{}")
        # Find all items (try with and without namespace)
        items = root.findall(".//item")
        if not items:
            # Try with namespace
            for prefix, uri in [("atom", ns.get("atom", "")), ("g", "http://base.google.com/ns/1.0")]:
                items = root.findall(f".//{{{uri}}}item") if uri else []
                if items:
                    break
        # Also try channel/item
        if not items:
            channel = root.find("channel") or root.find(f"{{{ns.get('atom', '')}}}channel")
            if channel is not None:
                items = channel.findall("item")

        total_items = len(items)

        # Check for empty g:description tags
        empty_descriptions = 0
        bad_gpc = 0
        g_ns = "http://base.google.com/ns/1.0"

        for item in items:
            # Check description
            desc = item.find(f"{{{g_ns}}}description")
            if desc is None:
                desc = item.find("g:description", {"g": g_ns})
            if desc is not None and (desc.text is None or desc.text.strip() == ""):
                empty_descriptions += 1

            # Check google_product_category is non-empty (numeric IDs and taxonomy strings both valid)
            gpc = item.find(f"{{{g_ns}}}google_product_category")
            if gpc is None:
                gpc = item.find("g:google_product_category", {"g": g_ns})
            if gpc is not None and (gpc.text is None or gpc.text.strip() == ""):
                bad_gpc += 1

        has_issues = ns0_count > 0 or empty_descriptions > 0 or bad_gpc > 0
        status = "error" if has_issues else "ok"

        return {
            "status": status,
            "total_items": total_items,
            "ns0_count": ns0_count,
            "empty_descriptions": empty_descriptions,
            "bad_gpc": bad_gpc,
        }

    except Exception as e:
        return {"status": "error", "total_items": 0, "ns0_count": 0,
                "empty_descriptions": 0, "bad_gpc": 0, "error": str(e)}


def main():
    log.info("=" * 60)
    log.info("EdisonHaus Watchdog")
    log.info("=" * 60)

    # Theme drift detection
    theme_drift = check_theme_drift()
    log.info(f"Theme drift check: {theme_drift}")

    heartbeats   = check_heartbeats()
    shopify      = check_shopify()
    db           = check_db()
    feed_health  = check_feed_health()
    log.info(f"Feed health: {feed_health}")

    errors = []
    warnings = []

    for name, hb in heartbeats.items():
        if hb["status"] == "error":
            errors.append(f"{name}: {hb['details']}")
        elif hb["status"] in ("stale", "missing"):
            warnings.append(f"{name} is {hb['status']} (last run: {hb.get('last_run', 'never')})")

    if shopify["status"] == "error":
        errors.append(f"Shopify API error: {shopify.get('error')}")

    if theme_drift.get("removed", 0) > 0:
        warnings.append(f"Theme drift: removed {theme_drift['removed']} off-theme products")

    if db["status"] == "error":
        errors.append(f"DB error: {db.get('error')}")
    elif db.get("listed", 0) == 0:
        warnings.append("No products listed on Shopify yet")

    if feed_health["status"] == "error":
        feed_issues = []
        if feed_health.get("error"):
            feed_issues.append(feed_health["error"])
        if feed_health["ns0_count"] > 0:
            feed_issues.append(f"{feed_health['ns0_count']} ns0: namespace prefixes")
        if feed_health["empty_descriptions"] > 0:
            feed_issues.append(f"{feed_health['empty_descriptions']} empty descriptions")
        if feed_health["bad_gpc"] > 0:
            feed_issues.append(f"{feed_health['bad_gpc']} empty google_product_category")
        errors.append(f"Feed health: {'; '.join(feed_issues)}")

    # Build feed health section for emails
    feed_section = f"\n--- Feed Health ---\nTotal products in feed: {feed_health['total_items']}\n"
    if feed_health["status"] == "error":
        if feed_health.get("error"):
            feed_section += f"🚨 Feed fetch error: {feed_health['error']}\n"
        if feed_health["ns0_count"] > 0:
            feed_section += f"🚨 ns0: namespace prefixes found: {feed_health['ns0_count']}\n"
        if feed_health["empty_descriptions"] > 0:
            feed_section += f"🚨 Empty g:description tags: {feed_health['empty_descriptions']}\n"
        if feed_health["bad_gpc"] > 0:
            feed_section += f"🚨 Empty google_product_category: {feed_health['bad_gpc']}\n"
    else:
        feed_section += "All checks passed.\n"

    # Send alerts
    if errors:
        send_alert(
            "ERRORS DETECTED",
            "The following errors were detected:\n\n" + "\n".join(f"- {e}" for e in errors) + "\n" + feed_section,
            urgent=True
        )

    if warnings and not errors:
        # Only send warning email if no hard errors (avoid double emails)
        send_alert(
            "Warnings",
            "The following warnings were detected:\n\n" + "\n".join(f"- {w}" for w in warnings) + "\n" + feed_section
        )

    overall = "error" if errors else ("warning" if warnings else "ok")

    report = {
        "generated_at":  datetime.now().isoformat(),
        "overall_status": overall,
        "errors":   errors,
        "warnings": warnings,
        "modules": {
            "heartbeats": heartbeats,
            "shopify":    shopify,
            "database":   db,
            "theme_drift": theme_drift,
            "feed_health": feed_health,
        }
    }

    write_dashboard(report)
    log.info(f"Watchdog complete. Status: {overall}")
    if errors:
        log.error(f"Errors: {errors}")
    if warnings:
        log.warning(f"Warnings: {warnings}")


if __name__ == "__main__":
    main()
