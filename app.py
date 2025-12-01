# app.py
# -----------------------------------------
# Report Rocket – Flask application entry
# -----------------------------------------

import os
import csv
from datetime import datetime

from flask import (
    Flask, render_template, redirect, url_for, flash, request, jsonify,
    send_from_directory
)
from flask_login import (
    LoginManager, login_user, login_required, logout_user, current_user
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# Forms & Models
from forms import RegistrationForm, LoginForm
from models import db, User
try:
    from models import ClassProfile
except Exception:
    ClassProfile = None

try:
    from models import ClassRow  # optional child-row model
except Exception:
    ClassRow = None

# Optional OpenAI (safe if not configured)
try:
    from openai import OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
    openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    openai_client = None

# Optional Email (SendGrid) – best-effort only
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "").strip()
FROM_EMAIL = os.getenv("FROM_EMAIL", "no-reply@report-rocket.com")
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
except Exception:
    SendGridAPIClient = None
    Mail = None


def send_email(to_email: str, subject: str, html: str) -> bool:
    """Best-effort email; silently no-ops if not configured."""
    if not (SENDGRID_API_KEY and SendGridAPIClient and Mail):
        return False
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        message = Mail(from_email=FROM_EMAIL, to_emails=to_email,
                       subject=subject, html_content=html)
        sg.send(message)
        return True
    except Exception:
        return False


# =========================================
# Flask & DB configuration
# =========================================
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")

# Prefer Postgres (Render), fall back to local SQLite
db_url = os.getenv("DATABASE_URL", "sqlite:///app.db")

# Normalize Render's URL for psycopg3
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
elif db_url.startswith("postgresql://") and "+psycopg" not in db_url:
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {
    "pool_pre_ping": True,
    "pool_recycle": 300,
})

db.init_app(app)

# Create tables on import (safe locally and on Render)
with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        app.logger.warning(f"db.create_all() failed: {e}")


# =========================================
# Global template vars (brand, etc.)
# =========================================
@app.context_processor
def inject_brand():
    return {"BRAND": "Report Rocket"}


# =========================================
# Login manager
# =========================================
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"


@login_manager.user_loader
def load_user(user_id: str):
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None


# =========================================
# Pricing per number of reports (exact spec)
# =========================================
# You can rename the internal slugs freely; the limits & prices follow your request.
PLANS = [
    {
        "slug": "free",
        "name": "Free",
        "price": 0.00,
        "limit": 10,
        "period": "total",  # total lifetime
        "cta": {"text": "Start Free", "href": "/register?plan=free"},
        "features": [
            "10 reports total",
            "CSV export",
            "No credit card required",
        ],
        "is_featured": False,
    },
    {
        "slug": "basic",
        "name": "Basic",
        "price": 1.99,
        "limit": 30,
        "period": "monthly",
        "cta": {"text": "Choose Basic", "href": "/register?plan=basic"},
        "features": [
            "30 reports / month",
            "Fast generation",
            "CSV export",
        ],
        "is_featured": False,
    },
    {
        "slug": "standard",
        "name": "Standard",
        "price": 4.99,
        "limit": 100,
        "period": "monthly",
        "cta": {"text": "Choose Standard", "href": "/register?plan=standard"},
        "features": [
            "100 reports / month",
            "Priority generation",
            "Team-friendly exports",
        ],
        "is_featured": True,
    },
    {
        "slug": "pro",
        "name": "Pro",
        "price": 9.99,
        "limit": 1000,
        "period": "monthly",
        "cta": {"text": "Choose Pro", "href": "/register?plan=pro"},
        "features": [
            "1000 reports / month",
            "Highest throughput",
            "Priority support",
        ],
        "is_featured": False,
    },
]

# Helper lookups
PLAN_BY_SLUG = {p["slug"]: p for p in PLANS}


def current_yyyymm() -> int:
    """Return YYYYMM as an int, e.g., 202510."""
    now = datetime.utcnow()
    return now.year * 100 + now.month


