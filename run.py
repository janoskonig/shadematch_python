from app import create_app
import os

app = create_app()

if __name__ == '__main__':
    print("DATABASE_URL:", os.getenv("DATABASE_URL"))
    app.run(debug=True)