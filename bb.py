# ======================================================================
# blood_bank_flask_v2_fixed.py
# Fully refactored backend (merged, corrected, and dashboard-updated)
# ======================================================================
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_from_directory, current_app, abort
# imports at the top ONLY
from flask import Flask, request, jsonify, render_template, flash, redirect, url_for
import math
import os

import sqlite3
from datetime import datetime
from io import BytesIO
import csv
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    send_file, abort, jsonify, Response, make_response
)
from flask_login import (
    LoginManager, login_user, login_required,
    logout_user, current_user, UserMixin
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Optional PDF export (ReportLab)
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import simpleSplit
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False


# ======================================================================
# CONFIG
# ======================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "blood_bank.db")

app = Flask(__name__)
# --- File upload settings ---
ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "gif"}
MAX_IMAGE_BYTES = 2 * 1024 * 1024

UPLOAD_FOLDER = os.path.join("static", "uploads")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(os.path.join(BASE_DIR, UPLOAD_FOLDER), exist_ok=True)
app.secret_key = "dev-secret-change-this"  # change in production

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


# ======================================================================
# DB HELPERS
# ======================================================================

def rows_to_dicts(rows):
    """
    Converts a list of sqlite3.Row objects (or lists/tuples) into a list of dictionaries.
    Ensures all values are JSON-serializable.
    """
    if not rows:
        return []

    # Try to determine column names if rows are sqlite3.Row objects
    if hasattr(rows[0], 'keys'):
        columns = rows[0].keys()
    else:
        # If rows are just tuples/lists, we can't reliably convert them to dicts
        # unless columns are explicitly passed. For now, return them as is,
        # but the JSON issue likely happens with dicts.
        return rows

    result = []
    for row in rows:
        row_dict = {}
        for col_name in columns:
            value = row[col_name]

            # --- JSON Safety Check ---
            if isinstance(value, datetime):
                # Convert datetime objects to ISO format string
                row_dict[col_name] = value.isoformat()
            elif value is None:
                # None is fine in Python/JSON, but sometimes old library wrappers
                # return an un-serializable 'Undefined' object for missing data.
                # Explicitly cast to Python's None or a safe default.
                row_dict[col_name] = None
            elif not isinstance(value, (int, float, str, bool, list, dict)):
                # Catch any other non-standard object (like old SQLite types)
                # and convert to string defensively.
                row_dict[col_name] = str(value)
            else:
                row_dict[col_name] = value

        result.append(row_dict)

    return result

def row_get(row, key_or_index, default=None):
    """Safely retrieve a value from a row object."""
    if row is None:
        return default
    try:
        # Handle dict-like access (sqlite3.Row or actual dict)
        value = row[key_or_index]
    except (IndexError, KeyError, TypeError):
        return default

    # JSON Safety Check for the single value
    if isinstance(value, datetime):
        return value.isoformat()
    elif value is None:
        return None
    elif not isinstance(value, (int, float, str, bool, list, dict)):
        return str(value)
    else:
        return value

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def db_execute(sql, params=()):
    """Executes query and commits changes.
    Uses try...finally to ensure connection is always closed, preventing locks.
    """
    conn = get_db()
    lastrowid = None
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        lastrowid = cur.lastrowid
        return lastrowid
    finally:
        # CRITICAL FIX: Guarantee connection closure to release the DB lock.
        if conn:
            conn.close()


def db_fetchall(sql, params=()):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        return rows
    finally:
        # CRITICAL FIX: Guarantee connection closure to release the DB lock.
        if conn:
            conn.close()


def db_fetchone(sql, params=()):
    """Executes query and returns a single row.
    Uses try...finally to ensure connection is always closed, preventing locks.
    """
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return row
    finally:
        # CRITICAL FIX: Guarantee connection closure to release the DB lock.
        if conn:
            conn.close()


def rows_to_dicts(rows):
    """Convert sqlite Row list to list of dicts (JSON-serializable)."""
    return [dict(r) for r in rows]


def row_get(row, key, default=None):
    """Safe access helper for sqlite3.Row objects (no .get())."""
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        # fallback: treat row as dict-like if possible
        try:
            return dict(row).get(key, default)
        except Exception:
            return default
#----------------------------------------------------
# Custom Decorators for Role-Based Access Control
# ----------------------------------------------------

def admin_required(f):
    """
    Custom decorator to restrict access to only users with the 'admin' role.
    """
    @wraps(f) # <--- 'wraps' is now defined
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))

        if current_user.role != 'admin':
            flash("Access denied. Admin privileges required.", "danger")
            return redirect(url_for('dashboard'))

        return f(*args, **kwargs)
    return decorated_function

# ======================================================================
# INITIAL DB CREATION
# ======================================================================

