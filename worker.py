"""One-shot worker for DadDeals Phase 2A.

This file deliberately avoids background loops, browser automation, and
heavyweight job systems. It reads active rows from SQLite, checks exact product
URLs with requests and BeautifulSoup, checks stocks with yfinance, creates local
alert records when thresholds are met, optionally sends unsent alerts through
Telegram, prints a summary, and exits.
"""

import argparse
import io
import os
import re
import sqlite3
import sys
import warnings
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv


load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = BASE_DIR / "instance" / "daddeals.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"
CANOPY_PROVIDER = "canopy"
CANOPY_AMAZON_SOURCE = "Amazon via Canopy"
AMAZON_MANUAL_MESSAGE = (
    "Amazon automatic checks need Canopy enabled and available monthly requests. "
    "Open the product page manually."
)


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
    migrate_price_check_columns(db)
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


def migrate_price_check_columns(db):
    """Add Phase 1G.1 source URL storage to older databases."""
    existing_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(price_checks)").fetchall()
    }

    if "source_url" not in existing_columns:
        db.execute("ALTER TABLE price_checks ADD COLUMN source_url TEXT")

    db.execute(
        """
        UPDATE price_checks
        SET source_url = (
            SELECT tracked_products.url
            FROM tracked_products
            WHERE tracked_products.id = price_checks.product_id
        )
        WHERE source_url IS NULL
        """
    )


class ProductFetchError(Exception):
    """Friendly error for one product URL failure."""


def product_source_name(url):
    """Return a short source label for a product URL."""
    host = urlparse(url).netloc
    return host or "Exact URL checker"


def is_amazon_url(url):
    """Return True for common Amazon shopping domains."""
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return (
        host == "amazon.com"
        or host.startswith("amazon.")
        or host.endswith(".amazon.com")
        or ".amazon." in host
    )


def extract_amazon_asin(url):
    """Extract an ASIN from common Amazon URL paths.

    Supported examples:
    - /dp/B08N5WRWNW
    - /gp/product/B08N5WRWNW
    - /product/B08N5WRWNW
    Extra path text and query strings are ignored by urlparse.
    """
    parsed = urlparse(url)
    path_parts = [
        unquote(part).strip()
        for part in parsed.path.split("/")
        if unquote(part).strip()
    ]
    asin_pattern = re.compile(r"^[A-Z0-9]{10}$", re.IGNORECASE)

    for marker in ("dp", "product"):
        if marker in [part.lower() for part in path_parts]:
            for index, part in enumerate(path_parts):
                if part.lower() == marker and index + 1 < len(path_parts):
                    candidate = path_parts[index + 1].upper()
                    if asin_pattern.match(candidate):
                        return candidate

    for index, part in enumerate(path_parts):
        if part.lower() == "gp" and index + 2 < len(path_parts):
            if path_parts[index + 1].lower() == "product":
                candidate = path_parts[index + 2].upper()
                if asin_pattern.match(candidate):
                    return candidate

    for part in path_parts:
        candidate = part.upper()
        if asin_pattern.match(candidate):
            return candidate

    return None


def parse_price_text(text):
    """Convert common price text into a float.

    Handles examples such as "$199.99", "USD 199.99", "£51.77", and
    "1,299.00". This is intentionally conservative because product pages vary.
    """
    if not text:
        return None

    normalized = " ".join(str(text).split())
    pattern = r"(?:USD|US\$|[$£€])?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)"
    for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
        raw_number = match.group(1).replace(",", "")
        try:
            price = float(raw_number)
        except ValueError:
            continue
        if price > 0:
            return round(price, 2)

    return None


def soup_from_html(html):
    """Parse HTML with lxml when available, falling back to html.parser."""
    from bs4 import BeautifulSoup, FeatureNotFound

    try:
        return BeautifulSoup(html, "lxml")
    except FeatureNotFound:
        return BeautifulSoup(html, "html.parser")


