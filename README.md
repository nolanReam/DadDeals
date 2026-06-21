# DadDeals

DadDeals is a small local Flask dashboard for tracking products and stocks. Phase 1B adds a safe one-shot worker foundation that creates simulated product and stock check history.

This phase does not include scraping, yfinance, real stock downloads, Telegram sending, cron jobs, infinite background loops, APIs, recommendations, Docker, Redis, Celery, Postgres, Selenium, or Playwright.

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
```

The Telegram values stay as placeholders for now.

Initialize the database:

```powershell
python app.py --init-db
```

This creates the `instance/` folder if needed and creates any missing tables from `schema.sql`. Running it again is safe because the schema uses `CREATE TABLE IF NOT EXISTS`; it does not erase existing data.

Preview the simulated worker without saving anything:

```powershell
python worker.py --dry-run
```

Run one simulated worker pass and save check history plus alerts:

```powershell
python worker.py --run
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

Preview the simulated worker without saving anything:

```bash
python worker.py --dry-run
```

Run one simulated worker pass and save check history plus alerts:

```bash
python worker.py --run
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

There is no reset command in Phase 1B. That is intentional, so a beginner command cannot accidentally wipe saved products, stocks, checks, or alerts.

## Worker Commands

Phase 1B uses simulated data only. The worker does not scrape product pages, does not call yfinance, and does not send Telegram messages. Real product checking and real stock checking come in later phases.

Preview one worker pass without saving rows:

```bash
python worker.py --dry-run
```

Run one worker pass and save rows:

```bash
python worker.py --run
```

The worker runs once and exits. It reads active products and stocks from SQLite, inserts rows into `price_checks` and `stock_checks`, and creates local rows in `alerts` when simulated values meet your saved thresholds.

To view worker results:

1. Run `python worker.py --run`.
2. Start the web app with `python app.py`.
3. Open the dashboard to see recent alerts.
4. Open a product detail page to see recent price checks.
5. Open a stock detail page to see recent stock checks.

If you run the worker repeatedly on the same day, DadDeals avoids creating the exact same alert over and over.

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
18. Run `python worker.py --run` and confirm it prints a summary with saved checks.
19. Refresh the dashboard and confirm recent alerts appear if simulated thresholds were met.
20. Open product and stock detail pages and confirm recent check history appears.

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

## Notes for Later Phases

`worker.py` is a simulated foundation in Phase 1B. Later phases can replace the fake check values with real product checks, yfinance stock checks, Telegram alerts, and scheduled background work.