def init_db():
    if os.path.exists(DB_PATH):
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # users
    cur.execute(
        """CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            role TEXT DEFAULT '',
            linked_entity_id INTEGER DEFAULT NULL,
            profile_pic TEXT DEFAULT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )

    # donors
    cur.execute(
        """CREATE TABLE donors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            blood_group TEXT,
            phone TEXT,
            last_donation TEXT,
            total_units INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )

    # requests
    cur.execute(
        """CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_id INTEGER,
            requester_name TEXT,
            requester_username TEXT,
            blood_group TEXT,
            units INTEGER,
            status TEXT DEFAULT 'open',
            accepted_by INTEGER DEFAULT NULL,
            accepted_by_name TEXT,
            assigned_donor_id INTEGER DEFAULT NULL,
            assigned_donor_name TEXT,
            note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )

    # donations
    cur.execute(
        """CREATE TABLE donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            donor_id INTEGER,
            donor_name TEXT,
            blood_group TEXT,
            units INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )

    # issues
    cur.execute(
        """CREATE TABLE issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT,
            reference_id INTEGER,
            blood_group TEXT,
            units INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )

    # store
    cur.execute(
        """CREATE TABLE store (
            blood_group TEXT PRIMARY KEY,
            units INTEGER DEFAULT 0
        )"""
    )

    # seed store
    groups = ['A+','A-','B+','B-','AB+','AB-','O+','O-']
    for g in groups:
        cur.execute("INSERT INTO store (blood_group, units) VALUES (?, 0)", (g,))

    # default admin
    admin_pw = generate_password_hash("admin")
    cur.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                ("admin", admin_pw, "admin"))

    conn.commit()
    conn.close()


init_db()


# ======================================================================
# USER MODEL (Flask-Login)
# ======================================================================

class User(UserMixin):
    def __init__(self, row):
        # 1. Standard Flask-Login attributes
        self.id = row_get(row, 'id')
        self.username = row_get(row, 'username')
        self.password_hash = row_get(row, 'password_hash')
        self.role = row_get(row, 'role', 'donor')

        # 🌟 CRITICAL FIX: Assign to a private variable, NOT the public property.
        self._is_active = row_get(row, 'is_active', 1) == 1

        # 2. Custom attributes (to fix 'Unknown' blood type and 'name' error)
        self.name = row_get(row, 'name')
        self.blood_type = row_get(row, 'blood_type')
        self.linked_entity_id = row_get(row, 'linked_entity_id')
        self.email_id = row_get(row, 'email_id')
        self.phone = row_get(row, 'phone')

    def get_id(self):
        return str(self.id)

    # 🌟 CRITICAL FIX: Define the required property getter to read the private variable
    @property
    def is_active(self):
        """Returns True if the user is active (enabled)."""
        return self._is_active


@login_manager.user_loader
def load_user(user_id):
    # Ensure ALL required columns are explicitly selected.
    row = db_fetchone(
        """
        SELECT
            id, username, role, password_hash, name,
            blood_type, linked_entity_id, is_active, email_id, phone
        FROM users WHERE id = ?
        """,
        (user_id,)
    )

    if row:
        # The User constructor will now correctly map the 'blood_type' column.
        return User(row)

    return None


# ======================================================================
# ROLE HELPERS
# ======================================================================

def is_admin():
    return current_user.is_authenticated and current_user.role == 'admin'


def is_staff():
    return current_user.is_authenticated and current_user.role == 'staff'


def is_donor():
    return current_user.is_authenticated and current_user.role == 'donor'


def is_requester():
    return current_user.is_authenticated and current_user.role == 'requester'


# ======================================================================
# TEMPLATE HELPERS
# ======================================================================

@app.context_processor
def inject_helpers():
    def current_year():
        return datetime.utcnow().year

    def utcnow():
        return datetime.utcnow().isoformat(sep=' ', timespec='seconds')

    return {"current_year": current_year, "utcnow": utcnow}


# ======================================================================
# AUTH ROUTES
# ======================================================================

@app.route("/")
def home():
    return redirect(url_for("dashboard"))


@app.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        row = db_fetchone("SELECT * FROM users WHERE username = ?", (username,))
        if not row:
            flash("Invalid username or password", "danger")
            return redirect(url_for("login"))
        user = User(row)
        if not check_password_hash(user.password_hash, password):
            flash("Invalid username or password", "danger")
            return redirect(url_for("login"))
        login_user(user)
        flash("Logged in successfully.", "success")
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout", methods=("GET", "POST"))
@login_required
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


@app.route("/change-password", methods=("GET", "POST"))
@login_required
def change_password():
    if request.method == "POST":
        old = request.form.get("old_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if new != confirm:
            flash("New password and confirm do not match.", "danger")
            return redirect(url_for("change_password"))
        row = db_fetchone("SELECT * FROM users WHERE id = ?", (current_user.id,))
        if not row:
            flash("User not found.", "danger")
            return redirect(url_for("login"))
        if not check_password_hash(row["password_hash"], old):
            flash("Old password is incorrect.", "danger")
            return redirect(url_for("change_password"))
        new_hash = generate_password_hash(new)
        db_execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, current_user.id))
        flash("Password changed.", "success")
        return redirect(url_for("dashboard"))
    return render_template("change_password.html")

# ======================================================================
# DASHBOARD ROUTING (role-based)
# ======================================================================

@app.route("/dashboard")
@login_required
def dashboard():
    # ---------- ADMIN / STAFF ----------
    if is_admin() or is_staff():

        # Top cards
        total_donors = int(
            row_get(
                db_fetchone(
                    "SELECT COUNT(*) AS c "
                    "FROM users "
                    "WHERE role = 'donor' AND is_active = 1"
                ),
                "c",
                0,
            ) or 0
        )

        # Number of blood requests (you can change this to only open if you want)
        total_requests = int(
            row_get(
                db_fetchone(
                    "SELECT COUNT(*) AS c "
                    "FROM requests"
                ),
                "c",
                0,
            ) or 0
        )

        # Number of donation records (transactions), NOT units
        # Excluding any 'Store donation' rows (donor_id = -1) if you ever had them
        total_donations = int(
            row_get(
                db_fetchone(
                    "SELECT COUNT(*) AS c "
                    "FROM donations "
                    "WHERE donor_id IS NULL OR donor_id != -1"
                ),
                "c",
                0,
            ) or 0
        )

        # Latest open requests for the table on dashboard (if used)
        latest_open_requests = rows_to_dicts(
            db_fetchall(
                """
                SELECT *
                FROM requests
                WHERE status = 'open'
                ORDER BY created_at DESC
                LIMIT 5
                """
            )
        )

        # ---------- INVENTORY: SUM(units in donations) - SUM(units in issues) ----------
        # This is UNITS in stock, not number of donations.
        inv_row = db_fetchone(
            """
            SELECT
              COALESCE(
                (
                  SELECT SUM(units)
                  FROM donations
                  WHERE donor_id IS NULL OR donor_id != -1
                ),
                0
              )
              -
              COALESCE(
                (
                  SELECT SUM(units)
                  FROM issues
                  WHERE origin IN ('store_issue','request_completed')
                ),
                0
              ) AS units_in_stock
            """
        )
        store_total = max(0, int(row_get(inv_row, "units_in_stock", 0) or 0))

        # Per-blood-group stock for "Blood Store Distribution" chart
        store_summary = rows_to_dicts(
            db_fetchall(
                """
                WITH
                  d AS (
                    SELECT blood_group, SUM(units) AS donated
                    FROM donations
                    WHERE donor_id IS NULL OR donor_id != -1
                    GROUP BY blood_group
                  ),
                  i AS (
                    SELECT blood_group, SUM(units) AS issued
                    FROM issues
                    WHERE origin IN ('store_issue','request_completed')
                    GROUP BY blood_group
                  ),
                  bg AS (
                    SELECT blood_group FROM d
                    UNION
                    SELECT blood_group FROM i
                  )
                SELECT
                  bg.blood_group,
                  COALESCE(d.donated, 0) - COALESCE(i.issued, 0) AS total_units
                FROM bg
                LEFT JOIN d ON d.blood_group = bg.blood_group
                LEFT JOIN i ON i.blood_group = bg.blood_group
                ORDER BY bg.blood_group
                """
            )
        )

        # ---------- Monthly donations chart (real donations only) ----------
        monthly_donations_json = rows_to_dicts(
            db_fetchall(
                """
                SELECT
                    strftime('%Y-%m', created_at) AS month_year,
                    SUM(units) AS total_units
                FROM donations
                WHERE created_at IS NOT NULL
                  AND (donor_id IS NULL OR donor_id != -1)
                GROUP BY month_year
                ORDER BY month_year DESC
                LIMIT 12
                """
            )
        )

        return render_template(
            "dashboard.html",
            total_donors=total_donors,
            total_requests=total_requests,
            total_donations=total_donations,  # number of donation records
            store_total=store_total,          # total UNITS in stock
            store_summary=store_summary,
            monthly_donations_json=monthly_donations_json,
            latest_open_requests=latest_open_requests,
        )

    # ---------- DONOR ----------
    if is_donor():
        return redirect(url_for("donor_dashboard"))

    # ---------- REQUESTER ----------
    if is_requester():
        return redirect(url_for("requester_dashboard"))

    flash("Unsupported user role. Redirecting to login.", "warning")
    return redirect(url_for("login"))

# ----------------------------------------------------------------------
# ADMIN DASHBOARD (replacement with required template variables)
# ----------------------------------------------------------------------
@app.route("/dashboard/admin")
@login_required
def admin_dashboard():
    if not is_admin():
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    total_donors = int(row_get(db_fetchone("SELECT COUNT(*) AS c FROM users WHERE role='donor'"), "c", 0) or 0)
    total_requests = int(row_get(db_fetchone("SELECT COUNT(*) AS c FROM requests"), "c", 0) or 0)
    total_donations = int(row_get(db_fetchone("SELECT COUNT(*) AS c FROM donations"), "c", 0) or 0)
    total_issues = int(row_get(db_fetchone("SELECT COUNT(*) AS c FROM issues"), "c", 0) or 0)

    # store summary (dict: blood_group -> units)
    store_rows = db_fetchall("SELECT blood_group, units FROM store ORDER BY blood_group")
    store_summary = {r["blood_group"]: int(r["units"] or 0) for r in store_rows}

    # total stock count
    store_total_row = db_fetchone("SELECT SUM(units) AS total_units FROM store")
    store_total = int(row_get(store_total_row, "total_units", 0) or 0)

    # monthly donations (last 12 months)
    rows = db_fetchall("""
        SELECT strftime('%Y-%m', created_at) AS month, SUM(units) AS total
        FROM donations
        GROUP BY month
        ORDER BY month ASC
    """)
    monthly_lookup = {r["month"]: int(r["total"] or 0) for r in rows}

    # Generate last 12 months string list (YYYY-MM)
    from datetime import datetime

    now = datetime.utcnow().replace(day=1)  # first day of current month
    months = []
    for i in range(11, -1, -1):
        # Move back i months manually using pure Python
        year = now.year
        month = now.month - i
        while month <= 0:
            month += 12
            year -= 1
        months.append(f"{year:04d}-{month:02d}")

    monthly = [{"month": m, "total": monthly_lookup.get(m, 0)} for m in months]

    return render_template(
        "dashboard.html",
        title="Admin Dashboard",
        role="admin",
        total_donors=total_donors,
        total_requests=total_requests,
        total_donations=total_donations,
        total_issues=total_issues,
        store_summary=store_summary,
        store_total=store_total,
        monthly_donations_json=monthly
    )

# ----------------------------------------------------------------------
# STAFF DASHBOARD (replacement)
# ----------------------------------------------------------------------
@app.route("/dashboard/staff")
@login_required
def staff_dashboard():
    if not is_staff():
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    total_requests_open = int(row_get(db_fetchone("SELECT COUNT(*) AS c FROM requests WHERE status='open'"), "c", 0) or 0)
    total_donations = int(row_get(db_fetchone("SELECT COUNT(*) AS c FROM donations"), "c", 0) or 0)

    # total units in store
    store_total_row = db_fetchone("SELECT SUM(units) AS c FROM store")
    store_total = int(row_get(store_total_row, "c", 0) or 0)

    # store summary & monthly donations for charts
    store_rows = db_fetchall("SELECT blood_group, units FROM store ORDER BY blood_group")
    store_summary = {r["blood_group"]: int(r["units"] or 0) for r in store_rows}

    rows = db_fetchall("""
        SELECT strftime('%Y-%m', created_at) AS month, SUM(units) AS total
        FROM donations
        GROUP BY month
        ORDER BY month ASC
    """)
    monthly = [{"month": r["month"], "total": int(r["total"] or 0)} for r in rows]

    return render_template(
        "staff_dashboard.html",
        total_requests_open=total_requests_open,
        total_donations=total_donations,
        store_total=store_total,
        store_summary=store_summary,
        monthly_donations_json=monthly
    )

# ----------------------------------------------------------------------
# DONOR DASHBOARD (replacement)
# ----------------------------------------------------------------------
@app.route("/donor_dashboard")
@login_required
def donor_dashboard():
    # Only donors should see this dashboard
    if not is_donor():
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    try:
        donor_user_id = int(current_user.id)
    except Exception:
        flash("Cannot identify donor account.", "danger")
        return redirect(url_for("dashboard"))

    # ---------- Donation stats for this donor ----------
    stats_row = db_fetchone(
        """
        SELECT
            COUNT(*)               AS donations_count,
            COALESCE(SUM(units),0) AS units_donated
        FROM donations
        WHERE donor_id = ?
        """,
        (donor_user_id,),
    )

    total_donations = int(row_get(stats_row, "donations_count", 0) or 0)
    units_donated   = int(row_get(stats_row, "units_donated", 0) or 0)

    # Donation history
    donation_rows = db_fetchall(
        """
        SELECT
            id,
            blood_group,
            units,
            donation_date,
            created_at
        FROM donations
        WHERE donor_id = ?
        ORDER BY COALESCE(donation_date, created_at) DESC, id DESC
        """,
        (donor_user_id,),
    )
    donations = rows_to_dicts(donation_rows)

    # ---------- Assigned requests: SHOW ALL STATUSES ----------
    assigned_rows = db_fetchall(
        """
        SELECT
            id,
            requester_name,
            blood_group,
            units,
            status,
            scheduled_at,
            created_at
        FROM requests
        WHERE assigned_donor_id = ?
        ORDER BY created_at DESC
        """,
        (donor_user_id,),
    )
    assigned_requests = rows_to_dicts(assigned_rows)
    assigned_count = len(assigned_requests)

    # ---------- Accepted / scheduled / completed COUNT for card ----------
    accepted_row = db_fetchone(
        """
        SELECT COUNT(*) AS c
        FROM requests
        WHERE assigned_donor_id = ?
          AND status IN ('accepted','scheduled','completed','completed_from_store')
        """,
        (donor_user_id,),
    )
    accepted_count = int(row_get(accepted_row, "c", 0) or 0)

    return render_template(
        "donor_dashboard.html",
        total_donations=total_donations,
        units_donated=units_donated,
        assigned_count=assigned_count,
        accepted_count=accepted_count,
        donations=donations,
        assigned_requests=assigned_requests,
    )



# ----------------------------------------------------------------------
# REQUESTER DASHBOARD (replacement)
# ----------------------------------------------------------------------
@app.route("/dashboard/requester")
@login_required
def requester_dashboard():
    # Only requesters should see this dashboard
    if not is_requester():
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    try:
        requester_user_id = int(current_user.id)
    except Exception:
        flash("Cannot identify requester account.", "danger")
        return redirect(url_for("dashboard"))

    # ---------- Aggregate stats for this requester ----------
    stats_row = db_fetchone(
        """
        SELECT
            COUNT(*) AS total_requests,
            SUM(CASE WHEN status IN ('accepted','scheduled','completed') THEN 1 ELSE 0 END) AS accepted_requests,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_requests,
            SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_requests
        FROM requests
        WHERE requester_id = ?
        """,
        (requester_user_id,),
    )

    total_requests     = int(row_get(stats_row, "total_requests", 0) or 0)
    accepted_requests  = int(row_get(stats_row, "accepted_requests", 0) or 0)
    completed_requests = int(row_get(stats_row, "completed_requests", 0) or 0)
    open_requests      = int(row_get(stats_row, "open_requests", 0) or 0)

    # ---------- Recent requests list for the table ----------
    req_rows = db_fetchall(
        """
        SELECT
            id,
            blood_group,
            units,
            status,
            assigned_donor_name,
            scheduled_at,
            created_at,
            completed_at
        FROM requests
        WHERE requester_id = ?
        ORDER BY created_at DESC
        LIMIT 25
        """,
        (requester_user_id,),
    )
    requests_list = rows_to_dicts(req_rows)

    # Data for "Open vs Completed" chart
    chart_data = {
        "labels": ["Open", "Accepted/Scheduled", "Completed"],
        "counts": [open_requests,
                   accepted_requests,
                   completed_requests],
    }

    return render_template(
        "requester_dashboard.html",
        total_requests=total_requests,
        accepted_requests=accepted_requests,
        open_requests=open_requests,
        completed_requests=completed_requests,
        chart_data=chart_data,
        requests=requests_list,
    )



# ======================================================================
# DONORS MODULE
# ======================================================================

@app.route("/donors")
@login_required
def donors():
    if not (is_admin() or is_staff()):
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    q = request.args.get("q", "").strip()
    page = int(request.args.get("page", 1))
    per_page = 50
    offset = (page - 1) * per_page

    sql = """
        SELECT
            d.*,
            (SELECT COUNT(*) FROM donations WHERE donor_id = d.id) AS donations_count,
            (SELECT COUNT(*) FROM requests WHERE assigned_donor_id = d.id) AS requests_assigned_count,
            (SELECT COUNT(*) FROM requests WHERE assigned_donor_id = d.id AND status='accepted') AS accepted_requests_count
        FROM donors d
    """

    params = []
    where_clauses = []
    if q:
        where_clauses.append("(d.name LIKE ? OR d.blood_group LIKE ? OR d.phone LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])

    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)

    sql += " ORDER BY d.created_at DESC LIMIT ? OFFSET ?"
    params.extend([per_page, offset])

    rows = db_fetchall(sql, tuple(params))
    donors_list = rows_to_dicts(rows)

    for d in donors_list:
        d["donations_count"] = int(d.get("donations_count", 0) or 0)
        d["requests_assigned_count"] = int(d.get("requests_assigned_count", 0) or 0)
        d["accepted_requests_count"] = int(d.get("accepted_requests_count", 0) or 0)

    pagination = {"page": page, "per_page": per_page, "has_prev": page > 1, "has_next": len(donors_list) == per_page,
                  "prev_page": page - 1, "next_page": page + 1, "total_pages": None}
    return render_template("donors.html", donors=donors_list, pagination=pagination)


@app.route("/donor/<int:id>")
@login_required
def donor_detail(id):
    if not (is_admin() or is_staff() or is_donor()):
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    row = db_fetchone("SELECT * FROM users WHERE id = ? AND role='donor'", (id,))
    if not row:
        abort(404)
    donations = db_fetchall("SELECT * FROM donations WHERE donor_id = ? ORDER BY created_at DESC", (id,))

    donations_count_row = db_fetchone("SELECT COUNT(*) as c FROM donations WHERE donor_id = ?", (id,))
    donations_count = int(row_get(donations_count_row, "c", 0) or 0)

    requests_assigned_row = db_fetchone("SELECT COUNT(*) as c FROM requests WHERE assigned_donor_id = ?", (id,))
    requests_assigned_count = int(row_get(requests_assigned_row, "c", 0) or 0)

    accepted_requests_row = db_fetchone("SELECT COUNT(*) as c FROM requests WHERE assigned_donor_id = ? AND status = 'accepted'", (id,))
    accepted_requests_count = int(row_get(accepted_requests_row, "c", 0) or 0)

    return render_template(
        "donor_detail.html",
        donor=dict(row),
        donations=rows_to_dicts(donations),
        donations_count=donations_count,
        requests_assigned_count=requests_assigned_count,
        accepted_requests_count=accepted_requests_count
    )


from werkzeug.security import generate_password_hash
from flask import request, render_template, redirect, url_for, flash

@app.route("/register/donor", methods=["GET", "POST"])
def register_donor():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        blood_group = (request.form.get("blood_group") or "").strip()
        password = (request.form.get("password") or "").strip()

        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template(
                "register_donor.html",
                username=username,
                full_name=full_name,
                email=email,
                phone=phone,
                blood_group=blood_group,
            )

        password_hash = generate_password_hash(password)

        try:
            # 1) Insert the new donor user
            db_execute(
                """
                INSERT INTO users
                    (username, password_hash, role, name, email_id, phone, blood_type,
                     is_active, created_at)
                VALUES (?, ?, 'donor', ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                """,
                (username, password_hash, full_name, email, phone, blood_group),
            )

            # 2) Set linked_entity_id = id for THIS username (donor)
            db_execute(
                """
                UPDATE users
                SET linked_entity_id = id
                WHERE username = ?
                  AND role = 'donor'
                  AND (linked_entity_id IS NULL OR linked_entity_id = '')
                """,
                (username,),
            )

            flash("Registration successful. You can now log in.", "success")
            return redirect(url_for("login"))

        except Exception as e:
            current_app.logger.exception("Failed donor registration")
            flash(f"Registration failed: {e}", "danger")
            return render_template(
                "register_donor.html",
                username=username,
                full_name=full_name,
                email=email,
                phone=phone,
                blood_group=blood_group,
            )

    return render_template("register_donor.html")


@app.route("/register/requester", methods=["GET", "POST"])
def register_requester():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        blood_group = (request.form.get("blood_group") or "").strip()
        password = (request.form.get("password") or "").strip()

        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template(
                "register_requester.html",
                username=username,
                full_name=full_name,
                email=email,
                phone=phone,
                blood_group=blood_group,
            )

        password_hash = generate_password_hash(password)

        try:
            # 1) Insert the new requester user
            db_execute(
                """
                INSERT INTO users
                    (username, password_hash, role, name, email_id, phone, blood_type,
                     is_active, created_at)
                VALUES (?, ?, 'requester', ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                """,
                (username, password_hash, full_name, email, phone, blood_group),
            )

            # 2) Set linked_entity_id = id for THIS username
            #    (username is UNIQUE, so this affects exactly one row)
            db_execute(
                """
                UPDATE users
                SET linked_entity_id = id
                WHERE username = ?
                  AND role = 'requester'
                  AND (linked_entity_id IS NULL OR linked_entity_id = '')
                """,
                (username,),
            )

            flash("Registration successful. You can now log in.", "success")
            return redirect(url_for("login"))

        except Exception as e:
            current_app.logger.exception("Failed requester registration")
            flash(f"Registration failed: {e}", "danger")
            return render_template(
                "register_requester.html",
                username=username,
                full_name=full_name,
                email=email,
                phone=phone,
                blood_group=blood_group,
            )

    return render_template("register_requester.html")


@app.route("/donors/<int:id>/edit", methods=["GET", "POST"])
@login_required
def edit_donor(id):
    # only admin or staff can edit donors
    if not (current_user.role in ("admin", "staff")):
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    # fetch donor from users table
    donor = db_fetchone("SELECT * FROM users WHERE id = ? AND role = 'donor'", (id,))
    if not donor:
        flash("Donor not found.", "danger")
        return redirect(url_for("donors"))

    # If your db_fetchone returns tuples, you might need to convert; adapt as needed.
    # Here we assume rows_to_dicts/db_fetchone already integrates with row_get helper; otherwise extract fields by index.

    if request.method == "POST":
        username = (request.form.get("username") or row_get(donor, "username", "")).strip()
        full_name = (request.form.get("full_name") or row_get(donor, "full_name", row_get(donor, "name", ""))).strip()
        email = (request.form.get("email") or row_get(donor, "email", "")).strip()
        phone = (request.form.get("phone") or row_get(donor, "phone", "")).strip()
        blood_group = (request.form.get("blood_group") or row_get(donor, "blood_group", row_get(donor, "blood_type", ""))).strip()
        password = request.form.get("password")  # optional: update if provided

        try:
            # update fields
            if password:
                from werkzeug.security import generate_password_hash
                pw_hash = generate_password_hash(password)
                db_execute(
                    "UPDATE users SET username = ?, password = ?, full_name = ?, email = ?, phone = ?, blood_group = ? WHERE id = ? AND role = 'donor'",
                    (username, pw_hash, full_name, email, phone, blood_group, id)
                )
            else:
                db_execute(
                    "UPDATE users SET username = ?, full_name = ?, email = ?, phone = ?, blood_group = ? WHERE id = ? AND role = 'donor'",
                    (username, full_name, email, phone, blood_group, id)
                )

            flash("Donor updated successfully.", "success")
            return redirect(url_for("donor_detail", id=id))
        except Exception as e:
            app.logger.exception("Failed to update donor")
            flash(f"Failed to update donor: {e}", "danger")
            # fall through and re-render form with previous values

    # GET: render edit form populated by donor row
    # Convert donor record to dict-friendly values if needed
    donor_dict = donor if isinstance(donor, dict) else {
        "id": row_get(donor, "id"),
        "username": row_get(donor, "username"),
        "full_name": row_get(donor, "full_name") or row_get(donor, "name") or row_get(donor, "username"),
        "email": row_get(donor, "email"),
        "phone": row_get(donor, "phone"),
        "blood_group": row_get(donor, "blood_group") or row_get(donor, "blood_type")
    }

    return render_template("edit_donor.html", donor=donor_dict)


@app.route("/donors/<int:id>/delete", methods=["POST"])
@login_required
def delete_donor(id):
    # only admin can delete donors
    if not (current_user.role == "admin"):
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    # ensure the target exists and is a donor
    row = db_fetchone("SELECT id FROM users WHERE id = ? AND role = 'donor'", (id,))
    if not row:
        flash("Donor not found.", "danger")
        return redirect(url_for("donors"))

    try:
        # Soft delete: mark inactive
        db_execute("UPDATE users SET is_active = 0 WHERE id = ? AND role = 'donor'", (id,))
        flash("Donor deactivated.", "info")
    except Exception as e:
        app.logger.exception("Failed to deactivate donor")
        flash(f"Failed to deactivate donor: {e}", "danger")

    return redirect(url_for("donors"))


@app.route("/export/donors/<fmt>")
@login_required
def export_donors(fmt):
    if current_user.role not in ("admin","staff"):
        flash("Access denied.", "danger")
        return redirect(url_for("donors"))

    rows = db_fetchall("SELECT * FROM donors ORDER BY created_at DESC")
    donors = rows_to_dicts(rows)

    if fmt == "csv":
        si = BytesIO()
        writer = csv.writer(si)
        writer.writerow(["id","name","blood_group","phone","last_donation","total_units","created_at"])
        for d in donors:
            writer.writerow([d.get("id"), d.get("name"), d.get("blood_group"), d.get("phone"), d.get("last_donation"), d.get("total_units"), d.get("created_at")])
        si.seek(0)
        return send_file(si, as_attachment=True, download_name="donors.csv", mimetype="text/csv")
    else:
        if REPORTLAB_AVAILABLE:
            buffer = BytesIO()
            c = canvas.Canvas(buffer, pagesize=letter)
            y = 750
            c.setFont("Helvetica-Bold", 12)
            c.drawString(30, y, "Donors Report")
            y -= 30
            c.setFont("Helvetica", 10)
            for d in donors:
                line = f"{d['id']} - {d.get('name','')} | {d.get('blood_group','')} | {d.get('phone','')} | units:{d.get('total_units','')}"
                c.drawString(30, y, line)
                y -= 14
                if y < 60:
                    c.showPage()
                    y = 750
            c.save()
            buffer.seek(0)
            return send_file(buffer, as_attachment=True, download_name="donors.pdf", mimetype="application/pdf")
        else:
            si = BytesIO()
            writer = csv.writer(si)
            writer.writerow(["id","name","blood_group","phone","last_donation","total_units","created_at"])
            for d in donors:
                writer.writerow([d.get("id"), d.get("name"), d.get("blood_group"), d.get("phone"), d.get("last_donation"), d.get("total_units"), d.get("created_at")])
            si.seek(0)
            flash("ReportLab not installed — exported CSV instead.", "warning")
            return send_file(si, as_attachment=True, download_name="donors.csv", mimetype="text/csv")


# ======================================================================
# REQUESTS MODULE
# ======================================================================
from flask import render_template, flash, current_app  # safe imports
def get_donors_list():
    """Fetches a list of active donor users (ID, Name, Blood Type) for dropdowns."""
    try:
        rows = db_fetchall("""
            SELECT id, name, blood_type
            FROM users
            WHERE role = 'donor' AND is_active = 1
            ORDER BY name
        """)
    except Exception as e:
        print(f"Database error fetching donors list: {e}")
        return []

    # Ensure all required fields exist for consistency
    donors = []
    for row in rows:
        donors.append({
            'id': row_get(row, 'id'),
            'name': row_get(row, 'name', row_get(row, 'username', 'Unknown Donor')),
            'blood_group': row_get(row, 'blood_type', 'Unknown'),
        })
    return donors

@app.route("/requests")
@login_required
def requests_page():

    # Pagination setup (simplified, adjust if you use complex pagination)
    page = request.args.get('page', 1, type=int)
    per_page = 20 # items per page
    offset = (page - 1) * per_page

    # Status and search filtering
    status_filter = request.args.get('status', '').strip()
    q_filter = request.args.get('q', '').strip()

    base_query = "SELECT * FROM requests WHERE 1=1"
    count_query = "SELECT COUNT(*) FROM requests WHERE 1=1"
    params = []
    count_params = []

    # 1. Role-based filtering
    if is_requester():
        # Requester sees only their own requests
        base_query += " AND requester_id = ?"
        count_query += " AND requester_id = ?"
        params.append(current_user.id)
        count_params.append(current_user.id)

    elif is_donor():
        donor_blood_type = getattr(current_user, 'blood_type', None)
        donor_id = current_user.id # Donor's own user ID

        # 🌟 FIX 1: Donors see OPEN matching requests OR assigned requests (accepted, scheduled, completed)

        # Start the OR block
        base_query += " AND ("
        count_query += " AND ("

        # Condition 1: Open requests matching their blood type
        if donor_blood_type and donor_blood_type not in ('Unknown', 'N/A'):
            base_query += " (status = 'open' AND blood_group = ?)"
            count_query += " (status = 'open' AND blood_group = ?)"
            params.append(donor_blood_type)
            count_params.append(donor_blood_type)
        else:
            # If blood type is unknown, they cannot accept new requests
            base_query += " (1=0)"
            count_query += " (1=0)"
            flash("Your blood type is missing. You can only view requests assigned to you.", "warning")

        # Condition 2: Any request assigned to them (excluding open ones which were handled above)
        base_query += " OR (assigned_donor_id = ? AND status != 'open')"
        count_query += " OR (assigned_donor_id = ? AND status != 'open')"
        params.append(donor_id)
        count_params.append(donor_id)

        # Close the OR block
        base_query += ")"
        count_query += ")"

    # 2. General filters (applies to all roles except where overridden above)
    if status_filter:
        base_query += " AND status = ?"
        count_query += " AND status = ?"
        params.append(status_filter)
        count_params.append(status_filter)

    if q_filter:
        q_like = f"%{q_filter}%"
        base_query += " AND (requester_name LIKE ? OR requester_username LIKE ? OR blood_group LIKE ?)"
        count_query += " AND (requester_name LIKE ? OR requester_username LIKE ? OR blood_group LIKE ?)"
        params.extend([q_like, q_like, q_like])
        count_params.extend([q_like, q_like, q_like])

    # Final query execution
    total_count_row = db_fetchone(count_query, tuple(count_params))
    total_count = int(row_get(total_count_row, 0, 0) or 0)

    requests = rows_to_dicts(db_fetchall(
        f"{base_query} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        tuple(params + [per_page, offset])
    ))

    # Pagination calculation (omitted for brevity, assume correct)
    total_pages = (total_count + per_page - 1) // per_page if per_page else 1
    pagination = {
        "page": page,
        "per_page": per_page,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_page": page - 1,
        "next_page": page + 1,
        "total_pages": total_pages,
        "total_count": total_count
    }

    # Donors list is only needed for Admin/Staff accepting open requests
    donors_list = []
    if is_admin() or is_staff():
        # Ensure get_donors_list is defined or available
        donors_list = get_donors_list()

    return render_template("requests.html",
                           requests=requests,
                           pagination=pagination,
                           donors=donors_list)
# --- end function ---
# --- Robust donor select helper ---
import sqlite3

def get_donors_for_select(db_path=None):
    """
    Return list of donors as dicts: {id, name, blood_group}.
    Safe: will inspect users table columns and fall back gracefully if name/blood columns missing.
    """
    # Resolve DB path
    try:
        db_path = db_path or app.config.get('DATABASE')
    except Exception:
        db_path = db_path or 'database.db'

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Inspect columns present in users table
    cur.execute("PRAGMA table_info(users);")
    cols = [r[1] for r in cur.fetchall()]  # r[1] is column name

    # Determine available name and blood columns
    name_candidates = ['full_name', 'name', 'username']  # prefer full_name, then name, then username
    blood_candidates = ['blood_group', 'blood_type']

    name_col = next((c for c in name_candidates if c in cols), None)
    blood_col = next((c for c in blood_candidates if c in cols), None)

    # Build SELECT dynamically using only existing columns
    select_parts = ["id"]
    if name_col:
        select_parts.append(f"{name_col} AS name")
    else:
        select_parts.append("'' AS name")

    if blood_col:
        select_parts.append(f"{blood_col} AS blood_group")
    else:
        select_parts.append("'' AS blood_group")

    # Prefer ordering by name if name_col exists, otherwise by id
    order_by = "name" if name_col else "id"

    sql = f"SELECT {', '.join(select_parts)} FROM users WHERE role = 'donor' ORDER BY {order_by}"

    try:
        cur.execute(sql)
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        # Last-resort fallback: select id only
        cur.execute("SELECT id FROM users WHERE role = 'donor' ORDER BY id")
        rows = [(r[0], "", "") for r in cur.fetchall()]

    conn.close()

    # Normalize into list of dicts
    result = [{"id": r[0], "name": (r[1] or ""), "blood_group": (r[2] or "")} for r in rows]
    return result
# --- end helper ---

    # Provide donors list for the accept form (only admins/staff need full list; donors don't)
    donors = []
    if current_user.role in ("admin", "staff"):
        donors = get_donors_for_select(app.config.get('DATABASE', 'database.db'))

    # Pagination object expected by template
    total_pages = (total + per_page - 1) // per_page if per_page else 1
    pagination = {
        "page": page,
        "per_page": per_page,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_page": page - 1,
        "next_page": page + 1,
        "total_pages": total_pages,
    }

    # IMPORTANT: template expects 'requests' (not 'rows') and also 'donors' & 'pagination'
    return render_template(
        "requests.html",
        requests=rows,
        donors=donors,
        pagination=pagination,
        page=page,
        per_page=per_page,
        total=total,
        q=q,
        status=status,
    )


def get_requesters_data():
    """
    Fetches list of active requesters (ID, Username, Name, Blood Type) for staff/admin dropdown.
    """
    try:
        rows = db_fetchall("""
            SELECT id, username, name, blood_type
            FROM users
            WHERE role = 'requester' AND is_active = 1
            ORDER BY username
        """)
    except Exception as e:
        print(f"Database error fetching requesters list: {e}")
        return []

    requesters = []
    for row in rows:
        requesters.append({
            'id': row_get(row, 'id'),
            'username': row_get(row, 'username'),
            'name': row_get(row, 'name'),
            'blood_type': row_get(row, 'blood_type'),
        })
    return requesters


@app.route("/request/create", methods=["GET", "POST"])
@login_required
def create_request():

    is_requester_role = current_user.role == 'requester'

    # Initialize requester data structure
    requester_data = {
        'requester_id': None,
        'requester_username': None,
        'requester_name': None,
        'requester_blood_type': None
    }
    requesters_list = [] # List for dropdown (Admin/Staff only)

    # --- GET/SETUP Handling (Prep data for template) ---
    if is_requester_role:
        # Requester: Get their own details
        requester_data = {
            'requester_id': current_user.id,
            'requester_username': current_user.username,
            'requester_name': getattr(current_user, 'name', current_user.username),
            'requester_blood_type': getattr(current_user, 'blood_type', 'Unknown'),
        }
    else:
        # Admin/Staff: Get list of all requesters
        requesters_list = get_requesters_data()

    # --- POST Handling ---
    if request.method == 'POST':
        # blood_group is the Required Blood Type selected by the user
        blood_group = request.form.get('blood_group')
        units = request.form.get('units')

        if is_requester_role:
            # Requester: Data comes from auto-fetched data (hidden inputs)
            requester_id = requester_data['requester_id']
            requester_name = requester_data['requester_name']
            requester_username = requester_data['requester_username']
            # Source of Requester's blood type for fallback
            requester_blood_type_for_fallback = requester_data['requester_blood_type']

        else:
            # Admin/Staff: Data comes from the selected dropdown (requester_id) and hidden inputs (name, username)
            requester_id = request.form.get('requester_id')

            if requester_id and requester_id.isdigit():
                # Fetch full user details from DB based on selection
                user_row = db_fetchone(
                    "SELECT username, name, blood_type FROM users WHERE id = ?",
                    (int(requester_id),)
                )
                if user_row:
                    requester_username = row_get(user_row, 'username')
                    requester_name = row_get(user_row, 'name')
                    # Source of Requester's blood type for fallback
                    requester_blood_type_for_fallback = row_get(user_row, 'blood_type')
                else:
                    flash("Selected Requester not found.", "danger")
                    requesters_list = get_requesters_data()
                    return render_template("create_request.html", is_requester_role=is_requester_role, requester=requester_data, requesters_list=requesters_list)
            else:
                flash("Please select a Requester.", "danger")
                requesters_list = get_requesters_data()
                return render_template("create_request.html", is_requester_role=is_requester_role, requester=requester_data, requesters_list=requesters_list)

        # --- 🌟 UNIVERSAL CRITICAL FIX: Auto-Select Blood Type Fallback ---
        # If the required blood group is missing, use the requester's blood type as a fallback.
        if not blood_group and requester_blood_type_for_fallback:
            blood_group = requester_blood_type_for_fallback
            flash(f"Required Blood Group not selected. Defaulting to Requester's type: {blood_group}", "warning")

        # Final validation (now ensures blood_group is set, either by user or fallback)
        if not all([requester_id, requester_name, requester_username, blood_group]) or not units or not units.isdigit():
            flash("Missing or invalid request details (ID, Name, Username, Blood Group, or Units).", "danger")
            requesters_list = get_requesters_data()
            return render_template("create_request.html", is_requester_role=is_requester_role, requester=requester_data, requesters_list=requesters_list)

        # Database Insertion
        try:
            db_execute(
                """
                INSERT INTO requests (requester_id, requester_name, requester_username, blood_group, units, status)
                VALUES (?, ?, ?, ?, ?, 'open')
                """,
                (requester_id, requester_name, requester_username, blood_group, int(units))
            )
            flash(f"Blood request created successfully for {requester_username}. Requested Blood Group: {blood_group}", "success")
            return redirect(url_for('requests_page'))

        except Exception as e:
            flash(f"Failed to create request: {e}", "danger")
            requesters_list = get_requesters_data()
            return render_template("create_request.html", is_requester_role=is_requester_role, requester=requester_data, requesters_list=requesters_list)

    # --- GET Final Render ---
    return render_template("create_request.html",
                           is_requester_role=is_requester_role,
                           requester=requester_data,
                           requesters_list=requesters_list)


@app.route("/request/<int:id>")
@login_required
def request_detail(id):
    row = db_fetchone("SELECT * FROM requests WHERE id = ?", (id,))
    if not row:
        abort(404)
    return render_template("request_detail.html", request=dict(row))


@app.route("/requests/accept", methods=["POST"])
@login_required
def requests_accept():
    req_id = request.form.get('req_id', type=int)
    scheduled_at = request.form.get('scheduled_at')
    note = request.form.get('note')

    if not req_id:
        flash("Missing request ID.", "danger")
        return redirect(url_for('requests_page'))

    # Load the request row
    req_row = db_fetchone("SELECT * FROM requests WHERE id = ?", (req_id,))
    if not req_row:
        flash("Request not found.", "danger")
        return redirect(url_for('requests_page'))

    current_status = row_get(req_row, "status")
    blood_group = row_get(req_row, "blood_group")
    units = int(row_get(req_row, "units", 0) or 0)

    if current_status != "open":
        flash(f"Only open requests can be accepted (current status: {current_status}).", "warning")
        return redirect(url_for('requests_page'))

    if not blood_group or units <= 0:
        flash("Request must have a valid blood group and positive units.", "danger")
        return redirect(url_for('requests_page'))

    # --------- ROLE: DONOR (self-accept, no store involvement) ---------
    if is_donor():
        donor_id = current_user.id
        donor_name = getattr(current_user, 'name', current_user.username)

        if not donor_id:
            flash("Could not identify your donor profile to accept the request.", "danger")
            return redirect(url_for('requests_page'))

        status = 'scheduled' if scheduled_at else 'accepted'

        try:
            db_execute(
                """
                UPDATE requests SET
                    status = ?,
                    assigned_donor_id = ?,
                    assigned_donor_name = ?,
                    scheduled_at = ?,
                    note = ?,
                    accepted_by = ?
                WHERE id = ? AND status = 'open'
                """,
                (status, donor_id, donor_name, scheduled_at, note, current_user.id, req_id)
            )
            flash(f"Request #{req_id} accepted and assigned to you. Status: {status}.", "success")
        except Exception as e:
            flash(f"Failed to accept request: {e}", "danger")
            current_app.logger.exception("Error accepting request (donor path)")

        return redirect(url_for('requests_page'))

    # --------- ROLE: ADMIN / STAFF ---------
    elif is_admin() or is_staff():
        donor_id_from_form = request.form.get('donor_id', type=int)

        # ===== PATH A: donor selected -> assign donor, no store change =====
        if donor_id_from_form:
            donor_id = donor_id_from_form

            donor_row = db_fetchone(
                "SELECT name, username FROM users WHERE id = ? AND role='donor'",
                (donor_id,)
            )
            donor_name = None
            if donor_row:
                donor_name = row_get(donor_row, 'name') or row_get(donor_row, 'username')
            if not donor_name:
                donor_name = f"Donor {donor_id}"

            status = 'scheduled' if scheduled_at else 'accepted'

            try:
                db_execute(
                    """
                    UPDATE requests SET
                        status = ?,
                        assigned_donor_id = ?,
                        assigned_donor_name = ?,
                        scheduled_at = ?,
                        note = ?,
                        accepted_by = ?
                    WHERE id = ? AND status = 'open'
                    """,
                    (status, donor_id, donor_name, scheduled_at, note, current_user.id, req_id)
                )
                flash(f"Request #{req_id} accepted and assigned to {donor_name}. Status: {status}.", "success")
            except Exception as e:
                flash(f"Failed to accept request: {e}", "danger")
                current_app.logger.exception("Error accepting request (admin/staff donor path)")

            return redirect(url_for('requests_page'))

        # ===== PATH B: NO donor -> issue from STORE immediately =====
        #   - deduct from store
        #   - insert into issues (origin='store_issue')
        #   - mark request completed
        #   - assigned_donor_id = -1, assigned_donor_name = 'Store donation'
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Check stock
            cur.execute(
                "SELECT units FROM store WHERE blood_group = ?",
                (blood_group,)
            )
            stock_row = cur.fetchone()
            current_stock = int(stock_row["units"]) if stock_row else 0

            if current_stock < units:
                flash(
                    f"Not enough stock in store for {blood_group}. "
                    f"Needed {units}, available {current_stock}.",
                    "danger",
                )
                return redirect(url_for('requests_page'))

            # Deduct from store
            cur.execute(
                "UPDATE store SET units = units - ? WHERE blood_group = ?",
                (units, blood_group),
            )

            issued_at = datetime.utcnow().isoformat(timespec="seconds")

            # Log in issues table
            cur.execute(
                """
                INSERT INTO issues (origin, reference_id, blood_group, units, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "store_issue",   # origin
                    req_id,          # reference_id -> requests.id
                    blood_group,
                    units,
                    issued_at,
                ),
            )

            # Update request as completed via store
            cur.execute(
                """
                UPDATE requests SET
                    status = 'completed',
                    completed_at = ?,
                    scheduled_at = ?,          -- use provided date as delivery/schedule
                    note = ?,
                    assigned_donor_id = -1,    -- sentinel for 'Store donation'
                    assigned_donor_name = 'Store donation',
                    accepted_by = ?
                WHERE id = ? AND status = 'open'
                """,
                (issued_at, scheduled_at, note, current_user.id, req_id),
            )

            conn.commit()
            flash(
                f"Request #{req_id} completed from store. "
                f"Issued {units} units of {blood_group} (Store donation).",
                "success",
            )

        except Exception as e:
            if conn:
                conn.rollback()
            flash(f"Failed to accept / issue request from store: {e}", "danger")
            current_app.logger.exception("Error accepting request (admin/staff store path)")
        finally:
            if conn:
                conn.close()

        return redirect(url_for('requests_page'))

    # Any other role (e.g. requester) has no access
    else:
        flash("You do not have permission to accept requests.", "danger")
        return redirect(url_for('requests_page'))


@app.route("/admin/request/<int:request_id>/create_donation", methods=["POST"])
@login_required
def admin_create_donation_from_request(request_id):
    """
    Admin/Staff only endpoint to manually create and link a donation record
    for an existing, potentially completed request that is missing a donation_id.
    """
    if not (is_admin() or is_staff()):
        flash("Access denied. Only Admin or Staff can perform this action.", "danger")
        return redirect(url_for("request_detail", id=request_id))

    # Fetch the request row
    R = db_fetchone("SELECT * FROM requests WHERE id = ?", (request_id,))
    if not R:
        flash("Request not found.", "danger")
        return redirect(url_for("requests_page"))

    # Check for required data
    donor_id = row_get(R, "assigned_donor_id")
    donor_name = row_get(R, "assigned_donor_name")
    blood_group = row_get(R, "blood_group")
    units = row_get(R, "units", 1)

    if not donor_id:
        flash("Cannot create donation: Request has no assigned donor.", "danger")
        return redirect(url_for("request_detail", id=request_id))

    try:
        # 1. Record the donation in the donations table
        donation_id = db_execute(
            """
            INSERT INTO donations (donor_id, donor_name, blood_group, units)
            VALUES (?, ?, ?, ?)
            """,
            (donor_id, donor_name, blood_group, units)
        )

        # 2. Update donor's last_donation date and total_units
        db_execute(
            "UPDATE users SET total_units = total_units + ?, last_donation = ? WHERE id = ? AND role='donor'",
            (units, datetime.utcnow().isoformat(timespec='seconds'), donor_id)
        )

        # 3. Link the new donation ID to the request
        db_execute(
            "UPDATE requests SET donation_id = ? WHERE id = ?",
            (donation_id, request_id)
        )

        flash(f"Donation record #{donation_id} successfully created and linked to Request #{request_id}.", "success")
        return redirect(url_for("request_detail", id=request_id))

    except Exception as e:
        flash(f"Failed to create and link donation: {e}", "danger")
        return redirect(url_for("request_detail", id=request_id))

@app.route("/requests/<int:id>/complete", methods=["POST"])
@login_required
def complete_request(id):
    """
    Marks a request as 'completed' and issues the blood by reducing
    the quantity from the central 'store' (inventory).
    This action addresses the 'issue' part of the blood request lifecycle.
    Accessible by Admin/Staff or the assigned Donor.
    Also logs a row into the issues table for outgoing reporting.
    """

    # Use db_fetchone to get the request data
    row = db_fetchone("SELECT * FROM requests WHERE id = ?", (id,))
    if not row:
        flash("Request not found.", "danger")
        return redirect(url_for("requests_page"))

    # Using row_get() directly
    status = row_get(row, "status")
    blood_group = row_get(row, "blood_group")
    units = int(row_get(row, "units", 1) or 0)

    # Access control: Must be Admin/Staff or the assigned Donor
    is_authorized_donor = (
        is_donor()
        and int(current_user.id) == int(row_get(row, "assigned_donor_id") or -1)
    )

    if not (is_admin() or is_staff() or is_authorized_donor):
        flash("Access denied. Only Staff, Admin, or the Assigned Donor can complete this request.", "danger")
        return redirect(url_for("requests_page"))

    if status in ("completed", "cancelled"):
        flash(f"Request is already marked as {status}.", "danger")
        return redirect(url_for("request_detail", id=id))

    # Only accepted or scheduled requests can be completed (issued)
    if status not in ("accepted", "scheduled"):
        flash("Request must be accepted or scheduled to be completed (issued).", "danger")
        return redirect(url_for("request_detail", id=id))

    if not blood_group:
        flash("Request has no blood group set; cannot complete.", "danger")
        return redirect(url_for("request_detail", id=id))

    if units <= 0:
        flash("Cannot complete request with zero units.", "danger")
        return redirect(url_for("request_detail", id=id))

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        # Set row factory to access columns by name when manually executing queries
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # 1. Check current stock to prevent negative inventory
        cur.execute("SELECT units FROM store WHERE blood_group = ?", (blood_group,))
        stock_row = cur.fetchone()
        current_stock = stock_row["units"] if stock_row else 0

        if current_stock < units:
            flash(
                f"Cannot complete: Insufficient stock ({current_stock} units) of {blood_group}. "
                f"Need {units} units.",
                "danger",
            )
            return redirect(url_for("request_detail", id=id))

        # 2. UPDATE INVENTORY (Issue Blood - Subtraction)
        cur.execute(
            "UPDATE store SET units = units - ? WHERE blood_group = ?",
            (units, blood_group),
        )

        # 3. LOG OUTGOING ISSUE IN issues TABLE
        # This is what your store_report now reads for outgoing transactions.
        issued_at = datetime.utcnow().isoformat(timespec="seconds")
        cur.execute(
            """
            INSERT INTO issues (origin, reference_id, blood_group, units, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "request_completed",  # origin
                id,                   # reference_id -> requests.id
                blood_group,
                units,
                issued_at,
            ),
        )

        # 4. Update the request status and completion time
        cur.execute(
            "UPDATE requests SET status = 'completed', completed_at = ? WHERE id = ?",
            (issued_at, id),
        )

        conn.commit()

        flash(
            f"Request #{id} successfully completed (Blood Issued). "
            f"Inventory reduced by {units} units of {blood_group} and logged in issues.",
            "success",
        )
        return redirect(url_for("request_detail", id=id))

    except Exception as e:
        # --- Defensive Schema Check for 'store' table ---
        error_message = str(e).lower()

        if "no such table: store" in error_message:
            try:
                conn_fix = sqlite3.connect(DB_PATH)
                cur_fix = conn_fix.cursor()
                cur_fix.execute(
                    """
                    CREATE TABLE store (
                        blood_group TEXT PRIMARY KEY,
                        units INTEGER DEFAULT 0
                    );
                    """
                )
                conn_fix.commit()
                conn_fix.close()
                flash(
                    "Missing 'store' table created. Please try completing the request again.",
                    "warning",
                )
                return redirect(url_for("request_detail", id=id))
            except sqlite3.OperationalError:
                pass  # If it fails to create (e.g., already exists), ignore.

        current_app.logger.exception(f"Failed to complete request {id} and issue blood.")
        flash(f"Failed to complete request: {e}", "danger")
        return redirect(url_for("requests_page"))

    finally:
        if conn:
            conn.close()


@app.route("/export/requests/<fmt>")
@login_required
def export_requests(fmt):
    if current_user.role not in ("admin","staff"):
        flash("Access denied.", "danger")
        return redirect(url_for("requests_page"))
    rows = rows_to_dicts(db_fetchall("SELECT * FROM requests ORDER BY created_at DESC"))
    items = rows

    if fmt == "csv":
        si = BytesIO()
        w = csv.writer(si)
        w.writerow(["id","requester_name","blood_group","units","status","accepted_by_name","assigned_donor_name","created_at"])
        for r in items:
            w.writerow([r.get("id"), r.get("requester_name"), r.get("blood_group"), r.get("units"), r.get("status"), r.get("accepted_by_name"), r.get("assigned_donor_name"), r.get("created_at")])
        si.seek(0)
        return send_file(si, as_attachment=True, download_name="requests.csv", mimetype="text/csv")
    else:
        if REPORTLAB_AVAILABLE:
            buffer = BytesIO()
            c = canvas.Canvas(buffer, pagesize=letter)
            y = 750
            c.setFont("Helvetica-Bold", 12)
            c.drawString(30, y, "Requests Report")
            y -= 30
            c.setFont("Helvetica", 10)
            for r in items:
                line = f"{r['id']} - {r.get('requester_name','')} | {r.get('blood_group','')} | units:{r.get('units','')} | status:{r.get('status','')}"
                c.drawString(30, y, line)
                y -= 14
                if y < 60:
                    c.showPage()
                    y = 750
            c.save()
            buffer.seek(0)
            return send_file(buffer, as_attachment=True, download_name="requests.pdf", mimetype="application/pdf")
        else:
            si = BytesIO()
            w = csv.writer(si)
            w.writerow(["id","requester_name","blood_group","units","status","accepted_by_name","assigned_donor_name","created_at"])
            for r in items:
                w.writerow([r.get("id"), r.get("requester_name"), r.get("blood_group"), r.get("units"), r.get("status"), r.get("accepted_by_name"), r.get("assigned_donor_name"), r.get("created_at")])
            si.seek(0)
            flash("ReportLab not installed — exported CSV instead.", "warning")
            return send_file(si, as_attachment=True, download_name="requests.csv", mimetype="text/csv")


# ======================================================================
# DONATIONS / ISSUES / STORE (inventory)
# ======================================================================

from datetime import datetime
from flask import current_app
import sqlite3
# Ensure DB_PATH is imported and available, e.g., DB_PATH = os.path.join(BASE_DIR, "blood_bank.db")

@app.route("/record_donation", methods=["GET", "POST"])
@login_required
def record_donation():
    # restrict access
    if current_user.role not in ("admin", "staff"):
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    # --- POST Request Handling ---
    if request.method == "POST":
        donor_val = request.form.get("donor_id")

        fetched_blood_group = None
        fetched_donor_name = None
        donor_id = None

        try:
            donor_id = int(donor_val) if donor_val and donor_val not in ("None", "") else None
        except ValueError:
            flash("Invalid donor selected.", "danger")
            return redirect(url_for("record_donation"))

        # 1. Fetch donor info (including blood type and name)
        if donor_id:
            # Assumes 'name' and 'blood_type' are in the 'users' table
            donor_row = db_fetchone("SELECT name, blood_type FROM users WHERE id = ?", (donor_id,))
            if donor_row:
                fetched_blood_group = row_get(donor_row, "blood_type")
                fetched_donor_name = row_get(donor_row, "name")
            else:
                flash("Selected donor not found in database.", "danger")
                return redirect(url_for("record_donation"))

        if not donor_id:
            fetched_donor_name = "Anonymous Donor"
            fetched_blood_group = request.form.get("blood_group")

        try:
            units = int(request.form.get("units") or 0)
        except ValueError:
            flash("Invalid units value.", "danger")
            return redirect(url_for("record_donation"))

        notes = request.form.get("notes") or ""
        donation_date = request.form.get("donation_date") or datetime.utcnow().date().isoformat()

        if units <= 0:
            flash("Units must be greater than zero.", "danger")
            return redirect(url_for("record_donation"))

        try:
            # 1. INSERT Donation Record
            sql_query = """
                INSERT INTO donations
                    (donor_id, donor_name, blood_group, units, donation_date, recorded_by, notes)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?)
            """
            params = (
                donor_id,
                fetched_donor_name,
                fetched_blood_group,
                units,
                donation_date,
                current_user.id,
                notes
            )
            db_execute(sql_query, params)

            # 2. Update Inventory/Store (FIXED: Direct cursor access to check rowcount)
            if fetched_blood_group and fetched_blood_group != 'Unknown':
                conn = None
                try:
                    conn = sqlite3.connect(DB_PATH)
                    cur = conn.cursor()

                    # Attempt to UPDATE existing record
                    cur.execute(
                        "UPDATE store SET units = units + ? WHERE blood_group = ?",
                        (units, fetched_blood_group)
                    )

                    # Check if the UPDATE affected any row
                    if cur.rowcount == 0:
                        # If no row was updated, it means the blood group is new. INSERT new record.
                        cur.execute(
                            "INSERT INTO store (blood_group, units) VALUES (?, ?)",
                            (fetched_blood_group, units)
                        )

                    conn.commit()

                except Exception as e:
                    current_app.logger.exception(f"Failed to update or insert blood store for {fetched_blood_group}. Error: {e}")
                    flash("Warning: Donation recorded, but blood stock inventory update failed.", "warning")
                finally:
                    if conn:
                        conn.close()

            # 3. Update donor's statistics (uses db_execute)
            if donor_id:
                 try:
                    # DeprecationWarning fix: Use datetime.now(datetime.UTC)
                    db_execute(
                        "UPDATE users SET total_units = total_units + ?, last_donation = ? WHERE id = ?",
                        (units, datetime.now(datetime.UTC).isoformat(), donor_id)
                    )
                 except Exception:
                    current_app.logger.exception("Failed to update donor stats after donation.")

            flash(f"Donation recorded successfully for {fetched_donor_name}. Inventory updated.", "success")
            return redirect(url_for("record_donation"))

        except Exception as e:
            error_message = str(e).lower()
            schema_fixed = False

            # --- DEFENSIVE SCHEMA FIX for 'donations' table ---
            try:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()

                # Check 1: Missing 'donor_name'
                if "no column named donor_name" in error_message:
                    cur.execute("ALTER TABLE donations ADD COLUMN donor_name TEXT;")
                    schema_fixed = True

                # Check 2: Missing 'blood_group'
                if "no column named blood_group" in error_message:
                    cur.execute("ALTER TABLE donations ADD COLUMN blood_group TEXT;")
                    schema_fixed = True

                # Check 3-5: Other missing columns
                if "no column named donation_date" in error_message:
                    cur.execute("ALTER TABLE donations ADD COLUMN donation_date TIMESTAMP;")
                    schema_fixed = True

                if "no column named recorded_by" in error_message:
                    cur.execute("ALTER TABLE donations ADD COLUMN recorded_by INTEGER;")
                    schema_fixed = True

                if "no column named notes" in error_message:
                    cur.execute("ALTER TABLE donations ADD COLUMN notes TEXT;")
                    schema_fixed = True

                if schema_fixed:
                    conn.commit()
                    conn.close()
                    flash("Database schema was successfully updated. Please try recording the donation again.", "warning")
                    return redirect(url_for("record_donation"))

                conn.close()

            except sqlite3.OperationalError as schema_e:
                current_app.logger.error(f"Schema fix failure: {schema_e}")

            # Fallback for generic errors
            current_app.logger.exception("Failed to record donation")
            flash(f"An error occurred while recording the donation: {e}", "danger")
            return redirect(url_for("record_donation"))

    # --- GET Request Handling ---
    try:
        donors_list = rows_to_dicts(db_fetchall(
            "SELECT id, name, blood_type FROM users WHERE role = 'donor' AND is_active = 1 ORDER BY name"
        ))
    except Exception:
        current_app.logger.exception("Failed to fetch donors for record_donation")
        donors_list = []

    # Note: Ensure you import datetime.UTC if using Python 3.11+.
    # If not available, use datetime.utcnow() for now.
    today_date = datetime.utcnow().date().isoformat()

    return render_template("record_donation.html", donors=donors_list, today_date=today_date)

from flask import request, jsonify, render_template, flash, redirect
from flask import jsonify, url_for

@app.route('/issue_blood', methods=['GET', 'POST'])
@login_required
def issue_blood():
    """
    Issue / schedule blood for a requester.

    - GET (no params): render form
    - GET ?requester_id=NN (AJAX): return JSON {blood_type, donors:[{id,name}]}
    - POST: perform issue-from-store OR schedule-from-donor
    """
    # Only admin/staff can issue blood
    if not (is_admin() or is_staff()):
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    # ----------------- helpers -----------------
    def _get_most_recent_requested_group_for_requester(rid: int):
        """
        Determine the blood type for a requester:

          1) Most recent entry in requests.requester_id = user.id (blood_group column)
             (kept for backward compatibility if you already store it there)
          2) Fallback ONLY to users.blood_type
        """
        # 1) Most recent request row (if you still store blood_group there)
        try:
            row = db_fetchone(
                "SELECT blood_group FROM requests "
                "WHERE requester_id = ? ORDER BY id DESC LIMIT 1",
                (rid,)
            )
            bg = row_get(row, "blood_group", None)
            if bg:
                return bg
        except Exception:
            # ignore and fall through to user row lookup
            pass

        # 2) Fallback ONLY to the requester user row: use blood_type column
        try:
            row = db_fetchone(
                "SELECT blood_type FROM users WHERE id = ?",
                (rid,)
            )
            if not row:
                return None
            bg = row_get(row, "blood_type", None)
            return bg
        except Exception:
            return None

    def _get_donors_for_blood_group(requester_blood: str):
        """
        Return list of donors [{id, name}] filtered by blood group if possible.
        Handles mixed schemas (blood_group / blood_type) safely.
        """
        donors = []

        # Discover available columns on users table
        try:
            cols_rows = db_fetchall("PRAGMA table_info(users)")
            col_names = {row_get(r, "name") for r in cols_rows}
        except Exception:
            col_names = set()

        has_bg = "blood_group" in col_names
        has_bt = "blood_type" in col_names

        where_clauses = ["u.role = 'donor'"]
        params = []

        # Add blood-group filter only if we know how
        if requester_blood and (has_bg or has_bt):
            if has_bg and has_bt:
                where_clauses.append(
                    "(u.blood_group = ? OR u.blood_type = ?)"
                )
                params.extend([requester_blood, requester_blood])
            elif has_bg:
                where_clauses.append("u.blood_group = ?")
                params.append(requester_blood)
            else:  # has_bt only
                where_clauses.append("u.blood_type = ?")
                params.append(requester_blood)

        where_sql = " AND ".join(where_clauses)

        # Build SELECT, picking some sensible name field
        # (try name, then username)
        select_fields = (
            "u.id, "
            "COALESCE(u.name, u.username) AS name"
        )
        # include a blood_group expression only if columns exist
        if has_bg or has_bt:
            if has_bg and has_bt:
                select_fields += ", COALESCE(u.blood_group, u.blood_type) AS blood_group"
            elif has_bg:
                select_fields += ", u.blood_group AS blood_group"
            else:
                select_fields += ", u.blood_type AS blood_group"

        try:
            rows = db_fetchall(
                f"SELECT {select_fields} "
                f"FROM users u "
                f"WHERE {where_sql} "
                f"ORDER BY u.username COLLATE NOCASE",
                tuple(params),
            )
            for d in rows_to_dicts(rows):
                donors.append({
                    "id": int(d.get("id")),
                    "name": d.get("name") or str(d.get("id")),
                })
        except Exception as e:
            current_app.logger.exception(
                "issue_blood AJAX donor lookup failed: %s", e
            )
            # leave donors as empty list on error

        return donors

    # ----------------- AJAX: donor lookup by requester -----------------
    requester_id_q = request.args.get("requester_id")
    if request.method == "GET" and requester_id_q:
        try:
            rid = int(requester_id_q)
        except Exception:
            return jsonify({"error": "invalid_requester"}), 400

        requester_blood = _get_most_recent_requested_group_for_requester(rid)
        donors = _get_donors_for_blood_group(requester_blood)

        return jsonify({"blood_type": requester_blood, "donors": donors})

    # ----------------- POST: perform issuance / schedule donor appointment -----------------
    if request.method == "POST":
        requester_id = request.form.get("requester_id")
        if not requester_id:
            flash("Please select a requester.", "danger")
            return redirect(url_for("issue_blood"))
        try:
            requester_id_int = int(requester_id)
        except Exception:
            flash("Invalid requester selected.", "danger")
            return redirect(url_for("issue_blood"))

        # Determine requested blood group (recent request or form fallback)
        requested_group = _get_most_recent_requested_group_for_requester(
            requester_id_int
        )
        if not requested_group:
            requested_group = (request.form.get("blood_group") or "").strip() or None

        if not requested_group:
            flash(
                "Cannot determine blood group for this requester. Please specify.",
                "danger",
            )
            return redirect(url_for("issue_blood"))

        from_donor = bool(request.form.get("from_donor"))

        # ---- Path 1: schedule donor appointment (From Donor checked) ----
        if from_donor:
            donor_id_raw = request.form.get("donor_id")
            appointment_date = request.form.get("appointment_date")

            try:
                donor_id = int(donor_id_raw) if donor_id_raw else None
            except Exception:
                donor_id = None

            if not donor_id:
                flash("Please select a donor.", "danger")
                return redirect(url_for("issue_blood"))
            if not appointment_date:
                flash("Please choose an appointment date.", "danger")
                return redirect(url_for("issue_blood"))

            try:
                db_execute(
                    "INSERT INTO issues "
                    "(origin, reference_id, blood_group, units, created_at) "
                    "VALUES (?,?,?,?,?)",
                    (
                        "donor_appointment",
                        donor_id,
                        requested_group,
                        1,
                        datetime.utcnow().isoformat(timespec="seconds"),
                    ),
                )
                flash("Donor appointment recorded.", "success")
            except Exception as e:
                current_app.logger.exception(
                    "issue_blood: failed to record appointment: %s", e
                )
                flash(
                    "Failed to record appointment (see server logs).",
                    "danger",
                )
            return redirect(url_for("issue_blood"))

        # ---- Path 2: issue directly from store ----
        try:
            units = int(request.form.get("units", 1))
        except Exception:
            units = 1
        if units <= 0:
            units = 1

        store_row = db_fetchone(
            "SELECT units FROM store WHERE blood_group = ?",
            (requested_group,),
        )
        if store_row is None:
            flash("Selected blood group not tracked in store.", "danger")
            return redirect(url_for("issue_blood"))

        available = int(row_get(store_row, "units", 0) or 0)
        if available < units:
            flash(
                f"Not enough units in store (available: {available}).",
                "danger",
            )
            return redirect(url_for("issue_blood"))

        try:
            db_execute(
                "UPDATE store SET units = units - ? WHERE blood_group = ?",
                (units, requested_group),
            )
            db_execute(
                "INSERT INTO issues "
                "(origin, reference_id, blood_group, units, created_at) "
                "VALUES (?,?,?,?,?)",
                (
                    "store_issue",
                    requester_id_int,
                    requested_group,
                    units,
                    datetime.utcnow().isoformat(timespec="seconds"),
                ),
            )
            flash("Blood issued from store.", "success")
        except Exception as e:
            current_app.logger.exception(
                "issue_blood: failed to issue from store: %s", e
            )
            flash("Failed to issue blood (see server logs).", "danger")
        return redirect(url_for("issue_blood"))

    # ----------------- GET: render page -----------------
    requester_rows = db_fetchall(
        "SELECT id, username "
        "FROM users u WHERE u.role = 'requester' "
        "ORDER BY username COLLATE NOCASE"
    )
    requesters = []
    for r in rows_to_dicts(requester_rows):
        name = r.get("username") or str(r.get("id"))
        requesters.append(
            {
                "id": int(r.get("id")),
                "name": name,
                "phone": "",  # phone not on users table; keep template happy
            }
        )

    store_rows = db_fetchall(
        "SELECT blood_group, units FROM store ORDER BY blood_group"
    )
    blood_groups = rows_to_dicts(store_rows)

    return render_template(
        "issue_blood.html",
        requesters=requesters,
        blood_groups=blood_groups,
    )



@app.route("/report/donations")
@login_required
def donations_report():
    rows = db_fetchall("SELECT d.* FROM donations d ORDER BY d.created_at DESC LIMIT 500")
    donations = rows_to_dicts(rows)
    return render_template("donations_report.html", donations=donations)


@app.route("/report/issues")
@login_required
def issues_report():
    rows = db_fetchall("SELECT * FROM issues ORDER BY created_at DESC LIMIT 500")
    return render_template("issues_report.html", issues=rows_to_dicts(rows))


@app.route("/store_report")
@login_required
def store_report():
    if current_user.role not in ("admin", "staff"):
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    # --- pagination params ---
    issues_page = request.args.get("issues_page", default=1, type=int) or 1
    donations_page = request.args.get("donations_page", default=1, type=int) or 1
    issues_page = max(1, issues_page)
    donations_page = max(1, donations_page)

    ISSUES_PER_PAGE = 10
    DONATIONS_PER_PAGE = 10

    # =========================================================
    # 1) OUTGOING: ISSUES (Blood Issued)
    # =========================================================

    # Count issues that represent real outgoing transactions
    issues_count_row = db_fetchone(
        """
        SELECT COUNT(*) AS c
        FROM issues
        WHERE origin IN ('store_issue', 'request_completed')
        """
    )
    issues_total = int(row_get(issues_count_row, "c", 0) or 0)
    issues_pages = max(1, math.ceil(issues_total / ISSUES_PER_PAGE)) if issues_total else 1
    if issues_page > issues_pages:
        issues_page = issues_pages
    issues_offset = (issues_page - 1) * ISSUES_PER_PAGE

    issue_query = """
        SELECT
            i.id,
            i.blood_group,
            i.units,
            COALESCE(r.requester_name, 'N/A') AS requester_name,
            i.created_at,
            CASE
                WHEN i.origin = 'store_issue' THEN 'Store Issue'
                WHEN i.origin = 'request_completed' THEN 'Request Completion'
                ELSE 'Issue'
            END AS issue_type
        FROM issues i
        LEFT JOIN requests r
          ON i.reference_id = r.id
        WHERE i.origin IN ('store_issue', 'request_completed')
        ORDER BY i.created_at DESC
        LIMIT ? OFFSET ?
    """
    issues = rows_to_dicts(
        db_fetchall(issue_query, (ISSUES_PER_PAGE, issues_offset))
    )

    issues_pagination = {
        "page": issues_page,
        "pages": issues_pages,
        "total": issues_total,
        "has_prev": issues_page > 1,
        "has_next": issues_page < issues_pages,
        "prev_page": issues_page - 1,
        "next_page": issues_page + 1 if issues_page < issues_pages else issues_pages,
    }

    # =========================================================
    # 2) INCOMING: DONATIONS (Blood Donations)
    # =========================================================

    # Count only "real" donations:
    #  - must have a valid linked user
    #  - exclude any sentinel rows with donor_id = -1 (Store donation)
    donations_count_row = db_fetchone(
        """
        SELECT COUNT(*) AS c
        FROM donations d
        JOIN users u ON d.donor_id = u.id
        WHERE d.donor_id IS NOT NULL
          AND d.donor_id != -1
        """
    )
    donations_total = int(row_get(donations_count_row, "c", 0) or 0)
    donations_pages = max(1, math.ceil(donations_total / DONATIONS_PER_PAGE)) if donations_total else 1
    if donations_page > donations_pages:
        donations_page = donations_pages
    donations_offset = (donations_page - 1) * DONATIONS_PER_PAGE

    donation_query = """
        SELECT
            d.id,
            d.units,
            d.blood_group,
            u.name AS donor_name,
            d.donation_date,
            'Donation' AS transaction_type
        FROM donations d
        JOIN users u ON d.donor_id = u.id
        WHERE d.donor_id IS NOT NULL
          AND d.donor_id != -1
        ORDER BY d.donation_date DESC
        LIMIT ? OFFSET ?
    """
    donations = rows_to_dicts(
        db_fetchall(donation_query, (DONATIONS_PER_PAGE, donations_offset))
    )

    donations_pagination = {
        "page": donations_page,
        "pages": donations_pages,
        "total": donations_total,
        "has_prev": donations_page > 1,
        "has_next": donations_page < donations_pages,
        "prev_page": donations_page - 1,
        "next_page": donations_page + 1 if donations_page < donations_pages else donations_pages,
    }

    # Render template with both sections + pagination
    return render_template(
        "store_report.html",
        issues=issues,
        donations=donations,
        issues_pagination=issues_pagination,
        donations_pagination=donations_pagination,
    )
from io import StringIO, BytesIO
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch


# ----------------------- PDF EXPORTS ----------------------- #

def _pdf_write_table_header(c, start_x, start_y, headers, col_widths):
    c.setFont("Helvetica-Bold", 9)
    x = start_x
    for i, h in enumerate(headers):
        c.drawString(x, start_y, str(h))
        x += col_widths[i]


def _pdf_write_table_rows(c, start_x, start_y, rows, col_widths, line_height=12, max_rows_per_page=35, headers=None):
    y = start_y
    c.setFont("Helvetica", 9)
    row_count = 0

    for row in rows:
        if row_count >= max_rows_per_page:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = start_y
            row_count = 0
            if headers:
                _pdf_write_table_header(c, start_x, y, headers, col_widths)
                y -= line_height

        x = start_x
        for i, cell in enumerate(row):
            c.drawString(x, y, str(cell) if cell is not None else "")
            x += col_widths[i]
        y -= line_height
        row_count += 1


@app.route("/export/issues.pdf")
@login_required
def export_issues_pdf():
    if current_user.role not in ("admin", "staff"):
        return "Access denied", 403

    rows = db_fetchall("""
        SELECT
            i.id,
            i.created_at,
            i.blood_group,
            i.units,
            COALESCE(r.requester_name, 'N/A') AS requester,
            CASE
                WHEN i.origin='store_issue' THEN 'Store Issue'
                WHEN i.origin='request_completed' THEN 'Request Completion'
                ELSE 'Issue'
            END AS type
        FROM issues i
        LEFT JOIN requests r ON r.id = i.reference_id
        WHERE i.origin IN ('store_issue', 'request_completed')
        ORDER BY i.created_at DESC
    """)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A4))
    width, height = landscape(A4)

    title_y = height - 40
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, title_y, "Blood Issued (Outgoing)")

    headers = ["ID", "Date", "Blood Group", "Units", "Requester", "Type"]
    col_widths = [40, 110, 80, 50, 200, 110]

    table_start_y = title_y - 30
    _pdf_write_table_header(c, 40, table_start_y, headers, col_widths)

    # convert rows to simple lists
    data_rows = [
        [
            r["id"],
            r["created_at"],
            r["blood_group"],
            r["units"],
            r["requester"],
            r["type"],
        ]
        for r in rows
    ]

    _pdf_write_table_rows(
        c,
        start_x=40,
        start_y=table_start_y - 14,
        rows=data_rows,
        col_widths=col_widths,
        line_height=12,
        max_rows_per_page=35,
        headers=headers,
    )

    c.showPage()
    c.save()
    pdf = buffer.getvalue()
    buffer.close()

    return (
        pdf,
        200,
        {
            "Content-Disposition": "attachment; filename=issued_blood.pdf",
            "Content-Type": "application/pdf",
        },
    )


@app.route("/export/donations.pdf")
@login_required
def export_donations_pdf():
    if current_user.role not in ("admin", "staff"):
        return "Access denied", 403

    rows = db_fetchall("""
        SELECT
            d.id,
            d.donation_date,
            d.blood_group,
            d.units,
            u.name AS donor_name
        FROM donations d
        JOIN users u ON d.donor_id = u.id
        WHERE d.donor_id IS NOT NULL
          AND d.donor_id != -1
        ORDER BY d.donation_date DESC
    """)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A4))
    width, height = landscape(A4)

    title_y = height - 40
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, title_y, "Blood Donations (Incoming)")

    headers = ["ID", "Date", "Blood Group", "Units", "Donor Name"]
    col_widths = [40, 110, 80, 50, 220]

    table_start_y = title_y - 30
    _pdf_write_table_header(c, 40, table_start_y, headers, col_widths)

    data_rows = [
        [
            r["id"],
            r["donation_date"],
            r["blood_group"],
            r["units"],
            r["donor_name"],
        ]
        for r in rows
    ]

    _pdf_write_table_rows(
        c,
        start_x=40,
        start_y=table_start_y - 14,
        rows=data_rows,
        col_widths=col_widths,
        line_height=12,
        max_rows_per_page=35,
        headers=headers,
    )

    c.showPage()
    c.save()
    pdf = buffer.getvalue()
    buffer.close()

    return (
        pdf,
        200,
        {
            "Content-Disposition": "attachment; filename=blood_donations.pdf",
            "Content-Type": "application/pdf",
        },
    )


@app.route("/export/donations/<fmt>")
@login_required
def export_donations(fmt):
    if current_user.role not in ("admin","staff"):
        flash("Access denied.", "danger")
        return redirect(url_for("donations_report"))
    rows = rows_to_dicts(db_fetchall("SELECT * FROM donations ORDER BY created_at DESC"))
    items = rows
    if fmt == "csv":
        si = BytesIO()
        w = csv.writer(si)
        w.writerow(["id","donor_id","donor_name","blood_group","units","created_at"])
        for r in items:
            w.writerow([r.get("id"), r.get("donor_id"), r.get("donor_name"), r.get("blood_group"), r.get("units"), r.get("created_at")])
        si.seek(0)
        return send_file(si, as_attachment=True, download_name="donations.csv", mimetype="text/csv")
    if REPORTLAB_AVAILABLE:
        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        y = 750
        c.setFont("Helvetica-Bold", 12)
        c.drawString(30, y, "Donations")
        y -= 30
        c.setFont("Helvetica", 10)
        for r in items:
            line = f"{r['id']} - {r.get('donor_name','')} | {r.get('blood_group','')} | units:{r.get('units','')}"
            c.drawString(30, y, line)
            y -= 14
            if y < 60:
                c.showPage()
                y = 750
        c.save()
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name="donations.pdf", mimetype="application/pdf")
    si = BytesIO()
    w = csv.writer(si)
    w.writerow(["id","donor_id","donor_name","blood_group","units","created_at"])
    for r in items:
        w.writerow([r.get("id"), r.get("donor_id"), r.get("donor_name"), r.get("blood_group"), r.get("units"), r.get("created_at")])
    si.seek(0)
    flash("ReportLab not installed — exported CSV instead.", "warning")
    return send_file(si, as_attachment=True, download_name="donations.csv", mimetype="text/csv")


# ======================================================================
# DIAGNOSTICS & CONSERVATIVE FIX
# ======================================================================
# ... (diagnostics functions unchanged) ...
# (omitted here for brevity in this paste but unchanged in your file)
# -- (full diagnostics code still above; unchanged) --

# ======================================================================
# USERS MODULE
# ======================================================================

@app.route("/users")
@login_required
def users():
    if not (is_admin() or is_staff()):
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    role_filter = request.args.get("role", "").strip()
    q = request.args.get("q", "").strip()
    order_by = request.args.get("order_by", "created_at").strip()
    direction = request.args.get("direction", "desc").lower().strip()
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    allowed_order = {"created_at", "username", "role", "id"}
    if order_by not in allowed_order:
        order_by = "created_at"
    if direction not in ("asc", "desc"):
        direction = "asc"

    where = []
    params = []

    if is_staff():
        where.append("role != 'admin'")

    if role_filter:
        where.append("role = ?")
        params.append(role_filter)

    if q:
        where.append("username LIKE ?")
        params.append(f"%{q}%")

    base_sql = "SELECT * FROM users"
    if where:
        base_sql += " WHERE " + " AND ".join(where)

    count_sql = base_sql.replace("SELECT *", "SELECT COUNT(*) as total")
    total_row = db_fetchone(count_sql, tuple(params))
    total_count = int(row_get(total_row, "total", 0))

    offset = (page - 1) * per_page
    sql = f"{base_sql} ORDER BY {order_by} {direction} LIMIT ? OFFSET ?"
    params_for_query = tuple(params) + (per_page, offset)

    rows = db_fetchall(sql, params_for_query)
    users_list = rows_to_dicts(rows)

    distinct_roles_rows = db_fetchall("SELECT DISTINCT role FROM users")
    all_roles = sorted([row_get(r, "role") for r in distinct_roles_rows if row_get(r, "role")])

    total_pages = (total_count + per_page - 1) // per_page if per_page else 1

    pagination = {
        "page": page, "per_page": per_page,
        "has_prev": page > 1, "has_next": page < total_pages,
        "prev_page": page - 1, "next_page": page + 1, "total_pages": total_pages,
        "total_count": total_count
    }

    donor_users = [u for u in users_list if u.get("role") == "donor"]
    requesters = [u for u in users_list if u.get("role") == "requester"]
    staff_list = [u for u in users_list if u.get("role") in ("admin", "staff")]

    active_filters = {
        "role": role_filter,
        "q": q,
        "order_by": order_by,
        "direction": direction,
        "per_page": per_page
    }

    return render_template(
        "users.html",
        users=users_list,
        donor_users=donor_users,
        requesters=requesters,
        staff=staff_list,
        roles=all_roles,
        pagination=pagination,
        active_filters=active_filters
    )


@app.route("/users_list")
@login_required
def users_list():
    return users()


from datetime import datetime
from flask import request, redirect, url_for, flash

# In blood_bank_flask_v2_fixed.py

@app.route("/user/create", methods=("GET", "POST"))
@login_required
def create_user():
    """
    Allows an admin to create a new user (donor, staff, requester, or admin).
    """
    default_user = {
        'id': None,
        'username': '',
        'role': 'donor',
        'blood_group': '',
        'name': '',
        'email': '',
        'phone': '',
    }

    if request.method == "POST":
        username    = (request.form.get("username") or "").strip()
        password    = (request.form.get("password") or "").strip()
        role        = (request.form.get("role") or "").strip()
        blood_group = (request.form.get("blood_group") or "").strip()
        name        = (request.form.get("name") or "").replace("\n", " ").replace("\r", " ").strip()
        email       = (request.form.get("email") or "").strip()
        phone       = (request.form.get("phone") or "").strip()

        default_user.update({
            'username': username,
            'role': role,
            'blood_group': blood_group,
            'name': name,
            'email': email,
            'phone': phone,
        })

        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template('create_user.html', user=default_user)

        try:
            password_hash = generate_password_hash(password)

            # 1) Insert user (admin-created)
            db_execute(
                """
                INSERT INTO users
                    (username, password_hash, role, blood_type, name, email_id, phone)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (username, password_hash, role, blood_group, name, email, phone),
            )

            # 2) For donor/requester, set linked_entity_id = id using username
            if role in ("donor", "requester"):
                db_execute(
                    """
                    UPDATE users
                    SET linked_entity_id = id
                    WHERE username = ?
                      AND role = ?
                      AND (linked_entity_id IS NULL OR linked_entity_id = '')
                    """,
                    (username, role),
                )

            flash(f"User '{username}' created successfully.", "success")
            return redirect(url_for("users"))

        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                flash("Username already exists. Please choose another.", "danger")
            else:
                current_app.logger.exception(f"Failed to create user: {e}")
                flash("Failed to create user (see server logs).", "danger")

            return render_template('create_user.html', user=default_user)

    # GET request: Render the empty form with default values
    return render_template('create_user.html', user=default_user)



