import os
import csv
from datetime import datetime

# --- add near the other imports ---
from flask import send_from_directory




from flask import Flask, render_template, redirect, url_for, flash, request, jsonify, send_from_directory
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from models import db, User, ClassProfile

from flask import (
    Flask, render_template, redirect, url_for, flash, request, jsonify,
    send_from_directory
)
from flask_login import (
    LoginManager, login_user, login_required, logout_user, current_user
)

from werkzeug.utils import secure_filename

# --- Your app modules (must exist) ---
# models.py must export: db, User, ClassProfile
# Optionally: ClassRow (child table) OR ClassProfile.rows_json (JSON)
from models import db, User, ClassProfile
try:
    from models import ClassRow  # optional; we handle both with/without it
except Exception:
    ClassRow = None

# Forms
from forms import RegistrationForm, LoginForm

# OpenAI v1 client
from openai import OpenAI

# Read key from env
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

# --- Flask app ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "dev-secret")
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "dev-secret")

# Use Postgres in Render; fall back to local SQLite for dev
db_url = os.getenv("DATABASE_URL", "sqlite:///site.db")

# Render’s DATABASE_URL may start with postgres://; SQLAlchemy prefers postgresql://
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Nice-to-haves for cloud DBs
app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {
    "pool_pre_ping": True,
    "pool_recycle": 300,
})

db.init_app(app)






# --- Login manager ---
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))





# --- update your home route to show the landing page ---
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/pricing')
def pricing():
    return render_template('pricing.html')

@app.route('/school', methods=['GET'])
def school():
    return render_template('school.html')

