from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from anvil_api.auth import (
    CurrentUser,
    SessionDep,
    hash_password,
    make_token,
    verify_password,
)
from anvil_api.models import User
from anvil_api.schemas import LoginIn, SignupIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenOut)
def signup(body: SignupIn, session: SessionDep) -> TokenOut:
    existing = session.exec(select(User).where(User.email == body.email)).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    user = User(email=body.email, name=body.name, password_hash=hash_password(body.password))
    session.add(user)
    session.commit()
    session.refresh(user)
    return TokenOut(access_token=make_token(user.id))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, session: SessionDep) -> TokenOut:
    user = session.exec(select(User).where(User.email == body.email)).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    return TokenOut(access_token=make_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut(id=user.id, email=user.email, name=user.name, created_at=user.created_at)
