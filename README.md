# DadDeals

DadDeals is a small local Flask dashboard for tracking products and stocks. Phase 2F adds optional Crawlbase fallback checks for exact product URLs.

This phase does not include Scrape.do, proxy scraping, Selenium, Playwright, browser automation, Celery, Redis, Postgres, Docker, product recommendations, or attempts to bypass API limits.

## Project Structure

```text
DadDeals/
|-- app.py
|-- worker.py
|-- schema.sql
|-- requirements.txt
|-- .env.example
|-- .gitignore
|-- README.md
|-- deployment/
|-- scripts/
|-- backups/
|-- templates/
|-- static/
`-- instance/
```

The SQLite database belongs in `instance/`. Backups belong in `backups/`. The real `.env` file, database files, logs, and backups are ignored by Git.

## Dad Handoff Guide

Open DadDeals from a phone or computer on the same home Wi-Fi:

```text
http://<pi-ip>:5000
```

If `raspberrypi.local` works on your network, this may also work:

```text
http://raspberrypi.local:5000
```

Use the dashboard password from `.env`.

To add a product:

1. Press `Add Product`.
2. Paste the exact product page URL.
3. Set a target price.
4. Leave it active and save.
5. DadDeals immediately tries one price check and shows the result.

To add a stock:

1. Press `Add Stock`.
2. Enter the company name and ticker, such as `AAPL`.
3. Set the target price or daily drop percent.
4. Save it.

What `Retry` means:

- Retry checks that one product again from the website.
- It is useful when a product check failed or was skipped.
- It does not run every product or stock.

Why Amazon checks are limited:

- Amazon pages often block simple automatic checks.
- DadDeals uses Canopy for Amazon when it is enabled.
- Canopy has a monthly request budget, so DadDeals avoids checking Amazon too often.

What Telegram alerts mean:

- DadDeals creates alerts when a saved target or drop rule is met.
- Telegram sends those alerts when configured.
- If Telegram fails, the alert still appears on the dashboard.

If a product check fails:

- Press `Retry`.
- Open the product source link and check manually.
- For Amazon, make sure Canopy is enabled and monthly usage is not exhausted.

What not to touch:

- Do not edit `.env` unless changing passwords or API keys.
- Do not delete `instance/daddeals.db`.
- Do not delete the project folder.
- Do not expose the site to the public internet.

## Maintenance Guide For Me

Restart the website:

```bash
sudo systemctl restart daddeals.service
```

Check website status:

```bash
sudo systemctl status daddeals.service
```

View website logs:

```bash
journalctl -u daddeals.service -n 80 --no-pager
```

View worker logs:

```bash
tail -n 80 logs/worker.log
```

Run the worker manually:

```bash
./scripts/run_worker.sh
```

Edit cron:

```bash
crontab -e
```

Back up the database:

```bash
./scripts/backup_db.sh
```

Restore note: stop the website first, then copy a backup over `instance/daddeals.db`, then start the website again.

```bash
sudo systemctl stop daddeals.service
cp backups/daddeals-YYYYMMDD-HHMMSS.db instance/daddeals.db
sudo systemctl start daddeals.service
```

Run a quick health check:

```bash
python worker.py --health
```

## Final Gift Test Checklist

1. Reboot the Raspberry Pi and confirm the website loads.
2. Run `python worker.py --test-telegram` and confirm Telegram receives a test.
3. Run `python worker.py --run` and confirm a stock check works.
4. Run `python worker.py --debug-canopy <ASIN>` and confirm Amazon Canopy works.
5. Press `Retry` on a product from the website and confirm it finishes.
6. Confirm `logs/worker.log` updates after cron or `./scripts/run_worker.sh`.
7. Open Settings and confirm database, Telegram, Canopy, backups, timezone, and worker status look healthy.

## Windows Local Setup

Open PowerShell in the DadDeals folder.

Create a virtual environment:

```powershell
python -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install requirements:

```powershell
pip install -r requirements.txt
```

This installs Flask, python-dotenv, requests, yfinance, BeautifulSoup, lxml, and Gunicorn.

Create your real local `.env` file:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and replace at least these values:

