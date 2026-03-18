#!/usr/bin/env python3
"""EdisonHaus Product Pipeline — CJ keyword search → Shopify listing with metafields."""

import builtins
def _no_input(*a, **k): raise RuntimeError("BLOCKED")
builtins.input = _no_input

import os, sys, json, time, sqlite3, logging, subprocess, traceback, re
from datetime import datetime, timezone
from pathlib import Path
import requests

# ── Config ────────────────────────────────────────────────────────────────
SHOPIFY_STORE = "fgtyz6-bj.myshopify.com"
SHOPIFY_BASE  = f"https://{SHOPIFY_STORE}/admin/api/2024-01"
CJ_BASE       = "https://developers.cjdropshipping.com/api2.0/v1"
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
CJ_EMAIL      = os.environ.get("CJ_EMAIL", "")
CJ_API_KEY    = os.environ.get("CJ_API_KEY", "")
DB_PATH       = Path("data/dropship.db")
HB_PATH       = Path("data/product_pipeline_heartbeat.json")
REPORTS       = Path("reports")

REPORTS.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── Collections & Keywords ────────────────────────────────────────────────
COLLECTIONS = [
    {"handle": "led-ambient-lighting",   "title": "LED & Ambient Lighting",
     "keywords": ["LED strip light","fairy lights","string lights","neon light","galaxy projector","sunset lamp"]},
    {"handle": "table-desk-lamps",       "title": "Table & Desk Lamps",
     "keywords": ["table lamp","desk lamp","bedside lamp","reading lamp"]},
    {"handle": "pendant-ceiling-lights",  "title": "Pendant & Ceiling Lights",
     "keywords": ["pendant light","chandelier","ceiling light","hanging lamp"]},
    {"handle": "wall-decor",             "title": "Wall Decor",
     "keywords": ["canvas wall art","wall painting","tapestry","decorative painting"]},
    {"handle": "cozy-textiles",          "title": "Cozy Textiles",
     "keywords": ["throw pillow cover","cushion cover"]},
    {"handle": "storage-accents",        "title": "Storage & Accents",
     "keywords": ["woven basket","rattan basket","candle holder","decorative vase"]},
]

# ── Logging ───────────────────────────────────────────────────────────────
_ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(REPORTS / f"pipeline_{_ts}.log"), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("pipeline")

# ── Stats ─────────────────────────────────────────────────────────────────
S = {"fetched":0,"created":0,"updated":0,"skipped":0,"errors":[]}

# ── Helpers ───────────────────────────────────────────────────────────────
def shop_h():
    return {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}

def cj_h(tok):
    return {"CJ-Access-Token": tok, "Content-Type": "application/json"}

def _req(method, url, retries=3, **kw):
    for i in range(retries):
        try:
            r = requests.request(method, url, timeout=(10,30), **kw)
            if r.status_code == 429:
                body = {}
                try: body = r.json()
                except: pass
                msg = body.get("message","")
                if "daily" in msg.lower():
                    log.error(f"CJ DAILY LIMIT HIT — stopping CJ calls")
                    S["_cj_exhausted"] = True
                    return None
                w = 10*(i+1)
                log.warning(f"429 on {url}, sleep {w}s (attempt {i+1})")
                time.sleep(w)
                continue
            return r
        except requests.RequestException as e:
            if i == retries-1: raise
            log.warning(f"Req error ({e}), retry in {5*(i+1)}s")
            time.sleep(5*(i+1))
    return None

def heartbeat(phase, status="running"):
    HB_PATH.write_text(json.dumps({
        "module":"b3_product_pipeline","last_run":datetime.now(timezone.utc).isoformat(),
        "phase":phase,"products_fetched":S["fetched"],"products_created":S["created"],
        "products_updated":S["updated"],"products_skipped":S["skipped"],
        "status":status,"errors":S["errors"][-30:]
    }, indent=2))

