"""Flask API entrypoint kept for the existing `python flask_api.py` command."""

from backend.flask_api import app


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