```text
APP_SECRET_KEY=use_a_long_random_value_here
ADMIN_PASSWORD=choose_a_password_for_the_dashboard
DATABASE_PATH=instance/daddeals.db
HOST=0.0.0.0
PORT=5000
APP_TIMEZONE=America/Los_Angeles
TELEGRAM_BOT_TOKEN=replace_me_later
TELEGRAM_CHAT_ID=replace_me_later
CANOPY_API_KEY=replace_me_later
ENABLE_CANOPY_AMAZON=false
CANOPY_MONTHLY_LIMIT=100
CANOPY_AUTH_HEADER=API-KEY
AMAZON_CHECK_INTERVAL_HOURS=24
ENABLE_CRAWLBASE=false
CRAWLBASE_NORMAL_TOKEN=replace_me_later
CRAWLBASE_JS_TOKEN=replace_me_later
CRAWLBASE_DAILY_LIMIT=200
CRAWLBASE_CHECK_INTERVAL_HOURS=24
CRAWLBASE_DEFAULT_COUNTRY=US
CRAWLBASE_USE_JS_FALLBACK=false
```

Leave the Telegram values as placeholders until you are ready to test message delivery.

Initialize the database:

```powershell
python app.py --init-db
```

This creates the `instance/` folder if needed and creates any missing tables from `schema.sql`. Running it again is safe because the schema uses `CREATE TABLE IF NOT EXISTS`; it does not erase existing data.

Preview the worker without saving anything:

```powershell
python worker.py --dry-run
```

Run one worker pass and save product checks, real stock checks, and alerts:

```powershell
python worker.py --run
```

Send unsent alerts through Telegram after you fill in the Telegram settings:

```powershell
python worker.py --send-alerts
```

Run checks and send any new unsent alerts in one command:

```powershell
python worker.py --run --send-alerts
```

Run the app:

```powershell
python app.py
```

Open this on the same PC:

```text
http://127.0.0.1:5000
```

Log in with the `ADMIN_PASSWORD` from your `.env` file.

## Raspberry Pi Setup

On Raspberry Pi OS, open a terminal in the DadDeals folder.

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install requirements:

```bash
pip install -r requirements.txt
```

This installs Flask, python-dotenv, requests, yfinance, BeautifulSoup, lxml, and Gunicorn.

Create your real local `.env` file:

```bash
cp .env.example .env
```

Edit `.env`:

```bash
nano .env
```

Use these network defaults unless you have a reason to change them:

```text
DATABASE_PATH=instance/daddeals.db
HOST=0.0.0.0
PORT=5000
```

Initialize the database:

```bash
python app.py --init-db
```

Preview the worker without saving anything:

```bash
python worker.py --dry-run
```

Run one worker pass and save product checks, real stock checks, and alerts:

```bash
python worker.py --run
```

Send unsent alerts through Telegram after you fill in the Telegram settings:

```bash
python worker.py --send-alerts
```

Run checks and send any new unsent alerts in one command:

```bash
python worker.py --run --send-alerts
```

Test the Raspberry Pi cron wrapper manually:

```bash
chmod +x scripts/run_worker.sh
./scripts/run_worker.sh
```

Run the app:

```bash
python app.py
```

From a phone on the same Wi-Fi network, try:

```text
http://raspberrypi.local:5000
```

If that does not work, use the Pi's IP address instead. The IP address is often more reliable than `raspberrypi.local` because some routers and phones do not handle local hostname resolution well.

Find the Pi's IP address:

```bash
hostname -I
```

Then open something like this from your phone:

```text
http://192.168.1.50:5000
```

## Run DadDeals Website on Boot

Use this after the manual Raspberry Pi setup works. `python app.py` is still useful for beginner testing, but it only keeps the site online while that terminal is open. Gunicorn runs the Flask app more reliably, and systemd starts it again after reboot or a crash.

DadDeals uses a small Gunicorn command for the Raspberry Pi 3 B:

```bash
gunicorn --workers 1 --threads 2 --bind 0.0.0.0:5000 app:app
```

The `app:app` part means "load the `app` object from `app.py`." Keep `python app.py` for manual testing; use systemd for the always-on website.

