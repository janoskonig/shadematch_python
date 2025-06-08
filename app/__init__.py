from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import os

db = SQLAlchemy()

def create_app():
    load_dotenv()
    
    base_dir = os.path.abspath(os.path.dirname(__file__))
    template_dir = os.path.join(base_dir, '..', 'templates')
    static_dir = os.path.join(base_dir, '..', 'static')

    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    print("Template folder used:", app.template_folder)
    print("Static folder used:", app.static_folder)
    app.config.from_object('config.Config')
    
    # Debug database connection
    db_url = os.getenv('DATABASE_URL')
    if db_url:
        # Mask the password in the URL for security
        masked_url = db_url.replace('://', '://***:***@')
        print("Database URL (masked):", masked_url)
    else:
        print("WARNING: DATABASE_URL environment variable is not set!")
    
    db.init_app(app)

    from .routes import main
    app.register_blueprint(main)

    return app