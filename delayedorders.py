import os
import re
import time
import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from datetime import datetime, timedelta
from dotenv import load_dotenv
import schedule

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
SHOP_DOMAIN   = os.environ["SHOPIFY_SHOP_DOMAIN"]
SHOPIFY_TOKEN = os.environ["SHOPIFY_ACCESS_TOKEN"]
SLACK_TOKEN   = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL = os.environ["SLACK_CHANNEL_ID"]
DELAY_DAYS    = int(os.getenv("DELAY_DAYS", 5))
API_VERSION   = "2024-04"
ROWS_PER_PAGE = 35

SHOPIFY_HEADERS = {
    "Content-Type": "application/json",
    "X-Shopify-Access-Token": SHOPIFY_TOKEN,
}

# ── Shopify helpers ────────────────────────────────────────────────────────────
def shopify_paginate(path, params=None):
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/{path}"
    while url:
        resp = requests.get(url, headers=SHOPIFY_HEADERS, params=params)
        resp.raise_for_status()
        data = resp.json()
        yield from data[next(iter(data))]
        params = None
        url = resp.links.get("next", {}).get("url")
        time.sleep(0.5)


def fetch_active_locations():
    locations = {}
    for loc in shopify_paginate("locations.json"):
        if loc.get("active"):
            name = loc["name"]
            short = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")
            locations[name] = short
    print(f"Found {len(locations)} active locations: {list(locations.keys())}")
    return locations


def fetch_unfulfilled_orders(cutoff_dt):
    params = {
        "fulfillment_status": "unshipped,partial",
        "financial_status": "paid",
        "status": "open",
        "limit": 250,
    }
    return [
        order for order in shopify_paginate("orders.json", params)
        if datetime.fromisoformat(order["created_at"]).replace(tzinfo=None) < cutoff_dt
    ]


def get_fulfillment_location(order_id, location_cache):
    resp = requests.get(
        f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/orders/{order_id}/fulfillment_orders.json",
        headers=SHOPIFY_HEADERS,
    )
    resp.raise_for_status()
    time.sleep(0.5)

    location_ids = {
        fo["assigned_location_id"]
        for fo in resp.json().get("fulfillment_orders", [])
        if fo.get("assigned_location_id")
    }

    for loc_id in location_ids:
        if loc_id not in location_cache:
            loc_resp = requests.get(
                f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/locations/{loc_id}.json",
                headers=SHOPIFY_HEADERS,
            )
            loc_resp.raise_for_status()
            location_cache[loc_id] = loc_resp.json().get("location", {}).get("name", "Unknown")
            time.sleep(0.5)

    names = [location_cache[lid] for lid in location_ids if lid in location_cache]
    return names[0] if names else "Unknown"


# ── Build report rows ──────────────────────────────────────────────────────────
def build_rows(orders, location_cache):
    rows = []
    for i, order in enumerate(orders, 1):
        print(f"  [{i}/{len(orders)}] Resolving {order['name']}...")
        location = get_fulfillment_location(order["id"], location_cache)

        shipping = order.get("shipping_address") or {}
        country  = shipping.get("country", "Unknown")
        date_str = datetime.fromisoformat(order["created_at"]).replace(tzinfo=None).strftime("%b %d, %Y")

        physical_items = [
            item for item in order.get("line_items", [])
            if item.get("requires_shipping") and item.get("fulfillable_quantity", 0) > 0
        ]
        if not physical_items:
            continue

        products_str = "  |  ".join(
            f"{item['name']} (x{item['fulfillable_quantity']})"
            for item in physical_items
        )

        rows.append({
            "Order":    order["name"],
            "Date":     date_str,
            "Country":  country,
            "Products": products_str,
            "Location": location,
        })
    return rows


# ── PDF generation ─────────────────────────────────────────────────────────────
HEADER_COLOR  = "#1E3A5F"
ROW_ALT_COLOR = "#EBF4FF"