Before installing the service, make sure requirements are installed:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python app.py --init-db
```

The example service file is:

```text
deployment/daddeals.service.example
```

It assumes the project is here:

```text
/home/pi/DadDeals
```

If your Pi username or project path is different, edit the service file first:

```bash
nano deployment/daddeals.service.example
```

Change these values if needed:

```text
User=pi
Group=pi
WorkingDirectory=/home/pi/DadDeals
EnvironmentFile=-/home/pi/DadDeals/.env
ExecStart=/home/pi/DadDeals/.venv/bin/gunicorn --workers 1 --threads 2 --bind 0.0.0.0:5000 app:app
```

Install with the helper script:

```bash
chmod +x scripts/install_web_service.sh
./scripts/install_web_service.sh
```

Or install manually:

```bash
sudo cp deployment/daddeals.service.example /etc/systemd/system/daddeals.service
sudo systemctl daemon-reload
sudo systemctl enable daddeals.service
sudo systemctl restart daddeals.service
```

Useful service commands:

```bash
sudo systemctl status daddeals.service
sudo systemctl stop daddeals.service
sudo systemctl start daddeals.service
sudo systemctl restart daddeals.service
```

View website logs:

```bash
journalctl -u daddeals.service -n 80 --no-pager
journalctl -u daddeals.service -f
```

Open the site from your phone:

```text
http://<pi-ip>:5000
```

Use `hostname -I` on the Pi to find `<pi-ip>`. The IP address is more reliable than `raspberrypi.local` if local hostname resolution fails.

The web service and cron worker do different jobs:

- systemd keeps the DadDeals website online.
- cron runs `worker.py` periodically to check prices/stocks and send alerts.
- `scripts/run_worker.sh` is still the cron helper; it is separate from `daddeals.service`.

## Database Commands

Initialize or update missing tables without deleting data:

```bash
python app.py --init-db
```

Back up the SQLite database:

```bash
chmod +x scripts/backup_db.sh
./scripts/backup_db.sh
```

Backups are stored in `backups/`, and DadDeals keeps the newest 10 backup files. The backup script copies only the SQLite database; it does not copy `.env`.

There is no reset command in Phase 2D. That is intentional, so a beginner command cannot accidentally wipe saved products, stocks, checks, alerts, API usage, backups, or delivery history.

## Worker Commands

Phase 2F uses exact-URL product checks, optional Canopy API checks for Amazon URLs, optional Crawlbase fallback for selected retailer pages, real yfinance stock data, Telegram delivery, optional cron scheduling, simple reliability controls, safer alert management, product retry controls, local timezone display, database backups, health checks, and a lightweight systemd web service. Multi-site product search and recommendations still come in a later phase.

Preview one worker pass without saving rows:

```bash
python worker.py --dry-run
```

Run one worker pass and save rows:

```bash
python worker.py --run
```

Send existing unsent alert rows through Telegram:

```bash
python worker.py --send-alerts
```

Send one Telegram test message without creating an alert row:

```bash
python worker.py --test-telegram
```

Debug one Canopy Amazon API request without creating alerts:

```bash
python worker.py --debug-canopy B08N5WRWNW
```

Debug one Crawlbase retailer product-page fetch without creating alerts or product checks:

```bash
python worker.py --debug-crawlbase-url "https://www.bestbuy.com/site/example/1234567.p"
```

Try Crawlbase JavaScript mode when normal mode returns incomplete HTML:

```bash
python worker.py --debug-crawlbase-url "https://www.bestbuy.com/site/example/1234567.p" --js --page-wait 3000
```

Print a quick maintenance health summary:

```bash
python worker.py --health
```

Create checks and then send unsent alerts:

```bash
python worker.py --run --send-alerts
```

The worker runs once and exits. It reads active products and stocks from SQLite, inserts rows into `price_checks` and `stock_checks`, and creates local rows in `alerts` when values meet your saved thresholds.

To view worker results:

1. Run `python worker.py --run`.
2. Start the web app with `python app.py`.
3. Open the dashboard to see recent alerts.
4. Open a product detail page to see recent price checks.
5. Open a stock detail page to see recent stock checks.

If you run the worker repeatedly on the same day, DadDeals avoids creating the exact same alert over and over. Telegram delivery also skips alerts that already have a `sent_at` value. Product alerts include the exact product URL in the stored alert message, so the same source link appears on the dashboard and in Telegram.

Daily rise percent is stored on stocks, but Phase 1G does not send rise alerts yet because the UI does not have a separate "notify on rise" setting. That is future work.

## Settings and Status

Open `Settings` from the top bar after logging in.

The settings page shows:

- app phase label: `DadDeals v2F - Crawlbase fallback`
- whether Telegram appears configured, without showing secrets
- database path
- last worker run time based on recent check rows
- recent `logs/worker.log` status when cron has run
- saved alert count
- Canopy Amazon status, API key presence, auth header mode, monthly usage, and Amazon check interval
- Crawlbase status, token presence, daily usage, interval, country, and JS fallback mode
- display timezone from `APP_TIMEZONE`

## Timezone Display

DadDeals keeps database timestamps in a simple SQLite-friendly format. User-facing pages format those timestamps with:

```text
APP_TIMEZONE=America/Los_Angeles
```

If `APP_TIMEZONE` is missing, DadDeals defaults to `America/Los_Angeles`. This means a stored UTC check time can display on the website as a readable California time, such as:

```text
Jun 21, 2026, 11:02 PM
```

This formatting is used on dashboard alert times, product check times, stock check times, product detail history, and Settings last-worker time.

The settings page also has a safe alert cleanup form. It deletes alert records only. It does not delete tracked products, tracked stocks, price checks, or stock checks.

To clear old alerts:

1. Open `Settings`.
2. Choose an age, such as 30 days.
3. Check the confirmation box.
4. Press `Clear Old Alerts`.

To clear all alerts from Settings, choose `All alerts - extra confirmation required`, check both confirmation boxes, and press `Clear Old Alerts`. This still does not delete product or stock check history.

To delete recent alerts while testing:

1. Open the dashboard.
2. Press `Delete Alert` on one alert to remove only that alert.
3. Or check several alerts, check the confirmation box under the alert list, and press `Delete Selected Alerts`.
4. If no alerts are selected, DadDeals shows a friendly message and deletes nothing.

## Exact-URL Product Checks

DadDeals checks only the exact product URL you save. It does not search other stores, compare multiple sellers, or use product recommendation APIs.

Good test URL:

```text
https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html
```

To test product checking:

1. Add a product from the dashboard.
2. Use the Books to Scrape URL above.
3. Set a target price above the page price if you want to trigger a target alert.
4. Run:

```bash
python worker.py --dry-run
python worker.py --run
```

DadDeals looks for prices in common HTML patterns such as:

- `meta[property="product:price:amount"]`
- `meta[itemprop="price"]`
- elements with `itemprop="price"`
- class or id names containing `price`

Real retail websites may not always work. Their HTML varies, some prices are loaded later with JavaScript, and some stores block simple automated requests. DadDeals does not use Selenium or Playwright in this phase because browser automation is heavy for a Raspberry Pi 3 B and more fragile to maintain.

If a product page cannot be fetched or no price is found, DadDeals stores a failed price check and continues checking the other products and stocks.

Amazon automatic scraping is not supported or reliable in v1. Amazon often blocks automated price checks, so DadDeals will still save an Amazon link, but you may need to check it manually or use a different store page.

When a product target or big-drop alert is created, DadDeals stores the product URL in the alert message. The dashboard renders that URL as a clickable link, and Telegram receives the same source link.

## Product Checks from the Website

When you add a product from the website, DadDeals saves it first, then immediately tries one price check for that product only. It does not run the full worker and does not check every product.

If that initial check creates a normal alert row because your saved thresholds match, DadDeals sends that new alert through Telegram immediately when Telegram is configured. If Telegram fails, the product and alert are still saved, and the dashboard shows the alert delivery status. You can retry delivery later with:

```bash
python worker.py --send-alerts
```

If the first check fails, the product is still saved. The dashboard and product detail page show the latest check status and friendly failure message.

Product cards on the dashboard show:

- latest successful price
- last checked time
- last check status: success, failed, skipped, or never checked
- source/store name
- link to the product source
- price change since the first successful check
- price change since the previous successful check

To retry one product:

1. Open the product detail page.
2. Press `Retry Price Check Now`.
3. Or, on the dashboard, press `Retry` on a failed or skipped product card.

To retry several failed/skipped products:

1. Open the dashboard.
2. Press `Retry Failed Product Checks`.
3. DadDeals retries up to 5 active products whose latest check failed or was skipped.

These website retries check only product rows. They do not run stock checks, do not run the full worker, and do not expose secrets.

Manual product retries bypass `AMAZON_CHECK_INTERVAL_HOURS` for that one product. This is intentional: the interval is for scheduled worker checks, not a user pressing a retry button. Manual retries still respect `CANOPY_MONTHLY_LIMIT`, so DadDeals will not spend Canopy requests after your configured monthly budget is exhausted.

## Amazon Product Checks with Canopy

Amazon is handled differently because normal product-page scraping often fails. Amazon pages can block automated requests, change markup frequently, or load price details in ways the lightweight BeautifulSoup checker cannot read. DadDeals does not use Selenium, Playwright, proxy scraping, or browser automation because those approaches are heavier and more fragile on a Raspberry Pi 3 B.

Phase 2A optionally uses Canopy API for Amazon URLs. Canopy provides structured Amazon product data by ASIN through a REST endpoint. DadDeals only uses it when you explicitly enable it.

Create a Canopy API key:

1. Go to `https://www.canopyapi.co/`.
2. Create an account.
3. Copy your API key from the Canopy dashboard.
4. Put the key in your real `.env` file, not `.env.example`.

