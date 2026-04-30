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
    # Drop stale TCP connections; cap initial connect wait (see PGCONNECT_TIMEOUT)
    try:
        _pg_timeout = int(os.getenv('PGCONNECT_TIMEOUT', '10') or '10')
    except ValueError:
        _pg_timeout = 10
    _pg_timeout = max(2, min(_pg_timeout, 120))
    if database_url and database_url.startswith('postgresql'):
        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_pre_ping': True,
            'connect_args': {'connect_timeout': _pg_timeout},
        }
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping': True}
    # Disable SQLAlchemy's modification tracking to save resources
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Set the application's secret key, defaulting to 'dev' if not provided
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev')
    # Change this (or set env CLIENT_STORAGE_VERSION) to force a full client reset on next visit.
    CLIENT_STORAGE_VERSION = os.getenv('CLIENT_STORAGE_VERSION', '2')