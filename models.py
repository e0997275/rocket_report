# models.py
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# Naming convention helps migrations on Postgres
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
db = SQLAlchemy(metadata=MetaData(naming_convention=convention))

class User(db.Model, UserMixin):
    __tablename__ = "users"   # <-- not "user"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # plans / counters
    plan = db.Column(db.String(20), default="free", nullable=False)
    reports_used = db.Column(db.Integer, default=0, nullable=False)
    reports_limit = db.Column(db.Integer, nullable=True)  # null = unlimited

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

class ClassProfile(db.Model):
    __tablename__ = "class_profiles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    class_name = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(120), nullable=False)
    max_words = db.Column(db.Integer, default=50, nullable=False)  # or 50 if you changed default
    # JSON storage for rows (if you’re not using a child table)
    rows_json = db.Column(db.JSON, nullable=True)
