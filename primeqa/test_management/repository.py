"""Repository for the test management domain.

DB queries scoped to: sections, requirements, test_cases, test_case_versions,
                      test_suites, suite_test_cases, ba_reviews, metadata_impacts

All list queries delegate pagination / search / sort / filter to
`primeqa.shared.query_builder.ListQuery` so there is a single code path for
client-supplied params (caps at 50/page, sort-field whitelist, soft-delete
awareness, search-wildcard escape).
"""

from datetime import datetime, timezone

from sqlalchemy import func

from primeqa.shared.query_builder import ListQuery, PageResult
from primeqa.test_management.models import (
    Section, Requirement, TestCase, TestCaseVersion,
    TestSuite, SuiteTestCase, BAReview, MetadataImpact,
)


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

class TestCaseRepository:
    def __init__(self, db):
        self.db = db

    def create_test_case(self, tenant_id, title, owner_id, created_by, **kwargs):
        tc = TestCase(
            tenant_id=tenant_id, title=title,
            owner_id=owner_id, created_by=created_by,
            requirement_id=kwargs.get("requirement_id"),
            section_id=kwargs.get("section_id"),
            visibility=kwargs.get("visibility", "private"),
            status=kwargs.get("status", "draft"),
        )
        self.db.add(tc)
        self.db.commit()
        self.db.refresh(tc)
        return tc

    def get_test_case(self, test_case_id, tenant_id, include_deleted=False):
        q = self.db.query(TestCase).filter(
            TestCase.id == test_case_id, TestCase.tenant_id == tenant_id,
        )
        if not include_deleted:
            q = q.filter(TestCase.deleted_at.is_(None))
        return q.first()

    def list_test_cases(self, tenant_id, user_id=None, requirement_id=None,
                        section_id=None, status=None, include_private_for=None,
                        include_deleted=False):
        q = self.db.query(TestCase).filter(TestCase.tenant_id == tenant_id)
        if not include_deleted:
            q = q.filter(TestCase.deleted_at.is_(None))
        if requirement_id:
            q = q.filter(TestCase.requirement_id == requirement_id)
        if section_id:
            q = q.filter(TestCase.section_id == section_id)
        if status:
            q = q.filter(TestCase.status == status)
        if include_private_for:
            q = q.filter(
                (TestCase.visibility == "shared") |
                (TestCase.owner_id == include_private_for)
            )
        else:
            q = q.filter(TestCase.visibility == "shared")
        return q.order_by(TestCase.updated_at.desc()).all()

    def list_for_grouping(self, tenant_id, *, user_id=None, q=None,
                          filters=None, include_deleted=False,
                          max_items=500):
        """Return the full set of visible TCs (up to `max_items`) matching
        the same filter/search semantics as `list_page`, but WITHOUT
        server-side pagination. Used by the group-by-requirement Test
        Library view where pagination is per-requirement, not per-TC.

        Returns a plain list (not PageResult) ordered by updated_at desc
        so the group's most-recent TC bubbles up. A hard cap of 500 keeps
        the endpoint bounded even for giant tenants; above that the user
        should use filters.
        """
        from sqlalchemy import or_
        query = self.db.query(TestCase).filter(TestCase.tenant_id == tenant_id)
        if not include_deleted:
            query = query.filter(TestCase.deleted_at.is_(None))
        if user_id:
            query = query.filter(or_(TestCase.visibility == "shared",
                                     TestCase.owner_id == user_id))
        else:
            query = query.filter(TestCase.visibility == "shared")

        f = filters or {}
        if f.get("status"):
            query = query.filter(TestCase.status == f["status"])
        if f.get("requirement_id"):
            query = query.filter(TestCase.requirement_id == f["requirement_id"])
        if f.get("section_id"):
            query = query.filter(TestCase.section_id == f["section_id"])
        if f.get("owner_id"):
            query = query.filter(TestCase.owner_id == f["owner_id"])
        if f.get("visibility"):
            query = query.filter(TestCase.visibility == f["visibility"])
        if f.get("coverage_type"):
            query = query.filter(TestCase.coverage_type == f["coverage_type"])

        if q:
            # Same wildcard-escape convention as ListQuery: escape %, _
            # before wrapping in the ILIKE wildcards.
            safe = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.filter(TestCase.title.ilike(f"%{safe}%"))

        return query.order_by(TestCase.updated_at.desc()).limit(max_items).all()

    def list_page(self, tenant_id, *, user_id=None, page=1, per_page=20, q=None,
                  sort="updated_at", order="desc", filters=None,
                  include_deleted=False) -> PageResult:
        base = self.db.query(TestCase).filter(TestCase.tenant_id == tenant_id)
        # visibility: owner sees own privates, others only shared
        if user_id:
            base = base.filter((TestCase.visibility == "shared") |
                               (TestCase.owner_id == user_id))
        else:
            base = base.filter(TestCase.visibility == "shared")

        return (ListQuery(base, TestCase,
                          search_fields=["title"],
                          sort_whitelist=["updated_at", "title", "status", "created_at"],
                          filter_spec={
                              "status": TestCase.status,
                              "requirement_id": TestCase.requirement_id,
                              "section_id": TestCase.section_id,
                              "owner_id": TestCase.owner_id,
                              "visibility": TestCase.visibility,
                          })
                .with_soft_delete(TestCase, include_deleted=include_deleted)
                .search(q).filter_by(filters or {}).sort(sort, order)
                .paginate(page, per_page))

    def update_test_case(self, test_case_id, tenant_id, updates, expected_version=None):
        tc = self.get_test_case(test_case_id, tenant_id)
        if not tc:
            return None, "not_found"
        if expected_version is not None and tc.version != expected_version:
            return None, "conflict"
        for k, v in updates.items():
            if hasattr(tc, k) and k not in (
                "id", "tenant_id", "created_by", "version",
                "deleted_at", "deleted_by",
            ):
                setattr(tc, k, v)
        tc.version = (tc.version or 0) + 1
        tc.updated_at = _now()
        self.db.commit()
        self.db.refresh(tc)
        return tc, "ok"

    def create_version(self, test_case_id, metadata_version_id, created_by, **kwargs):
        latest = self.db.query(func.max(TestCaseVersion.version_number)).filter(
            TestCaseVersion.test_case_id == test_case_id,
        ).scalar() or 0

        tcv = TestCaseVersion(
            test_case_id=test_case_id, version_number=latest + 1,
            metadata_version_id=metadata_version_id,
            steps=kwargs.get("steps", []),
            expected_results=kwargs.get("expected_results", []),
            preconditions=kwargs.get("preconditions", []),
            generation_method=kwargs.get("generation_method", "manual"),
            confidence_score=kwargs.get("confidence_score"),
            referenced_entities=kwargs.get("referenced_entities", []),
            created_by=created_by,
        )
        self.db.add(tcv)
        self.db.commit()
        self.db.refresh(tcv)

        tc = self.db.query(TestCase).filter(TestCase.id == test_case_id).first()
        if tc:
            tc.current_version_id = tcv.id
            self.db.commit()

        return tcv

    def get_versions(self, test_case_id):
        return self.db.query(TestCaseVersion).filter(
            TestCaseVersion.test_case_id == test_case_id,
        ).order_by(TestCaseVersion.version_number.desc()).all()

    def get_latest_version(self, test_case_id):
        return self.db.query(TestCaseVersion).filter(
            TestCaseVersion.test_case_id == test_case_id,
        ).order_by(TestCaseVersion.version_number.desc()).first()

    def soft_delete_test_case(self, test_case_id, tenant_id, user_id):
        tc = self.get_test_case(test_case_id, tenant_id)
        if not tc:
            return None
        tc.deleted_at = _now()
        tc.deleted_by = user_id
        self.db.commit()
        return tc

    def restore_test_case(self, test_case_id, tenant_id):
        tc = self.get_test_case(test_case_id, tenant_id, include_deleted=True)
        if not tc:
            return None
        tc.deleted_at = None
        tc.deleted_by = None
        self.db.commit()
        return tc

    def purge_test_case(self, test_case_id, tenant_id):
        tc = self.get_test_case(test_case_id, tenant_id, include_deleted=True)
        if not tc:
            return False
        self.db.delete(tc)
        self.db.commit()
        return True