Example `.env` settings:

```text
CANOPY_API_KEY=your_real_canopy_key_here
ENABLE_CANOPY_AMAZON=true
CANOPY_MONTHLY_LIMIT=100
CANOPY_AUTH_HEADER=API-KEY
AMAZON_CHECK_INTERVAL_HOURS=24
```

Use `CANOPY_AUTH_HEADER=API-KEY` unless your Canopy account/docs tell you to use bearer auth. If needed, set:

```text
CANOPY_AUTH_HEADER=Authorization
```

That sends:

```text
Authorization: Bearer <your key>
```

The default monthly budget is 100 requests. DadDeals tracks Canopy usage in SQLite in the `api_usage` table and will not make a Canopy request after the configured monthly limit is reached. When the limit is exhausted, Amazon checks are skipped with a friendly manual-check message.

Recommended Amazon frequency:

```text
AMAZON_CHECK_INTERVAL_HOURS=24
```

Daily scheduled checks are a better fit than hourly checks because they preserve free-tier requests. If an Amazon item was checked recently, `worker.py --dry-run` will say it is not due yet. DadDeals avoids writing noisy skipped rows every hour.

Website retry buttons bypass this interval for the selected product only. Scheduled worker commands and `scripts/run_worker.sh` still respect `AMAZON_CHECK_INTERVAL_HOURS`.

