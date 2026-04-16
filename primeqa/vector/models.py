"""SQLAlchemy models for the vector domain.

Tables owned: embeddings
"""

from sqlalchemy import (
    Column, Integer, String, DateTime, Text,
    ForeignKey, CheckConstraint,
)
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

from primeqa.db import Base


class Embedding(Base):
    __tablename__ = "embeddings"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    environment_id = Column(Integer, ForeignKey("environments.id"))
    content_type = Column(String(30), nullable=False)
    source_id = Column(String(255), nullable=False)
    content_text = Column(Text, nullable=False)
    embedding = Column(Vector(1024), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "content_type IN ('jira_description', 'jira_comment', 'confluence_doc', 'bug_report', 'ba_feedback')"
        ),
    )
