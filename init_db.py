from app import create_app, db
from app.models import User, Session, MixingSession

app = create_app()

with app.app_context():
    # Drop all existing tables
    db.drop_all()
    print("✅ Existing tables dropped!")
    
    # Create new tables
    db.create_all()
    print("✅ New database tables created!")