# ---------- Test suites -------------------------------------------------------

class TestSuiteRepository:
    def __init__(self, db):
        self.db = db

    def create_suite(self, tenant_id, name, suite_type, created_by, description=None):
        suite = TestSuite(
            tenant_id=tenant_id, name=name, suite_type=suite_type,
            description=description, created_by=created_by,
        )
        self.db.add(suite)
        self.db.commit()
        self.db.refresh(suite)
        return suite

    def get_suite(self, suite_id, tenant_id, include_deleted=False):
        q = self.db.query(TestSuite).filter(
            TestSuite.id == suite_id, TestSuite.tenant_id == tenant_id,
        )
        if not include_deleted:
            q = q.filter(TestSuite.deleted_at.is_(None))
        return q.first()

    def list_suites(self, tenant_id, include_deleted=False):
        q = self.db.query(TestSuite).filter(TestSuite.tenant_id == tenant_id)
        if not include_deleted:
            q = q.filter(TestSuite.deleted_at.is_(None))
        return q.order_by(TestSuite.created_at.desc()).all()

    def list_page(self, tenant_id, *, page=1, per_page=20, q=None,
                  sort="updated_at", order="desc", filters=None,
                  include_deleted=False) -> PageResult:
        base = self.db.query(TestSuite).filter(TestSuite.tenant_id == tenant_id)
        return (ListQuery(base, TestSuite,
                          search_fields=["name"],
                          sort_whitelist=["updated_at", "name", "suite_type", "created_at"],
                          filter_spec={"suite_type": TestSuite.suite_type})
                .with_soft_delete(TestSuite, include_deleted=include_deleted)
                .search(q).filter_by(filters or {}).sort(sort, order)
                .paginate(page, per_page))

    def update_suite(self, suite_id, tenant_id, updates, expected_version=None):
        suite = self.get_suite(suite_id, tenant_id)
        if not suite:
            return None, "not_found"
        if expected_version is not None and suite.version != expected_version:
            return None, "conflict"
        for k, v in updates.items():
            if hasattr(suite, k) and k not in (
                "id", "tenant_id", "created_by", "created_at", "version",
                "deleted_at", "deleted_by",
            ):
                setattr(suite, k, v)
        suite.version = (suite.version or 0) + 1
        suite.updated_at = _now()
        self.db.commit()
        self.db.refresh(suite)
        return suite, "ok"

    def add_test_case(self, suite_id, test_case_id, position=0):
        existing = self.db.query(SuiteTestCase).filter(
            SuiteTestCase.suite_id == suite_id,
            SuiteTestCase.test_case_id == test_case_id,
        ).first()
        if existing:
            return existing
        stc = SuiteTestCase(suite_id=suite_id, test_case_id=test_case_id, position=position)
        self.db.add(stc)
        self.db.commit()
        self.db.refresh(stc)
        return stc

    def remove_test_case(self, suite_id, test_case_id):
        stc = self.db.query(SuiteTestCase).filter(
            SuiteTestCase.suite_id == suite_id,
            SuiteTestCase.test_case_id == test_case_id,
        ).first()
        if stc:
            self.db.delete(stc)
            self.db.commit()
            return True
        return False

    def get_counts_by_suite(self, suite_ids):
        """For the suites list page: return {suite_id: {
            "total": N, "coverage": {positive: n, ...}, "requirements": set(),
        }} in one JOIN query \u2014 avoids N+1 on a list of many suites.
        """
        if not suite_ids:
            return {}
        from primeqa.test_management.models import TestCase
        rows = (self.db.query(
                    SuiteTestCase.suite_id,
                    TestCase.coverage_type,
                    TestCase.requirement_id,
                )
                .join(TestCase, TestCase.id == SuiteTestCase.test_case_id)
                .filter(
                    SuiteTestCase.suite_id.in_(list(suite_ids)),
                    TestCase.deleted_at.is_(None),
                )
                .all())
        out = {}
        for suite_id, cov, req_id in rows:
            bucket = out.setdefault(suite_id, {
                "total": 0, "coverage": {}, "requirements": set(),
            })
            bucket["total"] += 1
            key = cov or "other"
            bucket["coverage"][key] = bucket["coverage"].get(key, 0) + 1
            if req_id:
                bucket["requirements"].add(req_id)
        # Convert set to count for JSON-friendliness; keep ids if a caller needs them
        for v in out.values():
            v["requirement_count"] = len(v["requirements"])
            del v["requirements"]
        return out

    def add_test_cases_bulk(self, suite_id, test_case_ids):
        """Add many TCs at once, skipping those already in the suite.
        Assigns positions at the END of the current ordering so existing
        order is preserved. Returns {added: [...], already_in: [...]}.
        """
        if not test_case_ids:
            return {"added": [], "already_in": []}

        already_in = {
            stc.test_case_id for stc in
            self.db.query(SuiteTestCase.test_case_id).filter(
                SuiteTestCase.suite_id == suite_id,
                SuiteTestCase.test_case_id.in_(list(test_case_ids)),
            ).all()
        }
        # Next position: max(position)+1
        max_pos = self.db.query(func.max(SuiteTestCase.position)).filter(
            SuiteTestCase.suite_id == suite_id,
        ).scalar()
        next_pos = (max_pos or 0) + 1

        added = []
        # Preserve caller's order for the "added" list
        for tc_id in test_case_ids:
            if tc_id in already_in:
                continue
            stc = SuiteTestCase(
                suite_id=suite_id, test_case_id=tc_id, position=next_pos,
            )
            self.db.add(stc)
            added.append(tc_id)
            next_pos += 1
        if added:
            self.db.commit()
        return {"added": added, "already_in": sorted(already_in)}

    def get_suite_test_cases(self, suite_id):
        return self.db.query(SuiteTestCase).filter(
            SuiteTestCase.suite_id == suite_id,
        ).order_by(SuiteTestCase.position).all()

    def reorder_test_case(self, suite_id, test_case_id, new_position):
        stc = self.db.query(SuiteTestCase).filter(
            SuiteTestCase.suite_id == suite_id,
            SuiteTestCase.test_case_id == test_case_id,
        ).first()
        if stc:
            stc.position = new_position
            self.db.commit()
        return stc

    def soft_delete_suite(self, suite_id, tenant_id, user_id):
        suite = self.get_suite(suite_id, tenant_id)
        if not suite:
            return None
        suite.deleted_at = _now()
        suite.deleted_by = user_id
        self.db.commit()
        return suite

    def restore_suite(self, suite_id, tenant_id):
        suite = self.get_suite(suite_id, tenant_id, include_deleted=True)
        if not suite:
            return None
        suite.deleted_at = None
        suite.deleted_by = None
        self.db.commit()
        return suite

    def purge_suite(self, suite_id, tenant_id):
        suite = self.get_suite(suite_id, tenant_id, include_deleted=True)
        if not suite:
            return False
        self.db.delete(suite)
        self.db.commit()
        return True


