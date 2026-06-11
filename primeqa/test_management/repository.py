"""Repository for the test management domain.

DB queries scoped to: sections, requirements, test_cases, test_case_versions,
                      test_suites, suite_test_cases, ba_reviews

All list queries delegate pagination / search / sort / filter to
`primeqa.shared.query_builder.ListQuery` so there is a single code path for
client-supplied params (caps at 50/page, sort-field whitelist, soft-delete
awareness, search-wildcard escape).
"""

from datetime import datetime, timezone

from sqlalchemy import func

from primeqa.shared.query_builder import ListQuery, PageResult
from primeqa.test_management.models import Requirement, Section


def _now():
    return datetime.now(timezone.utc)


# ---------- Sections ----------------------------------------------------------

class SectionRepository:
    def __init__(self, db):
        self.db = db

    def create_section(self, tenant_id, name, created_by, parent_id=None,
                       description=None, position=0):
        """Idempotent: if an active section with the same (tenant, parent,
        name) already exists, return it instead of creating a duplicate.

        Integration tests ran against the live DB without cleaning up, so
        repeated runs were creating dozens of identical "Regression Tests"
        and "Account Tests" root/child sections. The sidebar then rendered
        each distinct id as a separate tree node. Deduping here stops the
        bleeding; existing dupes need a data-cleanup pass.
        """
        existing = self.db.query(Section).filter(
            Section.tenant_id == tenant_id,
            Section.name == name,
            Section.deleted_at.is_(None),
        )
        if parent_id is None:
            existing = existing.filter(Section.parent_id.is_(None))
        else:
            existing = existing.filter(Section.parent_id == parent_id)
        found = existing.first()
        if found:
            return found

        section = Section(
            tenant_id=tenant_id, name=name, parent_id=parent_id,
            description=description, position=position, created_by=created_by,
        )
        self.db.add(section)
        self.db.commit()
        self.db.refresh(section)
        return section

    def get_section(self, section_id, tenant_id, include_deleted=False):
        q = self.db.query(Section).filter(
            Section.id == section_id, Section.tenant_id == tenant_id,
        )
        if not include_deleted:
            q = q.filter(Section.deleted_at.is_(None))
        return q.first()

    def list_sections(self, tenant_id, parent_id=None, include_deleted=False):
        q = self.db.query(Section).filter(Section.tenant_id == tenant_id)
        if not include_deleted:
            q = q.filter(Section.deleted_at.is_(None))
        if parent_id is not None:
            q = q.filter(Section.parent_id == parent_id)
        else:
            q = q.filter(Section.parent_id.is_(None))
        return q.order_by(Section.position).all()

    def list_page(self, tenant_id, *, page=1, per_page=20, q=None,
                  sort="updated_at", order="desc", filters=None,
                  include_deleted=False) -> PageResult:
        base = self.db.query(Section).filter(Section.tenant_id == tenant_id)
        return (ListQuery(base, Section,
                          search_fields=["name"],
                          sort_whitelist=["updated_at", "name", "position", "created_at"],
                          filter_spec={"parent_id": Section.parent_id})
                .with_soft_delete(Section, include_deleted=include_deleted)
                .search(q).filter_by(filters or {}).sort(sort, order)
                .paginate(page, per_page))

    def get_section_tree(self, tenant_id, include_deleted=False):
        q = self.db.query(Section).filter(Section.tenant_id == tenant_id)
        if not include_deleted:
            q = q.filter(Section.deleted_at.is_(None))
        all_sections = q.order_by(Section.position).all()
        section_map = {s.id: {
            "id": s.id, "name": s.name, "description": s.description,
            "position": s.position, "parent_id": s.parent_id, "children": [],
        } for s in all_sections}
        roots = []
        for s in all_sections:
            node = section_map[s.id]
            if s.parent_id and s.parent_id in section_map:
                section_map[s.parent_id]["children"].append(node)
            else:
                roots.append(node)
        return roots

    def update_section(self, section_id, tenant_id, updates, expected_version=None):
        section = self.get_section(section_id, tenant_id)
        if not section:
            return None, "not_found"
        if expected_version is not None and section.version != expected_version:
            return None, "conflict"
        for k, v in updates.items():
            if hasattr(section, k) and k not in (
                "id", "tenant_id", "created_by", "created_at", "version",
                "deleted_at", "deleted_by",
            ):
                setattr(section, k, v)
        section.version = (section.version or 0) + 1
        section.updated_at = _now()
        self.db.commit()
        self.db.refresh(section)
        return section, "ok"

    def soft_delete_section(self, section_id, tenant_id, user_id):
        section = self.get_section(section_id, tenant_id)
        if not section:
            return None
        section.deleted_at = _now()
        section.deleted_by = user_id
        self.db.commit()
        return section

    def restore_section(self, section_id, tenant_id):
        section = self.get_section(section_id, tenant_id, include_deleted=True)
        if not section:
            return None
        section.deleted_at = None
        section.deleted_by = None
        self.db.commit()
        return section

    def purge_section(self, section_id, tenant_id):
        section = self.get_section(section_id, tenant_id, include_deleted=True)
        if not section:
            return False
        self.db.delete(section)
        self.db.commit()
        return True


