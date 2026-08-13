"""Pydantic request/response models for the POS API.

These describe the *wire* shapes the FastAPI routes accept/return. The
documents actually stored in Couchbase are plain dicts built in
services/order_service.py so they match the sample data in
data/samples/*.json exactly - see docs/data-model.md.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Station = Literal["ESPRESSO_BAR", "WARMING_STATION"]
LineItemStatus = Literal["QUEUED", "IN_PREPARATION", "READY"]
OrderStatus = Literal["PAID", "IN_PREPARATION", "READY", "COMPLETED", "CANCELLED"]


class CustomizationIn(BaseModel):
    type: str = Field(..., examples=["MILK_TYPE"])
    value: str = Field(..., examples=["OAT_MILK"])


class OrderLineItemIn(BaseModel):
    itemId: str = Field(..., examples=["DRINK_001"])
    size: str = Field(..., examples=["GRANDE"])
    quantity: int = 1
    customizations: list[CustomizationIn] = []


class OrderCreateRequest(BaseModel):
    storeId: str
    customerId: Optional[str] = None
    # The name a barista would call out / write on the cup - only meaningful
    # for guest orders (no customerId); a loyalty customer's own name is
    # always used instead. Leave unset to get a randomly-picked guest name
    # (see order_service._customer_name) - useful for scripts generating
    # realistic-looking demo traffic without hand-picking a name each time.
    orderName: Optional[str] = None
    employeeId: Optional[str] = None
    channelOrigin: str = "POS_REGISTER_1"
    paymentMethod: str = "STARBUCKS_CARD"
    items: list[OrderLineItemIn]


class LineItemStatusUpdate(BaseModel):
    status: LineItemStatus


class OrderStatusUpdate(BaseModel):
    status: Literal["COMPLETED", "CANCELLED"]
    reason: Optional[str] = None
    actorId: Optional[str] = None