For short testing only, you can temporarily set:

```text
AMAZON_CHECK_INTERVAL_HOURS=0
```

Then run a manual retry from the website or run:

```bash
python worker.py --dry-run
python worker.py --run
```

Set it back afterward:

```text
AMAZON_CHECK_INTERVAL_HOURS=24
```

Do not create multiple Canopy accounts, rotate API keys, or try to bypass API limits. If you need more than the free budget, use the plan or limit that fits your real usage.

To test Amazon ASIN extraction, add a product URL shaped like:

```text
https://www.amazon.com/dp/B08N5WRWNW
```

Then run:

```bash
python worker.py --dry-run
python worker.py --run
```

If Canopy is disabled or missing a key, DadDeals saves a skipped Amazon check with a message telling you to open the product page manually. If Canopy is enabled and the item is due, `--run` calls Canopy, stores the returned price, and uses the same product alert logic as other product checks.

## Canopy Debug Command

Use this when an Amazon check times out or you need to know whether the problem is the API key, auth header, endpoint, network, ASIN extraction, or DadDeals parsing.

Run:

```bash
python worker.py --debug-canopy B08N5WRWNW
```

This command:

- loads `CANOPY_API_KEY`, `CANOPY_AUTH_HEADER`, and related `.env` settings
- prints whether Canopy is enabled and whether a key is present, without printing the key
- calls the Canopy Amazon product endpoint with a 30-second timeout
- prints the HTTP status code
- prints a redacted response shape preview
- prints parsed title, price, display price, currency, availability, and source URL when parsing succeeds
- creates no alerts and sends no Telegram messages
- counts each actual Canopy request in the local `api_usage` table

To find an ASIN from an Amazon URL, look for the 10-character code after `/dp/` or `/gp/product/`.

Examples:

```text
https://www.amazon.com/dp/B08N5WRWNW
https://www.amazon.com/some-product-name/dp/B08N5WRWNW/ref=...
https://www.amazon.com/gp/product/B08N5WRWNW
```

The ASIN in all three examples is:

```text
B08N5WRWNW
```

Auth header options:

```text
CANOPY_AUTH_HEADER=API-KEY
CANOPY_AUTH_HEADER=Authorization
CANOPY_AUTH_HEADER=auto
```

`API-KEY` sends:

```text
API-KEY: <your key>
```

`Authorization` sends:

```text
Authorization: Bearer <your key>
```

`auto` makes the debug command try both modes. Use `auto` only while diagnosing, then set the value that works.

How to interpret common debug results:

- Timeout: Canopy or the network did not respond within 30 seconds. Check Pi internet access and try again later.
- HTTP 401 or 403: likely API key or auth header issue.
- HTTP 429: likely Canopy rate limit or request budget issue.
- JSON response but parsing failed: DadDeals saved a redacted response shape preview to `logs/canopy_debug_last.json` so the parser can be adjusted without exposing secrets.
- Key missing: `.env` still has `CANOPY_API_KEY=replace_me_later` or the key is blank.

## Crawlbase Fallback

Phase 2F lets DadDeals use Crawlbase as a controlled fallback for exact product URLs. It still does not search the web, compare stores, use proxies, or run browser automation on the Pi.

How product checks choose a method:

