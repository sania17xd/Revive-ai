"""
Database setup. Uses SQLite by default so you can run this with zero
external services. Swap DATABASE_URL in .env for Postgres later if you want.
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./revenue_recovery.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency: gives each request its own DB session and closes it after."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
