import logging
from datetime import timedelta
from fastapi import APIRouter, HTTPException, Depends
from app.schemas.auth_schema import LoginRequest, LoginResponse
from app.config import settings
from app.auth import create_access_token # I need to move auth functions too

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """Authenticate user and return JWT access token"""
    if (request.username == settings.AUTH_USERNAME and request.password == settings.AUTH_PASSWORD):
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
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
