"""Test Data Engine — templates, factories, snapshots.

Provides reliable, reusable, unique test data. Addresses flakiness root cause.
"""

import uuid
import time
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, JSON, DateTime, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from primeqa.db import Base


class DataTemplate(Base):
    __tablename__ = "data_templates"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    object_type = Column(String(255), nullable=False)
    field_values = Column(JSON, nullable=False, server_default="{}")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="data_templates_tenant_name_unique"),
    )


class DataFactory(Base):
    __tablename__ = "data_factories"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    factory_type = Column(String(30), nullable=False)
    config = Column(JSON, nullable=False, server_default="{}")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("factory_type IN ('uuid', 'email', 'phone', 'name', 'company', 'address', 'timestamp', 'counter', 'custom')"),
        UniqueConstraint("tenant_id", "name", name="data_factories_tenant_name_unique"),
    )


class DataSnapshot(Base):
    __tablename__ = "data_snapshots"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    environment_id = Column(Integer, ForeignKey("environments.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    snapshot_data = Column(JSON, nullable=False, server_default="{}")
    record_count = Column(Integer, nullable=False, server_default="0")
    status = Column(String(20), nullable=False, server_default="pending")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TestCaseDataBinding(Base):
    __tablename__ = "test_case_data_bindings"

    id = Column(Integer, primary_key=True)
    test_case_version_id = Column(Integer, ForeignKey("test_case_versions.id", ondelete="CASCADE"), nullable=False)
    binding_key = Column(String(100), nullable=False)
    binding_type = Column(String(20), nullable=False)
    reference_id = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("test_case_version_id", "binding_key", name="test_case_data_bindings_unique"),
    )


class DataEngineService:
    """Resolves template/factory/snapshot references in step field values."""

    def __init__(self, db):
        self.db = db

    def generate_value(self, factory_type, config=None, run_id=None, step_order=None):
        """Generate a value from a factory type."""
        config = config or {}
        if factory_type == "uuid":
            return str(uuid.uuid4())
        if factory_type == "email":
            domain = config.get("domain", "primeqa-test.example")
            return f"test-{uuid.uuid4().hex[:8]}@{domain}"
        if factory_type == "phone":
            base = config.get("prefix", "555")
            return f"{base}-{int(time.time() * 1000) % 10000000:07d}"
        if factory_type == "name":
            prefix = config.get("prefix", "Test")
            return f"{prefix} {uuid.uuid4().hex[:6].title()}"
        if factory_type == "company":
            prefix = config.get("prefix", "TestCorp")
            return f"{prefix} {uuid.uuid4().hex[:6].upper()}"
        if factory_type == "timestamp":
            return datetime.now(timezone.utc).isoformat()
        if factory_type == "counter":
            return str(int(time.time() * 1000))
        return f"generated-{uuid.uuid4().hex[:8]}"

    def list_templates(self, tenant_id, object_type=None):
        q = self.db.query(DataTemplate).filter(DataTemplate.tenant_id == tenant_id)
        if object_type:
            q = q.filter(DataTemplate.object_type == object_type)
        return q.order_by(DataTemplate.name).all()

    def list_factories(self, tenant_id):
        return self.db.query(DataFactory).filter(
            DataFactory.tenant_id == tenant_id,
        ).order_by(DataFactory.name).all()

    def create_template(self, tenant_id, name, object_type, field_values, created_by, description=None):
        t = DataTemplate(
            tenant_id=tenant_id, name=name, object_type=object_type,
            field_values=field_values, created_by=created_by, description=description,
        )
        self.db.add(t)
        self.db.commit()
        self.db.refresh(t)
        return t

    def create_factory(self, tenant_id, name, factory_type, config, created_by, description=None):
        f = DataFactory(
            tenant_id=tenant_id, name=name, factory_type=factory_type,
            config=config or {}, created_by=created_by, description=description,
        )
        self.db.add(f)
        self.db.commit()
        self.db.refresh(f)
        return f

    def resolve_references(self, value, tenant_id, run_id=None):
        """Replace {{template.X}}, {{factory.Y}} references in a value with real data."""
        if not isinstance(value, str) or "{{" not in value:
            return value

        import re
        def replace(m):
            ref = m.group(1).strip()
            if "." in ref:
                ref_type, ref_name = ref.split(".", 1)
                if ref_type == "factory":
                    f = self.db.query(DataFactory).filter(
                        DataFactory.tenant_id == tenant_id,
                        DataFactory.name == ref_name,
                    ).first()
                    if f:
                        return self.generate_value(f.factory_type, f.config, run_id)
                elif ref_type == "template":
                    t = self.db.query(DataTemplate).filter(
                        DataTemplate.tenant_id == tenant_id,
                        DataTemplate.name == ref_name,
                    ).first()
                    if t:
                        return str(t.field_values)
            return m.group(0)
        return re.sub(r"\{\{([^}]+)\}\}", replace, value)
