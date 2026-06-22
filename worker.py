"""One-shot simulated worker for DadDeals Phase 1C.

This file deliberately avoids background loops, yfinance, scraping, and
heavyweight dependencies. It reads active rows from SQLite, creates simulated
check history, creates local alert records when simple thresholds are met,
optionally sends unsent alerts through Telegram, prints a summary, and exits.
"""

import argparse
import hashlib
import os
import sqlite3
from datetime import date
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = BASE_DIR / "instance" / "daddeals.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def database_path():
    """Return the DATABASE_PATH from .env/environment, relative to this folder."""
    configured_path = Path(os.environ.get("DATABASE_PATH", str(DEFAULT_DATABASE_PATH)))
    if configured_path.is_absolute():
        return configured_path
    return BASE_DIR / configured_path


def connect_db():
    """Open SQLite and make rows act like dictionaries."""
    db_file = database_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_file)
    db.row_factory = sqlite3.Row
    return db


def init_db(db):
    """Create any missing tables without deleting existing data."""
    with SCHEMA_PATH.open("r", encoding="utf-8") as file:
        db.executescript(file.read())
    migrate_alert_delivery_columns(db)
    db.commit()


def migrate_alert_delivery_columns(db):
    """Add Phase 1C alert delivery columns to older databases."""
    existing_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(alerts)").fetchall()
    }

    if "sent_at" not in existing_columns:
        db.execute("ALTER TABLE alerts ADD COLUMN sent_at TEXT")
    if "delivery_status" not in existing_columns:
        db.execute(
            "ALTER TABLE alerts ADD COLUMN delivery_status TEXT NOT NULL DEFAULT 'unsent'"
        )
    if "delivery_error" not in existing_columns:
        db.execute("ALTER TABLE alerts ADD COLUMN delivery_error TEXT")
    if "delivery_attempts" not in existing_columns:
        db.execute(
            "ALTER TABLE alerts ADD COLUMN delivery_attempts INTEGER NOT NULL DEFAULT 0"
        )


def stable_number(text):
    """Return a repeatable number for the same input text."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def fake_product_price(product):
    """Create a deterministic simulated price near the product target.

    Later phases will replace this with real product checking. For now, the
    value is intentionally predictable so tests and demos are repeatable.
    """
    target_price = product["target_price"]
    base_price = target_price if target_price is not None else 100.0
    offsets = [-0.08, -0.03, 0.02, 0.06]
    offset = offsets[stable_number(f"product:{product['id']}:{product['name']}") % 4]
    return round(base_price * (1 + offset), 2)


def fake_stock_prices(stock):
    """Create deterministic simulated stock prices.

    The percentage choices include drops and rises so Phase 1B can exercise
    alert logic without calling yfinance or any external API.
    """
    target_price = stock["target_price"]
    base_price = target_price if target_price is not None else 100.0
    changes = [-6.5, -3.0, 1.5, 4.0]
    percent_change = changes[stable_number(f"stock:{stock['id']}:{stock['ticker']}") % 4]
    previous_close = round(base_price, 2)
    current_price = round(previous_close * (1 + percent_change / 100), 2)
    return current_price, previous_close, percent_change


def latest_product_price(db, product_id):
    """Return the last recorded product price, or None if there is no history."""
    row = db.execute(
        """
        SELECT current_price
        FROM price_checks
        WHERE product_id = ?
        ORDER BY checked_at DESC, id DESC
        LIMIT 1
        """,
        (product_id,),
    ).fetchone()
    return row["current_price"] if row else None


def latest_stock_close(db, stock_id):
    """Return the last recorded stock price, or None if there is no history."""
    row = db.execute(
        """
        SELECT current_price
        FROM stock_checks
        WHERE stock_id = ?
        ORDER BY checked_at DESC, id DESC
        LIMIT 1
        """,
        (stock_id,),
    ).fetchone()
    return row["current_price"] if row else None


def alert_already_exists_today(db, item_type, item_id, title, message):
    """Avoid creating the exact same alert more than once per day."""
    row = db.execute(
        """
        SELECT id
        FROM alerts
        WHERE item_type = ?
          AND item_id = ?
          AND title = ?
          AND message = ?
          AND date(created_at) = ?
        LIMIT 1
        """,
        (item_type, item_id, title, message, date.today().isoformat()),
    ).fetchone()
    return row is not None


def create_alert(db, item_type, item_id, title, message, dry_run):
    """Create an alert unless this exact alert already exists today."""
    if alert_already_exists_today(db, item_type, item_id, title, message):
        return False

    if dry_run:
        return True

    db.execute(
        """
        INSERT INTO alerts (
            item_type, item_id, title, message, alert_status, delivery_status
        )
        VALUES (?, ?, ?, ?, 'new', 'unsent')
        """,
        (item_type, item_id, title, message),
    )
    return True


def telegram_settings():
    """Read Telegram settings from .env/environment."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if token in {"replace_me_later", "replace_me"}:
        token = ""
    if chat_id in {"replace_me_later", "replace_me"}:
        chat_id = ""
    return token, chat_id


