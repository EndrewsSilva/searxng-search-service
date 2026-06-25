from typing import Optional, Any

from pydantic import BaseModel, Field


class ProcessEntity(BaseModel):
    source_url: str

    title: str = ""
    description: str = ""

    process_numbers: list[str] = Field(default_factory=list)
    has_process_number: bool = False

    status: str = ""
    court: str = ""
    subject: str = ""

    case_value: str = ""
    last_check: str = ""

    origin_location: str = ""
    origin_date: str = ""

    parties: dict[str, Any] = Field(default_factory=dict)

    movements_count: Optional[int] = None

    text_preview: Optional[str] = None


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str

    domain: Optional[str] = None

    html_full: Optional[str] = None
    html_raw: Optional[str] = None

    is_exact_match: bool = False
    matched_name: Optional[str] = None

    score: Optional[int] = None
    score_reasons: Optional[list[str]] = None

    process_links: Optional[list[str]] = None


class SearchRequest(BaseModel):
    query: str
    strict_name_match: bool = True


class SearchResponse(BaseModel):
    query: str

    total_found: int

    results: list[SearchResult] = Field(default_factory=list)

    process_entities: list[ProcessEntity] = Field(default_factory=list)