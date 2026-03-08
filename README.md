# Shopify Delayed Orders Reporter

Automatically fetches unfulfilled Shopify orders older than N days, generates a PDF report per warehouse showing exactly which physical products are pending, and posts them to a Slack channel every Monday.

---

## How It Works

1. Pulls all open, paid, unshipped orders from Shopify older than `DELAY_DAYS` days
2. For each order, resolves which warehouse it's assigned to via the Fulfillment Orders API
3. Filters to physical products only (skips digital/non-shipping items)
4. Generates one PDF per warehouse — each row is one order with all its unfulfilled products listed
5. Uploads every PDF to a single Slack channel
6. Repeats every Monday at 12:00

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/zeyadsayedabdullah/shopify-delayed-orders.git
cd shopify-delayed-orders
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy the example file and fill in your credentials:

```bash
cp .env.example .env
```

Open `.env` and set:

| Variable | Description |
|---|---|
| `SHOPIFY_SHOP_DOMAIN` | Your store domain, e.g. `yourstore.myshopify.com` |
| `SHOPIFY_ACCESS_TOKEN` | Shopify Admin API access token |
| `SLACK_BOT_TOKEN` | Slack bot token (`xoxb-...`) |
| `SLACK_CHANNEL_ID` | ID of the Slack channel to post reports to |
| `DELAY_DAYS` | Orders older than this many days are considered delayed (default: `5`) |

#### How to get a Shopify access token
1. Go to your Shopify Admin → **Settings → Apps and sales channels → Develop apps**
2. Create a new app → **Configure Admin API scopes**
3. Enable: `read_orders`, `read_fulfillments`, `read_inventory`, `read_locations`
4. Install the app and copy the **Admin API access token**

#### How to get a Slack bot token
1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App**
2. Add these **OAuth scopes** under *Bot Token Scopes*: `files:write`, `chat:write`
3. Install the app to your workspace and copy the **Bot User OAuth Token**
4. Invite the bot to your target channel: `/invite @YourBotName`

#### How to find a Slack channel ID
Right-click the channel name in Slack → **View channel details** → copy the ID at the bottom (starts with `C`).

---

## Running

### Run once immediately

```bash
python delayedorders.py
```

> The script will run the job once on startup, then continue running on the Monday 12:00 schedule.

To trigger it immediately without waiting, open a Python shell:

```python
import delayedorders
delayedorders.run()
```

### Run on a schedule (Windows / always-on machine)

Just leave the terminal open:

```bash
python delayedorders.py
```

The script checks every 60 seconds and fires every Monday at 12:00. Keep the terminal/window open or run it in the background with:

```bash
# Windows
start /B python delayedorders.py

# Linux/macOS
nohup python delayedorders.py &
```

---

## Output

- Active warehouses are fetched automatically from Shopify — no manual configuration needed
- One PDF per warehouse that has delayed orders (warehouses with no delays are skipped)
- Each row = one order, showing: Order #, Date, Country, and all unfulfilled physical products with quantities
- Digital/non-shipping products are excluded automatically
- PDFs are sent to the configured Slack channel with a summary message
- Multi-page PDFs are generated automatically if there are more than 35 orders

---

## Changing the Schedule

The schedule is set at the bottom of `delayedorders.py`:

```python
schedule.every().monday.at("12:00").do(run)
```

Change `monday` to `tuesday`, `wednesday`, etc., or use `schedule.every().day.at("09:00")` for daily reports. See the [schedule docs](https://schedule.readthedocs.io) for all options.
