"""Shared SQLAlchemy database setup used by all domain modules."""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session

Base = declarative_base()

engine = None
SessionLocal = None


def init_db(database_url):
    """Initialise the engine and session factory. Call once at app startup."""
    global engine, SessionLocal
    if engine is not None:
        return
    # pool_pre_ping: cheap SELECT 1 before reusing a pooled connection;
    # transparently reconnects if Railway's proxy has dropped the
    # connection (idle timeout ~15 min, shorter than Linux TCP
    # keepalive default of 2 hours — without this, dropped pool
    # connections cause indefinite poll() hangs).
    # pool_recycle: forces connection recycle every 5 minutes as a
    # belt-and-suspenders safety net for long-running operations
    # (sync phases that hold a connection across several Salesforce
    # API roundtrips can idle the connection long enough for the
    # proxy to drop it).
    # TCP keepalive (libpq + kernel level): probes start at 30s
    # idle, every 10s, 5 attempts. Keeps connections visible to
    # Railway's proxy across long-held phase transactions where
    # SQL operations may take several minutes (e.g., PV phase's
    # _batch_read_existing scanning ~500 external_ids). Without
    # this, the proxy idle-closes the connection mid-transaction
    # and the next query fails with OperationalError after the
    # TCP timeout. Distinct from pool_pre_ping: pre-ping only
    # validates connections AT CHECKOUT; keepalive keeps a
    # held-by-transaction connection alive mid-flight.
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    )
    SessionLocal = scoped_session(sessionmaker(bind=engine))


def get_db():
    """Yield a DB session and ensure it is closed after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