def candidate_price_texts(soup):
    """Yield likely price strings from common product-page patterns."""
    selectors = [
        'meta[property="product:price:amount"]',
        'meta[itemprop="price"]',
        '[itemprop="price"]',
    ]

    for selector in selectors:
        for element in soup.select(selector):
            content = element.get("content") or element.get("value")
            text = content or element.get_text(" ", strip=True)
            if text:
                yield text

    def has_price_word(value):
        if not value:
            return False
        if isinstance(value, (list, tuple)):
            value = " ".join(value)
        value = str(value).lower()
        return "price" in value

    for element in soup.find_all(attrs={"class": has_price_word}):
        text = element.get_text(" ", strip=True)
        if text:
            yield text

    for element in soup.find_all(attrs={"id": has_price_word}):
        text = element.get_text(" ", strip=True)
        if text:
            yield text


def extract_price_from_html(html):
    """Find the first plausible product price in an HTML document."""
    soup = soup_from_html(html)
    for text in candidate_price_texts(soup):
        price = parse_price_text(text)
        if price is not None:
            return price
    return None


def fetch_product_price(url):
    """Fetch one exact product URL and extract a product price."""
    try:
        import requests
    except ImportError as error:
        raise ProductFetchError(
            "The requests package is missing. Run pip install -r requirements.txt."
        ) from error

    headers = {
        "User-Agent": (
            "DadDeals/1.0 personal exact-url price checker "
            "(requests + BeautifulSoup)"
        )
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
    except requests.Timeout as error:
        raise ProductFetchError("The product page timed out.") from error
    except requests.RequestException as error:
        raise ProductFetchError("Could not fetch the product page.") from error

    if response.status_code >= 400:
        raise ProductFetchError(
            f"Product page returned HTTP {response.status_code}. Some sites block automated checks."
        )

    price = extract_price_from_html(response.text)
    if price is None:
        raise ProductFetchError("No product price was found on the page.")

    return price


def env_flag(name, default=False):
    """Read a simple true/false setting from .env/environment."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    """Read a positive integer setting with a beginner-safe fallback."""
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(value, 0)


def current_usage_month():
    """Calendar month key used for lightweight API budget tracking."""
    return datetime.now().strftime("%Y-%m")


def canopy_settings():
    """Read Canopy settings without ever printing the API key."""
    api_key = os.environ.get("CANOPY_API_KEY", "").strip()
    if api_key in {"replace_me", "replace_me_later"}:
        api_key = ""
    return {
        "enabled": env_flag("ENABLE_CANOPY_AMAZON", False),
        "api_key": api_key,
        "monthly_limit": env_int("CANOPY_MONTHLY_LIMIT", 100),
        "auth_header": os.environ.get("CANOPY_AUTH_HEADER", "API-KEY").strip() or "API-KEY",
        "interval_hours": env_int("AMAZON_CHECK_INTERVAL_HOURS", 24),
    }


def canopy_headers(settings):
    """Build the Canopy authentication header from CANOPY_AUTH_HEADER."""
    mode = settings["auth_header"].strip()
    if mode.lower() == "authorization":
        return {"Authorization": f"Bearer {settings['api_key']}"}
    return {mode: settings["api_key"]}


def api_usage_count(db, provider):
    """Return this month's request count for one API provider."""
    row = db.execute(
        """
        SELECT request_count
        FROM api_usage
        WHERE provider = ?
          AND usage_month = ?
        """,
        (provider, current_usage_month()),
    ).fetchone()
    return row["request_count"] if row else 0


def increment_api_usage(db, provider):
    """Count one actual external API request without losing existing rows."""
    db.execute(
        """
        INSERT INTO api_usage (provider, usage_month, request_count, last_request_at)
        VALUES (?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(provider, usage_month)
        DO UPDATE SET
            request_count = request_count + 1,
            last_request_at = CURRENT_TIMESTAMP
        """,
        (provider, current_usage_month()),
    )


def amazon_due_status(db, product_id, interval_hours):
    """Return whether an Amazon item is due for another Canopy attempt."""
    if interval_hours <= 0:
        return True, "Amazon check is due because the interval is 0 hours."

    row = db.execute(
        """
        SELECT checked_at,
               (julianday('now') - julianday(checked_at)) * 24.0 AS age_hours
        FROM price_checks
        WHERE product_id = ?
          AND source_name = ?
        ORDER BY checked_at DESC, id DESC
        LIMIT 1
        """,
        (product_id, CANOPY_AMAZON_SOURCE),
    ).fetchone()
    if row is None:
        return True, "Amazon check is due because it has not been checked yet."

    age_hours = row["age_hours"] if row["age_hours"] is not None else interval_hours
    if age_hours >= interval_hours:
        return True, f"Amazon check is due; last attempt was {age_hours:.1f} hour(s) ago."
    return (
        False,
        f"Amazon check is not due yet; last attempt was {age_hours:.1f} hour(s) ago "
        f"and the interval is {interval_hours} hour(s).",
    )


def nested_value(data, names):
    """Find the first matching key inside a nested Canopy JSON response."""
    wanted = {name.lower() for name in names}
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).lower() in wanted and value not in (None, ""):
                return value
        for value in data.values():
            found = nested_value(value, names)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for value in data:
            found = nested_value(value, names)
            if found not in (None, ""):
                return found
    return None