def _draw_page(pdf, df_page, title, subtitle, page_num, total_pages):
    fig, ax = plt.subplots(figsize=(16.54, 11.69))  # A4 landscape
    fig.patch.set_facecolor("white")
    ax.axis("off")

    ax.text(0.5, 0.975, title, transform=ax.transAxes,
            fontsize=14, weight="bold", ha="center", va="top", color="#1A202C")
    ax.text(0.5, 0.938, subtitle, transform=ax.transAxes,
            fontsize=9, ha="center", va="top", color="#718096")
    if total_pages > 1:
        ax.text(0.98, 0.01, f"Page {page_num} of {total_pages}",
                transform=ax.transAxes, fontsize=8, ha="right", va="bottom", color="#A0AEC0")

    table = ax.table(
        cellText=df_page.values,
        colLabels=df_page.columns,
        bbox=[0, 0.05, 1, 0.87],
        cellLoc="left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.auto_set_column_width(col=list(range(len(df_page.columns))))

    for col_idx in range(len(df_page.columns)):
        cell = table[0, col_idx]
        cell.set_facecolor(HEADER_COLOR)
        cell.set_text_props(color="white", weight="bold")
        cell.set_edgecolor(HEADER_COLOR)

    for row_idx in range(1, len(df_page) + 1):
        bg = ROW_ALT_COLOR if row_idx % 2 == 0 else "white"
        for col_idx in range(len(df_page.columns)):
            cell = table[row_idx, col_idx]
            cell.set_facecolor(bg)
            cell.set_edgecolor("#E2E8F0")

    pdf.savefig(fig, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def create_pdf(df, path, warehouse_name, date_str):
    total_pages = max(1, -(-len(df) // ROWS_PER_PAGE))  # ceil division
    title    = f"{warehouse_name} — Delayed Orders"
    subtitle = f"Generated {date_str}  ·  {len(df)} unfulfilled line item(s)"

    with PdfPages(path) as pdf:
        if df.empty:
            _draw_page(pdf, df, title, subtitle, 1, 1)
        else:
            for page_num, start in enumerate(range(0, len(df), ROWS_PER_PAGE), 1):
                _draw_page(pdf, df.iloc[start:start + ROWS_PER_PAGE],
                           title, subtitle, page_num, total_pages)

    print(f"Created: {path} ({total_pages} page(s), {len(df)} rows)")


# ── Slack upload ───────────────────────────────────────────────────────────────
def upload_to_slack(file_path, comment=""):
    filename  = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)

    url_resp = requests.get(
        "https://slack.com/api/files.getUploadURLExternal",
        params={"filename": filename, "length": file_size},
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
    )
    url_resp.raise_for_status()
    upload_data = url_resp.json()
    if not upload_data.get("ok"):
        raise RuntimeError(f"Slack URL error: {upload_data.get('error')}")

    with open(file_path, "rb") as f:
        requests.post(upload_data["upload_url"], data=f).raise_for_status()

    result = requests.post(
        "https://slack.com/api/files.completeUploadExternal",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}", "Content-Type": "application/json"},
        json={
            "files":           [{"id": upload_data["file_id"]}],
            "channel_id":      SLACK_CHANNEL,
            "initial_comment": comment,
        },
    ).json()

    if not result.get("ok"):
        raise RuntimeError(f"Slack upload error: {result.get('error')}")
    print(f"Sent '{filename}' → channel {SLACK_CHANNEL}")


# ── Main job ───────────────────────────────────────────────────────────────────
def run():
    location_cache = {}
    cutoff    = datetime.now() - timedelta(days=DELAY_DAYS)
    date_str  = datetime.now().strftime("%Y-%m-%d")
    warehouses = fetch_active_locations()

    print(f"\n[{date_str}] Fetching orders delayed >{DELAY_DAYS} days...")
    orders = fetch_unfulfilled_orders(cutoff)
    print(f"Found {len(orders)} delayed orders. Resolving products + locations...")

    rows = build_rows(orders, location_cache)
    if not rows:
        print("No unfulfilled line items found. Nothing to send.")
        return

    df = pd.DataFrame(rows)

    for warehouse_name, short_name in warehouses.items():
        wh_df = (
            df[df["Location"] == warehouse_name]
            .drop(columns=["Location"])
            .sort_values("Date")
            .reset_index(drop=True)
        )
        if wh_df.empty:
            print(f"No delayed orders for {warehouse_name}. Skipping.")
            continue

        pdf_path = f"{short_name}_late_orders_{date_str}.pdf"
        create_pdf(wh_df, pdf_path, warehouse_name, date_str)
        upload_to_slack(
            pdf_path,
            comment=f":warning: *{warehouse_name}* — {len(wh_df)} unfulfilled line item(s) as of {date_str}",
        )


# ── Scheduler ──────────────────────────────────────────────────────────────────
schedule.every().monday.at("12:00").do(run)

if __name__ == "__main__":
    while True:
        print(f"Scheduler active — {datetime.now():%A %H:%M}")
        schedule.run_pending()
        time.sleep(60)
