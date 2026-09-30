"""Validation and ALL budget math. The UI only renders what this returns."""
import re
from datetime import date, datetime

from db import CATEGORIES, MONTHLY_BUDGET, ensure_month

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
WARN_AT = 80  # % used at which a category shows a warning
NAMES = [c for c, _ in CATEGORIES]


class ValidationError(Exception):
    def __init__(self, errors):
        self.errors = errors


def parse_amount(v):
    try:
        n = int(str(v).strip().replace(",", "").lstrip("₹"))
    except (ValueError, TypeError):
        return None
    return n


def clean_month(month):
    month = month or date.today().strftime("%Y-%m")
    if not MONTH_RE.match(month):
        raise ValidationError({"month": "Use YYYY-MM"})
    return month


def clean_expense(data):
    errors = {}
    amount = parse_amount(data.get("amount"))
    if amount is None or amount <= 0:
        errors["amount"] = "Enter a whole amount greater than 0"
    desc = str(data.get("description", "")).strip()
    if not desc:
        errors["description"] = "Description required"
    category = data.get("category")
    if category not in NAMES:
        errors["category"] = "Pick a valid category"
    try:
        d = datetime.strptime(str(data.get("date", "")), "%Y-%m-%d").date().isoformat()
    except ValueError:
        d = None
        errors["date"] = "Use a valid date"
    if errors:
        raise ValidationError(errors)
    return (d, desc[:200], category, amount, str(data.get("notes", "")).strip()[:500])


def list_months(db):
    cur = db.execute(
        "SELECT month FROM month_budgets "
        "UNION "
        "SELECT TO_CHAR(date, 'YYYY-MM') AS month FROM expenses"
    )
    rows = cur.fetchall()
    months = {
        r["month"] if isinstance(r, dict) else r[0]
        for r in rows
        if (r.get("month") if isinstance(r, dict) else r[0])
    } | {date.today().strftime("%Y-%m")}
    return sorted(months, reverse=True)


def list_expenses(db, month, category="", q="", day=""):
    sql = "SELECT id, date, description, category, amount, notes FROM expenses WHERE TO_CHAR(date, 'YYYY-MM') = %s"
    args = [month]
    if category:
        sql += " AND category = %s"
        args.append(category)
    if q:
        sql += " AND description ILIKE %s"
        args.append(f"%{q}%")
    if day:
        sql += " AND date = %s"
        args.append(day)
    sql += " ORDER BY date DESC, id DESC"

    cur = db.execute(sql, args)
    rows = cur.fetchall()
    results = []
    for r in rows:
        item = dict(r)
        if hasattr(item.get("date"), "isoformat"):
            item["date"] = item["date"].isoformat()
        elif item.get("date") is not None:
            item["date"] = str(item["date"])
        results.append(item)
    return results


def summary(db, month):
    ensure_month(db, month)
    cur_b = db.execute("SELECT category, amount FROM month_budgets WHERE month = %s", (month,))
    budgets = {r["category"]: r["amount"] for r in cur_b.fetchall()}
    cur_s = db.execute(
        "SELECT category, SUM(amount) AS t FROM expenses WHERE TO_CHAR(date, 'YYYY-MM') = %s GROUP BY category",
        (month,)
    )
    spent = {r["category"]: int(r["t"] or 0) for r in cur_s.fetchall()}
    cats, group_totals = [], {"spend": 0, "invest": 0, "save": 0}
    for name, group in CATEGORIES:
        b, s = budgets.get(name, 0), spent.get(name, 0)
        pct = round(s * 100 / b) if b else (100 if s else 0)
        status = "over" if s > b else "warn" if pct >= WARN_AT else "ok"
        cats.append({"name": name, "group": group, "budget": b, "spent": s, "remaining": b - s,
                     "pct": pct, "status": status, "over_by": max(s - b, 0)})
        group_totals[group] += s
    used = sum(group_totals.values())
    budgets_total = sum(budgets.values())
    return {
        "month": month, "categories": cats,
        "monthly_budget": MONTHLY_BUDGET, "used": used, "remaining": MONTHLY_BUDGET - used,
        "pct_used": round(used * 100 / MONTHLY_BUDGET) if MONTHLY_BUDGET else 0,
        "over_by": max(used - MONTHLY_BUDGET, 0),
        "spending": group_totals["spend"], "invested": group_totals["invest"],
        "saved": group_totals["save"], "wealth": group_totals["invest"] + group_totals["save"],
        "budgets_total": budgets_total, "budgets_match": budgets_total == MONTHLY_BUDGET,
        "budgets_diff": budgets_total - MONTHLY_BUDGET,
    }


def save_budgets(db, month, data):
    errors, vals = {}, {}
    for name in NAMES:
        n = parse_amount(data.get(name))
        if n is None or n < 0:
            errors[name] = "Whole amount, 0 or more"
        else:
            vals[name] = n
    if errors:
        raise ValidationError(errors)
    ensure_month(db, month)
    with db.cursor() as cur:
        cur.executemany(
            "UPDATE month_budgets SET amount = %s WHERE month = %s AND category = %s",
            [(v, month, k) for k, v in vals.items()],
        )
    db.commit()
