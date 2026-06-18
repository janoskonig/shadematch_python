# Import the create_app function from the app package to initialize the Flask application
from app import create_app
# Import the os module for operating system related functionality
import os

# Create a Flask application instance using the create_app factory function
app = create_app()

# Check if this script is being run directly (not imported as a module)
if __name__ == '__main__':
    # Start the Flask development server with debug mode enabled.
    # Port is configurable via the PORT env var (default 5000); on macOS,
    # port 5000 is often taken by AirPlay/ControlCenter, so set PORT to override.
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, port=port)