@app.route("/user/<int:id>/edit", methods=["GET", "POST"])
@login_required
def edit_user(id):
    # Fetch existing user
    user_row = db_fetchone("SELECT * FROM users WHERE id = ?", (id,))
    if not user_row:
        flash("User not found.", "danger")
        return redirect(url_for("users"))

    user = dict(user_row)

    if request.method == "POST":
        # Read form values, but fall back to existing DB values if missing/blank
        username = (request.form.get("username") or "").strip()
        if not username:
            username = user.get("username", "")

        raw_role = (request.form.get("role") or "").strip()
        role = raw_role or user.get("role", "")

        raw_blood = (request.form.get("blood_group") or
                     request.form.get("blood_type") or "").strip()
        blood_type = raw_blood or user.get("blood_type", "")

        raw_name = (request.form.get("name") or "").strip()
        name = raw_name or user.get("name", "")

        raw_email = (request.form.get("email") or
                     request.form.get("email_id") or "").strip()
        email = raw_email or user.get("email_id", "")

        raw_phone = (request.form.get("phone") or "").strip()
        phone = raw_phone or user.get("phone", "")

        password = (request.form.get("password") or "").strip()

        # EXISTING profile pic value from DB (matches column + template)
        profile_pic = user.get("profile_pic")

        # Handle uploaded profile image (file input name="profile_pic")
        file = request.files.get("profile_pic")
        if file and file.filename:
            # basic extension check
            filename = secure_filename(file.filename)
            ext = filename.rsplit(".", 1)[-1].lower()
            if ext not in ALLOWED_IMAGE_EXT:
                flash("Invalid image type. Allowed: png, jpg, jpeg, gif.", "danger")
                return render_template("edit_user.html", user=user)

            # Tie filename to user id
            filename = f"user_{id}.{ext}"

            upload_folder = current_app.config.get(
                "UPLOAD_FOLDER",  # we use static/uploads
                os.path.join("static", "uploads")
            )
            abs_folder = os.path.join(BASE_DIR, upload_folder)
            os.makedirs(abs_folder, exist_ok=True)

            file_path = os.path.join(abs_folder, filename)
            file.save(file_path)

            # Delete old file if different
            old_filename = user.get("profile_pic")
            if old_filename and old_filename != filename:
                old_path = os.path.join(abs_folder, old_filename)
                try:
                    if os.path.exists(old_path):
                        os.remove(old_path)
                except Exception:
                    current_app.logger.warning(
                        "Failed to delete old profile pic %s", old_path
                    )

            profile_pic = filename

        # Update local user dict so template re-renders with latest values if needed
        user.update({
            "username": username,
            "role": role,
            "blood_type": blood_type,
            "name": name,
            "email_id": email,
            "phone": phone,
            "profile_pic": profile_pic,
        })

        try:
            # Base UPDATE (no password change)
            params = [username, role, blood_type, name, email, phone, profile_pic, id]
            sql = """
                UPDATE users
                SET username = ?, role = ?, blood_type = ?, name = ?, email_id = ?, phone = ?, profile_pic = ?
                WHERE id = ?
            """

            # If password provided, update hash as well
            if password:
                password_hash = generate_password_hash(password)
                sql = """
                    UPDATE users
                    SET username = ?, role = ?, blood_type = ?, name = ?, email_id = ?, phone = ?, profile_pic = ?, password_hash = ?
                    WHERE id = ?
                """
                params = [username, role, blood_type, name, email, phone, profile_pic, password_hash, id]

            db_execute(sql, tuple(params))

            # Keep linked_entity_id self-linked for donor/requester
            if role in ("donor", "requester"):
                db_execute(
                    """
                    UPDATE users
                    SET linked_entity_id = id
                    WHERE id = ?
                      AND (linked_entity_id IS NULL OR linked_entity_id = '')
                    """,
                    (id,),
                )

            flash("User updated successfully.", "success")

            # Redirect:
            #  - if user edited themselves => go to their profile
            #  - otherwise (admin/staff editing others) => back to users list
            try:
                if current_user.is_authenticated and int(current_user.get_id()) == int(id):
                    return redirect(url_for("user_profile", id=id))
            except Exception:
                pass

            return redirect(url_for("users"))

        except Exception as e:
            current_app.logger.exception(f"Failed to update user: {e}")
            flash("Failed to update user (see server logs).", "danger")
            return render_template("edit_user.html", user=user)

    # GET request – show form with current DB data
    return render_template("edit_user.html", user=user)



