# DadDeals

DadDeals is a small local Flask dashboard for tracking products and stocks. Phase 1D adds real stock checks with yfinance while product checks remain simulated.

This phase does not include cron jobs, real product scraping, product APIs, recommendations, Docker, Redis, Celery, Postgres, Selenium, or Playwright.

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

This installs Flask, python-dotenv, requests, and yfinance.

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

This installs Flask, python-dotenv, requests, and yfinance.

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

There is no reset command in Phase 1D. That is intentional, so a beginner command cannot accidentally wipe saved products, stocks, checks, alerts, or delivery history.

## Worker Commands

Phase 1D uses simulated product data and real yfinance stock data. Real product checking still comes in a later phase.

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

Daily rise percent is stored on stocks, but Phase 1D does not send rise alerts yet because the UI does not have a separate “notify on rise” setting. That is future work.

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

Products are still simulated in Phase 1D. Only stock checks use yfinance.

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

Cron and automatic scheduling come in a later phase. For now, run the worker command manually when you want checks or deliveries.

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
22. Add an invalid ticker and confirm the worker records a failed stock check without crashing.
23. Run `python worker.py --send-alerts` without Telegram settings and confirm it fails gracefully.
24. Add real Telegram settings to `.env`, run `python worker.py --send-alerts`, and confirm sent alerts show as sent on the dashboard.

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

## Notes for Later Phases

`worker.py` now uses real yfinance stock checks and simulated product checks. Later phases can replace the fake product values with real product checks and add scheduled background work.
