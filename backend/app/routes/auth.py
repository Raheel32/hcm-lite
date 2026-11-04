from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_token_user
from app.core.security import create_access_token
from app.database import get_db
from app.models.user import User
from app.schemas.auth import ChangePasswordRequest, LoginRequest, TokenResponse, UserOut
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    try:
        user = auth_service.authenticate(db, data.email, data.password)
    except auth_service.InvalidCredentials:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    except auth_service.AccountDisabled:
        raise HTTPException(status_code=403, detail="This account has been deactivated")

    token = create_access_token(user.id, user.role.value)
    return TokenResponse(
        access_token=token,
        role=user.role.value,
        must_change_password=user.must_change_password,
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_token_user)):
    return UserOut(
        id=user.id,
        email=user.email,
        role=user.role.value,
        must_change_password=user.must_change_password,
    )


@router.post("/change-password")
def change_password(
    data: ChangePasswordRequest,
    user: User = Depends(get_token_user),
    db: Session = Depends(get_db),
):
    try:
        auth_service.change_password(db, user, data.current_password, data.new_password)
    except auth_service.WrongCurrentPassword:
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    except auth_service.SamePassword:
        raise HTTPException(status_code=400, detail="New password must be different from the current one")
    return {"message": "Password changed"}