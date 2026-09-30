import os
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

import services as svc
from db import close_db, get_db, init_db

load_dotenv()


def create_app():
    app = Flask(__name__)
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

    @app.errorhandler(svc.ValidationError)
    def bad(e):
        return jsonify(errors=e.errors), 400

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/healthz")
    def health():
        get_db().execute("SELECT 1")
        return "ok"

    @app.get("/api/months")
    def months():
        return jsonify(svc.list_months(get_db()))

    @app.get("/api/summary")
    def summary():
        return jsonify(svc.summary(get_db(), svc.clean_month(request.args.get("month"))))

    @app.put("/api/budgets/<month>")
    def budgets(month):
        svc.save_budgets(get_db(), svc.clean_month(month), request.get_json(silent=True) or {})
        return jsonify(svc.summary(get_db(), month))

    @app.get("/api/expenses")
    def expenses():
        a = request.args
        return jsonify(svc.list_expenses(get_db(), svc.clean_month(a.get("month")),
                                         a.get("category", ""), a.get("q", "").strip(), a.get("date", "")))

    @app.post("/api/expenses")
    def add():
        vals = svc.clean_expense(request.get_json(silent=True) or {})
        db = get_db()
        cur = db.execute(
            "INSERT INTO expenses (date, description, category, amount, notes) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            vals,
        )
        row = cur.fetchone()
        new_id = row["id"] if isinstance(row, dict) else row[0]
        db.commit()
        return jsonify(id=new_id), 201

    @app.put("/api/expenses/<int:eid>")
    def edit(eid):
        vals = svc.clean_expense(request.get_json(silent=True) or {})
        db = get_db()
        cur = db.execute(
            "UPDATE expenses SET date = %s, description = %s, category = %s, amount = %s, notes = %s WHERE id = %s",
            vals + (eid,),
        )
        db.commit()
        return (jsonify(id=eid), 200) if cur.rowcount else (jsonify(errors={"id": "Not found"}), 404)

    @app.delete("/api/expenses/<int:eid>")
    def delete(eid):
        db = get_db()
        cur = db.execute("DELETE FROM expenses WHERE id = %s", (eid,))
        db.commit()
        return (jsonify(deleted=eid), 200) if cur.rowcount else (jsonify(errors={"id": "Not found"}), 404)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
