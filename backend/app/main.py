from fastapi import FastAPI, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db, Base, engine
import app.models  # noqa: F401
from app.routes import setup
from app.routes import setup, auth

Base.metadata.create_all(bind=engine)

app = FastAPI(title="HCM Lite API")
app.include_router(setup.router)

app.include_router(setup.router)
app.include_router(auth.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/health/db")
def health_db(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"database": "connected"}