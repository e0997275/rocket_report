# init_db.py
from app import app, db
from sqlalchemy import inspect

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print("Tables:", inspect(db.engine).get_table_names())
