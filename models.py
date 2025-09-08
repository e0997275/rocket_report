# models.py
from datetime import datetime
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    plan = db.Column(db.String(20), default="free")  # "free", "teacher", "school", etc.
    reports_used = db.Column(db.Integer, default=0)
    reports_limit = db.Column(db.Integer, nullable=True, default=10)

    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)

class ClassProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    class_name = db.Column(db.String(255), nullable=False)
    subject    = db.Column(db.String(255), nullable=False)
    max_words  = db.Column(db.Integer, default=100)

    # Stores the whole table (list of row dicts)
    rows_json  = db.Column(db.JSON, default=list)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='class_profiles')
