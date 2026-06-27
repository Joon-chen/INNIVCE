from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IdentityFact:
    user_id: str = ""
    open_id: str = ""
    display_name: str = ""
    role: str = ""
    department_id: str = ""
    department_names: tuple[str, ...] = ()
    job_title: str = ""
    email: str = ""
    domains: tuple[str, ...] = ()
    source: str = "runtime_identity"

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def identity_fact_from_context(runtime_context: Any) -> IdentityFact:
    identity = getattr(runtime_context, "identity", None)
    return IdentityFact(
        user_id=str(getattr(identity, "user_id", "") or ""),
        open_id=str(getattr(identity, "open_id", "") or ""),
        display_name=str(getattr(identity, "display_name", "") or ""),
        role=str(getattr(identity, "role", "") or ""),
        department_id=str(getattr(identity, "department_id", "") or ""),
        department_names=tuple(str(item) for item in getattr(identity, "department_names", ()) or () if str(item).strip()),
        job_title=str(getattr(identity, "job_title", "") or ""),
        email=str(getattr(identity, "email", "") or ""),
        domains=tuple(str(item) for item in getattr(identity, "domains", ()) or () if str(item).strip()),
    )


def identity_fact_payload(runtime_context: Any) -> dict[str, Any]:
    fact = identity_fact_from_context(runtime_context)
    return {
        "user_id": fact.user_id,
        "open_id": fact.open_id,
        "display_name": fact.display_name,
        "role": fact.role,
        "department_id": fact.department_id,
        "department_names": list(fact.department_names),
        "job_title": fact.job_title,
        "email": fact.email,
        "domains": list(fact.domains),
        "is_owner": fact.is_owner,
        "is_admin": fact.is_admin,
        "source": fact.source,
    }
