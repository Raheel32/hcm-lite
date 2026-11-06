from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db, Base, engine
import app.models  # noqa: F401
from app.routes import setup, auth, settings
from app.routes.org import departments_router, designations_router
from app.routes.leave_config import leave_types_router, holidays_router
from app.routes import setup, auth, settings, users, employees, attendance, corrections, leave, payroll, audit, comms


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once when the server starts (not when the file is imported)
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="HCM Lite API", lifespan=lifespan)
app.include_router(setup.router)
app.include_router(auth.router)
app.include_router(settings.router)
app.include_router(departments_router)
app.include_router(designations_router)
app.include_router(leave_types_router)
app.include_router(holidays_router)
app.include_router(users.router)
app.include_router(employees.router)
app.include_router(attendance.router)
app.include_router(corrections.router)
app.include_router(leave.router)
app.include_router(payroll.router)
app.include_router(audit.router)
app.include_router(comms.notifications_router)
app.include_router(comms.announcements_router)

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/health/db")
def health_db(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"database": "connected"}