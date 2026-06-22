"""One-shot worker for DadDeals Phase 2A.

This file deliberately avoids background loops, browser automation, and
heavyweight job systems. It reads active rows from SQLite, checks exact product
URLs with requests and BeautifulSoup, checks stocks with yfinance, creates local
alert records when thresholds are met, optionally sends unsent alerts through
Telegram, prints a summary, and exits.
"""

import argparse
import io
import json
import os
import re
import sqlite3
import sys
import warnings
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = BASE_DIR / "instance" / "daddeals.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"
CANOPY_PROVIDER = "canopy"
CRAWLBASE_PROVIDER = "crawlbase"
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
    migrate_product_crawlbase_columns(db)
    migrate_product_schedule_columns(db)
    migrate_stock_schedule_columns(db)
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


def migrate_product_crawlbase_columns(db):
    """Add Phase 2F product Crawlbase columns to older databases."""
    existing_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(tracked_products)").fetchall()
    }
    if "allow_crawlbase_fallback" not in existing_columns:
        db.execute(
            "ALTER TABLE tracked_products ADD COLUMN allow_crawlbase_fallback INTEGER NOT NULL DEFAULT 0"
        )
    if "prefer_crawlbase" not in existing_columns:
        db.execute(
            "ALTER TABLE tracked_products ADD COLUMN prefer_crawlbase INTEGER NOT NULL DEFAULT 0"
        )
    if "last_check_method" not in existing_columns:
        db.execute("ALTER TABLE tracked_products ADD COLUMN last_check_method TEXT")
    if "last_detected_store" not in existing_columns:
        db.execute("ALTER TABLE tracked_products ADD COLUMN last_detected_store TEXT")


def migrate_product_schedule_columns(db):
    """Add Phase 2G per-product schedule and notification settings."""
    existing_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(tracked_products)").fetchall()
    }
    if "product_check_interval_hours" not in existing_columns:
        db.execute(
            "ALTER TABLE tracked_products ADD COLUMN product_check_interval_hours INTEGER NOT NULL DEFAULT 24"
        )
    if "product_notify_cooldown_hours" not in existing_columns:
        db.execute(
            "ALTER TABLE tracked_products ADD COLUMN product_notify_cooldown_hours INTEGER NOT NULL DEFAULT 72"
        )


