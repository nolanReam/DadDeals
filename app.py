import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from markupsafe import Markup, escape


# Load settings from a local .env file if one exists.
# The real .env file is intentionally not committed to source control.
load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = BASE_DIR / "instance" / "daddeals.db"


def create_app():
    """Create and configure the Flask app.

    Keeping this setup in one function makes the app easier to test later,
    while still allowing `python app.py` to run it directly for beginners.
    """
    app = Flask(__name__, instance_path=str(BASE_DIR / "instance"))
    app.config["SECRET_KEY"] = os.environ.get("APP_SECRET_KEY", "dev-only-change-me")
    app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD", "replace_me")
    app.config["DATABASE_PATH"] = os.environ.get(
        "DATABASE_PATH", str(DEFAULT_DATABASE_PATH)
    )
    app.config["HOST"] = os.environ.get("HOST", "0.0.0.0")
    app.config["PORT"] = int(os.environ.get("PORT", "5000"))
    app.config["APP_TIMEZONE"] = os.environ.get("APP_TIMEZONE", "America/Los_Angeles")

    # Make sure the instance folder exists. Flask's instance folder is the
    # right place for local data that should not be committed, like SQLite DBs.
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    app.jinja_env.filters["linkify_urls"] = linkify_urls
    app.jinja_env.filters["local_time"] = local_time

    register_routes(app)
    register_cli_commands(app)

    @app.before_request
    def open_database_if_needed():
        """Create the database on first use if it is missing.

        This keeps `python app.py` beginner-friendly: the app can start even
        before the user learns the explicit init command.
        """
        ensure_database()

    @app.teardown_appcontext
    def close_database(error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    return app


def database_path():
    """Return the configured database path as an absolute Path object."""
    configured_path = Path(current_app_config("DATABASE_PATH"))
    if configured_path.is_absolute():
        return configured_path
    return BASE_DIR / configured_path


def current_app_config(key):
    """Tiny helper to avoid importing current_app in many beginner-facing spots."""
    from flask import current_app

    return current_app.config[key]


def get_db():
    """Open one SQLite connection per request and reuse it during that request."""
    if "db" not in g:
        db_file = database_path()
        db_file.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(db_file)
        g.db.row_factory = sqlite3.Row
    return g.db


def init_db():
    """Create all database tables from schema.sql."""
    schema_file = BASE_DIR / "schema.sql"
    db = get_db()
    with schema_file.open("r", encoding="utf-8") as file:
        db.executescript(file.read())
    migrate_alert_delivery_columns(db)
    migrate_price_check_columns(db)
    migrate_product_crawlbase_columns(db)
    db.commit()


def ensure_database():
    """Initialize the database if the file or required tables/columns are missing."""
    if not database_path().exists() or schema_needs_upgrade():
        init_db()


def schema_needs_upgrade():
    """Check for required tables and alert delivery columns."""
    db = get_db()
    required_tables = {
        "tracked_products",
        "tracked_stocks",
        "alerts",
        "price_checks",
        "stock_checks",
        "api_usage",
    }
    rows = db.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name IN (?, ?, ?, ?, ?, ?)
        """,
        tuple(required_tables),
    ).fetchall()
    existing_tables = {row["name"] for row in rows}
    if required_tables != existing_tables:
        return True

    return (
        alert_delivery_columns_missing(db)
        or price_check_columns_missing(db)
        or product_crawlbase_columns_missing(db)
    )


def alert_delivery_columns_missing(db):
    """Return True when an older alerts table needs Phase 1C columns."""
    existing_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(alerts)").fetchall()
    }
    required_columns = {"sent_at", "delivery_status", "delivery_error", "delivery_attempts"}
    return not required_columns.issubset(existing_columns)


def migrate_alert_delivery_columns(db):
    """Add Phase 1C alert delivery columns without touching existing rows."""
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


def price_check_columns_missing(db):
    """Return True when an older price_checks table needs Phase 1G.1 columns."""
    existing_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(price_checks)").fetchall()
    }
    return "source_url" not in existing_columns


def migrate_price_check_columns(db):
    """Add source URLs to older price check rows without deleting history."""
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


def product_crawlbase_columns_missing(db):
    """Return True when product rows need Phase 2F Crawlbase settings."""
    existing_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(tracked_products)").fetchall()
    }
    required_columns = {
        "allow_crawlbase_fallback",
        "prefer_crawlbase",
        "last_check_method",
        "last_detected_store",
    }
    return not required_columns.issubset(existing_columns)


def migrate_product_crawlbase_columns(db):
    """Add Crawlbase product options to older databases without touching saved rows."""
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


def linkify_urls(value):
    """Render plain alert text while turning http/https URLs into safe links."""
    text = str(value or "")
    pieces = []
    last_index = 0

    for match in re.finditer(r"https?://[^\s<]+", text):
        pieces.append(escape(text[last_index : match.start()]))
        url = match.group(0).rstrip(".,)")
        trailing = match.group(0)[len(url) :]
        safe_url = escape(url)
        pieces.append(
            Markup(
                '<a href="{0}" target="_blank" rel="noopener">{0}</a>'
            ).format(safe_url)
        )
        pieces.append(escape(trailing))
        last_index = match.end()

    pieces.append(escape(text[last_index:]))
    return Markup("").join(pieces).replace("\n", Markup("<br>"))


def local_timezone():
    """Return the configured display timezone with a safe default."""
    timezone_name = os.environ.get("APP_TIMEZONE", "America/Los_Angeles").strip()
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("America/Los_Angeles")


def local_time(value):
    """Format a stored SQLite timestamp for display in the configured timezone."""
    if not value:
        return ""

    if isinstance(value, datetime):
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


def login_required(view):
    """Require the single admin password before accessing a route."""

    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped_view


def parse_non_negative_number(value, field_label, errors):
    """Convert a blank-friendly number field and reject negative values."""
    value = value.strip()
    if not value:
        return None

    try:
        number = float(value)
    except ValueError:
        errors.append(f"{field_label} must be a number.")
        return None

    if number < 0:
        errors.append(f"{field_label} cannot be negative.")
        return None

    return number


def checkbox_value(name):
    """HTML checkboxes only submit a value when checked."""
    return 1 if request.form.get(name) == "on" else 0


def detected_store_label(url):
    """Return a Dad-friendly store label for a product URL."""
    host = urlparse(url).netloc.lower()
    if "amazon." in host:
        return "Amazon"
    if "bestbuy." in host:
        return "Best Buy"
    if "target." in host:
        return "Target"
    if "walmart." in host:
        return "Walmart"
    if "homedepot." in host:
        return "Home Depot"
    if "newegg." in host:
        return "Newegg"
    return "Other website"


def clean_status():
    """Only allow statuses the UI knows how to display."""
    status = request.form.get("status", "active")
    if status not in {"active", "paused"}:
        return "active"
    return status


def validate_required(value, field_label, errors):
    """Trim a required text field and collect a friendly error if it is blank."""
    clean_value = value.strip()
    if not clean_value:
        errors.append(f"{field_label} is required.")
    return clean_value


def validate_http_url(value, field_label, errors):
    """Require a normal http:// or https:// URL."""
    clean_value = validate_required(value, field_label, errors)
    if not clean_value:
        return clean_value

    parsed = urlparse(clean_value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        errors.append(f"{field_label} must start with http:// or https://.")

    return clean_value


def product_form_data():
    """Read and validate product form fields."""
    errors = []
    data = {
        "name": validate_required(request.form.get("name", ""), "Product name", errors),
        "url": validate_http_url(request.form.get("url", ""), "Product page URL", errors),
        "target_price": parse_non_negative_number(
            request.form.get("target_price", ""), "Target price", errors
        ),
        "big_drop_percent": parse_non_negative_number(
            request.form.get("big_drop_percent", ""), "Big drop percent", errors
        ),
        "notify_on_target": checkbox_value("notify_on_target"),
        "notify_on_big_drop": checkbox_value("notify_on_big_drop"),
        "status": clean_status(),
        "allow_crawlbase_fallback": checkbox_value("allow_crawlbase_fallback"),
        "prefer_crawlbase": checkbox_value("prefer_crawlbase"),
    }
    data["detected_store"] = detected_store_label(data["url"])
    return data, errors


def stock_form_data():
    """Read and validate stock form fields."""
    errors = []
    data = {
        "company_name": validate_required(
            request.form.get("company_name", ""), "Company name", errors
        ),
        "ticker": validate_required(request.form.get("ticker", ""), "Ticker", errors).upper(),
        "target_price": parse_non_negative_number(
            request.form.get("target_price", ""), "Target price", errors
        ),
        "daily_drop_percent": parse_non_negative_number(
            request.form.get("daily_drop_percent", ""), "Daily drop percent", errors
        ),
        "daily_rise_percent": parse_non_negative_number(
            request.form.get("daily_rise_percent", ""), "Daily rise percent", errors
        ),
        "notify_on_target": checkbox_value("notify_on_target"),
        "notify_on_big_drop": checkbox_value("notify_on_big_drop"),
        "status": clean_status(),
    }
    return data, errors


def flash_errors(errors):
    """Show each validation error as a normal Flask flash message."""
    for error in errors:
        flash(error, "error")


def telegram_config_present():
    """Check whether Telegram settings look configured without showing values."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    placeholder_values = {"", "replace_me", "replace_me_later"}
    return token not in placeholder_values and chat_id not in placeholder_values


def env_flag(name, default=False):
    """Read a simple true/false environment flag."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    """Read a small positive integer setting with a safe fallback."""
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(value, 0)


def canopy_status(db):
    """Return Canopy settings and usage without exposing the API key."""
    usage_month = datetime_month()
    row = db.execute(
        """
        SELECT request_count
        FROM api_usage
        WHERE provider = 'canopy'
          AND usage_month = ?
        """,
        (usage_month,),
    ).fetchone()
    monthly_limit = env_int("CANOPY_MONTHLY_LIMIT", 100)
    api_key = os.environ.get("CANOPY_API_KEY", "").strip()
    placeholder_values = {"", "replace_me", "replace_me_later"}
    return {
        "enabled": env_flag("ENABLE_CANOPY_AMAZON", False),
        "api_key_present": api_key not in placeholder_values,
        "auth_header": os.environ.get("CANOPY_AUTH_HEADER", "API-KEY").strip() or "API-KEY",
        "usage_month": usage_month,
        "request_count": row["request_count"] if row else 0,
        "monthly_limit": monthly_limit,
        "amazon_interval_hours": env_int("AMAZON_CHECK_INTERVAL_HOURS", 24),
    }


def crawlbase_status(db):
    """Return Crawlbase diagnostic settings without exposing tokens."""
    usage_day = datetime.now().strftime("%Y-%m-%d")
    row = db.execute(
        """
        SELECT request_count
        FROM api_usage
        WHERE provider = 'crawlbase'
          AND usage_month = ?
        """,
        (usage_day,),
    ).fetchone()
    normal_token = os.environ.get("CRAWLBASE_NORMAL_TOKEN", "").strip()
    js_token = os.environ.get("CRAWLBASE_JS_TOKEN", "").strip()
    placeholder_values = {"", "replace_me", "replace_me_later"}
    return {
        "enabled": env_flag("ENABLE_CRAWLBASE", False),
        "normal_token_present": normal_token not in placeholder_values,
        "js_token_present": js_token not in placeholder_values,
        "usage_day": usage_day,
        "request_count": row["request_count"] if row else 0,
        "daily_limit": env_int("CRAWLBASE_DAILY_LIMIT", 200),
        "check_interval_hours": env_int("CRAWLBASE_CHECK_INTERVAL_HOURS", 24),
        "default_country": os.environ.get("CRAWLBASE_DEFAULT_COUNTRY", "US").strip() or "US",
        "use_js_fallback": env_flag("CRAWLBASE_USE_JS_FALLBACK", False),
    }


def datetime_month():
    """Return the current calendar month key used for local API usage tracking."""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m")


def latest_worker_run_time(db):
    """Use the newest check row as the last worker run time."""
    product_row = db.execute("SELECT MAX(checked_at) AS checked_at FROM price_checks").fetchone()
    stock_row = db.execute("SELECT MAX(checked_at) AS checked_at FROM stock_checks").fetchone()
    timestamps = [
        row["checked_at"]
        for row in (product_row, stock_row)
        if row is not None and row["checked_at"]
    ]
    if not timestamps:
        return None
    return max(timestamps)


def worker_log_summary():
    """Read a small, safe summary from logs/worker.log if it exists."""
    log_file = BASE_DIR / "logs" / "worker.log"
    if not log_file.exists():
        return {
            "exists": False,
            "path": str(log_file),
            "last_modified": None,
            "last_status": "No log yet",
            "tail": [],
        }

    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    end_lines = [line for line in lines if "DadDeals worker end:" in line]
    last_status = end_lines[-1] if end_lines else "Log exists, but no end line was found."
    return {
        "exists": True,
        "path": str(log_file),
        "last_modified": log_file.stat().st_mtime,
        "last_status": last_status,
        "tail": lines[-8:],
    }


def backup_summary():
    """Return simple backup-folder status for Settings."""
    backup_dir = BASE_DIR / "backups"
    backup_files = sorted(backup_dir.glob("daddeals-*.db"), reverse=True)
    latest_backup = backup_files[0] if backup_files else None
    return {
        "exists": backup_dir.exists(),
        "path": str(backup_dir),
        "latest": latest_backup,
        "latest_time": datetime.fromtimestamp(latest_backup.stat().st_mtime, timezone.utc)
        if latest_backup
        else None,
    }


def latest_check_statuses(db, table_name, id_column):
    """Return latest check statuses keyed by product_id or stock_id."""
    rows = db.execute(
        f"""
        SELECT checks.{id_column} AS item_id, checks.status
        FROM {table_name} AS checks
        JOIN (
            SELECT {id_column}, MAX(id) AS latest_id
            FROM {table_name}
            GROUP BY {id_column}
        ) AS latest
          ON checks.id = latest.latest_id
        """
    ).fetchall()
    return {row["item_id"]: row["status"] for row in rows}


def target_hit_ids(db, item_type):
    """Return item ids that have ever had a target-hit alert."""
    rows = db.execute(
        """
        SELECT DISTINCT item_id
        FROM alerts
        WHERE item_type = ?
          AND title LIKE ?
        """,
        (item_type, "%target hit:%"),
    ).fetchall()
    return {row["item_id"] for row in rows if row["item_id"] is not None}


def status_label(item, latest_status, target_hits):
    """Choose one simple Dad-friendly status label."""
    if item["status"] == "paused":
        return "Paused", "paused"
    if latest_status == "failed":
        return "Last check failed", "failed"
    if item["id"] in target_hits:
        return "Target hit", "target-hit"
    return "Watching", "watching"


def decorate_items(rows, latest_statuses, target_hits):
    """Convert sqlite rows to dicts with a display status."""
    decorated = []
    for row in rows:
        item = dict(row)
        label, css_class = status_label(item, latest_statuses.get(item["id"]), target_hits)
        item["display_status"] = label
        item["display_status_class"] = css_class
        decorated.append(item)
    return decorated


def price_change_text(current_price, baseline_price):
    """Return a friendly price change string for dashboard/detail pages."""
    if current_price is None or baseline_price is None:
        return None

    difference = current_price - baseline_price
    if round(difference, 2) == 0:
        return "No change"

    direction = "higher" if difference > 0 else "lower"
    percent_text = ""
    if baseline_price:
        percent = abs((difference / baseline_price) * 100)
        percent_text = f" ({percent:.1f}%)"
    return f"${abs(difference):.2f} {direction}{percent_text}"


def product_check_summary(db, product_id):
    """Summarize product check history for one product."""
    latest_check = db.execute(
        """
        SELECT *
        FROM price_checks
        WHERE product_id = ?
        ORDER BY checked_at DESC, id DESC
        LIMIT 1
        """,
        (product_id,),
    ).fetchone()
    successful_checks = db.execute(
        """
        SELECT *
        FROM price_checks
        WHERE product_id = ?
          AND status = 'ok'
          AND current_price IS NOT NULL
        ORDER BY checked_at ASC, id ASC
        """,
        (product_id,),
    ).fetchall()

    first_success = successful_checks[0] if successful_checks else None
    latest_success = successful_checks[-1] if successful_checks else None
    previous_success = successful_checks[-2] if len(successful_checks) >= 2 else None
    latest_price = latest_success["current_price"] if latest_success else None

    if latest_check is None:
        status = "never"
        status_label_text = "Never checked"
    elif latest_check["status"] == "ok":
        status = "success"
        status_label_text = "Success"
    else:
        status = latest_check["status"]
        status_label_text = latest_check["status"].title()

    return {
        "latest_check": latest_check,
        "first_success": first_success,
        "latest_success": latest_success,
        "previous_success": previous_success,
        "latest_price": latest_price,
        "last_checked_at": latest_check["checked_at"] if latest_check else None,
        "last_status": status,
        "last_status_label": status_label_text,
        "last_message": latest_check["message"] if latest_check else None,
        "source_name": latest_check["source_name"] if latest_check else None,
        "source_url": latest_check["source_url"] if latest_check else None,
        "change_since_first": price_change_text(
            latest_price,
            first_success["current_price"] if first_success else None,
        ),
        "change_since_previous": price_change_text(
            latest_price,
            previous_success["current_price"] if previous_success else None,
        ),
    }


def attach_product_summaries(db, products):
    """Add check summary fields to dashboard product dictionaries."""
    for product in products:
        product["check_summary"] = product_check_summary(db, product["id"])
    return products


def run_single_product_check(db, product, force=False, force_crawlbase=False):
    """Run one product check from the web app without checking every product."""
    from worker import ProductFetchError, check_product

    try:
        result = check_product(
            db,
            product,
            dry_run=False,
            force=force,
            force_crawlbase=force_crawlbase,
        )
        db.commit()
        return result, None
    except ProductFetchError as error:
        db.rollback()
        return None, str(error)
    except Exception:
        db.rollback()
        return None, "DadDeals could not finish that product check."


def flash_product_check_result(product_name, result, error=None):
    """Show a friendly result after a web-triggered product check."""
    if error:
        flash(f"{product_name} was saved, but the first price check failed: {error}", "error")
        return

    message = result["messages"][0] if result["messages"] else "Check completed."
    if result["status"] == "ok":
        flash(f"{product_name} price check succeeded.", "success")
    elif result["status"] == "skipped":
        flash(f"{product_name} price check was skipped: {message}", "error")
    else:
        flash(f"{product_name} price check failed: {message}", "error")


def send_new_product_alerts(db, product_id, after_alert_id):
    """Send only new alerts created by the immediate product-add check."""
    if not telegram_config_present():
        return {"sent": 0, "failed": 0, "total": 0, "configured": False}

    from worker import alert_text, mark_alert_failed, mark_alert_sent, send_telegram_message

    alerts = db.execute(
        """
        SELECT *
        FROM alerts
        WHERE id > ?
          AND item_type = 'product'
          AND item_id = ?
          AND sent_at IS NULL
          AND COALESCE(delivery_status, 'unsent') != 'sent'
        ORDER BY id ASC
        """,
        (after_alert_id, product_id),
    ).fetchall()

    sent_count = 0
    failed_count = 0
    for alert in alerts:
        ok, error = send_telegram_message(alert_text(alert))
        if ok:
            mark_alert_sent(db, alert["id"])
            sent_count += 1
        else:
            mark_alert_failed(db, alert["id"], error)
            failed_count += 1

    db.commit()
    return {
        "sent": sent_count,
        "failed": failed_count,
        "total": len(alerts),
        "configured": True,
    }


def flash_initial_delivery_result(delivery_result):
    """Show the outcome of initial product alert Telegram delivery."""
    if not delivery_result["configured"] or delivery_result["total"] == 0:
        return

    if delivery_result["failed"]:
        flash(
            "DadDeals created an alert, but Telegram delivery failed. "
            "Check Settings and run python worker.py --send-alerts after fixing it.",
            "error",
        )
    elif delivery_result["sent"]:
        flash("Telegram alert sent for the new product.", "success")


def register_cli_commands(app):
    @app.cli.command("init-db")
    def init_db_command():
        """Initialize the SQLite database from schema.sql."""
        init_db()
        print(f"Initialized database at {database_path()}")


def register_routes(app):
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            password = request.form.get("password", "")
            if password == current_app_config("ADMIN_PASSWORD"):
                session.clear()
                session["logged_in"] = True
                flash("Logged in successfully.", "success")
                return redirect(request.args.get("next") or url_for("dashboard"))
            flash("That password did not match.", "error")

        return render_template("base.html", page_title="Log In", is_login=True)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("Logged out.", "success")
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def dashboard():
        db = get_db()
        product_rows = db.execute(
            "SELECT * FROM tracked_products ORDER BY created_at DESC"
        ).fetchall()
        stock_rows = db.execute(
            "SELECT * FROM tracked_stocks ORDER BY created_at DESC"
        ).fetchall()
        products = attach_product_summaries(db, decorate_items(
            product_rows,
            latest_check_statuses(db, "price_checks", "product_id"),
            target_hit_ids(db, "product"),
        ))
        stocks = decorate_items(
            stock_rows,
            latest_check_statuses(db, "stock_checks", "stock_id"),
            target_hit_ids(db, "stock"),
        )
        alerts = db.execute(
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        return render_template(
            "dashboard.html",
            products=products,
            stocks=stocks,
            alerts=alerts,
            page_title="Dashboard",
        )

    @app.route("/settings")
    @login_required
    def settings():
        db = get_db()
        alert_count = db.execute("SELECT COUNT(*) AS count FROM alerts").fetchone()["count"]
        return render_template(
            "settings.html",
            page_title="Settings",
            phase_label="DadDeals v2F - Crawlbase fallback",
            telegram_ready=telegram_config_present(),
            database_path=database_path(),
            last_worker_run=latest_worker_run_time(db),
            worker_log=worker_log_summary(),
            alert_count=alert_count,
            canopy=canopy_status(db),
            crawlbase=crawlbase_status(db),
            app_timezone=os.environ.get("APP_TIMEZONE", "America/Los_Angeles"),
            backup=backup_summary(),
        )

    @app.route("/alerts/clear-old", methods=["POST"])
    @login_required
    def clear_old_alerts():
        days_value = request.form.get("days", "30")

        if request.form.get("confirm") != "on":
            flash("Check the confirmation box before clearing old alerts.", "error")
            return redirect(url_for("settings"))

        db = get_db()

        if days_value == "all":
            if request.form.get("confirm_all") != "on":
                flash("Check the extra confirmation box before clearing all alerts.", "error")
                return redirect(url_for("settings"))

            result = db.execute("DELETE FROM alerts")
            db.commit()
            flash(
                f"Cleared all {result.rowcount} alert record(s). Check history stayed saved.",
                "success",
            )
            return redirect(url_for("settings"))

        try:
            days = int(days_value)
        except ValueError:
            days = 30

        if days not in {7, 30, 60, 90}:
            flash("Choose a valid alert cleanup age.", "error")
            return redirect(url_for("settings"))

        result = db.execute(
            "DELETE FROM alerts WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        db.commit()
        flash(f"Cleared {result.rowcount} alert(s) older than {days} days.", "success")
        return redirect(url_for("settings"))

    @app.route("/alerts/<int:alert_id>/delete", methods=["POST"])
    @login_required
    def delete_alert(alert_id):
        """Delete one alert row while leaving check history alone."""
        db = get_db()
        result = db.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
        db.commit()

        if result.rowcount:
            flash("Alert deleted. Product and stock check history stayed saved.", "success")
        else:
            flash("That alert was already gone.", "error")

        return redirect(url_for("dashboard"))

    @app.route("/alerts/delete-selected", methods=["POST"])
    @login_required
    def delete_selected_alerts():
        """Delete selected alert rows from the dashboard."""
        selected_ids = request.form.getlist("alert_ids")
        if not selected_ids:
            flash("Choose at least one alert before pressing Delete Selected Alerts.", "error")
            return redirect(url_for("dashboard"))

        if request.form.get("confirm_selected") != "on":
            flash("Check the confirmation box before deleting selected alerts.", "error")
            return redirect(url_for("dashboard"))

        try:
            alert_ids = [int(alert_id) for alert_id in selected_ids]
        except ValueError:
            flash("One selected alert was not valid. Nothing was deleted.", "error")
            return redirect(url_for("dashboard"))

        placeholders = ",".join("?" for _ in alert_ids)
        db = get_db()
        result = db.execute(
            f"DELETE FROM alerts WHERE id IN ({placeholders})",
            alert_ids,
        )
        db.commit()
        flash(
            f"Deleted {result.rowcount} selected alert(s). Check history stayed saved.",
            "success",
        )
        return redirect(url_for("dashboard"))

    @app.route("/products/add", methods=["GET", "POST"])
    @login_required
    def add_product():
        if request.method == "POST":
            data, errors = product_form_data()
            if errors:
                flash_errors(errors)
                return render_template(
                    "add_product.html",
                    page_title="Add Product",
                    form=data,
                    detected_store=data.get("detected_store", "Other website"),
                )

            db = get_db()
            cursor = db.execute(
                """
                INSERT INTO tracked_products (
                    name, url, target_price, big_drop_percent,
                    notify_on_target, notify_on_big_drop, status,
                    allow_crawlbase_fallback, prefer_crawlbase, last_detected_store
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["name"],
                    data["url"],
                    data["target_price"],
                    data["big_drop_percent"],
                    data["notify_on_target"],
                    data["notify_on_big_drop"],
                    data["status"],
                    data["allow_crawlbase_fallback"],
                    data["prefer_crawlbase"],
                    data["detected_store"],
                ),
            )
            db.commit()
            product = get_product_or_404(cursor.lastrowid)
            last_alert_id = db.execute(
                "SELECT COALESCE(MAX(id), 0) AS max_id FROM alerts"
            ).fetchone()["max_id"]
            result, error = run_single_product_check(db, product, force=True)
            flash("Product added.", "success")
            flash_product_check_result(product["name"], result, error)
            if result and result["alerts_created"]:
                delivery_result = send_new_product_alerts(db, product["id"], last_alert_id)
                flash_initial_delivery_result(delivery_result)
            return redirect(url_for("dashboard"))

        return render_template(
            "add_product.html",
            page_title="Add Product",
            form={},
            detected_store="Other website",
        )

    @app.route("/products/<int:product_id>")
    @login_required
    def product_detail(product_id):
        db = get_db()
        product = get_product_or_404(product_id)
        price_checks = db.execute(
            """
            SELECT *
            FROM price_checks
            WHERE product_id = ?
            ORDER BY checked_at DESC, id DESC
            LIMIT 10
            """,
            (product_id,),
        ).fetchall()
        return render_template(
            "product_detail.html",
            product=product,
            summary=product_check_summary(db, product_id),
            price_checks=price_checks,
            page_title=product["name"],
        )

    @app.route("/products/<int:product_id>/retry-check", methods=["POST"])
    @login_required
    def retry_product_check(product_id):
        db = get_db()
        product = get_product_or_404(product_id)
        result, error = run_single_product_check(db, product, force=True)
        flash_product_check_result(product["name"], result, error)
        return redirect(request.referrer or url_for("product_detail", product_id=product_id))

    @app.route("/products/<int:product_id>/retry-crawlbase", methods=["POST"])
    @login_required
    def retry_product_check_with_crawlbase(product_id):
        db = get_db()
        product = get_product_or_404(product_id)
        result, error = run_single_product_check(
            db,
            product,
            force=True,
            force_crawlbase=True,
        )
        flash_product_check_result(product["name"], result, error)
        return redirect(request.referrer or url_for("product_detail", product_id=product_id))

    @app.route("/products/retry-failed", methods=["POST"])
    @login_required
    def retry_failed_products():
        db = get_db()
        products = db.execute(
            """
            SELECT tracked_products.*
            FROM tracked_products
            JOIN (
                SELECT product_id, MAX(id) AS latest_id
                FROM price_checks
                GROUP BY product_id
            ) AS latest
              ON tracked_products.id = latest.product_id
            JOIN price_checks
              ON price_checks.id = latest.latest_id
            WHERE tracked_products.status = 'active'
              AND price_checks.status IN ('failed', 'skipped')
            ORDER BY price_checks.checked_at DESC, price_checks.id DESC
            LIMIT 5
            """
        ).fetchall()

        if not products:
            flash("No failed or skipped product checks need retrying.", "success")
            return redirect(url_for("dashboard"))

        succeeded = 0
        not_succeeded = 0
        for product in products:
            result, error = run_single_product_check(db, product, force=True)
            if error or result is None or result["status"] != "ok":
                not_succeeded += 1
            else:
                succeeded += 1

        flash(
            f"Retried {len(products)} product(s): {succeeded} succeeded, {not_succeeded} still need attention.",
            "success" if succeeded else "error",
        )
        return redirect(url_for("dashboard"))

    @app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_product(product_id):
        product = get_product_or_404(product_id)
        if request.method == "POST":
            data, errors = product_form_data()
            if errors:
                flash_errors(errors)
                return render_template(
                    "edit_product.html",
                    product=product,
                    form=data,
                    detected_store=data.get("detected_store", "Other website"),
                    page_title=f"Edit {product['name']}",
                )

            db = get_db()
            db.execute(
                """
                UPDATE tracked_products
                SET name = ?,
                    url = ?,
                    target_price = ?,
                    big_drop_percent = ?,
                    notify_on_target = ?,
                    notify_on_big_drop = ?,
                    status = ?,
                    allow_crawlbase_fallback = ?,
                    prefer_crawlbase = ?,
                    last_detected_store = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    data["name"],
                    data["url"],
                    data["target_price"],
                    data["big_drop_percent"],
                    data["notify_on_target"],
                    data["notify_on_big_drop"],
                    data["status"],
                    data["allow_crawlbase_fallback"],
                    data["prefer_crawlbase"],
                    data["detected_store"],
                    product_id,
                ),
            )
            db.commit()
            flash("Product updated.", "success")
            return redirect(url_for("product_detail", product_id=product_id))

        product = get_product_or_404(product_id)
        return render_template(
            "edit_product.html",
            product=product,
            form={},
            detected_store=product["last_detected_store"] or detected_store_label(product["url"]),
            page_title=f"Edit {product['name']}",
        )

    @app.route("/products/<int:product_id>/toggle", methods=["POST"])
    @login_required
    def toggle_product(product_id):
        product = get_product_or_404(product_id)
        new_status = "active" if product["status"] == "paused" else "paused"
        get_db().execute(
            """
            UPDATE tracked_products
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_status, product_id),
        )
        get_db().commit()
        flash(f"Product is now {new_status}.", "success")
        return redirect(url_for("edit_product", product_id=product_id))

    @app.route("/products/<int:product_id>/delete", methods=["POST"])
    @login_required
    def delete_product(product_id):
        get_product_or_404(product_id)
        get_db().execute("DELETE FROM tracked_products WHERE id = ?", (product_id,))
        get_db().commit()
        flash("Product deleted.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/stocks/add", methods=["GET", "POST"])
    @login_required
    def add_stock():
        if request.method == "POST":
            data, errors = stock_form_data()
            if errors:
                flash_errors(errors)
                return render_template("add_stock.html", page_title="Add Stock", form=data)

            db = get_db()
            db.execute(
                """
                INSERT INTO tracked_stocks (
                    company_name, ticker, target_price, daily_drop_percent,
                    daily_rise_percent, notify_on_target,
                    notify_on_big_drop, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["company_name"],
                    data["ticker"],
                    data["target_price"],
                    data["daily_drop_percent"],
                    data["daily_rise_percent"],
                    data["notify_on_target"],
                    data["notify_on_big_drop"],
                    data["status"],
                ),
            )
            db.commit()
            flash("Stock added.", "success")
            return redirect(url_for("dashboard"))

        return render_template("add_stock.html", page_title="Add Stock", form={})

    @app.route("/stocks/<int:stock_id>")
    @login_required
    def stock_detail(stock_id):
        db = get_db()
        stock = get_stock_or_404(stock_id)
        stock_checks = db.execute(
            """
            SELECT *
            FROM stock_checks
            WHERE stock_id = ?
            ORDER BY checked_at DESC, id DESC
            LIMIT 10
            """,
            (stock_id,),
        ).fetchall()
        return render_template(
            "stock_detail.html",
            stock=stock,
            stock_checks=stock_checks,
            page_title=stock["ticker"],
        )

    @app.route("/stocks/<int:stock_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_stock(stock_id):
        stock = get_stock_or_404(stock_id)
        if request.method == "POST":
            data, errors = stock_form_data()
            if errors:
                flash_errors(errors)
                return render_template(
                    "edit_stock.html",
                    stock=stock,
                    form=data,
                    page_title=f"Edit {stock['ticker']}",
                )

            db = get_db()
            db.execute(
                """
                UPDATE tracked_stocks
                SET company_name = ?,
                    ticker = ?,
                    target_price = ?,
                    daily_drop_percent = ?,
                    daily_rise_percent = ?,
                    notify_on_target = ?,
                    notify_on_big_drop = ?,
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    data["company_name"],
                    data["ticker"],
                    data["target_price"],
                    data["daily_drop_percent"],
                    data["daily_rise_percent"],
                    data["notify_on_target"],
                    data["notify_on_big_drop"],
                    data["status"],
                    stock_id,
                ),
            )
            db.commit()
            flash("Stock updated.", "success")
            return redirect(url_for("stock_detail", stock_id=stock_id))

        stock = get_stock_or_404(stock_id)
        return render_template(
            "edit_stock.html",
            stock=stock,
            form={},
            page_title=f"Edit {stock['ticker']}",
        )

    @app.route("/stocks/<int:stock_id>/toggle", methods=["POST"])
    @login_required
    def toggle_stock(stock_id):
        stock = get_stock_or_404(stock_id)
        new_status = "active" if stock["status"] == "paused" else "paused"
        get_db().execute(
            """
            UPDATE tracked_stocks
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_status, stock_id),
        )
        get_db().commit()
        flash(f"Stock is now {new_status}.", "success")
        return redirect(url_for("edit_stock", stock_id=stock_id))

    @app.route("/stocks/<int:stock_id>/delete", methods=["POST"])
    @login_required
    def delete_stock(stock_id):
        get_stock_or_404(stock_id)
        get_db().execute("DELETE FROM tracked_stocks WHERE id = ?", (stock_id,))
        get_db().commit()
        flash("Stock deleted.", "success")
        return redirect(url_for("dashboard"))


def get_product_or_404(product_id):
    product = get_db().execute(
        "SELECT * FROM tracked_products WHERE id = ?", (product_id,)
    ).fetchone()
    if product is None:
        abort(404)
    return product


def get_stock_or_404(stock_id):
    stock = get_db().execute(
        "SELECT * FROM tracked_stocks WHERE id = ?", (stock_id,)
    ).fetchone()
    if stock is None:
        abort(404)
    return stock


app = create_app()


def main():
    """Run beginner-friendly commands from `python app.py`."""
    if len(sys.argv) > 1 and sys.argv[1] == "--init-db":
        with app.app_context():
            init_db()
            print(f"Database is ready at {database_path()}")
        return

    if len(sys.argv) > 1:
        print("Unknown option. Use `python app.py` or `python app.py --init-db`.")
        sys.exit(2)

    app.run(
        host=app.config["HOST"],
        port=app.config["PORT"],
        debug=False,
    )


if __name__ == "__main__":
    main()