def apply_plan(user, slug: str):
    """Set a user's plan + counters. Works even if optional columns are missing."""
    plan = PLAN_BY_SLUG.get(slug, PLAN_BY_SLUG["free"])
    if hasattr(user, "plan"):
        user.plan = plan["slug"]
    if hasattr(user, "reports_limit"):
        user.reports_limit = plan["limit"]
    if hasattr(user, "reports_used"):
        user.reports_used = 0
    # Optional monthly reset tracking column (add to your model if desired)
    if plan["period"] == "monthly" and hasattr(user, "reports_month"):
        user.reports_month = current_yyyymm()
    elif hasattr(user, "reports_month"):
        user.reports_month = None



def maybe_reset_month(user):
    """
    If the user's plan is monthly and the month changed, reset counters.
    This requires an optional `reports_month` Integer column (YYYYMM) on User.
    If the column isn't present, this safely no-ops.
    """
    slug = getattr(user, "plan", "free")
    plan = PLAN_BY_SLUG.get(slug, PLAN_BY_SLUG["free"])
    if plan["period"] != "monthly":
        return
    if not hasattr(user, "reports_month"):
        # Can't track month without column; still enforce limit but can't auto reset
        return
    yyyymm_now = current_yyyymm()
    if getattr(user, "reports_month", None) != yyyymm_now:
        if hasattr(user, "reports_used"):
            user.reports_used = 0
        user.reports_month = yyyymm_now
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()


# =========================================
# File exports
# =========================================
EXPORT_DIR = os.path.join(os.path.dirname(__file__), "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)


# =========================================
# Helpers for ClassProfile rows
# =========================================
def _profile_to_dict(profile, include_rows=True):
    out = {
        "id": profile.id,
        "class_name": getattr(profile, "class_name", ""),
        "subject": getattr(profile, "subject", ""),
        "max_words": getattr(profile, "max_words", 50),
    }
    if not include_rows:
        return out

    rows_payload = []

    # Option A: normalized child rows
    if hasattr(profile, "rows") and profile.rows is not None and ClassRow is not None:
        for r in profile.rows:
            rows_payload.append({
                "name": r.name or "",
                "gender": r.gender or "",
                "tests": r.tests or "",
                "homework": r.homework or "",
                "organisation": r.organisation or "",
                "participation": r.participation or "",
                "comments": r.comments or "",
                "report": r.report or "",
            })

    # Option B: JSON column
    elif hasattr(profile, "rows_json") and profile.rows_json:
        try:
            for r in profile.rows_json:
                rows_payload.append({
                    "name": r.get("name", ""),
                    "gender": r.get("gender", ""),
                    "tests": r.get("tests", ""),
                    "homework": r.get("homework", ""),
                    "organisation": r.get("organisation", ""),
                    "participation": r.get("participation", ""),
                    "comments": r.get("comments", ""),
                    "report": r.get("report", ""),
                })
        except Exception:
            rows_payload = []

    out["rows"] = rows_payload
    return out


def _replace_rows(profile, rows):
    # Option A: normalized rows
    if hasattr(profile, "rows") and profile.rows is not None and ClassRow is not None:
        for child in list(profile.rows):
            db.session.delete(child)
        db.session.flush()
        for r in rows or []:
            child = ClassRow(
                profile_id=profile.id,
                name=(r.get("name") or "").strip(),
                gender=(r.get("gender") or "").strip(),
                tests=(r.get("tests") or "").strip(),
                homework=(r.get("homework") or "").strip(),
                organisation=(r.get("organisation") or "").strip(),
                participation=(r.get("participation") or "").strip(),
                comments=(r.get("comments") or "").strip(),
                report=(r.get("report") or "").strip(),
            )
            db.session.add(child)

    # Option B: JSON column
    elif hasattr(profile, "rows_json"):
        profile.rows_json = [
            {
                "name": (r.get("name") or "").strip(),
                "gender": (r.get("gender") or "").strip(),
                "tests": (r.get("tests") or "").strip(),
                "homework": (r.get("homework") or "").strip(),
                "organisation": (r.get("organisation") or "").strip(),
                "participation": (r.get("participation") or "").strip(),
                "comments": (r.get("comments") or "").strip(),
                "report": (r.get("report") or "").strip(),
            }
            for r in (rows or [])
        ]
    else:
        setattr(profile, "rows_json", rows or [])


# =========================================
# Routes – Public pages
# =========================================
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/school", methods=["GET"])
def school():
    return render_template("school.html")

@app.route("/school-trial", methods=["POST"])
def school_trial():
    """Handle school trial requests from the form on /school."""
    name = (request.form.get("name") or "").strip()
    role = (request.form.get("role") or "").strip()
    email = (request.form.get("email") or "").strip()

    website = (request.form.get("website") or "").strip()
    variant = (request.form.get("variant") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    accepted = bool(request.form.get("terms"))

    # Log or store; keep it simple for now
    app.logger.info("School trial: name=%s role=%s email=%s site=%s variant=%s accepted_terms=%s notes=%s",
                    name, role, email, website, variant, accepted, notes)

    # Optional: email yourself via SendGrid if configured
    try:
        send_email(
            to_email=email,
            subject="Thanks — Report Rocket school trial",
            html="<p>Thanks for your request. We’ll be in touch shortly.</p>"
        )
    except Exception:
        pass  # fine if email isn’t configured

    flash("Thanks! We’ll be in touch shortly with your school trial details.", "success")
    return redirect(url_for("school"))

@app.route("/pricing")
def pricing():
    # Show Free on the left, Standard highlighted
    plans_sorted = sorted(PLANS, key=lambda x: 0 if x["slug"] == "free" else 1)
    return render_template("pricing.html", plans=plans_sorted)

@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/fb")
def fb():
    return render_template("fb.html")


# =========================================
# Auth (forms.py backed)
# =========================================
@app.route("/register", methods=["GET", "POST"])
def register():
    form = RegistrationForm()
    requested_plan = request.args.get("plan", "free")

    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "danger")
            return render_template("register.html", form=form, plan=requested_plan), 400

        user = User(email=email)
        if hasattr(user, "set_password"):
            user.set_password(form.password.data)
        else:
            user.password_hash = generate_password_hash(form.password.data)

        # Apply requested plan (defaults to free)
        apply_plan(user, requested_plan)

        db.session.add(user)
        db.session.commit()

        # Auto-login to dashboard
        login_user(user)
        flash("Welcome to Report Rocket!", "success")
        return redirect(url_for("report"))

    return render_template("register.html", form=form, plan=requested_plan)


@app.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        user = User.query.filter_by(email=email).first()
        ok = False
        if user:
            if hasattr(user, "check_password"):
                ok = user.check_password(form.password.data)
            else:
                ok = check_password_hash(getattr(user, "password_hash", ""), form.password.data)

        if not user or not ok:
            flash("Invalid email or password.", "danger")
            return render_template("login.html", form=form), 401

        login_user(user)
        return redirect(url_for("report"))

    return render_template("login.html", form=form)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


# =========================================
# Report UI (template only)
# =========================================
@app.route("/report", methods=["GET"])
@login_required
def report():
    return render_template("report.html")


# =========================================
# APIs used by report.html
# =========================================
@app.route("/generate_report", methods=["POST"])
@login_required
def generate_report_api():
    # Monthly auto-reset if available
    maybe_reset_month(current_user)

    # Enforce plan limit
    limit = getattr(current_user, "reports_limit", None)
    used = getattr(current_user, "reports_used", 0)
    if limit is not None and used is not None and used >= limit:
        # Clarify if it's total or monthly based on plan
        slug = getattr(current_user, "plan", "free")
        period = PLAN_BY_SLUG.get(slug, PLAN_BY_SLUG["free"])["period"]
        msg = "Report limit reached"
        msg += " for this month." if period == "monthly" else " for your plan."
        return jsonify({"error": f"{msg} Please upgrade to continue."}), 402

    if not openai_client:
        return jsonify(error="Server missing OPENAI_API_KEY"), 500

    data = request.get_json(silent=True) or {}
    # Default to 50 words
    max_words = str(data.get("max_words") or 50).strip()

    prompt = (
        f"Write up to {max_words} words for student {data.get('name','').strip()}.\n"
        f"Class: {data.get('class','').strip()}; Subject: {data.get('subject','').strip()}.\n"
        f"Performance:\n"
        f"- Class tests: {data.get('tests','')}\n"
        f"- Homework: {data.get('homework','')}\n"
        f"- Organisation: {data.get('organisation','')}\n"
        f"- Participation: {data.get('participation','')}\n"
        f"Teacher notes: {data.get('comments','')}\n"
        "Be specific, supportive, and do NOT mention gender."
    )

    try:
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an experienced school teacher."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
        )
        text = (resp.choices[0].message.content or "").strip()

        # Increment usage counter when present
        if hasattr(current_user, "reports_used"):
            current_user.reports_used = (current_user.reports_used or 0) + 1
            db.session.commit()

        return jsonify(report=text)
    except Exception as e:
        return jsonify(error=f"AI error: {e}"), 500