- Amazon: Canopy is tried first when enabled and due. Crawlbase is used only if that product explicitly allows or prefers Crawlbase.
- Best Buy: Crawlbase normal mode is preferred by default because Best Buy pages often block simple requests or hide price data.
- Target, Walmart, Home Depot, Newegg, and other stores: DadDeals tries the normal lightweight requests + BeautifulSoup check first, then uses Crawlbase only when the product allows fallback.
- Manual `Retry with Crawlbase` uses Crawlbase for that one product right away. It bypasses the Crawlbase interval but still respects `CRAWLBASE_DAILY_LIMIT`.
- Scheduled worker checks respect `CRAWLBASE_CHECK_INTERVAL_HOURS` so cron does not spend requests too often.

Crawlbase is not guaranteed to work for every retailer. Some pages still hide prices, block automated traffic, or return HTML that needs a future parser adjustment. DadDeals saves a friendly failed check instead of crashing.

DadDeals uses the documented Crawlbase Crawling API endpoint:

```text
https://api.crawlbase.com/?token=YOUR_TOKEN&url=ENCODED_URL
```

Crawlbase has two token types:

- Normal token: faster path for static HTML.
- JavaScript token: rendered browser path for pages that need JavaScript. Use this with `--js`, `--page-wait`, or `--ajax-wait`.

Add these values to your real `.env`:

```text
ENABLE_CRAWLBASE=true
CRAWLBASE_NORMAL_TOKEN=your_normal_token_here
CRAWLBASE_JS_TOKEN=your_js_token_here
CRAWLBASE_DAILY_LIMIT=200
CRAWLBASE_CHECK_INTERVAL_HOURS=24
CRAWLBASE_DEFAULT_COUNTRY=US
CRAWLBASE_USE_JS_FALLBACK=false
```

Keep `CRAWLBASE_USE_JS_FALLBACK=false` at first. Turn it on only if normal Crawlbase mode cannot see a price and you are comfortable using the JavaScript token for fallback checks. Best Buy still uses normal mode by default.

Product form options:

- `Use Crawlbase if normal check fails`: normal request first, Crawlbase second.
- `Prefer Crawlbase for this product`: Crawlbase first, then normal request if Crawlbase fails.

The product dashboard and detail page show the last detected store and last check method, such as `Normal`, `Crawlbase Normal`, `Crawlbase Js`, or `Amazon Canopy`.

Run a normal-token diagnostic:

```bash
python worker.py --debug-crawlbase-url "https://www.bestbuy.com/site/example/1234567.p"
```

Try JavaScript mode if normal mode returns incomplete HTML:

```bash
python worker.py --debug-crawlbase-url "https://www.bestbuy.com/site/example/1234567.p" --js --country US --page-wait 3000
```

You can also try waiting for AJAX/network idle:

```bash
python worker.py --debug-crawlbase-url "https://www.bestbuy.com/site/example/1234567.p" --js --ajax-wait
```

The debug command:

- prints Crawlbase enabled/token status without printing tokens
- detects Best Buy, Newegg, Walmart, Target, and Home Depot URLs
- extracts Best Buy SKUs from `/SKU.p` and `skuId=SKU`
- makes one Crawlbase request only when enabled and a required token is present
- saves returned HTML/text to `logs/crawlbase_debug_last.html`
- saves redacted metadata to `logs/crawlbase_debug_last.json`
- attempts Best Buy title/price/availability parsing
- creates no alerts and sends no Telegram messages
- does not update tracked product `price_checks`

Crawlbase usage is tracked in the existing `api_usage` table with provider `crawlbase` and a daily key such as `2026-06-22`. DadDeals will not make a Crawlbase debug or fallback request after `CRAWLBASE_DAILY_LIMIT` is reached for that day.

Preserve Crawlbase credits by testing manually and daily, not constantly from cron.

## Real Stock Checks

Add a stock such as `AAPL` from the dashboard, then run:

```bash
python worker.py --dry-run
python worker.py --run
```

`--dry-run` fetches stock data and prints what would happen, but does not save `stock_checks` or alerts. `--run` saves the real yfinance check result.

Open the stock detail page to see:

- latest yfinance price used by DadDeals
- previous close
- percent change
- check status
- friendly failure message if the ticker could not be fetched

Products use exact-URL checks in Phase 1G. Stock checks use yfinance.

## Telegram Setup

DadDeals uses Telegram only when you run `python worker.py --send-alerts` or `python worker.py --run --send-alerts`.

Create a bot:

