# app.py
# -----------------------------------------
# Report Rocket – Flask application entry
# -----------------------------------------
from flask_migrate import Migrate

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

# --- Your app modules ---
from models import db, User, ClassProfile
try:
    # Optional child-row model; we handle both with/without it
    from models import ClassRow
except Exception:
    ClassRow = None

from forms import RegistrationForm, LoginForm

# --- OpenAI (v1 SDK) ---
from openai import OpenAI

# --- Optional Email (SendGrid) ---
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail


# =========================================
# Flask & DB configuration
# =========================================
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")

# Prefer Postgres (Render), fall back to local SQLite
db_url = os.getenv("DATABASE_URL", "sqlite:///site.db")

# Normalize Render's URL for psycopg3
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
elif db_url.startswith("postgresql://"):
    # if they gave us postgresql:// without driver, add psycopg
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url


app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {
    "pool_pre_ping": True,
    "pool_recycle": 300,
})
db.init_app(app)
migrate = Migrate(app, db)
# Login manager
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"

@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


# =========================================
# External services (OpenAI, SendGrid)
# =========================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# --- Email (optional) ---
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", "no-reply@report-rocket.com")

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
except Exception:
    SendGridAPIClient = None
    Mail = None

def send_email(to_email: str, subject: str, html: str):
    if not (SENDGRID_API_KEY and SendGridAPIClient and Mail):
        # Not configured; just skip silently
        return False
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        message = Mail(from_email=FROM_EMAIL, to_emails=to_email,
                       subject=subject, html_content=html)
        sg.send(message)
        return True
    except Exception as e:
        app.logger.warning(f"Email send failed: {e}")
        return False


# =========================================
# File exports
# =========================================
EXPORT_DIR = os.path.join(os.path.dirname(__file__), "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)


# =========================================
# Helpers for ClassProfile rows
# =========================================
def _profile_to_dict(profile, include_rows=True):
    """Return a dict for API responses. Supports either child rows or JSON."""
    out = {
        "id": profile.id,
        "class_name": profile.class_name,
        "subject": profile.subject,
        "max_words": profile.max_words,
    }
    if not include_rows:
        return out

    rows_payload = []

    # Option A: normalized child rows (if present)
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
    """Write rows to profile, supporting either child table or JSON column."""
    # Option A: normalized rows
    if hasattr(profile, "rows") and profile.rows is not None and ClassRow is not None:
        # clear existing children
        for child in list(profile.rows):
            db.session.delete(child)
        db.session.flush()
        # add new
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
        # In-memory fallback only (won't persist without a column)
        setattr(profile, "rows_json", rows or [])


# =========================================
# Routes – Public pages
# =========================================
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/pricing")
def pricing():
    return render_template("pricing.html")

@app.route("/school", methods=["GET"])
def school():
    return render_template("school.html")

@app.route("/school-trial", methods=["POST"])
def school_trial():
    # Simple capture; later store in DB or send a notification email
    payload = {k: request.form.get(k, "") for k in
               ("name", "role", "email", "website", "variant")}
    accepted = bool(request.form.get("terms"))
    app.logger.info("School trial request: %r | accepted terms=%s", payload, accepted)
    flash("Thanks! We'll be in touch shortly with your trial details.", "success")
    return redirect(url_for("school"))

@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


# =========================================
# Auth
# =========================================
@app.route("/register", methods=["GET", "POST"])
def register():
    form = RegistrationForm()

    # capture ?plan=free|teacher|school from the URL
    plan = request.args.get("plan", "free")

    if form.validate_on_submit():
        # block duplicate email
        if User.query.filter_by(email=form.email.data).first():
            flash("Email already registered.", "danger")
            return redirect(url_for("register", plan=plan))

        user = User(email=form.email.data)
        user.set_password(form.password.data)

        # Only set plan/limits if those fields exist on your model
        if hasattr(user, "plan"):
            user.plan = plan if plan in {"free", "teacher", "school"} else "free"
        if hasattr(user, "reports_limit"):
            user.reports_limit = 10 if plan == "free" else None
        if hasattr(user, "reports_used"):
            user.reports_used = 0

        db.session.add(user)
        db.session.commit()

        # (Optional) send welcome email
        try:
            send_email(
                to_email=form.email.data,
                subject="Welcome to Report Rocket 🚀",
                html="<p>Your account is ready. Happy reporting!</p>"
            )
        except Exception:
            pass

        flash("Account created — please log in!", "success")
        return redirect(url_for("login", plan=plan))

    return render_template("register.html", form=form, plan=plan)


@app.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            flash("Logged in successfully.", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("report"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html", form=form)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


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
    # Free plan limit enforcement (only if those columns exist)
    limit = getattr(current_user, "reports_limit", None)
    used  = getattr(current_user, "reports_used", 0)
    if limit is not None and used is not None and used >= limit:
        return jsonify({"error": "Free plan limit reached. Please upgrade to continue generating reports."}), 402

    if not client:
        return jsonify(error="Server missing OPENAI_API_KEY"), 500

    data = request.get_json(silent=True) or {}

    # Default to 50 words unless the client passes something else
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
        resp = client.chat.completions.create(
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
    subject    = (data.get("subject") or "Subject").strip()
    rows       = data.get("rows") or []

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


# ---------- Class Profiles ----------
@app.route("/class_profile/save", methods=["POST"])
@login_required
def save_class_profile():
    data = request.json or {}
    class_name = (data.get("class") or "").strip()
    subject    = (data.get("subject") or "").strip()
    max_words  = int(data.get("max_words") or 50)  # default now 50
    rows       = data.get("rows", [])

    if not class_name or not subject:
        return jsonify(error="Class and Subject are required"), 400

    # Upsert by (user_id, class_name, subject)
    existing = (ClassProfile.query
                .filter_by(user_id=current_user.id,
                           class_name=class_name,
                           subject=subject)
                .first())

    if existing:
        existing.max_words = max_words
        # Use helper to support child rows or JSON column
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
    # Set rows (works for JSON column or child rows)
    _replace_rows(cp, rows)

    db.session.add(cp)
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
            .order_by(ClassProfile.created_at.desc())
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
    # Build using helper so both storage modes are supported
    return jsonify(_profile_to_dict(cp, include_rows=True))


# =========================================
# Local boot
# =========================================
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    # Local dev server; on Render we use gunicorn
    app.run(debug=True)
