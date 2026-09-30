"""Comprehensive integration tests for the PostgreSQL (Neon) implementation.
Verifies all 13 critical requirements:
 1. Application starts.
 2. Database connection works.
 3. Tables are created.
 4. Current month gets default budgets.
 5. A new month gets a new set of default budgets.
 6. Previous month remains unchanged.
 7. Adding an expense works.
 8. Editing an expense works.
 9. Deleting an expense works.
10. Monthly totals are correct.
11. SIP is tracked as investment.
12. Savings is tracked separately.
13. Remaining budget is correct.
Plus: Validation, filters, and migration idempotency.
"""
import os
import sys
import tempfile
import sqlite3
from datetime import date
import unittest
import psycopg
from psycopg.rows import dict_row

# Set paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Test Database URL: from env or default local postgres test database
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or "postgresql://postgres@localhost:5432/expense_tracker_test"
os.environ["DATABASE_URL"] = TEST_DB_URL

from db import close_pools, get_pool, CATEGORIES, DEFAULT_BUDGETS, MONTHLY_BUDGET  # noqa: E402
from app import create_app  # noqa: E402
from migrate_sqlite_to_neon import migrate  # noqa: E402

M = date.today().strftime("%Y-%m")
TODAY = date.today().isoformat()


class TestExpenseTrackerPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reset database tables before test suite
        close_pools()
        with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS expenses, month_budgets CASCADE")

        cls.app = create_app()
        cls.c = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        close_pools()

    def summ(self, m=M):
        r = self.c.get(f"/api/summary?month={m}")
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def cat(self, s, name):
        return next(c for c in s["categories"] if c["name"] == name)

    # 1. Application starts
    def test_01_app_starts(self):
        r_index = self.c.get("/")
        self.assertEqual(r_index.status_code, 200)
        self.assertIn(b"Monthly Budget", r_index.data)

    # 2. Database connection works
    def test_02_database_connection_works(self):
        r_health = self.c.get("/healthz")
        self.assertEqual(r_health.status_code, 200)
        self.assertEqual(r_health.data.decode("utf-8"), "ok")

    # 3. Tables are created
    def test_03_tables_are_created(self):
        with psycopg.connect(TEST_DB_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                )
                tables = {r["table_name"] for r in cur.fetchall()}
                self.assertIn("expenses", tables)
                self.assertIn("month_budgets", tables)

                # Check index
                cur.execute(
                    "SELECT indexname FROM pg_indexes WHERE tablename = 'expenses'"
                )
                indexes = {r["indexname"] for r in cur.fetchall()}
                self.assertIn("idx_expenses_date", indexes)

    # 4. Current month gets default budgets (and initial seed data)
    def test_04_current_month_gets_default_budgets(self):
        s = self.summ(M)
        self.assertEqual(s["month"], M)
        self.assertEqual(s["monthly_budget"], 20000)

        # Verify all 8 default categories and amounts
        for cat_name, expected_amount in DEFAULT_BUDGETS.items():
            c = self.cat(s, cat_name)
            self.assertEqual(c["budget"], expected_amount, f"Budget for {cat_name} mismatch")

        self.assertEqual(s["budgets_total"], 20000)
        self.assertTrue(s["budgets_match"])

        # Initial seed transactions: Rent (7000) + Netflix (199) + BGMI UC (344) + Tea (20) + Gift (577) = 8140
        self.assertEqual(s["used"], 8140)
        self.assertEqual(s["remaining"], 20000 - 8140)
        self.assertEqual(self.cat(s, "Rent")["spent"], 7000)
        self.assertEqual(self.cat(s, "Entertainment")["spent"], 776)  # 199 + 577
        self.assertEqual(self.cat(s, "Entertainment")["over_by"], 276)
        self.assertEqual(self.cat(s, "Entertainment")["status"], "over")
        self.assertEqual(self.cat(s, "Gaming")["status"], "ok")
        self.assertEqual(self.cat(s, "Food")["spent"], 20)

    # 5. A new month gets a new set of default budgets
    # 6. Previous month remains unchanged
    def test_05_and_06_new_month_defaults_and_previous_month_unchanged(self):
        future_month = "2031-01"
        s_future = self.summ(future_month)

        # Fresh ₹20,000 budget with 0 used
        self.assertEqual(s_future["used"], 0)
        self.assertEqual(s_future["remaining"], 20000)
        self.assertEqual(s_future["budgets_total"], 20000)
        self.assertEqual(self.cat(s_future, "Rent")["budget"], 7000)
        self.assertEqual(self.cat(s_future, "Food")["budget"], 3500)

        # Modify future month category budgets
        new_budgets = {c["name"]: c["budget"] for c in s_future["categories"]}
        new_budgets["Food"] = 4000
        r = self.c.put(f"/api/budgets/{future_month}", json=new_budgets)
        self.assertEqual(r.status_code, 200)
        updated_future = r.get_json()
        self.assertEqual(self.cat(updated_future, "Food")["budget"], 4000)
        self.assertFalse(updated_future["budgets_match"])
        self.assertEqual(updated_future["budgets_diff"], 500)

        # Verify current month (previous month) is completely UNCHANGED
        s_current = self.summ(M)
        self.assertEqual(self.cat(s_current, "Food")["budget"], 3500)
        self.assertEqual(s_current["used"], 8140)
        self.assertEqual(s_current["remaining"], 11860)

    # 7. Adding an expense works
    def test_07_add_expense(self):
        r = self.c.post("/api/expenses", json={
            "amount": "1,200",
            "category": "Groceries",
            "description": "Vegetables and fruits",
            "date": TODAY,
            "notes": "Weekly market"
        })
        self.assertEqual(r.status_code, 201)
        data = r.get_json()
        self.assertIn("id", data)
        eid = data["id"]
        self.assertIsInstance(eid, int)

        # Verify it shows in expenses list
        expenses = self.c.get(f"/api/expenses?month={M}").get_json()
        added = next(e for e in expenses if e["id"] == eid)
        self.assertEqual(added["description"], "Vegetables and fruits")
        self.assertEqual(added["amount"], 1200)
        self.assertEqual(added["category"], "Groceries")
        self.assertEqual(added["date"], TODAY)
        self.assertEqual(added["notes"], "Weekly market")

    # 8. Editing an expense works
    def test_08_edit_expense(self):
        # Create an expense
        r = self.c.post("/api/expenses", json={
            "amount": 300,
            "category": "Gaming",
            "description": "Steam game",
            "date": TODAY
        })
        eid = r.get_json()["id"]

        # Edit the expense
        r_edit = self.c.put(f"/api/expenses/{eid}", json={
            "amount": "450",
            "category": "Gaming",
            "description": "Steam game bundle",
            "date": TODAY,
            "notes": "With DLC"
        })
        self.assertEqual(r_edit.status_code, 200)
        self.assertEqual(r_edit.get_json()["id"], eid)

        # Verify update persisted
        expenses = self.c.get(f"/api/expenses?month={M}").get_json()
        updated = next(e for e in expenses if e["id"] == eid)
        self.assertEqual(updated["amount"], 450)
        self.assertEqual(updated["description"], "Steam game bundle")
        self.assertEqual(updated["notes"], "With DLC")

        # Edit non-existent expense returns 404
        r_not_found = self.c.put("/api/expenses/999999", json={
            "amount": 100,
            "category": "Food",
            "description": "Ghost",
            "date": TODAY
        })
        self.assertEqual(r_not_found.status_code, 404)

        # Clean up
        self.c.delete(f"/api/expenses/{eid}")

    # 9. Deleting an expense works
    def test_09_delete_expense(self):
        r = self.c.post("/api/expenses", json={
            "amount": 150,
            "category": "Transport",
            "description": "Auto rickshaw",
            "date": TODAY
        })
        eid = r.get_json()["id"]

        # Delete it
        r_del = self.c.delete(f"/api/expenses/{eid}")
        self.assertEqual(r_del.status_code, 200)
        self.assertEqual(r_del.get_json(), {"deleted": eid})

        # Ensure deleted from list
        expenses = self.c.get(f"/api/expenses?month={M}").get_json()
        self.assertNotIn(eid, [e["id"] for e in expenses])

        # Second delete returns 404
        r_del2 = self.c.delete(f"/api/expenses/{eid}")
        self.assertEqual(r_del2.status_code, 404)

    # 10. Monthly totals are correct
    # 11. SIP is tracked as investment
    # 12. Savings is tracked separately
    # 13. Remaining budget is correct
    def test_10_to_13_totals_sip_savings_and_remaining(self):
        # We start with existing transactions for month M
        initial_s = self.summ(M)
        initial_used = initial_s["used"]
        initial_spending = initial_s["spending"]

        # Add SIP expense
        r_sip = self.c.post("/api/expenses", json={
            "amount": "3,000",
            "category": "SIP / Investment",
            "description": "Mutual Fund Nifty 50 Index",
            "date": TODAY
        })
        self.assertEqual(r_sip.status_code, 201)
        sip_id = r_sip.get_json()["id"]

        # Add Savings expense
        r_sav = self.c.post("/api/expenses", json={
            "amount": "2,000",
            "category": "Savings",
            "description": "Emergency fund transfer",
            "date": TODAY
        })
        self.assertEqual(r_sav.status_code, 201)
        sav_id = r_sav.get_json()["id"]

        s = self.summ(M)

        # 10. Monthly totals are correct
        expected_used = initial_used + 3000 + 2000
        self.assertEqual(s["used"], expected_used)

        # 11. SIP is tracked as investment
        self.assertEqual(s["invested"], 3000)

        # 12. Savings is tracked separately
        self.assertEqual(s["saved"], 2000)

        # Wealth combines invested + saved
        self.assertEqual(s["wealth"], 5000)

        # Consumption spending did NOT increase with SIP or Savings
        self.assertEqual(s["spending"], initial_spending)

        # 13. Remaining budget is correct: ₹20,000 - total used (including SIP & Savings)
        expected_remaining = 20000 - expected_used
        self.assertEqual(s["remaining"], expected_remaining)

        # Test over budget calculation
        r_big = self.c.post("/api/expenses", json={
            "amount": 25000,
            "category": "Rent",
            "description": "Extra deposit",
            "date": TODAY
        })
        big_id = r_big.get_json()["id"]
        s_over = self.summ(M)
        self.assertLess(s_over["remaining"], 0)
        self.assertEqual(s_over["over_by"], s_over["used"] - 20000)

        # Clean up added expenses
        self.c.delete(f"/api/expenses/{sip_id}")
        self.c.delete(f"/api/expenses/{sav_id}")
        self.c.delete(f"/api/expenses/{big_id}")

    # Validation and filtering tests
    def test_14_validation_and_filters(self):
        # Validation checks
        self.assertEqual(self.c.post("/api/expenses", json={"amount": -10}).status_code, 400)
        self.assertEqual(self.c.post("/api/expenses", json={"amount": "abc"}).status_code, 400)
        self.assertEqual(self.c.post("/api/expenses", json={"amount": 100, "description": ""}).status_code, 400)
        self.assertEqual(self.c.post("/api/expenses", json={"amount": 100, "description": "X", "category": "Invalid"}).status_code, 400)
        self.assertEqual(self.c.post("/api/expenses", json={"amount": 100, "description": "X", "category": "Food", "date": "bad-date"}).status_code, 400)

        # Filter tests: case-insensitive ILIKE search
        q_result = self.c.get(f"/api/expenses?month={M}&category=Food&q=tea").get_json()
        self.assertTrue(any(e["description"] == "Tea" for e in q_result))

    # Migration idempotency and sequence safety
    def test_15_migration_idempotent_no_duplicates(self):
        # Create a mock SQLite database with transactions
        tmp_dir = tempfile.mkdtemp()
        sq_path = os.path.join(tmp_dir, "test_migrate.db")
        sq_conn = sqlite3.connect(sq_path)
        sq_conn.execute("""
            CREATE TABLE expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                amount INTEGER NOT NULL,
                notes TEXT NOT NULL DEFAULT ''
            );
        """)
        sq_conn.execute("""
            CREATE TABLE month_budgets (
                month TEXT NOT NULL,
                category TEXT NOT NULL,
                amount INTEGER NOT NULL,
                PRIMARY KEY (month, category)
            );
        """)
        # Add a custom month and transaction in SQLite
        sq_conn.execute("INSERT INTO month_budgets VALUES ('2025-05', 'Rent', 8000)")
        sq_conn.execute("INSERT INTO expenses (id, date, description, category, amount, notes) "
                        "VALUES (101, '2025-05-10', 'Special item', 'Gaming', 999, 'Migration test')")
        sq_conn.commit()
        sq_conn.close()

        # Run migration 1st time
        res1 = migrate(sq_path, TEST_DB_URL, dry_run=False)
        self.assertGreaterEqual(res1["expenses_inserted"], 1)

        # Run migration 2nd time: must NOT duplicate the transaction
        res2 = migrate(sq_path, TEST_DB_URL, dry_run=False)
        self.assertEqual(res2["expenses_inserted"], 0)
        self.assertGreaterEqual(res2["expenses_skipped"], 1)

        # Verify subsequent insert via API gets next valid sequence ID
        r = self.c.post("/api/expenses", json={
            "amount": 25,
            "category": "Food",
            "description": "Chai snack",
            "date": TODAY
        })
        self.assertEqual(r.status_code, 201)
        new_id = r.get_json()["id"]
        self.assertGreater(new_id, 101)  # Sequence properly updated above 101


if __name__ == "__main__":
    unittest.main()
