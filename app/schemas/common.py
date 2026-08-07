"""Schemas shared by every listing endpoint."""

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field

MAX_PAGE_SIZE = 200


class Page[T](BaseModel):
    """One page of results plus the total, so a UI can render a pager."""

    items: list[T]
    total: int = Field(description="Rows matching the query, ignoring pagination")
    limit: int
    offset: int


class Pagination(BaseModel):
    """Query parameters accepted by every listing endpoint."""

    limit: Annotated[
        int,
        Query(ge=1, le=MAX_PAGE_SIZE, description="Rows to return"),
    ] = 50
    offset: Annotated[int, Query(ge=0, description="Rows to skip")] = 0
