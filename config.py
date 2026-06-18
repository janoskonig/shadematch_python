# Import the os module for environment variable access
import os
# Import urlparse for URL parsing functionality
from urllib.parse import urlparse

# Define the main configuration class for the application
class Config:
    # Get the database URL from environment variables
    database_url = os.getenv('DATABASE_URL')
    # Check if the database URL exists and starts with postgres://
    if database_url and database_url.startswith('postgres://'):
        # Replace postgres:// with postgresql:// for SQLAlchemy compatibility
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    
    # Set the SQLAlchemy database URI configuration
    SQLALCHEMY_DATABASE_URI = database_url
    # Disable SQLAlchemy's modification tracking to save resources
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Set the application's secret key, defaulting to 'dev' if not provided
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev')