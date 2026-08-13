"""Reads against the `people` scope (employees, customers)."""
from __future__ import annotations

from couchbase.exceptions import DocumentNotFoundException

from app import db
from app.config import get_settings

BUCKET = get_settings().cb_bucket


def get_customer(customer_id: str) -> dict | None:
    try:
        result = db.collection("people", "customers").get(f"customer::{customer_id}")
        return result.content_as[dict]
    except DocumentNotFoundException:
        return None


def list_customers() -> list[dict]:
    rows = db.query(
        f"SELECT c.* FROM `{BUCKET}`.`people`.`customers` AS c ORDER BY c.displayName"
    )
    return list(rows)


def get_employee(employee_id: str) -> dict | None:
    try:
        result = db.collection("people", "employees").get(f"employee::{employee_id}")
        return result.content_as[dict]
    except DocumentNotFoundException:
        return None


def list_employees(store_id: str) -> list[dict]:
    rows = db.query(
        f"SELECT e.* FROM `{BUCKET}`.`people`.`employees` AS e "
        "WHERE e.storeId = $storeId ORDER BY e.`role`, e.displayName",
        storeId=store_id,
    )
    return list(rows)
