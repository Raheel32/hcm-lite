from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
)


def test_password_hash_and_verify():
    hashed = hash_password("Secret123")
    assert hashed != "Secret123"
    assert verify_password("Secret123", hashed)
    assert not verify_password("wrong-password", hashed)


def test_token_round_trip():
    token = create_access_token(5, "hr")
    data = decode_access_token(token)
    assert data["sub"] == "5"
    assert data["role"] == "hr"


def test_bad_token_returns_none():
    assert decode_access_token("not-a-real-token") is None