def migrate_stock_schedule_columns(db):
    """Add Phase 2G per-stock check interval settings."""
    existing_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(tracked_stocks)").fetchall()
    }
    if "stock_check_interval_minutes" not in existing_columns:
        db.execute(
            "ALTER TABLE tracked_stocks ADD COLUMN stock_check_interval_minutes INTEGER NOT NULL DEFAULT 5"
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
        response = requests.get(url, headers=headers, timeout=10)
    except requests.Timeout as error:
        raise ProductFetchError("The product page timed out.") from error
    except requests.RequestException as error:
        raise ProductFetchError("Could not fetch the product page.") from error

    if response.status_code >= 400:
        raise ProductFetchError(
            f"Product page returned HTTP {response.status_code}. Some sites block automated checks."
        )

    parsed = parse_generic_product_page(response.text, url)
    price = parsed.get("current_price")
    if price is None:
        raise ProductFetchError("No product price was found on the page.")

    return price


def source_label_for_url(url):
    """Detect a known retailer source from a product URL."""
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host == "bestbuy.com" or host.endswith(".bestbuy.com"):
        return "Best Buy"
    if host == "newegg.com" or host.endswith(".newegg.com"):
        return "Newegg"
    if host == "walmart.com" or host.endswith(".walmart.com"):
        return "Walmart"
    if host == "target.com" or host.endswith(".target.com"):
        return "Target"
    if host == "homedepot.com" or host.endswith(".homedepot.com"):
        return "Home Depot"
    if is_amazon_url(url):
        return "Amazon"
    return product_source_name(url)


def is_best_buy_url(url):
    """Return True when the URL points at Best Buy."""
    return source_label_for_url(url) == "Best Buy"


def is_newegg_url(url):
    """Return True when the URL points at Newegg."""
    return source_label_for_url(url) == "Newegg"


def is_walmart_url(url):
    """Return True when the URL points at Walmart."""
    return source_label_for_url(url) == "Walmart"


def is_target_url(url):
    """Return True when the URL points at Target."""
    return source_label_for_url(url) == "Target"


def is_home_depot_url(url):
    """Return True when the URL points at Home Depot."""
    return source_label_for_url(url) == "Home Depot"


def extract_best_buy_sku(url):
    """Extract a Best Buy SKU from common product URL shapes."""
    parsed = urlparse(url)
    query_match = re.search(r"(?:^|&)skuId=([0-9A-Za-z_-]+)", parsed.query)
    if query_match:
        return query_match.group(1)

    path_match = re.search(r"/([0-9]{4,})\.p(?:/)?$", parsed.path)
    if path_match:
        return path_match.group(1)

    any_path_match = re.search(r"/([0-9]{4,})\.p(?:/|$)", parsed.path)
    if any_path_match:
        return any_path_match.group(1)

    return None


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


def local_timezone():
    """Return the configured display timezone with a safe default."""
    timezone_name = os.environ.get("APP_TIMEZONE", "America/Los_Angeles").strip()
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("America/Los_Angeles")


def format_local_time(value=None):
    """Format a timestamp for Telegram text using DadDeals display timezone."""
    if value is None:
        timestamp = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        timestamp = value
    else:
        text = str(value).strip()
        try:
            timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                timestamp = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return text

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    local_timestamp = timestamp.astimezone(local_timezone())
    return (
        f"{local_timestamp:%b} {local_timestamp.day}, "
        f"{local_timestamp.year}, {local_timestamp:%I:%M %p}"
    )


def current_usage_month():
    """Calendar month key used for lightweight API budget tracking."""
    return datetime.now().strftime("%Y-%m")


def current_usage_day():
    """Calendar day key used for Crawlbase diagnostic request tracking."""
    return datetime.now().strftime("%Y-%m-%d")


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


def crawlbase_settings():
    """Read Crawlbase settings without exposing tokens."""
    normal_token = os.environ.get("CRAWLBASE_NORMAL_TOKEN", "").strip()
    js_token = os.environ.get("CRAWLBASE_JS_TOKEN", "").strip()
    if normal_token in {"replace_me", "replace_me_later"}:
        normal_token = ""
    if js_token in {"replace_me", "replace_me_later"}:
        js_token = ""
    return {
        "enabled": env_flag("ENABLE_CRAWLBASE", False),
        "normal_token": normal_token,
        "js_token": js_token,
        "daily_limit": env_int("CRAWLBASE_DAILY_LIMIT", 200),
        "interval_hours": env_int("CRAWLBASE_CHECK_INTERVAL_HOURS", 24),
        "default_country": os.environ.get("CRAWLBASE_DEFAULT_COUNTRY", "US").strip() or "US",
        "use_js_fallback": env_flag("CRAWLBASE_USE_JS_FALLBACK", False),
    }


def canopy_headers(settings):
    """Build the Canopy authentication header from CANOPY_AUTH_HEADER."""
    mode = settings["auth_header"].strip()
    if mode.lower() == "auto":
        mode = "API-KEY"
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


def api_usage_count_for_period(db, provider, period):
    """Return request count for a provider and any period key."""
    row = db.execute(
        """
        SELECT request_count
        FROM api_usage
        WHERE provider = ?
          AND usage_month = ?
        """,
        (provider, period),
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


def increment_api_usage_for_period(db, provider, period):
    """Count one actual external API request for a specific period key."""
    db.execute(
        """
        INSERT INTO api_usage (provider, usage_month, request_count, last_request_at)
        VALUES (?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(provider, usage_month)
        DO UPDATE SET
            request_count = request_count + 1,
            last_request_at = CURRENT_TIMESTAMP
        """,
        (provider, period),
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
            timeout=10,
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


def canopy_debug_auth_modes(settings):
    """Choose which Canopy auth modes the debug command should try."""
    mode = settings["auth_header"].strip()
    if mode.lower() == "auto":
        return ["API-KEY", "Authorization"]
    if mode.lower() == "authorization":
        return ["Authorization"]
    return [mode or "API-KEY"]


def canopy_debug_headers(api_key, auth_mode):
    """Build one debug request auth header without printing the key."""
    if auth_mode.lower() == "authorization":
        return {"Authorization": f"Bearer {api_key}"}
    return {auth_mode: api_key}


def response_shape(value, depth=0):
    """Return a compact, secret-free preview of JSON keys and value types."""
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        preview = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= 20:
                preview["..."] = f"{len(value) - 20} more key(s)"
                break
            key_text = str(key)
            if any(secret in key_text.lower() for secret in ("key", "token", "secret")):
                preview[key_text] = "[redacted]"
            else:
                preview[key_text] = response_shape(child, depth + 1)
        return preview
    if isinstance(value, list):
        if not value:
            return []
        return [{"list_length": len(value), "first_item": response_shape(value[0], depth + 1)}]
    if isinstance(value, str):
        return "str"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


def save_canopy_debug_preview(preview):
    """Write the last debug response shape without secrets."""
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    output_path = log_dir / "canopy_debug_last.json"
    output_path.write_text(json.dumps(preview, indent=2), encoding="utf-8")
    return output_path


def canopy_debug_fields(data, asin, fallback_url):
    """Parse Canopy fields useful for diagnosing DadDeals parsing."""
    parsed = parse_canopy_product(data, asin, fallback_url)
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
    return {
        "title": nested_value(data, ("title", "name", "product_title", "productTitle")),
        "price": parsed["current_price"],
        "currency": nested_value(data, ("currency", "currency_code", "currencyCode")),
        "display_price": nested_value(
            data,
            ("display_price", "displayPrice", "price_display", "priceDisplay", "formatted_price"),
        )
        or (price_value if isinstance(price_value, str) else None),
        "availability": nested_value(
            data,
            ("availability", "availability_status", "availabilityStatus", "stock_status"),
        ),
        "source_url": parsed["source_url"],
    }


def debug_canopy(asin):
    """Diagnose a Canopy Amazon product request without creating alerts."""
    settings = canopy_settings()
    asin = asin.strip().upper()
    print("DadDeals Canopy debug")
    print(f"ASIN: {asin}")
    print(f"Canopy Amazon enabled: {'yes' if settings['enabled'] else 'no'}")
    print(f"Canopy API key present: {'yes' if bool(settings['api_key']) else 'no'}")
    print(f"Configured auth header: {settings['auth_header']}")
    print(f"Monthly limit: {settings['monthly_limit']}")
    print(f"Amazon check interval: {settings['interval_hours']} hour(s)")
    print("This debug command creates no alerts and sends no Telegram messages.")
    print("Each real Canopy request made here is counted in DadDeals api_usage.")

    if not re.match(r"^[A-Z0-9]{10}$", asin):
        print("ASIN format does not look valid. Expected 10 letters/numbers.")
        return False
    if not settings["api_key"]:
        print("Cannot call Canopy because CANOPY_API_KEY is missing or still a placeholder.")
        return False

    try:
        import requests
    except ImportError:
        print("The requests package is missing. Run pip install -r requirements.txt.")
        return False

    db = connect_db()
    try:
        init_db(db)
        auth_modes = canopy_debug_auth_modes(settings)
        fallback_url = f"https://www.amazon.com/dp/{asin}"
        for auth_mode in auth_modes:
            print("")
            print(f"Trying auth mode: {auth_mode}")
            try:
                increment_api_usage(db, CANOPY_PROVIDER)
                response = requests.get(
                    "https://rest.canopyapi.co/api/amazon/product",
                    params={"asin": asin, "domain": "US"},
                    headers=canopy_debug_headers(settings["api_key"], auth_mode),
                    timeout=30,
                )
                db.commit()
            except requests.Timeout:
                db.commit()
                print("Result: timeout after 30 seconds. This points to network/API slowness.")
                continue
            except requests.RequestException as error:
                db.commit()
                print(f"Result: network error while contacting Canopy: {error.__class__.__name__}")
                continue

            print(f"HTTP status: {response.status_code}")
            if response.status_code in {401, 403}:
                print("Likely auth issue: check CANOPY_API_KEY and CANOPY_AUTH_HEADER.")
            elif response.status_code == 429:
                print("Likely rate limit or budget issue from Canopy.")

            try:
                data = response.json()
            except ValueError:
                preview = {"non_json_preview": response.text[:500]}
                output_path = save_canopy_debug_preview(preview)
                print(f"Response was not JSON. Saved preview to {output_path}.")
                continue

            preview = response_shape(data)
            print("Response shape preview:")
            print(json.dumps(preview, indent=2))

            if response.status_code >= 400:
                continue

            try:
                fields = canopy_debug_fields(data, asin, fallback_url)
            except ProductFetchError as error:
                output_path = save_canopy_debug_preview(preview)
                print(f"DadDeals parsing failed: {error}")
                print(f"Saved response shape preview to {output_path}.")
                continue

            print("Parsed Canopy fields:")
            print(f"  Title: {fields['title'] or 'not found'}")
            print(f"  Price: {fields['price'] if fields['price'] is not None else 'not found'}")
            print(f"  Currency: {fields['currency'] or 'not found'}")
            print(f"  Display price: {fields['display_price'] or 'not found'}")
            print(f"  Availability: {fields['availability'] or 'not found'}")
            print(f"  Source URL: {fields['source_url'] or 'not found'}")
            return True

        return False
    finally:
        db.close()


def crawlbase_debug_token(settings, use_js):
    """Choose the Crawlbase token for a diagnostic request."""
    if use_js:
        return settings["js_token"], "JavaScript token"
    return settings["normal_token"], "Normal token"


def crawlbase_request_params(target_url, token, country, use_js, page_wait=None, ajax_wait=False):
    """Build Crawlbase Crawling API query parameters.

    Crawlbase documents the endpoint as:
    https://api.crawlbase.com/?token=YOUR_TOKEN&url=ENCODED_URL

    requests handles URL encoding for us when we pass params as a dict.
    """
    params = {
        "token": token,
        "url": target_url,
    }
    if country:
        params["country"] = country
    if use_js and page_wait is not None:
        params["page_wait"] = str(page_wait)
    if use_js and ajax_wait:
        params["ajax_wait"] = "true"
    return params


def redacted_crawlbase_params(params):
    """Return request params safe for logs/console."""
    return {key: ("[redacted]" if key == "token" else value) for key, value in params.items()}


def save_crawlbase_debug_files(body, metadata):
    """Save last Crawlbase debug response and redacted metadata."""
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    html_path = log_dir / "crawlbase_debug_last.html"
    json_path = log_dir / "crawlbase_debug_last.json"
    html_path.write_text(body, encoding="utf-8", errors="replace")
    json_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return html_path, json_path


def parse_json_ld_product(soup):
    """Try JSON-LD product data first because it is the cleanest source."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text(" ", strip=True)
        if not text:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            graph_items = item.get("@graph")
            if isinstance(graph_items, list):
                items.extend(graph_items)
            item_type = str(item.get("@type", "")).lower()
            if "product" not in item_type:
                continue
            offers = item.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            return {
                "title": item.get("name"),
                "current_price": price_from_canopy_value(
                    offers.get("price") if isinstance(offers, dict) else None
                ),
                "regular_price": None,
                "availability": offers.get("availability") if isinstance(offers, dict) else None,
                "source_url": item.get("url"),
                "image_url": item.get("image")[0]
                if isinstance(item.get("image"), list) and item.get("image")
                else item.get("image"),
            }
    return {}


def meta_content(soup, *names):
    """Return the first matching meta content value."""
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find(
            "meta", attrs={"name": name}
        )
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def visible_text_price(soup):
    """Find a plausible visible price in common retail selectors."""
    selectors = [
        "[class*=priceView-customer-price]",
        "[class*=customer-price]",
        "[class*=sale-price]",
        "[class*=price]",
        "[data-testid*=price]",
    ]
    for selector in selectors:
        for tag in soup.select(selector):
            price = parse_price_text(tag.get_text(" ", strip=True))
            if price is not None:
                return price
    return None


def parse_best_buy_debug_html(html, source_url):
    """Defensively parse Best Buy debug HTML for product info."""
    soup = soup_from_html(html)
    parsed = parse_json_ld_product(soup)
    title = parsed.get("title") or meta_content(
        soup, "og:title", "twitter:title", "title"
    )
    current_price = parsed.get("current_price")
    if current_price is None:
        current_price = price_from_canopy_value(
            meta_content(soup, "product:price:amount", "price")
        )
    if current_price is None:
        current_price = visible_text_price(soup)

    regular_price = None
    regular_text = soup.find(string=re.compile(r"Was\s+\$|Reg\.?\s+\$", re.IGNORECASE))
    if regular_text:
        regular_price = parse_price_text(regular_text)

    availability = parsed.get("availability") or meta_content(soup, "availability")
    if not availability:
        availability_text = soup.find(
            string=re.compile(r"in stock|sold out|unavailable|pickup|shipping", re.IGNORECASE)
        )
        availability = str(availability_text).strip() if availability_text else None

    image_url = parsed.get("image_url") or meta_content(soup, "og:image", "twitter:image")
    return {
        "title": title,
        "current_price": current_price,
        "regular_price": regular_price,
        "availability": availability,
        "source_url": parsed.get("source_url") or source_url,
        "image_url": image_url,
    }


def parse_generic_product_page(html, source_url):
    """Parse common ecommerce fields from a fetched product page."""
    soup = soup_from_html(html)
    parsed = parse_json_ld_product(soup)
    current_price = parsed.get("current_price")
    if current_price is None:
        current_price = price_from_canopy_value(
            meta_content(soup, "product:price:amount", "og:price:amount", "price")
        )
    if current_price is None:
        current_price = visible_text_price(soup)
    return {
        "title": parsed.get("title")
        or meta_content(soup, "og:title", "twitter:title", "title"),
        "current_price": current_price,
        "currency": meta_content(soup, "product:price:currency", "og:price:currency"),
        "availability": parsed.get("availability") or meta_content(soup, "availability"),
        "source_url": parsed.get("source_url") or source_url,
        "image_url": parsed.get("image_url") or meta_content(soup, "og:image", "twitter:image"),
    }


def parse_bestbuy_page(html, source_url):
    """Best Buy parser adapter."""
    return parse_best_buy_debug_html(html, source_url)


def parse_target_page(html, source_url):
    """Target parser placeholder using the generic parser for now."""
    return parse_generic_product_page(html, source_url)


def parse_walmart_page(html, source_url):
    """Walmart parser placeholder using the generic parser for now."""
    return parse_generic_product_page(html, source_url)


def parse_homedepot_page(html, source_url):
    """Home Depot parser placeholder using the generic parser for now."""
    return parse_generic_product_page(html, source_url)


def parse_newegg_page(html, source_url):
    """Newegg parser placeholder using the generic parser for now."""
    return parse_generic_product_page(html, source_url)


def parse_product_page_for_url(html, source_url):
    """Choose a store-specific parser when one exists."""
    if is_best_buy_url(source_url):
        return parse_bestbuy_page(html, source_url)
    if is_target_url(source_url):
        return parse_target_page(html, source_url)
    if is_walmart_url(source_url):
        return parse_walmart_page(html, source_url)
    if is_home_depot_url(source_url):
        return parse_homedepot_page(html, source_url)
    if is_newegg_url(source_url):
        return parse_newegg_page(html, source_url)
    return parse_generic_product_page(html, source_url)


def crawlbase_due_status(db, product_id, interval_hours):
    """Return whether scheduled Crawlbase checking is due."""
    if interval_hours <= 0:
        return True, "Crawlbase check is due because the interval is 0 hours."
    row = db.execute(
        """
        SELECT checked_at,
               (julianday('now') - julianday(checked_at)) * 24.0 AS age_hours
        FROM price_checks
        WHERE product_id = ?
          AND source_name LIKE '%Crawlbase%'
        ORDER BY checked_at DESC, id DESC
        LIMIT 1
        """,
        (product_id,),
    ).fetchone()
    if row is None:
        return True, "Crawlbase check is due because it has not been tried yet."
    age_hours = row["age_hours"] if row["age_hours"] is not None else interval_hours
    if age_hours >= interval_hours:
        return True, f"Crawlbase check is due; last attempt was {age_hours:.1f} hour(s) ago."
    return (
        False,
        f"Crawlbase check is not due yet; last attempt was {age_hours:.1f} hour(s) ago "
        f"and the interval is {interval_hours} hour(s).",
    )


def crawlbase_available(db, use_js):
    """Check Crawlbase config, token, and daily budget."""
    settings = crawlbase_settings()
    token, token_label = crawlbase_debug_token(settings, use_js)
    usage_day = current_usage_day()
    usage_count = api_usage_count_for_period(db, CRAWLBASE_PROVIDER, usage_day)
    if not settings["enabled"]:
        return False, "Crawlbase fallback is not enabled.", settings, token, token_label
    if not token:
        return False, f"Crawlbase {token_label.lower()} is missing.", settings, token, token_label
    if usage_count >= settings["daily_limit"]:
        return (
            False,
            f"Crawlbase daily limit reached ({usage_count}/{settings['daily_limit']}).",
            settings,
            token,
            token_label,
        )
    return True, "", settings, token, token_label


def fetch_crawlbase_html(db, url, use_js=False, page_wait=None, ajax_wait=False):
    """Fetch product HTML through Crawlbase and count actual requests."""
    available, reason, settings, token, token_label = crawlbase_available(db, use_js)
    if not available:
        raise ProductFetchError(reason)
    params = crawlbase_request_params(
        url,
        token,
        settings["default_country"],
        use_js,
        page_wait=page_wait,
        ajax_wait=ajax_wait,
    )
    try:
        import requests
    except ImportError as error:
        raise ProductFetchError(
            "The requests package is missing. Run pip install -r requirements.txt."
        ) from error
    increment_api_usage_for_period(db, CRAWLBASE_PROVIDER, current_usage_day())
    try:
        response = requests.get("https://api.crawlbase.com/", params=params, timeout=90)
    except requests.Timeout as error:
        raise ProductFetchError("Crawlbase request timed out.") from error
    except requests.RequestException as error:
        raise ProductFetchError("Crawlbase request failed.") from error
    if response.status_code >= 400:
        raise ProductFetchError(f"Crawlbase returned HTTP {response.status_code}.")
    return response.text, token_label


def fetch_product_with_crawlbase(db, url, use_js=False):
    """Fetch and parse one product URL through Crawlbase."""
    html, token_label = fetch_crawlbase_html(db, url, use_js=use_js, page_wait=3000 if use_js else None)
    parsed = parse_product_page_for_url(html, url)
    if parsed.get("current_price") is None:
        raise ProductFetchError("Crawlbase fetched the page, but DadDeals could not find a price yet.")
    method = "crawlbase_js" if use_js else "crawlbase_normal"
    source_prefix = source_label_for_url(url)
    source_name = f"{source_prefix} via Crawlbase"
    return {
        "current_price": parsed["current_price"],
        "source_url": parsed.get("source_url") or url,
        "source_name": source_name,
        "method": method,
        "message": (
            f"Crawlbase {'JS' if use_js else 'normal'} check completed."
            + (f" Title: {parsed['title']}." if parsed.get("title") else "")
            + (f" Availability: {parsed['availability']}." if parsed.get("availability") else "")
        ),
    }


def debug_crawlbase_url(target_url, use_js=False, country=None, page_wait=None, ajax_wait=False):
    """Run one Crawlbase diagnostic fetch without changing product checks."""
    settings = crawlbase_settings()
    country = country or settings["default_country"]
    source_label = source_label_for_url(target_url)
    sku = extract_best_buy_sku(target_url) if is_best_buy_url(target_url) else None
    token, token_label = crawlbase_debug_token(settings, use_js)
    usage_day = current_usage_day()

    print("DadDeals Crawlbase debug")
    print(f"Crawlbase enabled: {'yes' if settings['enabled'] else 'no'}")
    print(f"Normal token present: {'yes' if bool(settings['normal_token']) else 'no'}")
    print(f"JS token present: {'yes' if bool(settings['js_token']) else 'no'}")
    print(f"Daily limit: {settings['daily_limit']}")
    print(f"Check interval setting: {settings['interval_hours']} hour(s)")
    print(f"Default country: {settings['default_country']}")
    print(f"Detected source: {source_label}")
    if sku:
        print(f"Best Buy SKU: {sku}")
    print(f"Token mode: {token_label}")
    print(f"Target URL: {target_url}")

    if not settings["enabled"]:
        print("Crawlbase is disabled. Set ENABLE_CRAWLBASE=true to make a debug request.")
        return False
    if not token:
        print(f"Required {token_label.lower()} is missing or still a placeholder.")
        return False

    db = connect_db()
    try:
        init_db(db)
        usage_count = api_usage_count_for_period(db, CRAWLBASE_PROVIDER, usage_day)
        print(f"Crawlbase usage today: {usage_count} / {settings['daily_limit']} ({usage_day})")
        if usage_count >= settings["daily_limit"]:
            print("Crawlbase daily limit reached. No request was made.")
            return False

        params = crawlbase_request_params(
            target_url,
            token,
            country,
            use_js,
            page_wait=page_wait,
            ajax_wait=ajax_wait,
        )
        print("Crawlbase request params:")
        print(json.dumps(redacted_crawlbase_params(params), indent=2))

        try:
            import requests
        except ImportError:
            print("The requests package is missing. Run pip install -r requirements.txt.")
            return False

        increment_api_usage_for_period(db, CRAWLBASE_PROVIDER, usage_day)
        try:
            response = requests.get(
                "https://api.crawlbase.com/",
                params=params,
                timeout=90,
                headers={"Accept-Encoding": "gzip"},
            )
            db.commit()
        except requests.Timeout:
            db.commit()
            print("Crawlbase request timed out after 90 seconds.")
            return False
        except requests.RequestException as error:
            db.commit()
            print(f"Crawlbase request failed: {error.__class__.__name__}")
            return False

        content_type = response.headers.get("content-type", "unknown")
        body = response.text
        metadata = {
            "target_url": target_url,
            "source_label": source_label,
            "best_buy_sku": sku,
            "use_js": use_js,
            "country": country,
            "page_wait": page_wait,
            "ajax_wait": ajax_wait,
            "request_params": redacted_crawlbase_params(params),
            "http_status": response.status_code,
            "content_type": content_type,
            "response_length": len(response.content),
            "crawlbase_headers": {
                key: value
                for key, value in response.headers.items()
                if key.lower() in {"pc_status", "original_status", "url", "rid", "remaining"}
            },
        }
        html_path, json_path = save_crawlbase_debug_files(body, metadata)

        print(f"HTTP status code: {response.status_code}")
        print(f"Content type: {content_type}")
        print(f"Response length: {len(response.content)} bytes")
        print(f"Saved response body to {html_path}")
        print(f"Saved redacted metadata to {json_path}")

        if is_best_buy_url(target_url):
            parsed = parse_best_buy_debug_html(body, target_url)
            print("Best Buy parse attempt:")
            print(f"  Title: {parsed['title'] or 'not found'}")
            print(
                f"  Current price: "
                f"{('$%.2f' % parsed['current_price']) if parsed['current_price'] is not None else 'not found'}"
            )
            print(
                f"  Regular price: "
                f"{('$%.2f' % parsed['regular_price']) if parsed['regular_price'] is not None else 'not found'}"
            )
            print(f"  Availability: {parsed['availability'] or 'not found'}")
            print(f"  Source URL: {parsed['source_url'] or target_url}")
            print(f"  Image URL: {parsed['image_url'] or 'not found'}")
            if parsed["current_price"] is None:
                print("Crawlbase fetched the page, but DadDeals could not parse a price yet.")
        else:
            print("Parsing is only attempted for Best Buy in this diagnostic phase.")

        return response.status_code < 500
    finally:
        db.close()


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


def latest_check_age_hours(db, table_name, id_column, item_id):
    """Return the age in hours of the latest check row, or None."""
    row = db.execute(
        f"""
        SELECT (julianday('now') - julianday(checked_at)) * 24.0 AS age_hours
        FROM {table_name}
        WHERE {id_column} = ?
        ORDER BY checked_at DESC, id DESC
        LIMIT 1
        """,
        (item_id,),
    ).fetchone()
    if row is None or row["age_hours"] is None:
        return None
    return float(row["age_hours"])


def product_due_status(db, product):
    """Return whether a scheduled product check is due."""
    interval_hours = int(row_value(product, "product_check_interval_hours", 24) or 24)
    age_hours = latest_check_age_hours(db, "price_checks", "product_id", product["id"])
    if age_hours is None:
        return True, "Product check is due because it has not been checked yet."
    if age_hours >= interval_hours:
        return True, f"Product check is due; last check was {age_hours:.1f} hour(s) ago."
    return (
        False,
        f"Product check is not due yet; last check was {age_hours:.1f} hour(s) ago "
        f"and this product interval is {interval_hours} hour(s).",
    )


def stock_due_status(db, stock):
    """Return whether a scheduled stock check is due."""
    interval_minutes = int(row_value(stock, "stock_check_interval_minutes", 5) or 5)
    age_hours = latest_check_age_hours(db, "stock_checks", "stock_id", stock["id"])
    if age_hours is None:
        return True, "Stock check is due because it has not been checked yet."
    age_minutes = age_hours * 60
    if age_minutes >= interval_minutes:
        return True, f"Stock check is due; last check was {age_minutes:.1f} minute(s) ago."
    return (
        False,
        f"Stock check is not due yet; last check was {age_minutes:.1f} minute(s) ago "
        f"and this stock interval is {interval_minutes} minute(s).",
    )


def product_alert_cooling_down(db, product, title):
    """Return True when this product should not create another alert yet."""
    cooldown_hours = int(row_value(product, "product_notify_cooldown_hours", 72) or 0)
    if cooldown_hours <= 0:
        return False, ""

    row = db.execute(
        """
        SELECT created_at,
               (julianday('now') - julianday(created_at)) * 24.0 AS age_hours
        FROM alerts
        WHERE item_type = 'product'
          AND item_id = ?
          AND title = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (product["id"], title),
    ).fetchone()
    if row is None or row["age_hours"] is None:
        return False, ""

    age_hours = float(row["age_hours"])
    if age_hours >= cooldown_hours:
        return False, ""

    remaining = cooldown_hours - age_hours
    return (
        True,
        f"Target is still hit, but product notifications are cooling down for about {remaining:.1f} more hour(s).",
    )


def alert_already_exists_today(db, item_type, item_id, title, message):
    """Avoid creating the same alert title more than once per day."""
    row = db.execute(
        """
        SELECT id
        FROM alerts
        WHERE item_type = ?
          AND item_id = ?
          AND title = ?
          AND date(created_at) = date('now')
        LIMIT 1
        """,
        (item_type, item_id, title),
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


def product_target_alert_message(product_name, current_price, target_price, source_url):
    """Build a polished product target alert body for dashboard and Telegram."""
    return (
        f"🛒 Product target hit:\n{product_name}\n\n"
        f"💸 {target_alert_message(product_name, current_price, target_price)}\n\n"
        f"🔗 Source:\n{source_url}"
    )


def product_big_drop_alert_message(product_name, current_price, drop_percent, source_url):
    """Build a polished product drop alert body for dashboard and Telegram."""
    return (
        f"🛒 Product big drop:\n{product_name}\n\n"
        f"💸 {product_name} dropped {drop_percent:.1f}% to ${current_price:.2f}.\n\n"
        f"🔗 Source:\n{source_url}"
    )


def stock_target_alert_message(ticker, current_price, target_price):
    """Build a polished stock target alert body for dashboard and Telegram."""
    return (
        f"📈 Stock target hit:\n{ticker}\n\n"
        f"💵 {target_alert_message(ticker, current_price, target_price)}\n\n"
        f"🕒 Checked:\n{format_local_time()}"
    )


def stock_big_drop_alert_message(ticker, current_price, percent_change):
    """Build a polished stock drop alert body for dashboard and Telegram."""
    return (
        f"📉 Stock big drop:\n{ticker}\n\n"
        f"💵 {ticker} moved {percent_change:.1f}% to ${current_price:.2f}.\n\n"
        f"🕒 Checked:\n{format_local_time()}"
    )


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


def telegram_quiet_settings():
    """Read Telegram quiet hours settings without affecting alert creation."""
    return {
        "enabled": env_flag("TELEGRAM_QUIET_HOURS_ENABLED", False),
        "start": os.environ.get("TELEGRAM_QUIET_START", "22:00").strip(),
        "end": os.environ.get("TELEGRAM_QUIET_END", "07:00").strip(),
        "timezone": os.environ.get("TELEGRAM_QUIET_TIMEZONE", "America/Los_Angeles").strip()
        or "America/Los_Angeles",
    }


def parse_hhmm(value):
    """Parse a HH:MM quiet-hours value."""
    try:
        hour_text, minute_text = value.split(":", 1)
        return time(hour=int(hour_text), minute=int(minute_text))
    except (TypeError, ValueError):
        return None


def telegram_quiet_hours_active(now=None):
    """Return True when Telegram delivery should pause for quiet hours."""
    settings = telegram_quiet_settings()
    if not settings["enabled"]:
        return False

    start_time = parse_hhmm(settings["start"])
    end_time = parse_hhmm(settings["end"])
    if start_time is None or end_time is None:
        return False

    try:
        quiet_timezone = ZoneInfo(settings["timezone"])
    except ZoneInfoNotFoundError:
        quiet_timezone = ZoneInfo("America/Los_Angeles")

    current_time = (now or datetime.now(timezone.utc)).astimezone(quiet_timezone).time()
    if start_time <= end_time:
        return start_time <= current_time < end_time
    return current_time >= start_time or current_time < end_time


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
    """Format one alert row for Telegram."""
    message = alert["message"] or ""
    if "DadDeals Alert" in message:
        return message
    return f"‼️ DadDeals Alert ‼️\n\n{message}"


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

        if alerts and telegram_quiet_hours_active():
            print(
                "Telegram quiet hours are active. Alerts stayed unsent and will be tried later."
            )
            return {"sent": 0, "failed": 0, "total": len(alerts)}

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
    method=None,
    detected_store=None,
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
    db.execute(
        """
        UPDATE tracked_products
        SET last_check_method = COALESCE(?, last_check_method),
            last_detected_store = COALESCE(?, last_detected_store),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (method, detected_store, product["id"]),
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
        cooling_down, cooldown_message = product_alert_cooling_down(db, product, title)
        message = product_target_alert_message(
            product["name"], current_price, target_price, source_url
        )
        if cooling_down:
            messages.append(cooldown_message)
        elif create_alert(db, "product", product["id"], title, message, dry_run):
            alerts_created += 1
            messages.append(message)

    if previous_price is not None and product["big_drop_percent"] is not None:
        drop_percent = ((previous_price - current_price) / previous_price) * 100
        if product["notify_on_big_drop"] and drop_percent >= product["big_drop_percent"]:
            title = f"Product big drop: {product['name']}"
            cooling_down, cooldown_message = product_alert_cooling_down(db, product, title)
            message = product_big_drop_alert_message(
                product["name"], current_price, drop_percent, source_url
            )
            if cooling_down:
                messages.append(cooldown_message)
            elif create_alert(db, "product", product["id"], title, message, dry_run):
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


def row_value(row, key, default=None):
    """Read sqlite.Row values safely across migrated and older databases."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def product_allows_crawlbase(product):
    """Return True when a product explicitly allows Crawlbase fallback."""
    return bool(row_value(product, "allow_crawlbase_fallback", 0))


def product_prefers_crawlbase(product):
    """Return True when a product prefers Crawlbase first."""
    return bool(row_value(product, "prefer_crawlbase", 0))


def crawlbase_should_try(db, product, force, explicit=False):
    """Check Crawlbase interval/budget/config before a product check attempt."""
    settings = crawlbase_settings()
    if not force:
        due, due_message = crawlbase_due_status(db, product["id"], settings["interval_hours"])
        if not due:
            raise ProductFetchError(due_message)
    use_js = settings["use_js_fallback"] and not is_best_buy_url(product["url"])
    available, reason, _settings, _token, _label = crawlbase_available(db, use_js)
    if not available:
        if explicit or product_allows_crawlbase(product) or product_prefers_crawlbase(product):
            raise ProductFetchError(f"Crawlbase fallback is not available. {reason} Open the product page manually.")
        raise ProductFetchError(reason)
    return use_js


def try_crawlbase_product(db, product, force=False, explicit=False):
    """Fetch one product with Crawlbase and convert failures to ProductFetchError."""
    use_js = crawlbase_should_try(db, product, force, explicit=explicit)
    return fetch_product_with_crawlbase(db, product["url"], use_js=use_js)


def save_failed_product_check(
    db,
    product,
    source_name,
    source_url,
    previous_price,
    target_price,
    message,
    method,
    detected_store,
    dry_run,
    status="failed",
):
    """Store one failed product check unless this is a dry run."""
    if not dry_run:
        save_product_check(
            db,
            product,
            source_name,
            source_url,
            None,
            previous_price,
            target_price,
            status,
            message,
            method=method,
            detected_store=detected_store,
        )
    return product_result(product["name"], None, previous_price, 0, [message], status)


def save_successful_product_check(
    db,
    product,
    current_price,
    previous_price,
    target_price,
    source_name,
    source_url,
    message,
    method,
    detected_store,
    dry_run,
):
    """Store a successful product check and create any matching alerts."""
    if not dry_run:
        save_product_check(
            db,
            product,
            source_name,
            source_url,
            current_price,
            previous_price,
            target_price,
            "ok",
            message,
            method=method,
            detected_store=detected_store,
        )

    alerts_created, messages = check_product_alerts(
        db, product, current_price, previous_price, target_price, source_url, dry_run
    )
    return product_result(
        product["name"],
        current_price,
        previous_price,
        alerts_created,
        messages or [message],
        "ok",
    )


def check_crawlbase_only_product(db, product, dry_run, force=False):
    """Run one explicit Crawlbase retry for a product."""
    previous_price = latest_product_price(db, product["id"])
    target_price = product["target_price"]
    detected_store = source_label_for_url(product["url"])
    settings = crawlbase_settings()
    method = "crawlbase_js" if settings["use_js_fallback"] and not is_best_buy_url(product["url"]) else "crawlbase_normal"

    if dry_run:
        message = "Dry run only: Crawlbase retry would be attempted, but no Crawlbase request was made."
        return product_result(product["name"], None, previous_price, 0, [message], "skipped")

    try:
        crawlbase_product = try_crawlbase_product(db, product, force=force, explicit=True)
    except ProductFetchError as error:
        message = f"Crawlbase retry failed: {error}"
        return save_failed_product_check(
            db,
            product,
            "Crawlbase",
            product["url"],
            previous_price,
            target_price,
            message,
            method,
            detected_store,
            dry_run,
        )

    return save_successful_product_check(
        db,
        product,
        crawlbase_product["current_price"],
        previous_price,
        target_price,
        crawlbase_product["source_name"],
        crawlbase_product["source_url"],
        crawlbase_product["message"],
        crawlbase_product["method"],
        detected_store,
        dry_run,
    )


def try_amazon_crawlbase_fallback(db, product, previous_price, target_price, prior_message, dry_run, force):
    """Try Crawlbase for Amazon only when the product explicitly allows it."""
    if not (product_allows_crawlbase(product) or product_prefers_crawlbase(product)):
        return None

    detected_store = "Amazon"
    if dry_run:
        message = f"{prior_message} Dry run only: Crawlbase fallback would be attempted, but no Crawlbase request was made."
        return product_result(product["name"], None, previous_price, 0, [message], "skipped")

    try:
        crawlbase_product = try_crawlbase_product(db, product, force=force, explicit=True)
    except ProductFetchError as error:
        message = f"{prior_message} Crawlbase fallback failed: {error}"
        return save_failed_product_check(
            db,
            product,
            "Amazon via Crawlbase",
            product["url"],
            previous_price,
            target_price,
            message,
            "crawlbase_fallback",
            detected_store,
            dry_run,
        )

    return save_successful_product_check(
        db,
        product,
        crawlbase_product["current_price"],
        previous_price,
        target_price,
        crawlbase_product["source_name"],
        crawlbase_product["source_url"],
        crawlbase_product["message"],
        crawlbase_product["method"],
        detected_store,
        dry_run,
    )


def check_amazon_product(db, product, dry_run, force=False):
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
                method="amazon_canopy",
                detected_store="Amazon",
            )
        return product_result(product["name"], current_price, previous_price, 0, [message], "failed")

    settings = canopy_settings()
    due, due_message = amazon_due_status(db, product["id"], settings["interval_hours"])

    if not due and not force:
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
        fallback_result = try_amazon_crawlbase_fallback(
            db, product, previous_price, target_price, message, dry_run, force
        )
        if fallback_result is not None:
            return fallback_result
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
                method="amazon_canopy",
                detected_store="Amazon",
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
        fallback_result = try_amazon_crawlbase_fallback(
            db, product, previous_price, target_price, message, dry_run, force
        )
        if fallback_result is not None:
            return fallback_result
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
            method="amazon_canopy",
            detected_store="Amazon",
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
        method="amazon_canopy",
        detected_store="Amazon",
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


def check_product(db, product, dry_run, force=False, force_crawlbase=False):
    """Route Amazon URLs to Canopy support and other URLs to the exact checker."""
    if not force:
        due, due_message = product_due_status(db, product)
        if not due:
            return product_result(product["name"], None, latest_product_price(db, product["id"]), 0, [due_message], "skipped")

    if force_crawlbase:
        return check_crawlbase_only_product(db, product, dry_run, force=force)
    if is_amazon_url(product["url"]):
        return check_amazon_product(db, product, dry_run, force=force)
    return check_exact_product(db, product, dry_run, force=force)


def check_exact_product(db, product, dry_run, force=False):
    """Fetch and store one non-Amazon product check."""
    current_price = None
    previous_price = latest_product_price(db, product["id"])
    target_price = product["target_price"]
    source_name = product_source_name(product["url"])
    source_url = product["url"]
    detected_store = source_label_for_url(product["url"])
    method = "normal"
    check_message = "Exact URL product check completed."
    normal_error = None

    should_prefer_crawlbase = product_prefers_crawlbase(product) or is_best_buy_url(product["url"])
    if should_prefer_crawlbase:
        if dry_run:
            normal_error = (
                "Dry run only: Crawlbase would be used first for this product, "
                "but no Crawlbase request was made."
            )
        else:
            try:
                crawlbase_product = try_crawlbase_product(db, product, force=force)
                current_price = crawlbase_product["current_price"]
                source_name = crawlbase_product["source_name"]
                source_url = crawlbase_product["source_url"]
                method = crawlbase_product["method"]
                check_message = crawlbase_product["message"]
            except ProductFetchError as error:
                normal_error = str(error)

    if current_price is None:
        try:
            current_price = fetch_product_price(product["url"])
            source_name = product_source_name(product["url"])
            source_url = product["url"]
            method = "normal"
            check_message = "Exact URL product check completed."
        except ProductFetchError as error:
            normal_error = str(error)

    if current_price is None and product_allows_crawlbase(product) and not should_prefer_crawlbase:
        if dry_run:
            normal_error = (
                f"{normal_error} Crawlbase fallback would be attempted on --run, "
                "but no Crawlbase request was made."
            )
        else:
            try:
                crawlbase_product = try_crawlbase_product(db, product, force=force)
                current_price = crawlbase_product["current_price"]
                source_name = crawlbase_product["source_name"]
                source_url = crawlbase_product["source_url"]
                method = crawlbase_product["method"]
                check_message = crawlbase_product["message"]
            except ProductFetchError as error:
                normal_error = f"{normal_error} Crawlbase fallback: {error}"

    if current_price is None:
        status = "skipped" if dry_run and should_prefer_crawlbase else "failed"
        check_message = normal_error or "No product price was found."

        if not dry_run:
            save_product_check(
                db,
                product,
                source_name,
                source_url,
                current_price,
                previous_price,
                target_price,
                status,
                check_message,
                method=method,
                detected_store=detected_store,
            )

        return product_result(product["name"], current_price, previous_price, 0, [check_message], status)

    return save_successful_product_check(
        db,
        product,
        current_price,
        previous_price,
        target_price,
        source_name,
        source_url,
        check_message,
        method,
        detected_store,
        dry_run,
    )


def check_stock(db, stock, dry_run, force=False):
    """Fetch and store one real yfinance stock check."""
    ticker = clean_ticker(stock["ticker"])
    messages = []
    alerts_created = 0
    target_price = stock["target_price"]

    if not force:
        due, due_message = stock_due_status(db, stock)
        if not due:
            return {
                "ticker": ticker,
                "current_price": None,
                "previous_close": None,
                "percent_change": None,
                "alerts_created": 0,
                "messages": [due_message],
                "status": "skipped",
            }

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
        message = stock_target_alert_message(stock["ticker"], current_price, target_price)
        if create_alert(db, "stock", stock["id"], title, message, dry_run):
            alerts_created += 1
            messages.append(message)

    if stock["daily_drop_percent"] is not None:
        if (
            stock["notify_on_big_drop"]
            and percent_change <= -abs(stock["daily_drop_percent"])
        ):
            title = f"Stock big drop: {stock['ticker']}"
            message = stock_big_drop_alert_message(stock["ticker"], current_price, percent_change)
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
            elif result["status"] == "skipped":
                print(
                    f"  - {result['ticker']}: skipped - "
                    f"{result['messages'][0]}"
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


def latest_timestamp(db, table_name, column_name):
    """Return the latest timestamp from a table."""
    row = db.execute(f"SELECT MAX({column_name}) AS latest FROM {table_name}").fetchone()
    return row["latest"] if row and row["latest"] else "none"


def health_check():
    """Print a small DadDeals status summary for maintenance."""
    db = connect_db()
    try:
        init_db(db)
        print("DadDeals health")
        print(f"Database reachable: yes ({database_path()})")

        token, chat_id = telegram_settings()
        print(f"Telegram configured: {'yes' if token and chat_id else 'no'}")
        quiet = telegram_quiet_settings()
        print(f"Telegram quiet hours enabled: {'yes' if quiet['enabled'] else 'no'}")
        print(f"Telegram quiet hours: {quiet['start']} to {quiet['end']} ({quiet['timezone']})")

        canopy = canopy_settings()
        canopy_usage = api_usage_count(db, CANOPY_PROVIDER)
        print(f"Canopy Amazon enabled: {'yes' if canopy['enabled'] else 'no'}")
        print(f"Canopy API key present: {'yes' if bool(canopy['api_key']) else 'no'}")
        print(
            f"Canopy usage this month: {canopy_usage} / {canopy['monthly_limit']} "
            f"({current_usage_month()})"
        )

        crawlbase = crawlbase_settings()
        crawlbase_usage = api_usage_count_for_period(
            db, CRAWLBASE_PROVIDER, current_usage_day()
        )
        print(f"Crawlbase enabled: {'yes' if crawlbase['enabled'] else 'no'}")
        print(f"Crawlbase normal token present: {'yes' if bool(crawlbase['normal_token']) else 'no'}")
        print(f"Crawlbase JS token present: {'yes' if bool(crawlbase['js_token']) else 'no'}")
        print(f"Crawlbase JS fallback: {'yes' if crawlbase['use_js_fallback'] else 'no'}")
        print(
            f"Crawlbase usage today: {crawlbase_usage} / {crawlbase['daily_limit']} "
            f"({current_usage_day()})"
        )
        print(f"Crawlbase default country: {crawlbase['default_country']}")

        active_products = db.execute(
            "SELECT COUNT(*) AS count FROM tracked_products WHERE status = 'active'"
        ).fetchone()["count"]
        active_stocks = db.execute(
            "SELECT COUNT(*) AS count FROM tracked_stocks WHERE status = 'active'"
        ).fetchone()["count"]
        print(f"Active products: {active_products}")
        print(f"Active stocks: {active_stocks}")
        print("Product default check interval: 24 hour(s)")
        print("Product default notification cooldown: 72 hour(s)")
        print("Stock default check interval: 5 minute(s)")
        print(f"Last product check: {latest_timestamp(db, 'price_checks', 'checked_at')}")
        print(f"Last stock check: {latest_timestamp(db, 'stock_checks', 'checked_at')}")
        print(f"Last alert: {latest_timestamp(db, 'alerts', 'created_at')}")
        return True
    except sqlite3.Error as error:
        print(f"Database reachable: no ({short_error(str(error))})")
        return False
    finally:
        db.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Run the DadDeals simulated worker once.")
    parser.add_argument("--dry-run", action="store_true", help="Preview checks without saving.")
    parser.add_argument("--run", action="store_true", help="Save product/stock checks and alerts.")
    parser.add_argument("--send-alerts", action="store_true", help="Send unsent Telegram alerts.")
    parser.add_argument("--test-telegram", action="store_true", help="Send one Telegram test message.")
    parser.add_argument("--debug-canopy", metavar="ASIN", help="Debug one Canopy Amazon product request.")
    parser.add_argument("--debug-crawlbase-url", metavar="URL", help="Debug one Crawlbase product page fetch.")
    parser.add_argument("--js", action="store_true", help="Use Crawlbase JavaScript token for --debug-crawlbase-url.")
    parser.add_argument("--country", help="Crawlbase country code for --debug-crawlbase-url.")
    parser.add_argument("--page-wait", type=int, help="Crawlbase JS page_wait in milliseconds.")
    parser.add_argument("--ajax-wait", action="store_true", help="Use Crawlbase JS ajax_wait for --debug-crawlbase-url.")
    parser.add_argument("--health", action="store_true", help="Print DadDeals maintenance status.")
    args = parser.parse_args()

    if args.dry_run and (
        args.run
        or args.send_alerts
        or args.test_telegram
        or args.debug_canopy
        or args.debug_crawlbase_url
        or args.health
    ):
        parser.error("--dry-run cannot be combined with other worker actions.")
    if args.test_telegram and (
        args.run or args.send_alerts or args.debug_canopy or args.debug_crawlbase_url or args.health
    ):
        parser.error("--test-telegram must be run by itself.")
    if args.debug_canopy and (args.run or args.send_alerts or args.debug_crawlbase_url or args.health):
        parser.error("--debug-canopy must be run by itself.")
    if args.debug_crawlbase_url and (args.run or args.send_alerts or args.health):
        parser.error("--debug-crawlbase-url must be run by itself.")
    if not args.debug_crawlbase_url and (args.js or args.country or args.page_wait or args.ajax_wait):
        parser.error("--js, --country, --page-wait, and --ajax-wait require --debug-crawlbase-url.")
    if (args.page_wait is not None or args.ajax_wait) and args.debug_crawlbase_url and not args.js:
        parser.error("--page-wait and --ajax-wait require --js with --debug-crawlbase-url.")
    if args.health and (args.run or args.send_alerts):
        parser.error("--health must be run by itself.")
    if (
        not args.dry_run
        and not args.run
        and not args.send_alerts
        and not args.test_telegram
        and not args.debug_canopy
        and not args.debug_crawlbase_url
        and not args.health
    ):
        parser.error(
            "Choose --dry-run, --run, --send-alerts, --run --send-alerts, "
            "--test-telegram, --debug-canopy ASIN, --debug-crawlbase-url URL, or --health."
        )

    return args


def main():
    args = parse_args()
    if args.dry_run:
        run_worker(dry_run=True)
        return

    if args.test_telegram:
        sys.exit(0 if test_telegram() else 1)

    if args.debug_canopy:
        sys.exit(0 if debug_canopy(args.debug_canopy) else 1)

    if args.debug_crawlbase_url:
        sys.exit(
            0
            if debug_crawlbase_url(
                args.debug_crawlbase_url,
                use_js=args.js,
                country=args.country,
                page_wait=args.page_wait,
                ajax_wait=args.ajax_wait,
            )
            else 1
        )

    if args.health:
        sys.exit(0 if health_check() else 1)

    if args.run:
        run_worker(dry_run=False)

    if args.send_alerts:
        send_unsent_alerts()


if __name__ == "__main__":
    main()
