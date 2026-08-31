import os
import re
import asyncio
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from typing import Optional, List

import jwt
import io
from fastapi import FastAPI, HTTPException, Depends, status, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from pypdf import PdfReader
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, Field

from sqlalchemy import String, Text, DateTime, Boolean, Integer, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Import Agent Functions from ai.py
from ai import run_privacy_agent, run_auditor_agent, run_scoring_agent, PrivacyAgentError

# ==========================================
# 1. DATABASE CONFIGURATION & MODELS
# ==========================================
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql+asyncpg://postgres:postgreSQL@localhost:5432/postgres"
)

engine = create_async_engine(DATABASE_URL, echo=True, future=True)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class AuditRecord(Base):
    __tablename__ = "audit_records"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), 
    default=lambda: datetime.now(timezone.utc)
)
    submission_type: Mapped[str] = mapped_column(String(50))
    raw_text: Mapped[str] = mapped_column(Text)
    redacted_text: Mapped[str] = mapped_column(Text)
    audit_report: Mapped[str] = mapped_column(Text)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)    
    decision: Mapped[Optional[str]] = mapped_column(String(20), nullable=True) 
    
class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: datetime.now(timezone.utc)
    )
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# ==========================================
# 2. JWT AUTHENTICATION CONFIGURATION
# ==========================================
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "super-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

class Token(BaseModel):
    access_token: str
    token_type: str

class UserCreate(BaseModel):
    username: str
    email: str
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> UserRecord:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token or credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    result = await db.execute(select(UserRecord).where(UserRecord.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user

# ==========================================
# 3. SCHEMAS & FASTAPI LIFESPAN
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(
    title="Privacy-Preserving Agentic Resume Auditor",
    version="1.0.0",
    lifespan=lifespan
)

# ==========================================
# ADD CORS MIDDLEWARE HERE
# ==========================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # cvhelper.html may be opened directly from disk or any static server
    allow_credentials=False,  # no cookies are used, so a wildcard origin is allowed
    allow_methods=["*"],
    allow_headers=["*"],
)
# ==========================================

class AuditCreateRequest(BaseModel):
    submission_type: str = Field(default="resume", examples=["resume"])
    raw_text: str = Field(
        ..., 
        examples=["John Doe, Email: john@example.com, Phone: 555-0199. Senior Software Engineer with 5 years experience at Acme Corp. Led team of 4 engineers."]
    )
SCORE_THRESHOLD = 7
def extract_score_and_decision(report: str):
    score_match = (
        re.search(r"(\d+(?:\.\d+)?)\s*/\s*10\b", report) or
        re.search(r"OVERALL SCORE:\s*(\d+(?:\.\d+)?)\b", report, re.IGNORECASE) or
        re.search(r"Score[^\d]*?(\d+(?:\.\d+)?)\b", report, re.IGNORECASE)
    )
    if not score_match:
        return None, "UNDER_REVIEW"
    score = round(float(score_match.group(1)))
    if score < 0 or score > 10:
        return None, "UNDER_REVIEW"
    return score, ("ACCEPTED" if score >= SCORE_THRESHOLD else "REJECTED")
ENTITY_TAG_RE = re.compile(r"\[(NAME|EMAIL|PHONE|LOCATION)\]")


class AuditResponse(BaseModel):
    id: int
    created_at: datetime
    submission_type: str
    score: Optional[int] = None
    decision: Optional[str] = None
    redacted_text: str
    audit_report: str

    model_config = ConfigDict(from_attributes=True)
    
# ==========================================
# 4. AUTHENTICATION ROUTE
# ==========================================
@app.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    existing_user = await db.execute(
        select(UserRecord).where(
            (UserRecord.username == user_data.username) | (UserRecord.email == user_data.email)
        )
    )
    if existing_user.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username or email already registered")

    hashed_pwd = pwd_context.hash(user_data.password)
    new_user = UserRecord(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hashed_pwd
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user

@app.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(UserRecord).where(UserRecord.username == form_data.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

# ==========================================
# 5. PROTECTED AGENTIC API ROUTES
# ==========================================
@app.post(
    "/api/v1/audit", 
    response_model=AuditResponse, 
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_user)]  # Enforces auth without creating an unused parameter
)
async def process_and_store_audit(
    payload: AuditCreateRequest, 
    db: AsyncSession = Depends(get_db) 
):
    try:
        clean_text = await asyncio.to_thread(run_privacy_agent, payload.raw_text)
        report = await asyncio.to_thread(run_auditor_agent, clean_text, payload.submission_type)

        score, decision = extract_score_and_decision(report)

        db_record = AuditRecord(
            submission_type=payload.submission_type,
            raw_text=payload.raw_text,
            redacted_text=clean_text,
            audit_report=report,
            score=score,          
            decision=decision     
        )

        db.add(db_record)
        await db.commit()
        await db.refresh(db_record)

        return db_record

    except PrivacyAgentError as e:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail=f"Could not safely redact PII, so nothing was audited or stored. Please try again. ({e})",
        )
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
@app.get(
    "/api/v1/audits", 
    response_model=List[AuditResponse],
    dependencies=[Depends(get_current_user)]  # Enforces auth without creating an unused parameter
)
async def list_audits(
    skip: int = 0, 
    limit: int = 10, 
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(AuditRecord).offset(skip).limit(limit))
    return result.scalars().all()

