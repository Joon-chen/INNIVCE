from typing import Annotated
from uuid import UUID

from fastapi import Query

CompanyIdsQuery = Annotated[list[UUID] | None, Query(alias="company_ids")]