def short_error(message):
    """Keep delivery errors short and safe for storage."""
    return message.replace("\n", " ").strip()[:180]


def send_telegram_message(text):
    """Send one Telegram message with a timeout.

    The bot token is only used inside the request URL. Error messages returned
    from this function are intentionally generic so secrets do not end up in
    the database or dashboard.
    """
    token, chat_id = telegram_settings()
    if not token or not chat_id:
        return False, "Telegram is not configured. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env."

    try:
        import requests
    except ImportError:
        return False, "The requests package is missing. Run pip install -r requirements.txt."

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}

    try:
        response = requests.post(url, data=payload, timeout=10)
    except requests.RequestException:
        return False, "Network error while contacting Telegram."

    if response.status_code != 200:
        return False, f"Telegram API returned HTTP {response.status_code}."

    try:
        data = response.json()
    except ValueError:
        return False, "Telegram returned an invalid response."

    if not data.get("ok"):
        description = data.get("description") or "Telegram rejected the message."
        return False, short_error(description)

    return True, None


def alert_text(alert):
    """Format a simple Telegram message for one alert row."""
    return f"DadDeals alert\n\n{alert['title']}\n{alert['message']}"


def mark_alert_sent(db, alert_id):
    """Mark an alert as delivered."""
    db.execute(
        """
        UPDATE alerts
        SET delivery_status = 'sent',
            sent_at = CURRENT_TIMESTAMP,
            delivery_error = NULL,
            delivery_attempts = delivery_attempts + 1
        WHERE id = ?
        """,
        (alert_id,),
    )


def mark_alert_failed(db, alert_id, error):
    """Mark an alert delivery attempt as failed without exposing secrets."""
    db.execute(
        """
        UPDATE alerts
        SET delivery_status = 'failed',
            delivery_error = ?,
            delivery_attempts = delivery_attempts + 1
        WHERE id = ?
        """,
        (short_error(error), alert_id),
    )


def send_unsent_alerts():
    """Send all alerts that have not been successfully delivered yet."""
    db = connect_db()
    try:
        init_db(db)
        alerts = db.execute(
            """
            SELECT *
            FROM alerts
            WHERE sent_at IS NULL
              AND COALESCE(delivery_status, 'unsent') != 'sent'
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()

        sent_count = 0
        failed_count = 0

        print("DadDeals Telegram delivery starting")
        print(f"Unsent alerts found: {len(alerts)}")

        if not alerts:
            print("No Telegram messages to send.")
            return {"sent": 0, "failed": 0, "total": 0}

        for alert in alerts:
            ok, error = send_telegram_message(alert_text(alert))
            if ok:
                mark_alert_sent(db, alert["id"])
                sent_count += 1
                print(f"  - Sent alert #{alert['id']}: {alert['title']}")
            else:
                mark_alert_failed(db, alert["id"], error)
                failed_count += 1
                print(f"  - Could not send alert #{alert['id']}: {error}")

        db.commit()
        print(f"Telegram sent: {sent_count}")
        print(f"Telegram failed: {failed_count}")
        return {"sent": sent_count, "failed": failed_count, "total": len(alerts)}
    finally:
        db.close()


def check_product(db, product, dry_run):
    """Simulate one product check and return a small result dictionary."""
    current_price = fake_product_price(product)
    previous_price = latest_product_price(db, product["id"])
    if previous_price is None:
        previous_price = round(current_price * 1.12, 2)

    messages = []
    alerts_created = 0
    target_price = product["target_price"]

    if not dry_run:
        db.execute(
            """
            INSERT INTO price_checks (
                product_id, source_name, current_price, previous_price,
                target_price, status, message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product["id"],
                "Phase 1B simulated checker",
                current_price,
                previous_price,
                target_price,
                "ok",
                "Simulated product check completed.",
            ),
        )

    if (
        target_price is not None
        and product["notify_on_target"]
        and current_price <= target_price
    ):
        title = f"Product target hit: {product['name']}"
        message = (
            f"{product['name']} is ${current_price:.2f}, "
            f"at or below the ${target_price:.2f} target."
        )
        if create_alert(db, "product", product["id"], title, message, dry_run):
            alerts_created += 1
            messages.append(message)

    if previous_price and product["big_drop_percent"] is not None:
        drop_percent = ((previous_price - current_price) / previous_price) * 100
        if product["notify_on_big_drop"] and drop_percent >= product["big_drop_percent"]:
            title = f"Product big drop: {product['name']}"
            message = (
                f"{product['name']} dropped {drop_percent:.1f}% "
                f"to ${current_price:.2f}."
            )
            if create_alert(db, "product", product["id"], title, message, dry_run):
                alerts_created += 1
                messages.append(message)

    return {
        "name": product["name"],
        "current_price": current_price,
        "previous_price": previous_price,
        "alerts_created": alerts_created,
        "messages": messages,
    }


