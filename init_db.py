# Import the create_app function and db instance from the app package
import os
from datetime import date, datetime

from app import create_app, db
from app.models import User, Session, MixingSession, TargetColor

# Create a Flask application instance
app = create_app()

# Create an application context to work with the database
with app.app_context():
    from app.routes import _normalize_email, generate_user_id

    # Remove all existing database tables to start fresh
    db.drop_all()
    # Print confirmation message that tables were dropped
    print("✅ Existing tables dropped!")

    # Create new database tables based on the defined models
    db.create_all()
    # Print confirmation message that new tables were created
    print("✅ New database tables created!")

    # Optional: comma-separated emails → User rows (verified placeholder accounts).
    raw = (os.getenv("SEED_REGISTRATION_EMAILS") or "").strip()
    if raw:
        added = 0
        for part in raw.split(","):
            email = _normalize_email(part)
            if not email:
                print(f"⚠️  Skip invalid seed email: {part.strip()!r}")
                continue
            if User.query.filter_by(email=email).first():
                print(f"⚠️  Skip duplicate seed email: {email}")
                continue
            user_id = generate_user_id()
            while User.query.get(user_id) is not None:
                user_id = generate_user_id()
            db.session.add(
                User(
                    id=user_id,
                    birthdate=date(1990, 1, 1),
                    gender="male",
                    email=email,
                    email_verified_at=datetime.utcnow(),
                    email_opt_in_reminders=False,
                )
            )
            added += 1
        db.session.commit()
        print(f"✅ Seeded {added} user(s) from SEED_REGISTRATION_EMAILS.")