1. Open Telegram and search for `BotFather`.
2. Start a chat with BotFather.
3. Send `/newbot`.
4. Follow the prompts to choose a bot name and username.
5. BotFather will give you a bot token. Put that value in `.env` as `TELEGRAM_BOT_TOKEN`.

Get your chat ID:

1. Start a Telegram chat with your new bot and send it any message, such as `hello`.
2. In a browser, open this URL after replacing `<token>` with your bot token:

```text
https://api.telegram.org/bot<token>/getUpdates
```

3. Look for the `chat` object and copy its `id` value.
4. Put that value in `.env` as `TELEGRAM_CHAT_ID`.

Your `.env` should include:

```text
TELEGRAM_BOT_TOKEN=your_real_bot_token_here
TELEGRAM_CHAT_ID=your_real_chat_id_here
```

Do not put real Telegram values in `.env.example`.

Test Telegram delivery:

```bash
python worker.py --run
python worker.py --send-alerts
```

Or do both in one command:

```bash
python worker.py --run --send-alerts
```

Test Telegram without creating an alert row:

```bash
python worker.py --test-telegram
```

If Telegram settings are missing, the worker prints a friendly message and marks the delivery attempt as failed without crashing. After fixing `.env`, run `python worker.py --send-alerts` again.

## Raspberry Pi Cron

Phase 1E adds a small Bash script for cron:

```bash
scripts/run_worker.sh
```

The script:

- changes into the DadDeals project folder
- uses `.venv/bin/python` directly
- runs `python worker.py --run --send-alerts`
- writes output to `logs/worker.log`
- creates `logs/` if it does not exist
- prints timestamped start and end lines
- exits with a nonzero status if the worker fails

On the Pi, make the script executable:

```bash
chmod +x scripts/run_worker.sh
```

Test it manually:

```bash
./scripts/run_worker.sh
```

View the log:

```bash
tail -n 80 logs/worker.log
```

If the script says it cannot find Python, open `scripts/run_worker.sh` and edit `PROJECT_DIR` near the top. For example:

```bash
PROJECT_DIR="/home/pi/DadDeals"
```

Edit your cron jobs:

```bash
crontab -e
```

Option A, local 9 AM to 4 PM, Monday-Friday:

```cron
0 9-16 * * 1-5 /home/pi/DadDeals/scripts/run_worker.sh
```

Option B, U.S. stock market hours from California time, roughly 6 AM to 1 PM, Monday-Friday:

```cron
0 6-13 * * 1-5 /home/pi/DadDeals/scripts/run_worker.sh
```

If your Pi username or project path is different, replace `/home/pi/DadDeals` with the real path. You can check the current folder with:

```bash
pwd
```

Disable the cron job by editing crontab again and putting `#` at the start of the DadDeals line:

```cron
# 0 6-13 * * 1-5 /home/pi/DadDeals/scripts/run_worker.sh
```

Confirm cron is installed:

```bash
which cron
```

Confirm cron is running:

```bash
systemctl status cron
```

If cron is not running, start it:

```bash
sudo systemctl start cron
```

Cron runs the same worker command you run manually, so it creates exact-URL product checks, real yfinance stock checks, local alerts, and Telegram delivery attempts.

## Recommended First Real Test

1. Add this product URL:

```text
https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html
```

2. Add a stock such as `AAPL`.
3. Run:

```bash
python worker.py --dry-run
python worker.py --run
```

4. Open the dashboard and confirm status badges are clear.
5. Open the product detail page and confirm the price check appears.
6. Open the stock detail page and confirm the yfinance check appears.
7. Open `Settings` and confirm Telegram, database, worker, and alert status are readable.
8. If Telegram is configured, run:

```bash
python worker.py --test-telegram
```

## Manual Test Checklist

