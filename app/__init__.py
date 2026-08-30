from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from sqlalchemy.exc import OperationalError
import os

db = SQLAlchemy()


def _ensure_additive_columns(app):
    """Self-heal late-added nullable columns at boot.

    The deploy runs no migration step (Render free tier: no shell either), so a
    schema-touching release can only rely on what the app does itself. This covers the
    additive case only: nullable columns the ORM already maps but an existing database
    may lack — exactly what migrate_add_calibration_group.py does by hand. Idempotent
    (introspects first), dialect-agnostic, and never blocks boot: an unreachable
    database just logs and the request-time error handling takes over as before.
    """
    from sqlalchemy import inspect as sa_inspect
    wanted = {
        'calibration_trials': [('center_group', 'VARCHAR(16)')],
    }
    with app.app_context():
        try:
            insp = sa_inspect(db.engine)
            for table, columns in wanted.items():
                if not insp.has_table(table):
                    continue   # db.create_all / the table migration owns fresh installs
                have = {c['name'] for c in insp.get_columns(table)}
                for name, ddl_type in columns:
                    if name not in have:
                        db.session.execute(db.text(
                            'ALTER TABLE %s ADD COLUMN %s %s' % (table, name, ddl_type)))
                        db.session.commit()
                        app.logger.info('ensured column %s.%s', table, name)
        except Exception:
            db.session.rollback()
            app.logger.exception('additive column ensure failed (continuing boot)')


def create_app():
    load_dotenv()
    # Full local config (SMTP, push keys, APP_BASE_URL, etc.) lives in
    # shadestudy.env. Load it without overriding anything already set, so the
    # real environment (e.g. Render) always wins and this is a no-op in prod
    # where the file is absent.
    load_dotenv(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shadestudy.env'),
        override=False,
    )

    base_dir = os.path.abspath(os.path.dirname(__file__))
    template_dir = os.path.join(base_dir, '..', 'templates')
    static_dir = os.path.join(base_dir, '..', 'static')

    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    # Render terminates TLS at its proxy; trust one hop of X-Forwarded-* so
    # url_for(_external=True) builds https URLs (OG image/link previews).
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    print("Template folder used:", app.template_folder)
    print("Static folder used:", app.static_folder)
    app.config.from_object('config.Config')

    db.init_app(app)
    _ensure_additive_columns(app)

    @app.errorhandler(OperationalError)
    def handle_database_operational_error(_error):
        """Return JSON for API routes when Postgres is unreachable (timeout, firewall, etc.)."""
        db.session.rollback()
        if request.path.startswith('/api/'):
            return jsonify({
                'status': 'error',
                'error': 'database_unavailable',
                'message': (
                    'Cannot connect to the database server. '
                    'If DATABASE_URL points to a remote host, check VPN, firewall rules, '
                    'and that PostgreSQL is running. For local dev, use a reachable URL or SQLite.'
                ),
            }), 503
        return (
            '<p>Database unavailable.</p>'
            '<p>Check DATABASE_URL and network access to PostgreSQL.</p>',
            503,
            {'Content-Type': 'text/html; charset=utf-8'},
        )

    from .i18n import init_i18n
    init_i18n(app)

    from .routes import main
    app.register_blueprint(main)

    return app