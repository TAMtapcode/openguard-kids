from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def make_engine(url: str):
    is_sqlite = url.startswith("sqlite")
    eng = create_engine(url, connect_args={"check_same_thread": False} if is_sqlite else {}, pool_pre_ping=True)
    if is_sqlite:
        @event.listens_for(eng, "connect")
        def _pragma(con, _rec):
            cur = con.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return eng


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
