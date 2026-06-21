from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.serialization import json_safe
from app.models.entities import Customer, Person, Project


class EntityCreate(BaseModel):
    company_id: UUID
    name: str
    status: str = "active"
    owner: str | None = None
    external_id: str | None = None
    email: str | None = None
    role_title: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


def create_operations_entity_from_request(
    db: Session,
    *,
    entity_type: str,
    data: EntityCreate,
) -> dict[str, Any]:
    return create_operations_entity(
        db,
        entity_type=entity_type,
        company_id=data.company_id,
        name=data.name,
        status=data.status,
        owner=data.owner,
        external_id=data.external_id,
        email=data.email,
        role_title=data.role_title,
        payload=data.payload,
    )


def create_operations_entity(
    db: Session,
    *,
    entity_type: str,
    company_id: UUID,
    name: str,
    status: str = "active",
    owner: str | None = None,
    external_id: str | None = None,
    email: str | None = None,
    role_title: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if entity_type == "people":
        entity = Person(
            company_id=company_id,
            name=name,
            external_id=external_id,
            email=email,
            role_title=role_title,
            payload=json_safe(payload or {}),
        )
    elif entity_type == "projects":
        entity = Project(
            company_id=company_id,
            name=name,
            status=status,
            owner=owner,
            payload=json_safe(payload or {}),
        )
    elif entity_type == "customers":
        entity = Customer(
            company_id=company_id,
            name=name,
            status=status,
            owner=owner,
            payload=json_safe(payload or {}),
        )
    else:
        raise HTTPException(status_code=400, detail="entity_type must be people, projects, or customers")
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return {"id": str(entity.id), "entity_type": entity_type, "name": entity.name}
