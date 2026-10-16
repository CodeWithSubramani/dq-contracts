from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from dq_contracts import Contract

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 10, 16, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def contract() -> Contract:
    return Contract.from_yaml(EXAMPLES / "contracts" / "orders.v1.yml")


@pytest.fixture
def good_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": ["ord_0000000a", "ord_0000000b"],
            "customer_id": ["c_1", "c_2"],
            "status": ["placed", "delivered"],
            "amount": [10.0, 20.5],
            "currency": ["INR", None],
            "email": ["a@example.com", None],
            "updated_at": pd.to_datetime(["2026-10-16T08:00:00Z", "2026-10-16T09:00:00Z"], utc=True),
        }
    )


@pytest.fixture
def bad_df(good_df: pd.DataFrame) -> pd.DataFrame:
    bad = pd.DataFrame(
        {
            "order_id": ["ord_0000000a", "ORD-1"],  # duplicate PK + pattern violation
            "customer_id": [None, "c_9"],  # null in NOT NULL column
            "status": ["teleported", "paid"],  # outside enum
            "amount": [-1.0, 5.0],  # below min
            "currency": ["INR", "GBP"],  # outside enum
            "email": [None, None],
            "updated_at": pd.to_datetime(["2026-10-15T00:00:00Z", "2026-10-15T01:00:00Z"], utc=True),
        }
    )
    return pd.concat([good_df, bad], ignore_index=True)


@pytest.fixture
def sqlite_conn(bad_df: pd.DataFrame):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE orders (
            order_id TEXT, customer_id TEXT, status TEXT, amount REAL,
            currency TEXT, email TEXT, updated_at TIMESTAMP)"""
    )
    rows = bad_df.assign(updated_at=bad_df["updated_at"].dt.strftime("%Y-%m-%dT%H:%M:%S+00:00"))
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?)", rows.itertuples(index=False))
    yield conn
    conn.close()
