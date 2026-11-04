from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db, Base, engine
import app.models  # noqa: F401
from app.routes import setup, auth
from app.routes import setup, auth, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once when the server starts (not when the file is imported)
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="HCM Lite API", lifespan=lifespan)
app.include_router(setup.router)
app.include_router(auth.router)
app.include_router(settings.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/health/db")
def health_db(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"database": "connected"}