from typing import Optional
from pydantic import BaseModel, Field


class ComplianceSection(BaseModel):
    title: str
    content: str
    sources: list[str] = Field(default_factory=list)
    risk_level: Optional[str] = None   # LOW | MEDIUM | HIGH | CRITICAL | N/A


class GraphStats(BaseModel):
    persons: int = 0
    companies: int = 0
    processes: int = 0
    events: int = 0
    chunks: int = 0
    relationships: int = 0


class ComplianceReport(BaseModel):
    query: str
    generated_at: str
    sections: dict[str, ComplianceSection] = Field(default_factory=dict)
    overall_risk: str = "N/A"
    graph_stats: GraphStats = Field(default_factory=GraphStats)
    raw_process_count: int = 0
    references: list[dict] = Field(default_factory=list)  # [{"num": 1, "label": "...", "url": "..."}]


class ComplianceRequest(BaseModel):
    query: str
