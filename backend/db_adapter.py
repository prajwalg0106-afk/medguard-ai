
from __future__ import annotations

import re
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection


class DatabaseRow:
    """Support both SQLite-style named and positional row access."""

    def __init__(self, row):
        self._row = row
        self._mapping = row._mapping

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._mapping[key]
        return self._row[key]

    def keys(self):
        return self._mapping.keys()

    def items(self):
        return self._mapping.items()

    def __iter__(self):
        return iter(self._row)


class DatabaseResult:
    """Compatibility wrapper for SQLAlchemy query results."""

    def __init__(self, result):
        self._result = result

    def fetchone(self):
        row = self._result.fetchone()
        return DatabaseRow(row) if row is not None else None

    def fetchall(self):
        return [DatabaseRow(row) for row in self._result.fetchall()]

    def __iter__(self):
        for row in self._result:
            yield DatabaseRow(row)


class DatabaseConnection:
    """Preserve the existing execute-style database interface."""

    def __init__(self, connection: Connection):
        self._connection = connection

   
    @staticmethod
    def _convert_sql(sql: str) -> str:
        """Convert positional placeholders outside SQL strings."""
        result = []
        index = 0
        in_single = False
        in_double = False
        i = 0

        while i < len(sql):
            char = sql[i]

            if char == "'" and not in_double:
                result.append(char)
                if in_single and i + 1 < len(sql) and sql[i + 1] == "'":
                    result.append(sql[i + 1])
                    i += 2
                    continue
                in_single = not in_single

            elif char == '"' and not in_single:
                in_double = not in_double
                result.append(char)

            elif char == "?" and not in_single and not in_double:
                result.append(f":p{index}")
                index += 1

            else:
                result.append(char)

            i += 1

        return "".join(result)


    def execute(self, sql: str, parameters: Any = ()):
        converted_sql = self._convert_sql(sql)

        if isinstance(parameters, dict):
            values = parameters
        elif parameters:
            values = {
                f"p{i}": value
                for i, value in enumerate(parameters)
            }
        else:
            values = {}

        result = self._connection.execute(text(converted_sql), values)
        return DatabaseResult(result)

    def executemany(self, sql: str, parameters: Iterable):
        converted_sql = self._convert_sql(sql)

        rows = [
            {
                f"p{i}": value
                for i, value in enumerate(row)
            }
            for row in parameters
        ]

        result = self._connection.execute(text(converted_sql), rows)
        return DatabaseResult(result)