# ---------- BA reviews --------------------------------------------------------

class BAReviewRepository:
    def __init__(self, db):
        self.db = db

    def create_review(self, tenant_id, test_case_version_id, assigned_to):
        review = BAReview(
            tenant_id=tenant_id, test_case_version_id=test_case_version_id,
            assigned_to=assigned_to,
        )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)
        return review

    def get_review(self, review_id, include_deleted=False):
        q = self.db.query(BAReview).filter(BAReview.id == review_id)
        if not include_deleted:
            q = q.filter(BAReview.deleted_at.is_(None))
        return q.first()

    def list_reviews(self, tenant_id, status=None, assigned_to=None, include_deleted=False):
        q = self.db.query(BAReview).filter(BAReview.tenant_id == tenant_id)
        if not include_deleted:
            q = q.filter(BAReview.deleted_at.is_(None))
        if status:
            q = q.filter(BAReview.status == status)
        if assigned_to:
            q = q.filter(BAReview.assigned_to == assigned_to)
        return q.order_by(BAReview.created_at.desc()).all()

    def list_page(self, tenant_id, *, page=1, per_page=20, q=None,
                  sort="created_at", order="desc", filters=None,
                  include_deleted=False) -> PageResult:
        base = self.db.query(BAReview).filter(BAReview.tenant_id == tenant_id)
        return (ListQuery(base, BAReview,
                          search_fields=None,  # no natural text column
                          sort_whitelist=["created_at", "updated_at", "status", "reviewed_at"],
                          filter_spec={
                              "status": BAReview.status,
                              "assigned_to": BAReview.assigned_to,
                              "reviewed_by": BAReview.reviewed_by,
                          },
                          default_sort="created_at")
                .with_soft_delete(BAReview, include_deleted=include_deleted)
                .search(q).filter_by(filters or {}).sort(sort, order)
                .paginate(page, per_page))

    def update_review(self, review_id, status, feedback=None, reviewed_by=None, step_comments=None):
        review = self.get_review(review_id)
        if not review:
            return None
        review.status = status
        review.feedback = feedback
        review.reviewed_by = reviewed_by
        if step_comments is not None:
            review.step_comments = step_comments
        review.reviewed_at = _now()
        review.updated_at = _now()
        review.version = (review.version or 0) + 1
        self.db.commit()
        self.db.refresh(review)
        return review

    def soft_delete_review(self, review_id, tenant_id, user_id):
        review = self.db.query(BAReview).filter(
            BAReview.id == review_id, BAReview.tenant_id == tenant_id,
            BAReview.deleted_at.is_(None),
        ).first()
        if not review:
            return None
        review.deleted_at = _now()
        review.deleted_by = user_id
        self.db.commit()
        return review

    def restore_review(self, review_id, tenant_id):
        review = self.db.query(BAReview).filter(
            BAReview.id == review_id, BAReview.tenant_id == tenant_id,
        ).first()
        if not review:
            return None
        review.deleted_at = None
        review.deleted_by = None
        self.db.commit()
        return review

    def purge_review(self, review_id, tenant_id):
        review = self.db.query(BAReview).filter(
            BAReview.id == review_id, BAReview.tenant_id == tenant_id,
        ).first()
        if not review:
            return False
        self.db.delete(review)
        self.db.commit()
        return True


