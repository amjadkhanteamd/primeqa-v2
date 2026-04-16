"""Shared SQLAlchemy database setup used by all domain modules."""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session

Base = declarative_base()

engine = None
SessionLocal = None


def init_db(database_url):
    """Initialise the engine and session factory. Call once at app startup."""
    global engine, SessionLocal
    engine = create_engine(database_url, pool_pre_ping=True)
    SessionLocal = scoped_session(sessionmaker(bind=engine))


def get_db():
    """Yield a DB session and ensure it is closed after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