def price_from_canopy_value(value):
    """Convert a Canopy price field into a float when possible."""
    if isinstance(value, (int, float)):
        return round(float(value), 2) if value > 0 else None
    if isinstance(value, str):
        return parse_price_text(value)
    if isinstance(value, dict):
        for key in ("value", "amount", "price", "display", "text", "raw"):
            price = price_from_canopy_value(value.get(key))
            if price is not None:
                return price
    return None


def parse_canopy_product(data, asin, fallback_url):
    """Pull the small set of fields DadDeals needs from Canopy JSON."""
    price_value = nested_value(
        data,
        (
            "price",
            "current_price",
            "currentPrice",
            "buybox_price",
            "buyboxPrice",
            "sale_price",
            "salePrice",
            "deal_price",
            "dealPrice",
            "price_raw",
        ),
    )
    current_price = price_from_canopy_value(price_value)
    if current_price is None:
        raise ProductFetchError("Canopy returned product data, but no price was found.")

    title = nested_value(data, ("title", "name", "product_title", "productTitle"))
    availability = nested_value(
        data,
        ("availability", "availability_status", "availabilityStatus", "stock_status"),
    )
    product_link = nested_value(
        data,
        ("url", "link", "product_url", "productUrl", "canonical_url", "canonicalUrl"),
    )
    source_url = str(product_link or fallback_url)
    message_parts = [f"Amazon Canopy check completed for ASIN {asin}."]
    if title:
        message_parts.append(f"Title: {title}.")
    if availability:
        message_parts.append(f"Availability: {availability}.")
    return {
        "current_price": current_price,
        "source_url": source_url,
        "message": " ".join(message_parts),
    }


def fetch_canopy_amazon_product(asin, settings, fallback_url):
    """Fetch one Amazon product from Canopy's REST product endpoint."""
    try:
        import requests
    except ImportError as error:
        raise ProductFetchError(
            "The requests package is missing. Run pip install -r requirements.txt."
        ) from error

    try:
        response = requests.get(
            "https://rest.canopyapi.co/api/amazon/product",
            params={"asin": asin, "domain": "US"},
            headers=canopy_headers(settings),
            timeout=20,
        )
    except requests.Timeout as error:
        raise ProductFetchError("Canopy Amazon check timed out.") from error
    except requests.RequestException as error:
        raise ProductFetchError("Could not reach Canopy for the Amazon check.") from error

    if response.status_code >= 400:
        raise ProductFetchError(
            f"Canopy returned HTTP {response.status_code} for the Amazon check."
        )

    try:
        data = response.json()
    except ValueError as error:
        raise ProductFetchError("Canopy returned a response DadDeals could not read.") from error

    return parse_canopy_product(data, asin, fallback_url)


def clean_ticker(ticker):
    """Normalize ticker text before sending it to yfinance."""
    return ticker.strip().upper()


