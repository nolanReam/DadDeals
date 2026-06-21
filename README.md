# DadDeals

DadDeals is a small local Flask dashboard for tracking products and stocks. Phase 1A is intentionally simple: Flask pages, Jinja templates, SQLite tables, and basic create/edit/delete screens.

This phase does not include scraping, stock downloads, Telegram sending, cron jobs, background workers, APIs, recommendations, Docker, Redis, Celery, Postgres, Selenium, or Playwright.

## Project Structure

```text
DadDeals/
├── app.py
├── worker.py
├── schema.sql
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── templates/
├── static/
└── instance/
```

The SQLite database belongs in `instance/`. The `.gitignore` file prevents local database files and the real `.env` file from being committed.

## Setup on a PC

Open a terminal in the DadDeals folder.

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Activate it on macOS, Linux, or Raspberry Pi OS:

```bash
source .venv/bin/activate
```

Install requirements:

```bash
pip install -r requirements.txt
```

Create your real local `.env` file from the example:

```bash
cp .env.example .env
```

On Windows PowerShell, use:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and replace these values:

```text
APP_SECRET_KEY=use_a_long_random_value_here
ADMIN_PASSWORD=choose_a_password_for_the_dashboard
DATABASE_PATH=instance/daddash.db
```

The Telegram values stay as placeholders for now.

## Initialize the Database

The app can create the database automatically the first time it runs. You can also initialize it yourself:

```bash
flask --app app init-db
```

This creates the SQLite database at the path from `.env`, usually `instance/daddash.db`.

## Run Locally on a PC

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

Log in with the `ADMIN_PASSWORD` from your `.env` file.

## Run on Raspberry Pi

On the Raspberry Pi, activate the virtual environment and run:

```bash
flask --app app run --host 0.0.0.0
```

From a phone on the same Wi-Fi network, open:

```text
http://raspberrypi.local:5000
```

If that address does not open, use the Pi's IP address instead, such as:

```text
http://192.168.1.50:5000
```

## Manual Test Steps

1. Start the app with `python app.py`.
2. Open `http://127.0.0.1:5000`.
3. Confirm the login page appears.
4. Log in with the password from `.env`.
5. Confirm the dashboard shows products, stocks, and recent alerts sections.
6. Add a product with a name, URL, target price, and big drop percent.
7. Open the product detail page and confirm the placeholders are visible.
8. Edit the product, pause it, resume it, and then delete it.
9. Add a stock with company name, ticker, target price, daily drop percent, and daily rise percent.
10. Open the stock detail page and confirm the future price history placeholder is visible.
11. Edit the stock, pause it, resume it, and then delete it.
12. Log out and confirm the dashboard is protected.

## Notes for Later Phases

`worker.py` is only a placeholder in Phase 1A. Later phases can add scraping, stock checks, Telegram alerts, and scheduled background work there.