@app.post(
    "/api/v1/audit/pdf", 
    response_model=AuditResponse, 
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_user)]
)
async def process_pdf_audit(
    file: UploadFile = File(...),
    submission_type: str = "resume",
    db: AsyncSession = Depends(get_db)
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    try:
        contents = await file.read()
        pdf_reader = PdfReader(io.BytesIO(contents))
        
        raw_text = ""
        for page in pdf_reader.pages:
            extracted_text = page.extract_text()
            if extracted_text:
                raw_text += extracted_text + "\n"

        if not raw_text.strip():
            raise HTTPException(
                status_code=400, 
                detail="Could not extract text from PDF. Ensure it is not empty or scanned image-only."
            )

        clean_text = await asyncio.to_thread(run_privacy_agent, raw_text)
        report = await asyncio.to_thread(run_auditor_agent, clean_text, submission_type)

        score, decision = extract_score_and_decision(report)

        db_record = AuditRecord(
            submission_type=submission_type,
            raw_text=raw_text,
            redacted_text=clean_text,
            audit_report=report,
            score=score,
            decision=decision
        )

        db.add(db_record)
        await db.commit()
        await db.refresh(db_record)

        return db_record

    except HTTPException:
        raise
    except PrivacyAgentError as e:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail=f"Could not safely redact PII, so nothing was audited or stored. Please try again. ({e})",
        )
    except Exception as e:
        print(f"CRITICAL ERROR IN PDF AUDIT: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {str(e)}")


# ==========================================
# 6. PUBLIC ENDPOINT FOR cvhelper.html
# ==========================================
# The frontend has no login screen — it POSTs multipart form-data
# (job_role + either text or file) straight to /api/audit and expects:
#   { sanitized_text, entity_types, entities_redacted, scores: [{label, score}], review, llm_provider }
# This endpoint is intentionally unauthenticated so the static HTML page
# can call it directly. Every audit is still saved to the database.
@app.post("/api/audit")
async def public_audit(
    job_role: str = Form("resume"),
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        if file is not None:
            if not file.filename.lower().endswith(".pdf"):
                raise HTTPException(status_code=400, detail="Only PDF files are supported.")
            contents = await file.read()
            pdf_reader = PdfReader(io.BytesIO(contents))
            raw_text = ""
            for page in pdf_reader.pages:
                extracted = page.extract_text()
                if extracted:
                    raw_text += extracted + "\n"
            if not raw_text.strip():
                raise HTTPException(
                    status_code=400,
                    detail="Could not extract text from PDF. Ensure it is not empty or scanned image-only.",
                )
        elif text and text.strip():
            raw_text = text
        else:
            raise HTTPException(status_code=400, detail="Provide either 'text' or a PDF 'file'.")

        redacted_text = await asyncio.to_thread(run_privacy_agent, raw_text)

        entity_matches = ENTITY_TAG_RE.findall(redacted_text)
        entity_types = sorted(set(entity_matches))
        entities_redacted = len(entity_matches)

        score_data = await asyncio.to_thread(run_scoring_agent, redacted_text, job_role)
        report = await asyncio.to_thread(run_auditor_agent, redacted_text, job_role)
        score_num, decision = extract_score_and_decision(report)

        db_record = AuditRecord(
            submission_type=job_role,
            raw_text=raw_text,
            redacted_text=redacted_text,
            audit_report=report,
            score=score_num,
            decision=decision,
        )
        db.add(db_record)
        await db.commit()
        await db.refresh(db_record)

        return {
            "sanitized_text": redacted_text,
            "entity_types": entity_types,
            "entities_redacted": entities_redacted,
            "scores": [
                {"label": label, "score": score}
                for label, score in zip(score_data["labels"], score_data["scores"])
            ],
            "review": report,
            "llm_provider": "Groq (qwen/qwen3.6-27b)",
        }

    except HTTPException:
        raise
    except PrivacyAgentError as e:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail=f"Could not safely redact PII, so nothing was audited or stored. Please try again. ({e})",
        )
    except Exception as e:
        await db.rollback()
        print(f"CRITICAL ERROR IN /api/audit: {e}")
        raise HTTPException(status_code=500, detail=str(e))