@app.route("/user/<int:id>/delete", methods=("POST",))
@login_required
def delete_user(id):
    """
    Delete a user safely.
    - Only admins can delete admins. Staff may delete non-admins (but not admins or other staff).
    - If user has a linked donor/requester entity, delete that entity only if no other user
      of the same role references it.
    - Remove profile picture file if present.
    """
    # Fetch user row
    user_row = db_fetchone("SELECT * FROM users WHERE id = ?", (id,))
    if not user_row:
        flash("User not found.", "danger")
        return redirect(url_for("users"))

    user = dict(user_row)
    target_role = row_get(user_row, "role", "")
    linked_id = row_get(user_row, "linked_entity_id")

    # Access control: only admin can delete admins; staff can't delete admins/staff
    if is_staff() and target_role in ("admin", "staff"):
        flash("Access denied. Staff cannot delete admin/staff accounts.", "danger")
        return redirect(url_for("users"))

    if not (is_admin() or is_staff()):
        flash("Access denied.", "danger")
        return redirect(url_for("users"))

    # If we're deleting ourselves (current_user), prevent accidental self-delete
    try:
        if int(current_user.get_id()) == int(id):
            flash("You cannot delete your own account.", "danger")
            return redirect(url_for("users"))
    except Exception:
        # ignore if get_id not numeric
        pass

    # Remove profile picture file if exists (best-effort)
    try:
        profile_pic = row_get(user_row, "profile_pic")
        if profile_pic:
            upload_folder = app.config.get("UPLOAD_FOLDER", "static/uploads")
            pic_path = os.path.abspath(os.path.join(BASE_DIR, upload_folder, profile_pic))
            if os.path.exists(pic_path):
                try:
                    os.remove(pic_path)
                except Exception:
                    # ignore file remove errors but log
                    app.logger.exception("Failed to remove profile_pic file: %s", pic_path)
    except Exception:
        # keep going even if removal fails
        app.logger.exception("Error while trying to remove profile picture for user %s", id)

    # If user had a linked entity, delete that entity only if it is safe to do so
    if linked_id:
        try:
            # Count other users with the same role that reference this linked_id
            cnt_row = db_fetchone(
                "SELECT COUNT(*) as c FROM users WHERE role = ? AND linked_entity_id = ? AND id != ?",
                (target_role, linked_id, id)
            )
            other_refs = int(row_get(cnt_row, "c", 0) or 0)
        except Exception:
            other_refs = 0

        if other_refs == 0:
            # safe to delete the linked entity for this role
            try:
                if target_role == "requester":
                    db_execute("DELETE FROM requesters WHERE id = ?", (linked_id,))
                elif target_role == "donor":
                    # if you want to preserve donation history, consider only nulling or marking donor inactive
                    db_execute("DELETE FROM donors WHERE id = ?", (linked_id,))
                else:
                    # admins/staff usually do not have linked entities
                    pass
            except Exception as e:
                app.logger.exception("Failed to delete linked entity (role=%s id=%s): %s", target_role, linked_id, e)
                # do NOT abort user deletion; proceed but inform admin
                flash("Warning: failed to fully remove linked entity; check logs.", "warning")
        else:
            # linked entity referenced by other users, do not delete it
            app.logger.info("Linked entity id %s for role %s referenced by %s other user(s); skipping delete.",
                            linked_id, target_role, other_refs)

    # Finally delete the user row
    try:
        db_execute("DELETE FROM users WHERE id = ?", (id,))
        flash("User deleted successfully.", "success")
    except Exception as e:
        app.logger.exception("Failed to delete user id=%s: %s", id, e)
        flash("Failed to delete user. See logs.", "danger")

    return redirect(url_for("users"))