def safe_float(value):
    """Convert a yfinance/pandas value to a normal float or None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    # NaN is the one float value that does not equal itself.
    if number != number:
        return None
    return number


class StockFetchError(Exception):
    """Friendly error for one ticker failure."""


def fetch_stock_prices(ticker):
    """Fetch latest daily close and previous close from yfinance.

    Phase 1D intentionally keeps this simple. yfinance gives us a small daily
    price history, and DadDeals uses the latest close as the current/latest
    price. If markets are open, this may be delayed until yfinance updates.
    """
    try:
        import yfinance as yf
    except ImportError as error:
        raise StockFetchError(
            "yfinance is not installed. Run pip install -r requirements.txt."
        ) from error

    symbol = clean_ticker(ticker)
    if not symbol:
        raise StockFetchError("Ticker is blank.")

    try:
        ticker_data = yf.Ticker(symbol)
        captured_output = io.StringIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with redirect_stdout(captured_output), redirect_stderr(captured_output):
                history = ticker_data.history(
                    period="5d",
                    interval="1d",
                    auto_adjust=False,
                    timeout=10,
                )
    except Exception as error:
        raise StockFetchError("Could not reach yfinance for this ticker.") from error

    if history is None or history.empty or "Close" not in history:
        raise StockFetchError("No recent price data came back from yfinance.")

    closes = history["Close"].dropna()
    if closes.empty:
        raise StockFetchError("No closing prices came back from yfinance.")

    current_price = safe_float(closes.iloc[-1])
    if current_price is None:
        raise StockFetchError("The latest price was not a valid number.")

    if len(closes) >= 2:
        previous_close = safe_float(closes.iloc[-2])
    else:
        previous_close = current_price

    if previous_close is None or previous_close <= 0:
        raise StockFetchError("Previous close was not available.")

    percent_change = round(((current_price - previous_close) / previous_close) * 100, 2)
    return round(current_price, 2), round(previous_close, 2), percent_change


def latest_product_price(db, product_id):
    """Return the last successful product price, or None if there is no history."""
    row = db.execute(
        """
        SELECT current_price
        FROM price_checks
        WHERE product_id = ?
          AND status = 'ok'
          AND current_price IS NOT NULL
        ORDER BY checked_at DESC, id DESC
        LIMIT 1
        """,
        (product_id,),
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
          AND date(created_at) = date('now')
        LIMIT 1
        """,
        (item_type, item_id, title, message),
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


def target_alert_message(label, current_price, target_price):
    """Build friendly wording for a target-price alert."""
    if round(current_price, 2) == round(target_price, 2):
        return f"{label} is exactly at your ${target_price:.2f} target."

    difference = target_price - current_price
    return (
        f"{label} is ${current_price:.2f}, which is "
        f"${difference:.2f} below your ${target_price:.2f} target."
    )


def product_source_alert_message(message, source_url):
    """Add the exact product page to stored product alert text."""
    return f"{message}\nSource: {source_url}"


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


def test_telegram():
    """Send one direct Telegram test message without creating an alert row."""
    ok, error = send_telegram_message("DadDeals Telegram test message.")
    if ok:
        print("Telegram test message sent.")
        return True

    print(f"Telegram test failed: {error}")
    return False


