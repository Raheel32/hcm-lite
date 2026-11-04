from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.setup import SetupRequest, SetupStatus
from app.services import setup_service

router = APIRouter(prefix="/setup", tags=["Setup"])


@router.get("/status", response_model=SetupStatus)
def setup_status(db: Session = Depends(get_db)):
    return {"initialized": setup_service.is_initialized(db)}


@router.post("", status_code=status.HTTP_201_CREATED)
def first_run_setup(data: SetupRequest, db: Session = Depends(get_db)):
    try:
        company = setup_service.run_first_setup(db, data)
    except setup_service.SetupAlreadyDone:
        raise HTTPException(status_code=409, detail="Setup has already been completed")
    return {"message": "Setup complete", "company": company.name, "admin_email": data.admin_email.lower()}