@app.route("/user/<username>/reset", methods=("POST",))
@login_required
def admin_reset_user_pw(username):
    target_row = db_fetchone("SELECT * FROM users WHERE username = ?", (username,))
    if not target_row:
        flash("User not found.", "danger")
        return redirect(url_for("users"))

    target_role = row_get(target_row, "role")

    if current_user.role == "admin":
        db_execute("UPDATE users SET password_hash = ? WHERE username = ?", (generate_password_hash("password"), username))
        flash(f"Password for {username} reset to default 'password'.", "info")
        return redirect(url_for("users"))

    if current_user.role == "staff":
        if target_role in ("donor", "requester") or username == current_user.username:
            db_execute("UPDATE users SET password_hash = ? WHERE username = ?", (generate_password_hash("password"), username))
            flash(f"Password for {username} reset to default 'password'.", "info")
            return redirect(url_for("users"))
        else:
            flash("Access denied: staff may only reset donor/requester passwords.", "danger")
            return redirect(url_for("users"))

    flash("Access denied.", "danger")
    return redirect(url_for("users"))


# -----------------------
# Serve uploads safely (robust)
# -----------------------
@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    """
    Serve user-uploaded files from the configured UPLOAD_FOLDER.
    Accepts:
      - 'xxx.png'
      - 'uploads/xxx.png'
      - 'static/uploads/xxx.png'
    """
    configured = app.config.get("UPLOAD_FOLDER", "static/uploads")
    upload_folder = os.path.abspath(os.path.join(BASE_DIR, configured))

    normalized = filename
    # Remove common prefixes
    if normalized.startswith("static/"):
        normalized = normalized[len("static/"):]
    if normalized.startswith("uploads/"):
        normalized = normalized[len("uploads/"):]
    # In case someone stored full path, take basename ultimately
    normalized = os.path.basename(normalized)

    full_path = os.path.abspath(os.path.join(upload_folder, normalized))

    if not full_path.startswith(upload_folder):
        abort(404)
    if not os.path.exists(full_path):
        abort(404)

    rel = os.path.relpath(full_path, upload_folder)
    return send_from_directory(upload_folder, rel)


