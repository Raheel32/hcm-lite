from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.settings import CompanySettings
from app.schemas.settings import SettingsUpdate


class NotInitialized(Exception):
    pass


def get_settings(db: Session) -> CompanySettings:
    company = db.query(Company).first()
    if company is None:
        raise NotInitialized()

    settings = db.query(CompanySettings).filter(CompanySettings.company_id == company.id).first()
    if settings is None:
        # First time anyone asks: create the row with the default values
        settings = CompanySettings(company_id=company.id)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def update_settings(db: Session, data: SettingsUpdate) -> CompanySettings:
    settings = get_settings(db)
    for field, value in data.model_dump().items():
        setattr(settings, field, value)
    db.commit()
    db.refresh(settings)
    return settings