1. Start the app with `python app.py`.
2. Open `http://127.0.0.1:5000` on the computer running it.
3. Confirm the login page appears.
4. Log in with the password from `.env`.
5. Confirm the dashboard shows products, stocks, and recent alerts sections.
6. Confirm the empty states say no products, no stocks, and no alerts when the database is empty.
7. Add a product with an `http://` or `https://` URL.
8. Try adding a product with a blank name, a URL without `http://` or `https://`, and a negative target price. Confirm friendly errors appear.
9. Open the product detail page and confirm the future price history and source check placeholders are visible.
10. Edit the product, pause it, resume it, and delete it. Confirm delete asks before removing it.
11. Add a stock with a lowercase ticker and confirm it saves uppercase.
12. Try adding a stock with a blank company name and a negative percentage. Confirm friendly errors appear.
13. Open the stock detail page and confirm the future price history placeholder is visible.
14. Edit the stock, pause it, resume it, and delete it. Confirm delete asks before removing it.
15. Log out and confirm the dashboard is protected.
16. On the Raspberry Pi, open the app from a phone at `http://<pi-ip>:5000`.
17. Run `python worker.py --dry-run` and confirm it prints a summary without saving checks.
18. Add a stock such as `AAPL`.
19. Run `python worker.py --run` and confirm it prints a summary with saved checks.
20. Refresh the dashboard and confirm recent alerts appear if thresholds were met.
21. Open product and stock detail pages and confirm recent check history appears.
22. Add the Books to Scrape demo product URL and confirm the worker records a real price check.
23. Add an invalid product URL and confirm the worker records a failed price check without crashing.
24. Add an invalid ticker and confirm the worker records a failed stock check without crashing.
25. Run `python worker.py --send-alerts` without Telegram settings and confirm it fails gracefully.
26. Add real Telegram settings to `.env`, run `python worker.py --send-alerts`, and confirm sent alerts show as sent on the dashboard.
27. On the Pi, run `chmod +x scripts/run_worker.sh`.
28. Run `./scripts/run_worker.sh` and confirm `logs/worker.log` gets timestamped start and end lines.
29. Add one cron line with `crontab -e`, then confirm later runs appear in `logs/worker.log`.
30. On the Pi, run `chmod +x scripts/install_web_service.sh`.
31. Run `./scripts/install_web_service.sh` and confirm `sudo systemctl status daddeals.service` shows the website service running.
32. Reboot the Pi and confirm `http://<pi-ip>:5000` still opens from your phone.

## Troubleshooting

If `raspberrypi.local:5000` does not work:

- Make sure the phone and Pi are on the same Wi-Fi network.
- Use `hostname -I` on the Pi and open `http://<pi-ip>:5000` from the phone.
- Confirm the app was started with `python app.py`.
- Confirm `.env` has `HOST=0.0.0.0` and `PORT=5000`.
- Check whether the Pi firewall or router is blocking local device connections.

If the database does not exist:

```bash
python app.py --init-db
```

If login does not work, check `ADMIN_PASSWORD` in `.env`.

If the website service does not start:

- Run `sudo systemctl status daddeals.service`.
- Run `journalctl -u daddeals.service -n 80 --no-pager`.
- Confirm the service file paths match your Pi folder.
- Confirm `pip install -r requirements.txt` was run inside `.venv`.
- Confirm `/home/pi/DadDeals/.venv/bin/gunicorn` exists, or update the path in the service file.
- Confirm `python app.py --init-db` works before using systemd.

If Telegram delivery says it is not configured:

- Confirm `.env` has real `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` values.
- Confirm the values are not still `replace_me_later`.
- Send one message to your bot before using `getUpdates`.
- Run `python worker.py --send-alerts` again after editing `.env`.

If yfinance cannot fetch a ticker:

- Confirm the ticker is valid, such as `AAPL` or `TSLA`.
- Confirm the Raspberry Pi or PC has internet access.
- Try running `python worker.py --dry-run` again.
- Check the stock detail page for the friendly failed-check message.
- Some symbols, funds, or exchanges may need Yahoo Finance-specific ticker formats.

If a product URL cannot be checked:

- Confirm the URL starts with `http://` or `https://`.
- Try the Books to Scrape demo URL first.
- Confirm the Raspberry Pi or PC has internet access.
- Check the product detail page for the friendly failed-check message.
- Remember that some real retail sites block bots or load prices with JavaScript.
- DadDeals does not use Selenium or Playwright in this phase to keep the Pi lightweight.

If an Amazon URL cannot be checked:

- Confirm the URL includes an ASIN, such as `/dp/B08N5WRWNW` or `/gp/product/B08N5WRWNW`.
- Open Settings and confirm Canopy Amazon is enabled if you want automatic Amazon checks.
- Confirm `.env` has a real `CANOPY_API_KEY`.
- Confirm monthly usage has not reached `CANOPY_MONTHLY_LIMIT`.
- Remember that Amazon automatic checks are skipped until the item is due again based on `AMAZON_CHECK_INTERVAL_HOURS`.

## Notes for Later Phases

`worker.py` now uses exact-URL product checks, optional Canopy API checks for Amazon URLs, and real yfinance stock checks. Later phases can add broader product search, recommendations, and more robust per-store handling.

Coming later:

- better per-store product handling
- optional search across multiple stores
- recommendations
- richer alert controls
- more scheduling/deployment polish