def check_stock(db, stock, dry_run):
    """Simulate one stock check and return a small result dictionary."""
    current_price, simulated_previous_close, percent_change = fake_stock_prices(stock)
    previous_close = latest_stock_close(db, stock["id"]) or simulated_previous_close
    if previous_close:
        percent_change = round(((current_price - previous_close) / previous_close) * 100, 2)

    messages = []
    alerts_created = 0
    target_price = stock["target_price"]

    if not dry_run:
        db.execute(
            """
            INSERT INTO stock_checks (
                stock_id, ticker, current_price, previous_close,
                target_price, percent_change, status, message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stock["id"],
                stock["ticker"],
                current_price,
                previous_close,
                target_price,
                percent_change,
                "ok",
                "Simulated stock check completed.",
            ),
        )

    if (
        target_price is not None
        and stock["notify_on_target"]
        and current_price <= target_price
    ):
        title = f"Stock target hit: {stock['ticker']}"
        message = (
            f"{stock['ticker']} is ${current_price:.2f}, "
            f"at or below the ${target_price:.2f} target."
        )
        if create_alert(db, "stock", stock["id"], title, message, dry_run):
            alerts_created += 1
            messages.append(message)

    if stock["daily_drop_percent"] is not None:
        if (
            stock["notify_on_big_drop"]
            and percent_change <= -abs(stock["daily_drop_percent"])
        ):
            title = f"Stock big drop: {stock['ticker']}"
            message = f"{stock['ticker']} moved {percent_change:.1f}% to ${current_price:.2f}."
            if create_alert(db, "stock", stock["id"], title, message, dry_run):
                alerts_created += 1
                messages.append(message)

    return {
        "ticker": stock["ticker"],
        "current_price": current_price,
        "previous_close": previous_close,
        "percent_change": percent_change,
        "alerts_created": alerts_created,
        "messages": messages,
    }


def run_worker(dry_run):
    """Run one simulated checking pass and print a clear summary."""
    db = connect_db()
    try:
        init_db(db)

        products = db.execute(
            "SELECT * FROM tracked_products WHERE status = 'active' ORDER BY id"
        ).fetchall()
        stocks = db.execute(
            "SELECT * FROM tracked_stocks WHERE status = 'active' ORDER BY id"
        ).fetchall()

        product_results = [check_product(db, product, dry_run) for product in products]
        stock_results = [check_stock(db, stock, dry_run) for stock in stocks]

        if not dry_run:
            db.commit()

        total_alerts = sum(result["alerts_created"] for result in product_results)
        total_alerts += sum(result["alerts_created"] for result in stock_results)

        mode = "DRY RUN" if dry_run else "RUN"
        print(f"DadDeals worker {mode} complete")
        print(f"Database: {database_path()}")
        print(f"Products checked: {len(product_results)}")
        for result in product_results:
            print(
                f"  - {result['name']}: ${result['current_price']:.2f} "
                f"(previous ${result['previous_price']:.2f}), "
                f"alerts: {result['alerts_created']}"
            )

        print(f"Stocks checked: {len(stock_results)}")
        for result in stock_results:
            print(
                f"  - {result['ticker']}: ${result['current_price']:.2f} "
                f"({result['percent_change']:.1f}%), "
                f"alerts: {result['alerts_created']}"
            )

        print(f"Alerts {'that would be created' if dry_run else 'created'}: {total_alerts}")
        if dry_run:
            print("Dry run only: no check rows or alert rows were saved.")
    finally:
        db.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Run the DadDeals simulated worker once.")
    parser.add_argument("--dry-run", action="store_true", help="Preview checks without saving.")
    parser.add_argument("--run", action="store_true", help="Save simulated checks and alerts.")
    parser.add_argument("--send-alerts", action="store_true", help="Send unsent Telegram alerts.")
    args = parser.parse_args()

    if args.dry_run and (args.run or args.send_alerts):
        parser.error("--dry-run cannot be combined with --run or --send-alerts.")
    if not args.dry_run and not args.run and not args.send_alerts:
        parser.error("Choose --dry-run, --run, --send-alerts, or --run --send-alerts.")

    return args


def main():
    args = parse_args()
    if args.dry_run:
        run_worker(dry_run=True)
        return

    if args.run:
        run_worker(dry_run=False)

    if args.send_alerts:
        send_unsent_alerts()


if __name__ == "__main__":
    main()
