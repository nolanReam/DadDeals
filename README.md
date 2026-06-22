# DadDeals

DadDeals is a small local Flask dashboard for tracking products and stocks. Phase 1F adds exact-URL product price checking with requests and BeautifulSoup.

This phase does not include search-across-websites, recommendations, product APIs, Amazon-specific automation, Selenium, Playwright, Celery, Redis, Postgres, Docker, systemd web service setup, or Nginx config.

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
|-- scripts/
|-- templates/
|-- static/
`-- instance/
```

The SQLite database belongs in `instance/`. The real `.env` file and database files are ignored by Git.

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

This installs Flask, python-dotenv, requests, yfinance, BeautifulSoup, and lxml.

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
TELEGRAM_BOT_TOKEN=replace_me_later
TELEGRAM_CHAT_ID=replace_me_later
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

This installs Flask, python-dotenv, requests, yfinance, BeautifulSoup, and lxml.

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

## Database Commands

Initialize or update missing tables without deleting data:

```bash
python app.py --init-db
```

There is no reset command in Phase 1F. That is intentional, so a beginner command cannot accidentally wipe saved products, stocks, checks, alerts, or delivery history.

## Worker Commands

Phase 1F uses exact-URL product checks, real yfinance stock data, Telegram delivery, and optional cron scheduling. Multi-site product search and recommendations still come in a later phase.

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

If you run the worker repeatedly on the same day, DadDeals avoids creating the exact same alert over and over. Telegram delivery also skips alerts that already have a `sent_at` value.

Daily rise percent is stored on stocks, but Phase 1F does not send rise alerts yet because the UI does not have a separate "notify on rise" setting. That is future work.

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

Products use exact-URL checks in Phase 1F. Stock checks use yfinance.

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

## Notes for Later Phases

`worker.py` now uses exact-URL product checks and real yfinance stock checks. Later phases can add broader product search, recommendations, and more robust per-store handling.
