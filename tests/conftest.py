"""Shared pytest fixtures.

These are *characterization* tests: they pin the behaviour the app has today so
that the refactors described in ARCHITECTURE_REVIEW.md can be made with
confidence. They run against a throwaway SQLite database so no external services
(Postgres, SMTP, push) are required.
"""
import os
import tempfile

import pytest

# Point the app at a private, file-backed SQLite DB *before* config.Config is
# imported (it reads DATABASE_URL at class-definition time). A file (not
# :memory:) keeps a single shared schema across connections/threads.
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".sqlite")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ.setdefault("SECRET_KEY", "test-secret")

from flask import Flask  # noqa: E402

from app import db  # noqa: E402
from app.models import User, TargetColor  # noqa: E402


def _make_minimal_app():
    """A Flask app wired to `db` + models only.

    Deliberately does NOT call app.create_app(), which imports routes.py and the
    heavy dashboard stack (pandas/statsmodels/matplotlib). Phase-0 tests
    characterize domain logic (colour science, gamification, drops), so they need
    only the ORM — keeping the suite fast and dependency-light.
    """
    application = Flask(__name__)
    application.config.update(
        SQLALCHEMY_DATABASE_URI=os.environ["DATABASE_URL"],
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SECRET_KEY=os.environ["SECRET_KEY"],
        TESTING=True,
    )
    db.init_app(application)
    return application


# A tiny deterministic catalog spanning a few sum-drop bands, so the
# quota/level engine has recipes to work with. sum_drop = white+black+red+yellow+blue.
SEED_CATALOG = [
    # (catalog_order, name, type, r, g, b, white, black, red, yellow, blue)  sum_drop
    (1, "band4-a", "basic", 200, 180, 170, 2, 0, 1, 1, 0),   # 4
    (2, "band4-b", "basic", 190, 170, 160, 1, 1, 1, 1, 0),   # 4
    (3, "band6",   "basic", 150, 120, 110, 2, 0, 2, 1, 1),   # 6
    (4, "band8",   "skin",  120, 90, 80, 2, 1, 2, 2, 1),     # 8
]

SEED_USER_ID = "ABC123"


@pytest.fixture(scope="session")
def app():
    application = _make_minimal_app()
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.drop_all()
    try:
        os.close(_DB_FD)
        os.unlink(_DB_PATH)
    except OSError:
        pass


@pytest.fixture()
def session(app):
    """Fresh app context + clean tables for every test (re-seeded catalog/user)."""
    from datetime import date

    with app.app_context():
        # Clean slate: wipe everything, then reseed the deterministic catalog.
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()

        for (order, name, ctype, r, g, b, w, k, rr, y, bl) in SEED_CATALOG:
            db.session.add(TargetColor(
                catalog_order=order, name=name, color_type=ctype,
                r=r, g=g, b=b,
                drop_white=w, drop_black=k, drop_red=rr,
                drop_yellow=y, drop_blue=bl,
            ))
        db.session.add(User(id=SEED_USER_ID, birthdate=date(1990, 1, 1), gender="other"))
        db.session.commit()

        yield db.session

        db.session.rollback()


@pytest.fixture()
def target_ids(session):
    from app.models import TargetColor
    return [tc.id for tc in TargetColor.query.order_by(TargetColor.catalog_order).all()]
