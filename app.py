import os
import sqlite3
from functools import wraps
from pathlib import Path

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


# Load settings from a local .env file if one exists.
# The real .env file is intentionally not committed to source control.
load_dotenv()


BASE_DIR = Path(__file__).resolve().parent


def create_app():
    """Create and configure the Flask app.

    Keeping this setup in one function makes the app easier to test later,
    while still allowing `python app.py` to run it directly for beginners.
    """
    app = Flask(__name__, instance_path=str(BASE_DIR / "instance"))
    app.config["SECRET_KEY"] = os.environ.get("APP_SECRET_KEY", "dev-only-change-me")
    app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD", "replace_me")
    app.config["DATABASE_PATH"] = os.environ.get(
        "DATABASE_PATH", str(BASE_DIR / "instance" / "daddash.db")
    )

    # Make sure the instance folder exists. Flask's instance folder is the
    # right place for local data that should not be committed, like SQLite DBs.
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

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
    db.commit()


def ensure_database():
    """Initialize the database if the configured database file is missing."""
    if not database_path().exists():
        init_db()


def login_required(view):
    """Require the single admin password before accessing a route."""

    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped_view


def parse_money(value):
    """Convert a form field to a float, or None when the field is blank."""
    value = value.strip()
    if not value:
        return None
    return float(value)


def parse_percent(value):
    """Convert a percentage form field to a float, or None when blank."""
    value = value.strip()
    if not value:
        return None
    return float(value)


def checkbox_value(name):
    """HTML checkboxes only submit a value when checked."""
    return 1 if request.form.get(name) == "on" else 0


def clean_status():
    """Only allow statuses the UI knows how to display."""
    status = request.form.get("status", "active")
    if status not in {"active", "paused"}:
        return "active"
    return status


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
        products = db.execute(
            "SELECT * FROM tracked_products ORDER BY created_at DESC"
        ).fetchall()
        stocks = db.execute(
            "SELECT * FROM tracked_stocks ORDER BY created_at DESC"
        ).fetchall()
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

    @app.route("/products/add", methods=["GET", "POST"])
    @login_required
    def add_product():
        if request.method == "POST":
            try:
                db = get_db()
                db.execute(
                    """
                    INSERT INTO tracked_products (
                        name, url, target_price, big_drop_percent,
                        notify_on_target, notify_on_big_drop, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.form["name"].strip(),
                        request.form["url"].strip(),
                        parse_money(request.form.get("target_price", "")),
                        parse_percent(request.form.get("big_drop_percent", "")),
                        checkbox_value("notify_on_target"),
                        checkbox_value("notify_on_big_drop"),
                        clean_status(),
                    ),
                )
                db.commit()
                flash("Product added.", "success")
                return redirect(url_for("dashboard"))
            except (KeyError, ValueError):
                flash("Please check the product fields and try again.", "error")

        return render_template("add_product.html", page_title="Add Product")

    @app.route("/products/<int:product_id>")
    @login_required
    def product_detail(product_id):
        product = get_product_or_404(product_id)
        return render_template(
            "product_detail.html", product=product, page_title=product["name"]
        )

    @app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_product(product_id):
        product = get_product_or_404(product_id)
        if request.method == "POST":
            try:
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
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        request.form["name"].strip(),
                        request.form["url"].strip(),
                        parse_money(request.form.get("target_price", "")),
                        parse_percent(request.form.get("big_drop_percent", "")),
                        checkbox_value("notify_on_target"),
                        checkbox_value("notify_on_big_drop"),
                        clean_status(),
                        product_id,
                    ),
                )
                db.commit()
                flash("Product updated.", "success")
                return redirect(url_for("product_detail", product_id=product_id))
            except (KeyError, ValueError):
                flash("Please check the product fields and try again.", "error")

        product = get_product_or_404(product_id)
        return render_template(
            "edit_product.html", product=product, page_title=f"Edit {product['name']}"
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
            try:
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
                        request.form["company_name"].strip(),
                        request.form["ticker"].strip().upper(),
                        parse_money(request.form.get("target_price", "")),
                        parse_percent(request.form.get("daily_drop_percent", "")),
                        parse_percent(request.form.get("daily_rise_percent", "")),
                        checkbox_value("notify_on_target"),
                        checkbox_value("notify_on_big_drop"),
                        clean_status(),
                    ),
                )
                db.commit()
                flash("Stock added.", "success")
                return redirect(url_for("dashboard"))
            except (KeyError, ValueError):
                flash("Please check the stock fields and try again.", "error")

        return render_template("add_stock.html", page_title="Add Stock")

    @app.route("/stocks/<int:stock_id>")
    @login_required
    def stock_detail(stock_id):
        stock = get_stock_or_404(stock_id)
        return render_template(
            "stock_detail.html", stock=stock, page_title=stock["ticker"]
        )

    @app.route("/stocks/<int:stock_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_stock(stock_id):
        stock = get_stock_or_404(stock_id)
        if request.method == "POST":
            try:
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
                        request.form["company_name"].strip(),
                        request.form["ticker"].strip().upper(),
                        parse_money(request.form.get("target_price", "")),
                        parse_percent(request.form.get("daily_drop_percent", "")),
                        parse_percent(request.form.get("daily_rise_percent", "")),
                        checkbox_value("notify_on_target"),
                        checkbox_value("notify_on_big_drop"),
                        clean_status(),
                        stock_id,
                    ),
                )
                db.commit()
                flash("Stock updated.", "success")
                return redirect(url_for("stock_detail", stock_id=stock_id))
            except (KeyError, ValueError):
                flash("Please check the stock fields and try again.", "error")

        stock = get_stock_or_404(stock_id)
        return render_template(
            "edit_stock.html", stock=stock, page_title=f"Edit {stock['ticker']}"
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


if __name__ == "__main__":
    # host=127.0.0.1 is safest for local PC use.
    # On the Raspberry Pi, use: flask --app app run --host 0.0.0.0
    app.run(debug=True)
