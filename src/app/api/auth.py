"""
TaskPilot JWT Authentication
Аутентификация пользователей с access/refresh токенами
"""

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.orm import Session
import uuid

from app.db.engine import get_db
from app.db.models import User, Group
from app.config import settings

# ============================================================================
# Configuration
# ============================================================================

router = APIRouter(prefix="/auth", tags=["Authentication"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# ============================================================================
# Models
# ============================================================================

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    user_id: Optional[str] = None
    group_id: Optional[str] = None

class UserCreate(BaseModel):
    username: str
    password: str
    email: str
    group_id: str

class UserResponse(BaseModel):
    user_id: str
    username: str
    group_id: str
    email: str

# ============================================================================
# Helper Functions
# ============================================================================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=30))
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def create_refresh_token( dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=7)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        token_data = TokenData(user_id=user_id, group_id=payload.get("group_id"))
    except JWTError:
        raise credentials_exception
    
    user = db.query(User).filter(User.id == uuid.UUID(token_data.user_id)).first()
    if user is None:
        raise credentials_exception
    return user

# ============================================================================
# Routes
# ============================================================================

@router.post("/login", response_model=Token)
async def login(form_ OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Получение access/refresh токенов"""
    user = db.query(User).filter(User.username == form_data.username).first()
    
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is disabled")
    
    access_token = create_access_token(
        data={"sub": str(user.id), "group_id": str(user.group_id)},
        expires_delta=timedelta(minutes=settings.JWT_EXPIRY)
    )
    refresh_token = create_refresh_token(
        data={"sub": str(user.id), "group_id": str(user.group_id)}
    )
    
    return Token(access_token=access_token, refresh_token=refresh_token)

@router.post("/refresh", response_model=Token)
async def refresh_token(refresh_token: str, db: Session = Depends(get_db)):
    """Обновление access токена"""
    try:
        payload = jwt.decode(refresh_token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        token_type = payload.get("type")
        if token_type != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        
        user_id = payload.get("sub")
        user = db.query(User).filter(User.id == uuid.UUID(user_id)).first()
        
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="User not found or disabled")
        
        new_access_token = create_access_token(
            data={"sub": str(user.id), "group_id": str(user.group_id)},
            expires_delta=timedelta(minutes=settings.JWT_EXPIRY)
        )
        new_refresh_token = create_refresh_token(
            data={"sub": str(user.id), "group_id": str(user.group_id)}
        )
        
        return Token(access_token=new_access_token, refresh_token=new_refresh_token)
        
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Получение информации о текущем пользователе"""
    return UserResponse(
        user_id=str(current_user.id),
        username=current_user.username,
        group_id=str(current_user.group_id),
        email=current_user.email
    )

@router.post("/register", response_model=UserResponse)
async def register(user_ UserCreate, db: Session = Depends(get_db)):
    """Регистрация нового пользователя"""
    # Проверка существования
    if db.query(User).filter(User.username == user_data.username).first():
        raise HTTPException(status_code=400, detail="Username already registered")
    
    if db.query(User).filter(User.email == user_data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Создание пользователя
    user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=get_password_hash(user_data.password),
        group_id=uuid.UUID(user_data.group_id)
    )
    
    db.add(user)
    db.commit()
    db.refresh(user)
    
    return UserResponse(
        user_id=str(user.id),
        username=user.username,
        group_id=str(user.group_id),
        email=user.email
    )