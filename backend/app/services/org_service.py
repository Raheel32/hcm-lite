from sqlalchemy import func
from sqlalchemy.orm import Session


class DuplicateName(Exception):
    pass


class ItemNotFound(Exception):
    pass


def _name_taken(db: Session, model, name: str, exclude_id: int | None = None) -> bool:
    query = db.query(model).filter(func.lower(model.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(model.id != exclude_id)
    return query.first() is not None


def list_items(db: Session, model, include_inactive: bool = False):
    query = db.query(model)
    if not include_inactive:
        query = query.filter(model.is_active.is_(True))
    return query.order_by(func.lower(model.name)).all()


def create_item(db: Session, model, name: str):
    if _name_taken(db, model, name):
        raise DuplicateName()
    item = model(name=name, is_active=True)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_item(db: Session, model, item_id: int, name: str, is_active: bool):
    item = db.get(model, item_id)
    if item is None:
        raise ItemNotFound()
    if _name_taken(db, model, name, exclude_id=item_id):
        raise DuplicateName()
    item.name = name
    item.is_active = is_active
    db.commit()
    db.refresh(item)
    return item