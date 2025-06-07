from app import create_app
import os

app = create_app()

print("DATABASE_URL:", os.getenv("DATABASE_URL"))

if __name__ == '__main__':
    app.run(debug=True)