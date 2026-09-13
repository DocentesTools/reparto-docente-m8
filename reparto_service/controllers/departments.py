"""Department controller."""

from __future__ import annotations

import uuid

from fastapi import status
from fastapi_m8 import RoleType, UserModel, has_minimum_role
from sqlmodel import Session, col, func, select

from reparto_service.core.errors import DomainHTTPException
from reparto_service.controllers.base import DomainController
from reparto_service.db_models.departments import (
    Department,
    DepartmentCreate,
    DepartmentPublic,
    DepartmentsPublic,
    DepartmentUpdate,
)
from reparto_service.db_models.schools import School
from reparto_service.services.read_scope import UNRESTRICTED, visible_department_ids
from reparto_service.services.user_directory import (
    UserDirectoryUnavailable,
    UserRoleLookup,
)


class DepartmentController(DomainController):
    """CRUD logic for departments."""

    @staticmethod
    def validate_department_head(
        head_user_id: uuid.UUID | None, lookup: UserRoleLookup
    ) -> None:
        """Refuse a recorded department head who could not act as one (§21.2).

        The field authorizes nothing, so this is not a permission check — it is
        an accuracy check. A department whose recorded head is a ``READER``
        tells every reader of that record something false, and the old
        role-independent binding is exactly the mistake §21.2 removed; storing
        one would keep the shape of it alive in the data.

        Clearing the field is always allowed: "nobody is recorded as head" is
        an honest state, and refusing to clear it would strand a department
        whose head has left.

        Raises:
            HTTPException: ``400`` when the issuer does not know the id or
                holds a role below ``ADMIN``; ``503`` when the issuer could not
                be consulted at all — an unconfirmable head is never recorded.
        """
        if head_user_id is None:
            return
        try:
            role = lookup(head_user_id)
        except UserDirectoryUnavailable as ex:
            raise DomainHTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="departments.could_not_confirm_department_head_s_role_with",
                message=f"Could not confirm the department head's role with the identity service ({ex.reason}); the head was not changed.",
                params={"ex_reason": ex.reason},
            ) from ex
        if role is None:
            raise DomainHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="departments.identity_service_does_not_know_user",
                message="The identity service does not know this user.",
                params={},
            )
        if not has_minimum_role(role, RoleType.ADMIN):
            raise DomainHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="departments.department_head_must_hold_at_least_admin_role",
                message=f"A department head must hold at least the admin role; this account holds {role.value}.",
                params={"role": role.value},
            )

    @staticmethod
    def list_departments(
        session: Session,
        current_user: UserModel,
        school_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> DepartmentsPublic:
        count_stmt = select(func.count()).select_from(Department)
        list_stmt = select(Department)
        departments = visible_department_ids(session, current_user)
        if departments is not UNRESTRICTED:
            count_stmt = count_stmt.where(col(Department.id).in_(departments))
            list_stmt = list_stmt.where(col(Department.id).in_(departments))
        if school_id is not None:
            count_stmt = count_stmt.where(Department.school_id == school_id)
            list_stmt = list_stmt.where(Department.school_id == school_id)
        count = session.exec(count_stmt).one()
        items = list(session.exec(list_stmt.offset(skip).limit(limit)).all())
        return DepartmentsPublic(
            data=[DepartmentPublic.model_validate(item) for item in items],
            count=count,
        )

    @staticmethod
    def get_department(
        session: Session, current_user: UserModel, department_id: uuid.UUID
    ) -> DepartmentPublic:
        department = DomainController.get_or_404(session, Department, department_id)
        departments = visible_department_ids(session, current_user)
        if departments is not UNRESTRICTED and department.id not in departments:
            # 404, not 403: confirming the row exists is itself out of scope.
            raise DomainHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                code="departments.department_not_found",
                message=f"Department {department_id} not found.",
                params={"department_id": department_id},
            )
        return DepartmentPublic.model_validate(department)

    @staticmethod
    def create_department(
        session: Session, department_in: DepartmentCreate, lookup: UserRoleLookup
    ) -> DepartmentPublic:
        # Validate the school exists.
        DomainController.get_or_404(session, School, department_in.school_id)
        DepartmentController.validate_department_head(
            department_in.department_head_user_id, lookup
        )
        department = Department.model_validate(department_in.model_dump())
        session.add(department)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise DomainHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="departments.could_not_create_department_check_slug_is_unique",
                message="Could not create department: check that the slug is unique within the school.",
                params={},
            ) from exc
        session.refresh(department)
        return DepartmentPublic.model_validate(department)

    @staticmethod
    def update_department(
        session: Session,
        department_id: uuid.UUID,
        department_in: DepartmentUpdate,
        lookup: UserRoleLookup,
    ) -> DepartmentPublic:
        department = DomainController.get_or_404(session, Department, department_id)
        changes = department_in.model_dump(exclude_unset=True)
        if "department_head_user_id" in changes:
            DepartmentController.validate_department_head(
                changes["department_head_user_id"], lookup
            )
        department.sqlmodel_update(changes)
        session.add(department)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise DomainHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="departments.could_not_update_department_check_slug_is_unique",
                message="Could not update department: check that the slug is unique within the school.",
                params={},
            ) from exc
        session.refresh(department)
        return DepartmentPublic.model_validate(department)


__all__ = ["DepartmentController"]
