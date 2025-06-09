# Import the create_app function from the app package to initialize the Flask application
from app import create_app
# Import the os module for operating system related functionality
import os

# Create a Flask application instance using the create_app factory function
app = create_app()

# Check if this script is being run directly (not imported as a module)
if __name__ == '__main__':
    # Start the Flask development server with debug mode enabled
    app.run(debug=True)