# Import the create_app function and db instance from the app package
from app import create_app, db
# Import the database models that need to be initialized
from app.models import User, Session, MixingSession

# Create a Flask application instance
app = create_app()

# Create an application context to work with the database
with app.app_context():
    # Remove all existing database tables to start fresh
    db.drop_all()
    # Print confirmation message that tables were dropped
    print("✅ Existing tables dropped!")
    
    # Create new database tables based on the defined models
    db.create_all()
    # Print confirmation message that new tables were created
    print("✅ New database tables created!")