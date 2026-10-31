
import unittest

from sqlalchemy import create_engine
from sqlalchemy import create_engine, text
from backend.db_adapter import DatabaseConnection


class DatabaseAdapterTests(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite://")

    def tearDown(self):
        self.engine.dispose()

    def test_execute_and_positional_parameters(self):
        with self.engine.begin() as raw:
            connection = DatabaseConnection(raw)

            connection.execute(
                "CREATE TABLE medicines (id INTEGER, name TEXT)"
            )
            connection.execute(
                "INSERT INTO medicines (id, name) VALUES (?, ?)",
                (1, "Norepinephrine"),
            )

            row = connection.execute(
                "SELECT id, name FROM medicines WHERE id=?",
                (1,),
            ).fetchone()

            self.assertEqual(row["name"], "Norepinephrine")
            self.assertEqual(row[0], 1)

    
    def test_executemany_and_fetchall(self):
        with self.engine.begin() as raw:
            connection = DatabaseConnection(raw)

            connection.execute(
                "CREATE TABLE hospitals (id INTEGER, name TEXT)"
            )
            connection.executemany(
                "INSERT INTO hospitals (id, name) VALUES (?, ?)",
                [(1, "Aster"), (2, "Lakeside")],
            )

            rows = connection.execute(
                "SELECT id, name FROM hospitals ORDER BY id"
            ).fetchall()

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["name"], "Lakeside")
            self.assertEqual(rows[1][0], 2)

    def test_question_mark_inside_string_is_not_parameter(self):
        with self.engine.begin() as raw:
            connection = DatabaseConnection(raw)

            row = connection.execute(
                "SELECT '?' AS question, ? AS answer",
                (42,),
            ).fetchone()

            self.assertEqual(row["question"], "?")
            self.assertEqual(row["answer"], 42)

    def test_transaction_rolls_back_on_error(self):
        with self.engine.begin() as raw:
            connection = DatabaseConnection(raw)
            connection.execute(
                "CREATE TABLE rollback_test (id INTEGER)"
            )

        with self.assertRaises(Exception):
            with self.engine.begin() as raw:
                connection = DatabaseConnection(raw)
                connection.execute(
                    "INSERT INTO rollback_test (id) VALUES (?)",
                    (1,),
                )
                raise RuntimeError("Simulated failure")

        with self.engine.connect() as raw:
            count = raw.execute(
                text("SELECT COUNT(*) FROM rollback_test")
            ).scalar_one()

        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