# -----------------------
# PDF receipt for a donation
# -----------------------
@app.route("/donation/<int:id>/pdf")
@login_required
def donation_pdf(id):
    """
    Generate a PDF receipt + thank-you letter for a specific donation.
    If ReportLab is available (REPORTLAB_AVAILABLE), return a PDF inline so it opens in a new tab.
    Otherwise render an HTML page (which the user can Print->Save as PDF).
    Access: admin/staff can view any; donors may view their own donation.
    """
    # fetch donation with donor info
    row = db_fetchone(
        "SELECT d.*, u.phone AS donor_phone, u.blood_type AS donor_blood_type "
        "FROM donations d LEFT JOIN users u ON u.id = d.donor_id "
        "WHERE d.id = ?",
        (id,)
    )
    if not row:
        abort(404)

    donation = dict(row)

    # access control: admin/staff can view any; donor may view their own donation record
    if not (is_admin() or is_staff()):
        if is_donor():
            # if donor, ensure the donation donor_id matches current user's linked_entity_id
            donor_id = donation.get("donor_id")
            if not donor_id or int(donor_id) != int(current_user.linked_entity_id):
                flash("Access denied.", "danger")
                return redirect(url_for("dashboard"))
        else:
            flash("Access denied.", "danger")
            return redirect(url_for("dashboard"))

    donor_name = donation.get("donor_name") or "Donor"
    donor_phone = donation.get("donor_phone") or ""
    blood_group = donation.get("blood_group") or donation.get("donor_blood_group") or ""
    units = str(donation.get("units") or "")
    created_at = donation.get("created_at") or ""

    # If ReportLab is available, create PDF
    if REPORTLAB_AVAILABLE:
        buffer = BytesIO()
        # Use letter page size (works well cross-platform)
        c = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter

        # Header
        c.setFont("Helvetica-Bold", 16)
        c.drawString(40, height - 50, "Blood Bank Donation Receipt")

        c.setFont("Helvetica", 10)
        c.drawString(40, height - 70, f"Receipt ID: {id}")
        c.drawString(300, height - 70, f"Date: {created_at}")

        # Donation details
        y = height - 110
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, "Donation Details")
        y -= 18
        c.setFont("Helvetica", 10)
        c.drawString(50, y, f"Donor Name: {donor_name}")
        y -= 14
        c.drawString(50, y, f"Phone: {donor_phone}")
        y -= 14
        c.drawString(50, y, f"Blood Group: {blood_group}")
        y -= 14
        c.drawString(50, y, f"Units Donated: {units}")
        y -= 14
        c.drawString(50, y, f"Recorded at: {created_at}")

        # Divider
        y -= 26
        c.line(40, y, width - 40, y)
        y -= 24

        # Thank you letter content
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, "Thank you from the Blood Bank")
        y -= 18
        c.setFont("Helvetica", 10)
        thank_you_text = (
            f"Dear {donor_name},\n\n"
            "On behalf of the blood bank and the patients we serve, thank you for your generous donation. "
            "Your contribution helps save lives and supports our ongoing mission to provide safe blood "
            "to those in need. We appreciate your time and commitment.\n\n"
            "Please keep a record of this donation and follow any post-donation guidance provided by our staff. "
            "If you have any concerns, contact us at the blood bank."
        )

        # Wrap text using simpleSplit (ReportLab utility)
        lines = simpleSplit(thank_you_text, "Helvetica", 10, width - 80)
        for line in lines:
            if y < 80:
                c.showPage()
                y = height - 50
                c.setFont("Helvetica", 10)
            c.drawString(40, y, line)
            y -= 14

        # Footer
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(40, 40, "Thank you — Blood Bank")

        c.save()
        buffer.seek(0)

        response = make_response(buffer.read())
        response.headers.set('Content-Type', 'application/pdf')
        response.headers.set('Content-Disposition', f'inline; filename=donation_receipt_{id}.pdf')
        return response

    # Fallback: render an HTML page user can print/save as PDF
    return render_template("donation_receipt.html", donation=donation, donor_name=donor_name, donor_phone=donor_phone, blood_group=blood_group, units=units, created_at=created_at)