def calc_price(cost):
    if cost <= 0: return None
    if   cost < 5:  sell = cost * 2.5
    elif cost < 15: sell = cost * 2.2
    elif cost < 30: sell = cost * 2.0
    else:           sell = cost * 1.8
    sell = max(sell, 14.99)
    sell = float(int(sell)) + 0.99
    sell = max(sell, 14.99)
    margin = (sell - cost) / sell
    if margin < 0.35: return None
    return (sell, margin)

# ── Database ──────────────────────────────────────────────────────────────
def init_db():
    db = sqlite3.connect(str(DB_PATH))
    db.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cj_id TEXT UNIQUE, cj_vid TEXT, title TEXT, shopify_id TEXT,
        cost_usd REAL, sell_price REAL, profit_margin REAL,
        shopify_collection_id INTEGER, image_url TEXT,
        status TEXT DEFAULT 'listed',
        last_synced TEXT DEFAULT (datetime('now')),
        created_at TEXT DEFAULT (datetime('now')))""")
    db.commit()
    return db

# ── Step 1: CJ Auth ──────────────────────────────────────────────────────
def step1_auth():
    log.info("── STEP 1: CJ Auth ──")
    for attempt in range(2):
        r = _req("POST", f"{CJ_BASE}/authentication/getAccessToken",
                 json={"email":CJ_EMAIL,"password":CJ_API_KEY})
        if r and r.status_code == 200:
            d = r.json()
            tok = d.get("data",{}).get("accessToken") if isinstance(d.get("data"),dict) else d.get("data")
            if tok:
                log.info(f"Auth OK (token starts {str(tok)[:20]}...)")
                heartbeat("auth_done")
                return tok
        log.error(f"Auth failed: {r.status_code if r else 'None'}")
        if attempt == 0:
            log.info("Sleeping 310s for auth rate limit...")
            time.sleep(310)
    log.error("Auth failed after retry. Aborting.")
    sys.exit(1)

# ── Step 2: Search CJ by keyword ─────────────────────────────────────────
def step2_search(tok):
    log.info("── STEP 2: Keyword Search ──")
    MAX_PAGES = 2  # CJ returns thousands; 2 pages (100) per keyword is plenty
    results = {}  # handle → [{search_item},...]
    seen = set()
    for coll in COLLECTIONS:
        h = coll["handle"]
        results[h] = []
        for kw in coll["keywords"]:
            page = 1
            while page <= MAX_PAGES:
                time.sleep(1.5)
                r = _req("GET", f"{CJ_BASE}/product/list", headers=cj_h(tok),
                         params={"productNameEn":kw,"pageNum":page,"pageSize":50})
                if not r or r.status_code != 200:
                    log.warning(f"  Search fail: '{kw}' p{page}")
                    break
                d = r.json()
                items = d.get("data",{}).get("list",[]) if isinstance(d.get("data"),dict) else (d.get("data") or [])
                if not items: break
                for p in items:
                    pid = p.get("pid") or p.get("productId")
                    name = (p.get("productNameEn") or "").lower()
                    # Filter: product name must contain the full keyword phrase
                    if kw.lower() not in name: continue
                    if pid and pid not in seen:
                        seen.add(pid)
                        results[h].append(p)  # keep full search data
                log.info(f"  '{kw}' p{page}: {len(items)} results, {len(results[h])} uniq for {h}")
                if len(items) < 50: break
                page += 1
    total = sum(len(v) for v in results.values())
    log.info(f"Search done: {total} unique products")
    for h,pids in results.items(): log.info(f"  {h}: {len(pids)}")
    heartbeat("search_done")
    return results

# ── Step 3: Fetch product details (with search-data fallback) ─────────────
def step3_details(tok, search_map):
    """Fetch full details via /product/query. If daily limit hit, fall back to search data."""
    log.info("── STEP 3: Fetch Details ──")
    details = {}  # handle → [product,...]
    total = sum(len(v) for v in search_map.values())

    for h, items in search_map.items():
        details[h] = []
        for item in items:
            pid = str(item.get("pid") or item.get("productId",""))
            if not pid: continue

            # If daily limit hit, use search data directly
            if S.get("_cj_exhausted"):
                name = item.get("productNameEn","")
                if not name: continue
                # Parse sellPrice from search (may be "6.10 -- 9.09" or "1.67")
                sp = str(item.get("sellPrice","0"))
                cost_str = sp.split("--")[0].strip() if "--" in sp else sp
                try: cost_val = float(cost_str)
                except: continue
                if cost_val <= 0: continue
                # Build a minimal product from search data
                fallback = {
                    "pid": pid,
                    "productNameEn": name,
                    "productImage": item.get("productImage",""),
                    "productWeight": item.get("productWeight", 0),
                    "productSkuEn": item.get("productSku","CJ"),
                    "productDescription": item.get("remark",""),
                    "variants": [{"vid": pid, "variantSellPrice": str(cost_val), "variantNameEn": "Default"}],
                    "_from_search": True,
                }
                details[h].append(fallback)
                S["fetched"] += 1
                continue

            time.sleep(1.5)
            try:
                r = _req("GET", f"{CJ_BASE}/product/query", headers=cj_h(tok), params={"pid":pid})
                if not r or r.status_code != 200:
                    S["errors"].append(f"detail_fail:{pid}")
                    continue
                prod = r.json().get("data")
                if not prod: continue
                name = prod.get("productNameEn","")
                if not name: continue
                details[h].append(prod)
                S["fetched"] += 1
                if S["fetched"] % 50 == 0: log.info(f"  {S['fetched']}/{total} details fetched")
            except Exception as e:
                S["errors"].append(f"detail_err:{pid}:{str(e)[:60]}")

    from_search = sum(1 for h in details for p in details[h] if p.get("_from_search"))
    log.info(f"Details done: {S['fetched']} total ({S['fetched']-from_search} from API, {from_search} from search fallback)")
    heartbeat("details_done")
    return details

# ── Step 7 helper: ensure collection ──────────────────────────────────────
def ensure_coll(handle, title):
    r = _req("GET", f"{SHOPIFY_BASE}/custom_collections.json?handle={handle}", headers=shop_h())
    if r and r.status_code == 200:
        cc = r.json().get("custom_collections",[])
        if cc: return cc[0]["id"]
    r2 = _req("POST", f"{SHOPIFY_BASE}/custom_collections.json", headers=shop_h(),
              json={"custom_collection":{"title":title,"handle":handle,"published":True}})
    if r2 and r2.status_code in (200,201):
        cid = r2.json()["custom_collection"]["id"]
        log.info(f"  Created collection {handle} ({cid})")
        return cid
    log.error(f"  Collection {handle} failed")
    return None

# ── Steps 4-8: Price, Create/Update, Metafields, Collection, DB ──────────
def steps4_8(detail_map, db):
    log.info("── STEPS 4-8: Process Products ──")
    cur = db.cursor()
    coll_ids = {}
    for c in COLLECTIONS:
        coll_ids[c["handle"]] = ensure_coll(c["handle"], c["title"])
        time.sleep(0.3)

    for coll in COLLECTIONS:
        handle = coll["handle"]
        coll_title = coll["title"]
        coll_id = coll_ids.get(handle)
        products = detail_map.get(handle, [])
        log.info(f"Processing {len(products)} for {coll_title}...")

        for prod in products:
            pid = str(prod.get("pid") or prod.get("productId",""))
            title = prod.get("productNameEn","")
            try:
                variants = prod.get("variants",[])
                if not variants:
                    S["skipped"] += 1; continue

                # cheapest variant
                cheapest = min(variants, key=lambda v: float(v.get("variantSellPrice",999999)))
                cost = float(cheapest.get("variantSellPrice",0))
                vid = str(cheapest.get("vid",""))

                pricing = calc_price(cost)
                if not pricing:
                    S["skipped"] += 1; continue
                sell, margin = pricing

                # check DB
                cur.execute("SELECT shopify_id, cost_usd FROM products WHERE cj_id=? AND shopify_id IS NOT NULL",(pid,))
                row = cur.fetchone()
                existing_sid = row[0] if row else None

                if existing_sid:
                    # UPDATE if cost changed
                    old_cost = row[1] if row else None
                    if old_cost and abs(float(old_cost)-cost) < 0.01:
                        continue  # unchanged
                    vpay = []
                    for v in variants:
                        vc = float(v.get("variantSellPrice",0))
                        vp = calc_price(vc)
                        if not vp: continue
                        vpay.append({"price":str(vp[0]),"sku":f"{prod.get('productSkuEn','CJ')}-{v.get('vid','')}",
                            "option1":v.get("variantNameEn","Default"),"weight":prod.get("productWeight",0),
                            "weight_unit":"g","inventory_management":None,"fulfillment_service":"manual",
                            "requires_shipping":True,"taxable":True})
                    r = _req("PUT",f"{SHOPIFY_BASE}/products/{existing_sid}.json",headers=shop_h(),
                             json={"product":{"id":int(existing_sid),"variants":vpay}})
                    if r and r.status_code == 200:
                        S["updated"] += 1
                        log.info(f"  Updated {existing_sid}")
                    shopify_id = existing_sid
                    time.sleep(0.5)
                else:
                    # CREATE
                    imgs = []
                    pi = prod.get("productImage","")
                    if pi: imgs.append({"src":pi})
                    for iu in (prod.get("productImageSet") or prod.get("productImages") or []):
                        if isinstance(iu,str) and iu and iu != pi: imgs.append({"src":iu})
                        elif isinstance(iu,dict):
                            u = iu.get("imageUrl",iu.get("url",""))
                            if u and u != pi: imgs.append({"src":u})

                    vpay = []; opts = []
                    for v in variants:
                        vc = float(v.get("variantSellPrice",0))
                        vp = calc_price(vc)
                        if not vp: continue
                        vn = v.get("variantNameEn","Default")
                        vpay.append({"price":str(vp[0]),"sku":f"{prod.get('productSkuEn','CJ')}-{v.get('vid','')}",
                            "option1":vn,"weight":prod.get("productWeight",0),"weight_unit":"g",
                            "inventory_management":None,"fulfillment_service":"manual",
                            "requires_shipping":True,"taxable":True})
                        if vn not in opts: opts.append(vn)
                    if not vpay:
                        S["skipped"] += 1; continue

                    body_html = prod.get("productDescription") or prod.get("description","")
                    payload = {"product":{
                        "title":title,
                        "body_html":body_html,
                        "vendor":"EdisonHaus",
                        "product_type":coll_title,
                        "tags":f"EdisonHaus, {coll_title}",
                        "status":"active",
                        "images":imgs[:10],
                        "variants":vpay,
                        "options":[{"name":"Option","values":opts}],
                    }}
                    r = _req("POST",f"{SHOPIFY_BASE}/products.json",headers=shop_h(),json=payload)
                    if not r or r.status_code not in (200,201):
                        msg = r.text[:200] if r else "none"
                        log.warning(f"  Create fail {pid}: {msg}")
                        S["errors"].append(f"create_fail:{pid}")
                        time.sleep(0.5); continue

                    created = r.json()["product"]
                    shopify_id = str(created["id"])
                    S["created"] += 1
                    log.info(f"  Created {shopify_id} — {title[:50]}")

                    # STEP 6: Metafields
                    for mf in [
                        {"namespace":"dropship","key":"cj_product_id","value":pid,"type":"single_line_text_field"},
                        {"namespace":"dropship","key":"cj_variant_id","value":vid,"type":"single_line_text_field"},
                        {"namespace":"dropship","key":"cj_cost_price","value":str(cost),"type":"single_line_text_field"},
                        {"namespace":"dropship","key":"supplier","value":"CJDropshipping","type":"single_line_text_field"},
                    ]:
                        mr = _req("POST",f"{SHOPIFY_BASE}/products/{shopify_id}/metafields.json",
                                  headers=shop_h(),json={"metafield":mf})
                        if not mr or mr.status_code not in (200,201):
                            log.warning(f"    Metafield {mf['key']} fail")
                        time.sleep(0.2)
                    time.sleep(0.5)

                # STEP 7: Collection assign
                if coll_id:
                    cr = _req("POST",f"{SHOPIFY_BASE}/collects.json",headers=shop_h(),
                              json={"collect":{"product_id":int(shopify_id),"collection_id":int(coll_id)}})
                    if cr and cr.status_code in (200,201,422):
                        log.info(f"    → {handle}")
                    time.sleep(0.3)

                # STEP 8: DB
                cur.execute("""INSERT OR REPLACE INTO products
                    (cj_id,cj_vid,title,shopify_id,cost_usd,sell_price,profit_margin,
                     shopify_collection_id,image_url,status,last_synced)
                    VALUES (?,?,?,?,?,?,?,?,?,'listed',datetime('now'))""",
                    (pid,vid,title,shopify_id,cost,sell,round(margin,4),coll_id,prod.get("productImage","")))
                db.commit()

            except Exception as e:
                log.error(f"  Product {pid} error: {e}\n{traceback.format_exc()}")
                S["errors"].append(f"err:{pid}:{str(e)[:60]}")

    heartbeat("processing_done")

# ── Step 10: Git commit ──────────────────────────────────────────────────
def step10_commit():
    log.info("── STEP 10: Git Commit ──")
    try:
        def g(cmd):
            return subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        g(["git","config","user.name","EdisonHaus Bot"])
        g(["git","config","user.email","bot@edisonhaus.store"])
        g(["git","add","-f","data/"])
        g(["git","stash"])
        g(["git","pull","--rebase","origin","main"])
        subprocess.run(["git","stash","pop"],capture_output=True,text=True)
        g(["git","add","-f","data/"])
        if subprocess.run(["git","diff","--staged","--quiet"],capture_output=True).returncode != 0:
            ds = datetime.now().strftime("%Y-%m-%d")
            g(["git","commit","-m",f"Product pipeline {ds} [skip ci]"])
            g(["git","push","origin","main"])
            log.info("  Committed & pushed")
        else:
            log.info("  No changes")
    except Exception as e:
        log.warning(f"  Git fail: {e}")

# ── Verification ──────────────────────────────────────────────────────────
def verify(db):
    log.info("── VERIFICATION ──")
    r = _req("GET",f"{SHOPIFY_BASE}/products/count.json",headers=shop_h())
    if r and r.status_code == 200:
        log.info(f"Shopify products: {r.json().get('count',0)}")
    cur = db.cursor()
    cur.execute("SELECT shopify_id FROM products WHERE shopify_id IS NOT NULL ORDER BY RANDOM() LIMIT 5")
    for (sid,) in cur.fetchall():
        mr = _req("GET",f"{SHOPIFY_BASE}/products/{sid}/metafields.json?namespace=dropship",headers=shop_h())
        if mr and mr.status_code == 200:
            mfs = mr.json().get("metafields",[])
            has = any(m["key"]=="cj_variant_id" for m in mfs)
            log.info(f"  {sid} cj_variant_id: {'PASS' if has else 'FAIL'}")
        time.sleep(0.3)
    r = _req("GET",f"{SHOPIFY_BASE}/custom_collections.json?limit=250",headers=shop_h())
    if r and r.status_code == 200:
        for c in r.json().get("custom_collections",[]):
            cr = _req("GET",f"{SHOPIFY_BASE}/products/count.json?collection_id={c['id']}",headers=shop_h())
            cnt = cr.json().get("count","?") if cr and cr.status_code==200 else "?"
            log.info(f"  {c['title']:<35} | {cnt}")
            time.sleep(0.3)

# ── Phase 6: Fill missing descriptions via Anthropic ─────────────────────
def phase6_fill_descriptions():
    """For products with no/short description, generate one via Anthropic and PUT body_html only."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        log.info("ANTHROPIC_API_KEY not set, skipping description generation")
        return
    log.info("── PHASE 6: Fill Missing Descriptions ──")
    # Fetch all products with short/missing descriptions
    missing = []
    url = f"{SHOPIFY_BASE}/products.json?limit=250&fields=id,title,body_html"
    while url:
        r = _req("GET", url, headers=shop_h())
        if not r or r.status_code != 200: break
        for p in r.json().get("products", []):
            html = (p.get("body_html") or "").strip()
            if len(html) < 50:
                missing.append({"id": p["id"], "title": p["title"]})
        link = r.headers.get("Link", "")
        url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
    if not missing:
        log.info("  All products have descriptions")
        return
    log.info(f"  {len(missing)} products need descriptions")
    updated = 0
    for i, p in enumerate(missing):
        try:
            r = requests.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": anthropic_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-sonnet-4-20250514", "max_tokens": 400,
                    "system": ("You are a product copywriter for EdisonHaus, a warm ambient home lighting "
                        "and decor store. Write a compelling product description using only <p> and "
                        "<ul><li> HTML tags. No headers, no bold, no other tags. 80-120 words. Focus "
                        "on ambiance, style, and home decor appeal. Do not invent specific measurements "
                        "or specs. Do not mention other brand names."),
                    "messages": [{"role": "user", "content": f"Write a product description for: {p['title']}"}]},
                timeout=30)
            if r.status_code != 200:
                log.warning(f"  Anthropic error for {p['id']}: {r.status_code}")
                time.sleep(1); continue
            desc = r.json()["content"][0]["text"].strip()
            if desc.startswith("```"):
                desc = desc.split("\n", 1)[1] if "\n" in desc else desc[3:]
                if desc.endswith("```"): desc = desc[:-3]
                desc = desc.strip()
            if "<p>" not in desc:
                log.warning(f"  Bad response for {p['id']}, skipping"); time.sleep(1); continue
            time.sleep(1)
            r2 = _req("PUT", f"{SHOPIFY_BASE}/products/{p['id']}.json", headers=shop_h(),
                json={"product": {"id": p["id"], "body_html": desc}})
            if r2 and r2.status_code == 200:
                updated += 1
                log.info(f"  [{i+1}/{len(missing)}] Updated: {p['title'][:50]}")
            else:
                log.warning(f"  Shopify PUT failed for {p['id']}")
            time.sleep(0.5)
        except Exception as e:
            log.error(f"  Desc error {p['id']}: {e}")
    log.info(f"  Descriptions: {updated}/{len(missing)} updated")

