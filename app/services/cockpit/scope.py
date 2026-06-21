from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class CockpitScope:
    company_id: UUID | None = None
    company_ids: tuple[UUID, ...] = ()
    all_companies: bool = False

    @classmethod
    def single_company(cls, company_id: UUID | None) -> "CockpitScope":
        return cls(company_id=company_id)

    @classmethod
    def multi_company(cls, company_ids: list[UUID] | tuple[UUID, ...]) -> "CockpitScope":
        return cls(company_ids=tuple(company_ids))

    @classmethod
    def all(cls) -> "CockpitScope":
        return cls(all_companies=True)

    @property
    def effective_company_ids(self) -> tuple[UUID, ...]:
        if self.company_id:
            return (self.company_id,)
        return self.company_ids

    def as_payload(self) -> dict[str, object]:
        return {
            "company_id": str(self.company_id) if self.company_id else None,
            "company_ids": [str(item) for item in self.company_ids],
            "all_companies": self.all_companies,
        }
