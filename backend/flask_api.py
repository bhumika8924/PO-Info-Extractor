from flask import Flask, jsonify, request
from flask_cors import CORS

from backend.utils.database import get_latest_records
from backend.utils.po_processor import (
    database_status,
    database_summary,
    make_json_safe,
    process_uploaded_pdfs,
)


app = Flask(__name__)
app.json.sort_keys = False
CORS(app)


@app.get("/")
@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "api_version": "2.1.0",
            "frontend_contract_version": 2,
            "database": database_status(),
        }
    )


@app.get("/database-summary")
def database_summary_endpoint():
    return jsonify(make_json_safe(database_summary()))


@app.post("/extract")
def extract():
    files = request.files.getlist("files")
    if not files:
        return jsonify(
            {
                "status_code": 400,
                "success": False,
                "message": "No PDF files were uploaded. Use form-data key 'files'.",
                "documents": [],
            }
        ), 400
    include_debug = request.args.get("include_debug", "").lower() == "true"
    return jsonify(process_uploaded_pdfs(files, include_debug=include_debug))


@app.get("/headers")
def headers():
    limit = request.args.get("limit", default=25, type=int)
    latest = get_latest_records(limit=limit)
    return jsonify(
        make_json_safe(
            {
                "success": latest.get("success", False),
                "data": latest.get("headers", []),
                "message": latest.get("message", ""),
            }
        )
    )


@app.get("/items")
def items():
    limit = request.args.get("limit", default=25, type=int)
    latest = get_latest_records(limit=limit)
    return jsonify(
        make_json_safe(
            {
                "success": latest.get("success", False),
                "items": latest.get("items", []),
                "message": latest.get("message", ""),
            }
        )
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
