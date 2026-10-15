from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from crmbuilder_web.config import Settings
from crmbuilder_web.deps import SESSION_COOKIE, current_user, get_db, get_settings
from crmbuilder_web.models import LoginSession, User
from crmbuilder_web.security import hash_password, new_token, token_hash, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=200)


class UserOut(BaseModel):
    id: int
    email: str


def _start_session(user: User, db: Session, response: Response, settings: Settings) -> None:
    token = new_token()
    expires = datetime.now(UTC) + timedelta(days=settings.session_days)
    db.add(LoginSession(token_hash=token_hash(token), user_id=user.id, expires_at=expires))
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    body: Credentials,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    if not settings.allow_signup:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Регистрация закрыта")
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот email уже зарегистрирован")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    _start_session(user, db, response, settings)
    return UserOut(id=user.id, email=user.email)


@router.post("/login")
def login(
    body: Credentials,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль")
    # Housekeeping: drop this user's expired sessions.
    db.execute(
        delete(LoginSession).where(
            LoginSession.user_id == user.id, LoginSession.expires_at < datetime.now(UTC)
        )
    )
    _start_session(user, db, response, settings)
    return UserOut(id=user.id, email=user.email)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(token)))
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut(id=user.id, email=user.email)
