
from datetime import timedelta
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from typing import Optional
import os
import jwt
from datetime import datetime
import logging

from app.schemas import LoginRequest, LoginResponse

logger = logging.getLogger(__name__)

router = APIRouter()
security = HTTPBearer()

# Static login credentials (can be overridden via environment variables)
AUTH_USERNAME = os.getenv('AUTH_USERNAME', 'Goldy@gmail.com')
AUTH_PASSWORD = os.getenv('AUTH_PASSWORD', '123456')
SECRET_KEY = os.getenv('SECRET_KEY', 'change-this-secret-key')


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=30)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm="HS256")
    return encoded_jwt


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify JWT token"""
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.post("/auth/login", response_model=LoginResponse, tags=["Auth"])
async def login(request: LoginRequest):
    """Authenticate user and return JWT access token"""
    if (request.username == AUTH_USERNAME and request.password == AUTH_PASSWORD):
        access_token_expires = timedelta(minutes=30)
        access_token = create_access_token(
            data={"sub": request.username},
            expires_delta=access_token_expires
        )
        
        logger.info(f"User {request.username} logged in successfully")
        return LoginResponse(access_token=access_token, token_type="bearer")
    else:
        logger.warning(f"Failed login attempt for username: {request.username}")
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password"
        )