# ======================================================================
# USERS Module continuation already above
# (users, create_user, edit_user, delete_user done earlier)
# ======================================================================

# The users routes are already above; ensure naming consistency for template usage.
@app.route("/user/<int:id>/profile")
@login_required
def user_profile(id):
    """
    Profile view using ONLY users table for donor/requester.

    - Accepts id = users.id OR id = linked_entity_id (for backwards compatibility)
    - Admin/staff: view any profile
    - Users: view their own profile
    """
    # 1) Resolve user row: try by users.id, then by linked_entity_id
    user_row = db_fetchone("SELECT * FROM users WHERE id = ?", (id,))
    if not user_row:
        user_row = db_fetchone(
            "SELECT * FROM users WHERE linked_entity_id = ? LIMIT 1",
            (id,),
        )
    if not user_row:
        abort(404)

    user = dict(user_row)

    # Basic IDs
    try:
        target_user_id = int(user.get("id") or 0)
    except Exception:
        target_user_id = None

    target_linked_id = row_get(user_row, "linked_entity_id")
    try:
        target_linked_id_int = int(target_linked_id) if target_linked_id is not None else None
    except Exception:
        target_linked_id_int = None

    # 2) Access control
    allow = False

    if is_admin() or is_staff():
        allow = True

    # Logged-in user viewing their own user row
    try:
        if (
            not allow
            and current_user.is_authenticated
            and target_user_id
            and int(current_user.get_id()) == int(target_user_id)
        ):
            allow = True
    except Exception:
        pass

    # Allow when URL id is current_user.linked_entity_id (legacy behaviour)
    try:
        if (
            not allow
            and current_user.is_authenticated
            and current_user.linked_entity_id
            and int(current_user.linked_entity_id) == int(id)
        ):
            allow = True
    except Exception:
        pass

    # Allow when both have linked_entity_id and they match (legacy)
    try:
        if (
            not allow
            and current_user.is_authenticated
            and current_user.linked_entity_id
            and target_linked_id_int
            and int(current_user.linked_entity_id) == int(target_linked_id_int)
        ):
            allow = True
    except Exception:
        pass

    if not allow:
        flash("Access denied.", "danger")
        return redirect(url_for("users") if (is_admin() or is_staff()) else url_for("dashboard"))

    # 3) Enrich with donations/requests using ONLY users.id
    linked = None
    linked_type = None
    role = row_get(user_row, "role", "")

    try:
        if role == "donor" and target_user_id:
            # donations now point directly to users.id as donor_id
            donations_rows = db_fetchall(
                "SELECT * FROM donations WHERE donor_id = ? ORDER BY created_at DESC LIMIT 10",
                (target_user_id,),
            )
            linked = {
                "donations": rows_to_dicts(donations_rows)
            }
            linked_type = "donor"

        elif role == "requester" and target_user_id:
            # requests now point directly to users.id as requester_id
            req_rows = db_fetchall(
                "SELECT * FROM requests WHERE requester_id = ? ORDER BY created_at DESC LIMIT 50",
                (target_user_id,),
            )
            linked = {
                "requests": rows_to_dicts(req_rows)
            }
            linked_type = "requester"

    except Exception:
        linked = None
        linked_type = None

    profile_pic = row_get(user_row, "profile_pic", None)
    return render_template(
        "profile.html",
        user=user,
        linked=linked,
        linked_type=linked_type,
        profile_pic=profile_pic,
    )