# ---------- Metadata impacts --------------------------------------------------

class MetadataImpactRepository:
    def __init__(self, db):
        self.db = db

    def get_impact(self, impact_id, tenant_id, include_deleted=False):
        q = self.db.query(MetadataImpact).join(
            TestCase, MetadataImpact.test_case_id == TestCase.id,
        ).filter(
            MetadataImpact.id == impact_id,
            TestCase.tenant_id == tenant_id,
        )
        if not include_deleted:
            q = q.filter(MetadataImpact.deleted_at.is_(None))
        return q.first()

    def list_pending_impacts(self, tenant_id, include_deleted=False):
        q = self.db.query(MetadataImpact).join(
            TestCase, MetadataImpact.test_case_id == TestCase.id,
        ).filter(
            TestCase.tenant_id == tenant_id,
            MetadataImpact.resolution == "pending",
        )
        if not include_deleted:
            q = q.filter(MetadataImpact.deleted_at.is_(None))
        return q.all()

    def list_page(self, tenant_id, *, page=1, per_page=20, q=None,
                  sort="created_at", order="desc", filters=None,
                  include_deleted=False) -> PageResult:
        base = self.db.query(MetadataImpact).join(
            TestCase, MetadataImpact.test_case_id == TestCase.id,
        ).filter(TestCase.tenant_id == tenant_id)
        return (ListQuery(base, MetadataImpact,
                          search_fields=["entity_ref"],
                          sort_whitelist=["created_at", "updated_at", "entity_ref", "impact_type"],
                          filter_spec={
                              "resolution": MetadataImpact.resolution,
                              "impact_type": MetadataImpact.impact_type,
                              "test_case_id": MetadataImpact.test_case_id,
                          },
                          default_sort="created_at")
                .with_soft_delete(MetadataImpact, include_deleted=include_deleted)
                .search(q).filter_by(filters or {}).sort(sort, order)
                .paginate(page, per_page))

    def resolve_impact(self, impact_id, resolution, resolved_by):
        impact = self.db.query(MetadataImpact).filter(
            MetadataImpact.id == impact_id,
        ).first()
        if not impact:
            return None
        impact.resolution = resolution
        impact.resolved_by = resolved_by
        impact.resolved_at = _now()
        impact.updated_at = _now()
        self.db.commit()
        self.db.refresh(impact)
        return impact

    def soft_delete_impact(self, impact_id, tenant_id, user_id):
        impact = self.get_impact(impact_id, tenant_id)
        if not impact:
            return None
        impact.deleted_at = _now()
        impact.deleted_by = user_id
        self.db.commit()
        return impact

    def restore_impact(self, impact_id, tenant_id):
        impact = self.get_impact(impact_id, tenant_id, include_deleted=True)
        if not impact:
            return None
        impact.deleted_at = None
        impact.deleted_by = None
        self.db.commit()
        return impact

    def purge_impact(self, impact_id, tenant_id):
        impact = self.get_impact(impact_id, tenant_id, include_deleted=True)
        if not impact:
            return False
        self.db.delete(impact)
        self.db.commit()
        return True
