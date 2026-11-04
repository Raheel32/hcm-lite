from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.database import get_db
from app.models.org import Department, Designation
from app.models.user import Role, User
from app.schemas.org import OrgItemCreate, OrgItemOut, OrgItemUpdate
from app.services import org_service


def build_router(prefix: str, tag: str, label: str, model) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])

    @router.get("", response_model=list[OrgItemOut])
    def list_items(
        include_inactive: bool = False,
        user: User = Depends(require_roles(Role.admin, Role.hr)),
        db: Session = Depends(get_db),
    ):
        return org_service.list_items(db, model, include_inactive)

    @router.post("", response_model=OrgItemOut, status_code=201)
    def create_item(
        data: OrgItemCreate,
        user: User = Depends(require_roles(Role.admin)),
        db: Session = Depends(get_db),
    ):
        try:
            return org_service.create_item(db, model, data.name)
        except org_service.DuplicateName:
            raise HTTPException(status_code=409, detail=f"A {label.lower()} with this name already exists")

    @router.put("/{item_id}", response_model=OrgItemOut)
    def update_item(
        item_id: int,
        data: OrgItemUpdate,
        user: User = Depends(require_roles(Role.admin)),
        db: Session = Depends(get_db),
    ):
        try:
            return org_service.update_item(db, model, item_id, data.name, data.is_active)
        except org_service.ItemNotFound:
            raise HTTPException(status_code=404, detail=f"{label} not found")
        except org_service.DuplicateName:
            raise HTTPException(status_code=409, detail=f"A {label.lower()} with this name already exists")

    return router


departments_router = build_router("/departments", "Departments", "Department", Department)
designations_router = build_router("/designations", "Designations", "Designation", Designation)
