# Color Mixing Application

A web-based color mixing application that allows users to experiment with color combinations and match target colors using a sophisticated perceptual color mixing algorithm.

## Features

- **Perceptual Color Mixing**: Uses the Mixbox library for realistic color mixing simulation
- **User Authentication**: Track individual user progress and achievements
- **Color Matching**: Match target colors with a Delta E color difference metric
- **Session Tracking**: Record and analyze user performance and color mixing attempts
- **Interactive UI**: Intuitive interface for adding color drops and monitoring progress

## Base Colors

The application uses five base colors for mixing:
- White [255, 255, 255]
- Black [0, 0, 0]
- Red [255, 0, 0]
- Yellow [255, 255, 0]
- Blue [0, 0, 255]

## Target Colors

The application includes a variety of target colors to match:
- Orange [255, 102, 30]
- Purple [113, 1, 105]
- Green [78, 150, 100]
- Pink [255, 179, 188]
- Olive [128, 128, 0]
- Custom [98, 135, 96]
- Peach [255, 229, 180]
- Coral [255, 128, 80]
- Turquoise [64, 224, 208]
- Chartreuse [128, 255, 0]
- Teal [0, 128, 128]

## Technical Stack

### Backend
- Flask 2.1.2
- Flask-SQLAlchemy 2.5.1
- Python-dotenv 1.0.0
- Gunicorn 20.1.0
- Colormath 3.0.0
- NumPy 1.24.3

### Frontend
- HTML5
- CSS3
- JavaScript
- Mixbox.js (for perceptual color mixing)

## Project Structure

```
.
├── app/                    # Application package
├── static/                 # Static files (JS, CSS)
├── templates/             # HTML templates
├── config.py              # Configuration settings
├── init_db.py            # Database initialization
├── requirements.txt       # Python dependencies
└── run.py                # Application entry point
```

## Setup and Installation

1. Clone the repository
2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Initialize the database:
   ```bash
   python init_db.py
   ```
5. Run the application:
   ```bash
   python run.py
   ```

## Usage

1. Start the application and log in
2. Click "Start" to begin a new color matching session
3. Add drops of base colors by clicking on the color circles
4. Try to match the target color shown
5. The Delta E value shows how close your mix is to the target
6. When Delta E < 5, the colors are considered matched
7. Use the control buttons to:
   - Start/Stop the session
   - Skip to the next color
   - Retry the current color
   - Restart the entire sequence

## Color Mixing Algorithm

The application uses the Mixbox library for perceptual color mixing, which:
- Converts RGB colors to a latent space representation
- Performs weighted mixing in the latent space
- Converts back to RGB for display
- Provides realistic color mixing simulation

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details. 