# ---------- Requirements ------------------------------------------------------

class RequirementRepository:
    def __init__(self, db):
        self.db = db

    def create_requirement(self, tenant_id, section_id, source, created_by, **kwargs):
        req = Requirement(
            tenant_id=tenant_id, section_id=section_id, source=source,
            created_by=created_by,
            jira_key=kwargs.get("jira_key"),
            jira_summary=kwargs.get("jira_summary"),
            jira_description=kwargs.get("jira_description"),
            acceptance_criteria=kwargs.get("acceptance_criteria"),
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def get_requirement(self, requirement_id, tenant_id, include_deleted=False):
        q = self.db.query(Requirement).filter(
            Requirement.id == requirement_id, Requirement.tenant_id == tenant_id,
        )
        if not include_deleted:
            q = q.filter(Requirement.deleted_at.is_(None))
        return q.first()

    def list_requirements(self, tenant_id, section_id=None, include_deleted=False):
        q = self.db.query(Requirement).filter(Requirement.tenant_id == tenant_id)
        if not include_deleted:
            q = q.filter(Requirement.deleted_at.is_(None))
        if section_id:
            q = q.filter(Requirement.section_id == section_id)
        return q.order_by(Requirement.created_at.desc()).all()

    def get_requirements_by_ids(self, requirement_ids, tenant_id,
                                include_deleted=False):
        """Batch-load requirements for the group-by-requirement Test
        Library view. Returns {id: Requirement}."""
        if not requirement_ids:
            return {}
        q = self.db.query(Requirement).filter(
            Requirement.tenant_id == tenant_id,
            Requirement.id.in_(list(requirement_ids)),
        )
        if not include_deleted:
            q = q.filter(Requirement.deleted_at.is_(None))
        return {r.id: r for r in q.all()}

    def list_page(self, tenant_id, *, page=1, per_page=20, q=None,
                  sort="updated_at", order="desc", filters=None,
                  include_deleted=False) -> PageResult:
        base = self.db.query(Requirement).filter(Requirement.tenant_id == tenant_id)
        return (ListQuery(base, Requirement,
                          search_fields=["jira_summary", "jira_key"],
                          sort_whitelist=["updated_at", "created_at", "jira_key"],
                          filter_spec={
                              "section_id": Requirement.section_id,
                              "source": Requirement.source,
                              "is_stale": Requirement.is_stale,
                          })
                .with_soft_delete(Requirement, include_deleted=include_deleted)
                .search(q).filter_by(filters or {}).sort(sort, order)
                .paginate(page, per_page))

    def update_requirement(self, requirement_id, tenant_id, updates, expected_version=None):
        req = self.get_requirement(requirement_id, tenant_id)
        if not req:
            return None, "not_found"
        if expected_version is not None and req.version != expected_version:
            return None, "conflict"
        for k, v in updates.items():
            if hasattr(req, k) and k not in (
                "id", "tenant_id", "created_by", "created_at", "version",
                "deleted_at", "deleted_by",
            ):
                setattr(req, k, v)
        req.version = (req.version or 0) + 1
        req.updated_at = _now()
        self.db.commit()
        self.db.refresh(req)
        return req, "ok"

    def find_by_jira_key(self, tenant_id, jira_key):
        return self.db.query(Requirement).filter(
            Requirement.tenant_id == tenant_id,
            Requirement.jira_key == jira_key,
            Requirement.deleted_at.is_(None),
        ).first()

    def mark_stale(self, requirement_id, tenant_id):
        req = self.get_requirement(requirement_id, tenant_id)
        if req:
            req.is_stale = True
            req.updated_at = _now()
            self.db.commit()
        return req

    def soft_delete_requirement(self, requirement_id, tenant_id, user_id):
        req = self.get_requirement(requirement_id, tenant_id)
        if not req:
            return None
        req.deleted_at = _now()
        req.deleted_by = user_id
        self.db.commit()
        return req

    def restore_requirement(self, requirement_id, tenant_id):
        req = self.get_requirement(requirement_id, tenant_id, include_deleted=True)
        if not req:
            return None
        req.deleted_at = None
        req.deleted_by = None
        self.db.commit()
        return req

    def purge_requirement(self, requirement_id, tenant_id):
        req = self.get_requirement(requirement_id, tenant_id, include_deleted=True)
        if not req:
            return False
        self.db.delete(req)
        self.db.commit()
        return True


# ---------- Test cases --------------------------------------------------------