def save_product_check(
    db,
    product,
    source_name,
    source_url,
    current_price,
    previous_price,
    target_price,
    status,
    message,
):
    """Insert one product check row."""
    db.execute(
        """
        INSERT INTO price_checks (
            product_id, source_name, source_url, current_price, previous_price,
            target_price, status, message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product["id"],
            source_name,
            source_url,
            current_price,
            previous_price,
            target_price,
            status,
            message,
        ),
    )


def check_product_alerts(db, product, current_price, previous_price, target_price, source_url, dry_run):
    """Create product target/drop alerts for a successful price check."""
    messages = []
    alerts_created = 0

    if (
        target_price is not None
        and product["notify_on_target"]
        and current_price <= target_price
    ):
        title = f"Product target hit: {product['name']}"
        message = product_source_alert_message(
            target_alert_message(product["name"], current_price, target_price),
            source_url,
        )
        if create_alert(db, "product", product["id"], title, message, dry_run):
            alerts_created += 1
            messages.append(message)

    if previous_price is not None and product["big_drop_percent"] is not None:
        drop_percent = ((previous_price - current_price) / previous_price) * 100
        if product["notify_on_big_drop"] and drop_percent >= product["big_drop_percent"]:
            title = f"Product big drop: {product['name']}"
            message = (
                f"{product['name']} dropped {drop_percent:.1f}% "
                f"to ${current_price:.2f}."
            )
            message = product_source_alert_message(message, source_url)
            if create_alert(db, "product", product["id"], title, message, dry_run):
                alerts_created += 1
                messages.append(message)

    return alerts_created, messages


def product_result(name, current_price, previous_price, alerts_created, messages, status):
    """Build the small summary object printed by run_worker."""
    return {
        "name": name,
        "current_price": current_price,
        "previous_price": previous_price,
        "alerts_created": alerts_created,
        "messages": messages,
        "status": status,
    }


def check_amazon_product(db, product, dry_run):
    """Check an Amazon product with optional Canopy support."""
    current_price = None
    previous_price = latest_product_price(db, product["id"])
    target_price = product["target_price"]
    asin = extract_amazon_asin(product["url"])

    if not asin:
        message = "Amazon ASIN could not be found in the URL. Open the product page manually."
        if not dry_run:
            save_product_check(
                db,
                product,
                CANOPY_AMAZON_SOURCE,
                product["url"],
                current_price,
                previous_price,
                target_price,
                "failed",
                message,
            )
        return product_result(product["name"], current_price, previous_price, 0, [message], "failed")

    settings = canopy_settings()
    due, due_message = amazon_due_status(db, product["id"], settings["interval_hours"])

    if not due:
        message = f"{due_message} ASIN: {asin}."
        return product_result(product["name"], current_price, previous_price, 0, [message], "skipped")

    usage_count = api_usage_count(db, CANOPY_PROVIDER)
    ready = settings["enabled"] and settings["api_key"] and usage_count < settings["monthly_limit"]
    if not ready:
        if not settings["enabled"]:
            reason = "Canopy Amazon is disabled."
        elif not settings["api_key"]:
            reason = "Canopy API key is missing."
        else:
            reason = f"Canopy monthly limit reached ({usage_count}/{settings['monthly_limit']})."
        message = f"{AMAZON_MANUAL_MESSAGE} {reason} ASIN: {asin}."
        if not dry_run:
            save_product_check(
                db,
                product,
                CANOPY_AMAZON_SOURCE,
                product["url"],
                current_price,
                previous_price,
                target_price,
                "skipped",
                message,
            )
        return product_result(product["name"], current_price, previous_price, 0, [message], "skipped")

    if dry_run:
        message = (
            f"Amazon Canopy check is due for ASIN {asin}. "
            "Dry run only: no Canopy request, check row, alert, or API usage was saved."
        )
        return product_result(product["name"], current_price, previous_price, 0, [message], "skipped")

    try:
        increment_api_usage(db, CANOPY_PROVIDER)
        canopy_product = fetch_canopy_amazon_product(asin, settings, product["url"])
        current_price = canopy_product["current_price"]
        source_url = canopy_product["source_url"]
        check_message = canopy_product["message"]
        status = "ok"
    except ProductFetchError as error:
        message = str(error)
        save_product_check(
            db,
            product,
            CANOPY_AMAZON_SOURCE,
            product["url"],
            current_price,
            previous_price,
            target_price,
            "failed",
            message,
        )
        return product_result(product["name"], current_price, previous_price, 0, [message], "failed")

    save_product_check(
        db,
        product,
        CANOPY_AMAZON_SOURCE,
        source_url,
        current_price,
        previous_price,
        target_price,
        status,
        check_message,
    )
    alerts_created, messages = check_product_alerts(
        db, product, current_price, previous_price, target_price, source_url, dry_run
    )
    return product_result(
        product["name"],
        current_price,
        previous_price,
        alerts_created,
        messages or [check_message],
        status,
    )


def check_product(db, product, dry_run):
    """Route Amazon URLs to Canopy support and other URLs to the exact checker."""
    if is_amazon_url(product["url"]):
        return check_amazon_product(db, product, dry_run)
    return check_exact_product(db, product, dry_run)


def check_exact_product(db, product, dry_run):
    """Fetch and store one exact-URL product check."""
    current_price = None
    previous_price = latest_product_price(db, product["id"])
    target_price = product["target_price"]

    try:
        current_price = fetch_product_price(product["url"])
        status = "ok"
        check_message = "Exact URL product check completed."
    except ProductFetchError as error:
        status = "failed"
        check_message = str(error)

        if not dry_run:
            save_product_check(
                db,
                product,
                product_source_name(product["url"]),
                product["url"],
                current_price,
                previous_price,
                target_price,
                status,
                check_message,
            )

        return product_result(product["name"], current_price, previous_price, 0, [check_message], status)

    if not dry_run:
        save_product_check(
            db,
            product,
            product_source_name(product["url"]),
            product["url"],
            current_price,
            previous_price,
            target_price,
            status,
            check_message,
        )

    alerts_created, messages = check_product_alerts(
        db, product, current_price, previous_price, target_price, product["url"], dry_run
    )
    return product_result(
        product["name"],
        current_price,
        previous_price,
        alerts_created,
        messages,
        status,
    )


def check_stock(db, stock, dry_run):
    """Fetch and store one real yfinance stock check."""
    ticker = clean_ticker(stock["ticker"])
    messages = []
    alerts_created = 0
    target_price = stock["target_price"]

    try:
        current_price, previous_close, percent_change = fetch_stock_prices(ticker)
        status = "ok"
        check_message = "Real stock check completed with yfinance."
    except StockFetchError as error:
        current_price = None
        previous_close = None
        percent_change = None
        status = "failed"
        check_message = str(error)

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
                    ticker,
                    current_price,
                    previous_close,
                    target_price,
                    percent_change,
                    status,
                    check_message,
                ),
            )

        return {
            "ticker": ticker,
            "current_price": current_price,
            "previous_close": previous_close,
            "percent_change": percent_change,
            "alerts_created": 0,
            "messages": [check_message],
            "status": status,
        }

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
                ticker,
                current_price,
                previous_close,
                target_price,
                percent_change,
                status,
                check_message,
            ),
        )

    if (
        target_price is not None
        and stock["notify_on_target"]
        and current_price <= target_price
    ):
        title = f"Stock target hit: {stock['ticker']}"
        message = target_alert_message(stock["ticker"], current_price, target_price)
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
        "ticker": ticker,
        "current_price": current_price,
        "previous_close": previous_close,
        "percent_change": percent_change,
        "alerts_created": alerts_created,
        "messages": messages,
        "status": status,
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
            if result["status"] == "ok":
                previous_text = (
                    f"${result['previous_price']:.2f}"
                    if result["previous_price"] is not None
                    else "none"
                )
                print(
                    f"  - {result['name']}: ${result['current_price']:.2f} "
                    f"(previous {previous_text}), "
                    f"alerts: {result['alerts_created']}"
                )
            elif result["status"] == "skipped":
                print(
                    f"  - {result['name']}: skipped - "
                    f"{result['messages'][0]}"
                )
            else:
                print(
                    f"  - {result['name']}: check failed - "
                    f"{result['messages'][0]}"
                )

        print(f"Stocks checked with yfinance: {len(stock_results)}")
        for result in stock_results:
            if result["status"] == "ok":
                print(
                    f"  - {result['ticker']}: ${result['current_price']:.2f} "
                    f"({result['percent_change']:.1f}%), "
                    f"alerts: {result['alerts_created']}"
                )
            else:
                print(
                    f"  - {result['ticker']}: check failed - "
                    f"{result['messages'][0]}"
                )

        print(f"Alerts {'that would be created' if dry_run else 'created'}: {total_alerts}")
        if dry_run:
            print("Dry run only: no check rows or alert rows were saved.")
    finally:
        db.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Run the DadDeals simulated worker once.")
    parser.add_argument("--dry-run", action="store_true", help="Preview checks without saving.")
    parser.add_argument("--run", action="store_true", help="Save product/stock checks and alerts.")
    parser.add_argument("--send-alerts", action="store_true", help="Send unsent Telegram alerts.")
    parser.add_argument("--test-telegram", action="store_true", help="Send one Telegram test message.")
    args = parser.parse_args()

    if args.dry_run and (args.run or args.send_alerts or args.test_telegram):
        parser.error("--dry-run cannot be combined with other worker actions.")
    if args.test_telegram and (args.run or args.send_alerts):
        parser.error("--test-telegram must be run by itself.")
    if not args.dry_run and not args.run and not args.send_alerts and not args.test_telegram:
        parser.error(
            "Choose --dry-run, --run, --send-alerts, --run --send-alerts, or --test-telegram."
        )

    return args


def main():
    args = parse_args()
    if args.dry_run:
        run_worker(dry_run=True)
        return

    if args.test_telegram:
        sys.exit(0 if test_telegram() else 1)

    if args.run:
        run_worker(dry_run=False)

    if args.send_alerts:
        send_unsent_alerts()


if __name__ == "__main__":
    main()