@app.route("/my_profile")
@login_required
def my_profile():
    """
    Redirect convenience route: always redirect to the user's own user_profile (by users.id).
    This avoids ambiguity between users.id and linked_entity_id.
    """
    try:
        return redirect(url_for("user_profile", id=int(current_user.get_id())))
    except Exception:
        return redirect(url_for("dashboard"))


@app.route("/user/<int:id>/toggle_user_active", methods=["POST"])
@login_required
def toggle_user_active(id):
    # admin-only toggle
    if current_user.role != "admin":
        flash("Only admin can change active status.", "danger")
        return redirect(url_for("users"))

    row = db_fetchone("SELECT * FROM users WHERE id = ?", (id,))
    if not row:
        flash("User not found.", "danger")
        return redirect(url_for("users"))

    # read existing status defensively (default active)
    current_status = int(row_get(row, "is_active", 1))
    new_status = 0 if current_status == 1 else 1

    # attempt update — if column missing, add it then update
    try:
        db_execute("UPDATE users SET is_active = ? WHERE id = ?", (new_status, id))
    except Exception:
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1;")
            conn.commit()
            conn.close()
            db_execute("UPDATE users SET is_active = ? WHERE id = ?", (new_status, id))
        except Exception as e:
            flash(f"Failed to change active status: {e}", "danger")
            return redirect(url_for("users"))

    flash(f"User {'deactivated' if new_status == 0 else 'activated'}.", "info")
    return redirect(url_for("users"))


if __name__ == "__main__":
    app.run(debug=True)
