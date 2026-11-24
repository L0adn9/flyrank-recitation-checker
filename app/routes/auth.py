from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, Field, field_validator

from app.auth import current_user, hash_password, issue_token, verify_password
from app.db import connection

router = APIRouter(prefix="/auth", tags=["auth"])

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class Signup(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=60)

    @field_validator("email")
    @classmethod
    def lower(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("name")
    @classmethod
    def strip(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()


class Login(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


def _session(user: dict) -> dict:
    token, ttl = issue_token(user["id"])
    return {"access_token": token, "token_type": "bearer", "expires_in": ttl,
            "user": {"id": user["id"], "email": user["email"], "name": user["name"]}}


@router.post("/signup", status_code=201)
def signup(body: Signup):
    try:
        with connection() as conn:
            user = conn.execute(
                "INSERT INTO users (email, name, password_hash) VALUES (%s, %s, %s) RETURNING id, email, name",
                (body.email, body.name, hash_password(body.password)),
            ).fetchone()
            conn.commit()
    except UniqueViolation:
        raise HTTPException(409, "An account with this email already exists") from None
    return _session(user)


@router.post("/login")
def login(body: Login):
    with connection() as conn:
        user = conn.execute(
            "SELECT id, email, name, password_hash FROM users WHERE email = %s", (body.email.strip().lower(),)
        ).fetchone()
    if not verify_password(user["password_hash"] if user else None, body.password):
        raise HTTPException(401, "Wrong email or password")
    return _session(user)


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return user