@app.route('/school-trial', methods=['POST'])
def school_trial():
    # Simple capture; replace with DB insert or email as needed
    form = {k: request.form.get(k, '') for k in
            ('name','role','email','website','variant')}
    accepted = bool(request.form.get('terms'))
    app.logger.info("School trial request: %r | accepted terms=%s", form, accepted)

    flash("Thanks! We'll be in touch shortly with your trial details.", "success")
    return redirect(url_for('school'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    # If your RegistrationForm differs, keep your version
    form = RegistrationForm()
    if form.validate_on_submit():
        if User.query.filter_by(email=form.email.data).first():
            flash('Email already registered.', 'danger')
            return redirect(url_for('register'))
        user = User(email=form.email.data)
        # Your User model must implement set_password()
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash('Account created—please log in!', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            flash('Logged in successfully.', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('report'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html', form=form)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


# ---------- Report page (template only) ----------
@app.route('/report', methods=['GET'])
@login_required
def report():
    # Rendering only; all logic is JS + JSON endpoints below
    return render_template('report.html')


# ---------- OpenAI: generate single row ----------
@app.route('/generate_report', methods=['POST'])
@login_required
def generate_report_api():
    if not OPENAI_API_KEY:
        return jsonify(error="Server missing OPENAI_API_KEY"), 500

    data = request.get_json(silent=True) or {}
    # Build a compact prompt from the row + header controls
    max_words = str(data.get('max_words') or 100)
    prompt = (
        f"Write up to {max_words} words for student {data.get('name','').strip()}."
        f" Class: {data.get('class','').strip()}; Subject: {data.get('subject','').strip()}.\n"
        f"Performance:\n"
        f"- Class tests: {data.get('tests','')}\n"
        f"- Homework: {data.get('homework','')}\n"
        f"- Organisation: {data.get('organisation','')}\n"
        f"- Participation: {data.get('participation','')}\n"
        f"Teacher notes: {data.get('comments','')}\n"
        "Be specific, supportive, and do NOT mention gender."
    )
    try:
        # gpt-4o-mini is inexpensive/fast; change if you prefer
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an experienced school teacher."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
        )
        text = (resp.choices[0].message.content or "").strip()
        return jsonify(report=text)
    except Exception as e:
        return jsonify(error=f"AI error: {e}"), 500


# ---------- Download CSV: Name / Gender / Report ----------
EXPORT_DIR = os.path.join(os.path.dirname(__file__), 'exports')
os.makedirs(EXPORT_DIR, exist_ok=True)


@app.route('/save_report', methods=['POST'])
@login_required
def save_report():
    data = request.get_json(silent=True) or {}
    class_name = (data.get('class') or 'Class').strip()
    subject = (data.get('subject') or 'Subject').strip()
    rows = data.get('rows') or []

    base = f"{class_name}_{subject}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    safe = secure_filename(base)
    path = os.path.join(EXPORT_DIR, safe)

    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['Name', 'Gender', 'Report Generated'])
        for r in rows:
            w.writerow([
                (r.get('name') or '').strip(),
                (r.get('gender') or '').strip(),
                (r.get('report') or '').strip()
            ])

    return jsonify(url=url_for('download_export', filename=safe))


@app.route('/download/<path:filename>')
@login_required
def download_export(filename):
    return send_from_directory(EXPORT_DIR, filename, as_attachment=True)


# ---------- Class Profiles (save header + rows) ----------
def _profile_to_dict(profile, include_rows=True):
    """Convert a ClassProfile to API dict; supports either .rows relationship
    (list of ClassRow) or .rows_json (JSON list)."""
    out = {
        "id": profile.id,
        "class_name": profile.class_name,
        "subject": profile.subject,
        "max_words": profile.max_words,
    }
    if not include_rows:
        return out

    rows_payload = []
    # Option A: normalized rows
    if hasattr(profile, "rows") and profile.rows is not None:
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
        for r in rows:
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
        # If neither attribute exists, create a quick JSON attribute in-memory.
        # (Will be lost unless your model actually defines a column.)
        setattr(profile, "rows_json", rows or [])


# Save header + ALL rows (overwrite with a new saved profile)
# app.py  — replace the whole /class_profile/save route with this
@app.route('/class_profile/save', methods=['POST'])
@login_required
def save_class_profile():
    data = request.json or {}
    class_name = (data.get("class") or "").strip()
    subject    = (data.get("subject") or "").strip()
    max_words  = int(data.get("max_words") or 100)
    rows       = data.get("rows", [])  # array of row dicts (Name→Report)

    if not class_name or not subject:
        return jsonify(error="Class and Subject are required"), 400

    # Try to find an existing profile for this user / class / subject
    existing = (ClassProfile.query
                .filter_by(user_id=current_user.id,
                           class_name=class_name,
                           subject=subject)
                .first())

    if existing:
        # overwrite
        existing.max_words = max_words
        existing.rows_json = rows
        db.session.commit()
        return jsonify(
            id=existing.id,
            class_name=existing.class_name,
            subject=existing.subject,
            max_words=existing.max_words,
            message="UPDATED"
        ), 200

    # create new
    row = ClassProfile(
        user_id=current_user.id,
        class_name=class_name,
        subject=subject,
        max_words=max_words,
        rows_json=rows
    )
    db.session.add(row)
    db.session.commit()
    return jsonify(
        id=row.id,
        class_name=row.class_name,
        subject=row.subject,
        max_words=row.max_words,
        message="CREATED"
    ), 201


@app.route('/class_profiles', methods=['GET'])
@login_required
def list_class_profiles():
    rows = (ClassProfile.query
            .filter_by(user_id=current_user.id)
            .order_by(ClassProfile.created_at.desc())
            .all())
    return jsonify([
        {
            "id": r.id,
            "class_name": r.class_name,
            "subject": r.subject,
            "max_words": r.max_words
        } for r in rows
    ])


@app.route('/class_profile/<int:cp_id>', methods=['GET'])
@login_required
def get_class_profile_header(cp_id):
    cp = ClassProfile.query.filter_by(id=cp_id, user_id=current_user.id).first_or_404()
    return jsonify(_profile_to_dict(cp, include_rows=False))


@app.route('/class_profile/<int:cp_id>/full', methods=['GET'])
@login_required
def get_class_profile_full(cp_id):
    r = ClassProfile.query.filter_by(id=cp_id, user_id=current_user.id).first_or_404()
    return jsonify({
        "id": r.id,
        "class_name": r.class_name,
        "subject": r.subject,
        "max_words": r.max_words,
        "rows": r.rows_json or []
    })


# ---------- DB bootstrap ----------
def _ensure_models_have_basics():
    """
    Friendly checks. Your User model should either:
    - subclass flask_login.UserMixin, or
    - provide is_authenticated/is_active/is_anonymous/get_id properties.
    """
    missing = []
    u = User()
    for attr in ("is_authenticated", "is_active", "is_anonymous", "get_id"):
        if not hasattr(u, attr):
            missing.append(attr)
    if missing:
        print(
            "[WARN] Your User model is missing these Flask-Login attributes:",
            ", ".join(missing),
        )
    if not hasattr(User, "set_password") or not hasattr(User, "check_password"):
        print(
            "[WARN] User missing set_password/check_password "
            "(make sure you implemented password hashing)."
        )


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        _ensure_models_have_basics()
    app.run(debug=True)