# ── Main ──────────────────────────────────────────────────────────────────
def main():
    log.info("="*60)
    log.info("EdisonHaus Product Pipeline")
    log.info("="*60)
    for v in ("SHOPIFY_ACCESS_TOKEN","CJ_API_KEY","CJ_EMAIL"):
        if not os.environ.get(v):
            log.error(f"Missing: {v}"); sys.exit(1)
    heartbeat("starting")
    try:
        tok = step1_auth()
        pid_map = step2_search(tok)
        if not any(pid_map.values()):
            log.warning("No products found."); heartbeat("complete","partial"); return
        details = step3_details(tok, pid_map)
        db = init_db()
        steps4_8(details, db)
        rp = REPORTS / f"pipeline_{datetime.now().strftime('%Y-%m-%d')}.json"
        rp.write_text(json.dumps({"created":S["created"],"updated":S["updated"],
            "skipped":S["skipped"],"errors":S["errors"][:50]},indent=2))
        verify(db)
        db.close()
        status = "success" if not S["errors"] else "partial"
        heartbeat("complete", status)
        log.info(f"Done: {S['created']} created, {S['updated']} updated, {S['skipped']} skipped, {len(S['errors'])} errors")
        phase6_fill_descriptions()
        step10_commit()
    except Exception as e:
        log.error(f"FATAL: {e}\n{traceback.format_exc()}")
        S["errors"].append(f"fatal:{str(e)[:200]}")
        heartbeat("failed","error")
        sys.exit(1)

if __name__ == "__main__":
    main()