@app.route("/save_report", methods=["POST"])
@login_required
def save_report():
    data = request.get_json(silent=True) or {}
    class_name = (data.get("class") or "Class").strip()
    subject = (data.get("subject") or "Subject").strip()
    rows = data.get("rows") or []

    base = f"{class_name}_{subject}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    safe = secure_filename(base)
    path = os.path.join(EXPORT_DIR, safe)

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Gender", "Report Generated"])
        for r in rows:
            w.writerow([
                (r.get("name") or "").strip(),
                (r.get("gender") or "").strip(),
                (r.get("report") or "").strip()
            ])

    return jsonify(url=url_for("download_export", filename=safe))


@app.route("/download/<path:filename>")
@login_required
def download_export(filename):
    return send_from_directory(EXPORT_DIR, filename, as_attachment=True)


# ---------- Class Profiles (only if model exists) ----------
if ClassProfile is not None:

    @app.route("/class_profile/save", methods=["POST"])
    @login_required
    def save_class_profile():
        data = request.json or {}
        class_name = (data.get("class") or "").strip()
        subject = (data.get("subject") or "").strip()
        max_words = int(data.get("max_words") or 50)
        rows = data.get("rows", [])

        if not class_name or not subject:
            return jsonify(error="Class and Subject are required"), 400

        existing = (ClassProfile.query
                    .filter_by(user_id=current_user.id,
                               class_name=class_name,
                               subject=subject)
                    .first())

        if existing:
            existing.max_words = max_words
            _replace_rows(existing, rows)
            db.session.commit()
            return jsonify(
                id=existing.id,
                class_name=existing.class_name,
                subject=existing.subject,
                max_words=existing.max_words,
                message="UPDATED"
            ), 200

        cp = ClassProfile(
            user_id=current_user.id,
            class_name=class_name,
            subject=subject,
            max_words=max_words
        )
        db.session.add(cp)
        db.session.flush()  # ensure cp.id exists for child rows
        _replace_rows(cp, rows)

        db.session.commit()
        return jsonify(
            id=cp.id,
            class_name=cp.class_name,
            subject=cp.subject,
            max_words=cp.max_words,
            message="CREATED"
        ), 201

    @app.route("/class_profiles", methods=["GET"])
    @login_required
    def list_class_profiles():
        rows = (ClassProfile.query
                .filter_by(user_id=current_user.id)
                .order_by(ClassProfile.id.desc())
                .all())
        return jsonify([
            {"id": r.id, "class_name": r.class_name, "subject": r.subject, "max_words": r.max_words}
            for r in rows
        ])

    @app.route("/class_profile/<int:cp_id>", methods=["GET"])
    @login_required
    def get_class_profile_header(cp_id):
        cp = ClassProfile.query.filter_by(id=cp_id, user_id=current_user.id).first_or_404()
        return jsonify(_profile_to_dict(cp, include_rows=False))

    @app.route("/class_profile/<int:cp_id>/full", methods=["GET"])
    @login_required
    def get_class_profile_full(cp_id):
        cp = ClassProfile.query.filter_by(id=cp_id, user_id=current_user.id).first_or_404()
        return jsonify(_profile_to_dict(cp, include_rows=True))


# =========================================
# Local boot / CLI
# =========================================
@app.cli.command("init-db")
def init_db_command():
    """flask init-db — create tables and show them."""
    from sqlalchemy import inspect
    with app.app_context():
        db.create_all()
        print("Tables:", inspect(db.engine).get_table_names())


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
