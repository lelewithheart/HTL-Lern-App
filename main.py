"""FastAPI application entry point with all routes."""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ai_service import OllamaProvider
from models import Base, Class, Progress, Question, User

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://lernapp:lernapp123@localhost:3306/lernapp",
)
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-please")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 8  # 8 hours

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ai = OllamaProvider()


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="HTL Lern-App", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend
if os.path.exists("index.html"):
    @app.get("/", include_in_schema=False)
    async def serve_frontend():
        return FileResponse("index.html")


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str
    password: str
    class_id: Optional[int] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    user_id: int
    class_id: Optional[int]


class SyncContentRequest(BaseModel):
    subject: str
    stoff: str
    archive_old: bool = False


class SubmitAnswerRequest(BaseModel):
    question_id: int
    given_answer: str


# ---------------------------------------------------------------------------
# Dependency: get current user from Bearer token
# ---------------------------------------------------------------------------

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_token(credentials.credentials)
    user_id: Optional[str] = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    user = db.get(User, int(user_id))
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


class RoleChecker:
    def __init__(self, allowed_roles: list[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user


require_admin = RoleChecker(["admin"])
require_class_admin = RoleChecker(["admin", "class_admin"])
require_student = RoleChecker(["admin", "class_admin", "student"])

# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------


@app.get("/classes", tags=["auth"])
def list_classes(db: Session = Depends(get_db)):
    """Return all available classes for the registration dropdown."""
    classes = db.query(Class).order_by(Class.name).all()
    return [{"id": c.id, "name": c.name} for c in classes]


@app.post("/register", tags=["auth"])
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new user (default role: student)."""
    existing = db.query(User).filter(User.username == req.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken")
    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        role="student",
        class_id=req.class_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "username": user.username, "role": user.role}


@app.post("/login", response_model=TokenResponse, tags=["auth"])
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate and return a JWT."""
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(
        {"sub": str(user.id), "role": user.role},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return TokenResponse(
        access_token=token,
        role=user.role,
        user_id=user.id,
        class_id=user.class_id,
    )


# ---------------------------------------------------------------------------
# Class-Admin routes
# ---------------------------------------------------------------------------


@app.post("/admin/sync-content", tags=["admin"])
def sync_content(
    req: SyncContentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_class_admin),
):
    """Archive old questions and generate new ones via AI."""
    if current_user.class_id is None:
        raise HTTPException(
            status_code=400, detail="Admin has no class assigned"
        )

    if req.archive_old:
        db.query(Question).filter(
            Question.class_id == current_user.class_id,
            Question.subject == req.subject,
            Question.is_archived.is_(False),
        ).update({"is_archived": True})
        db.commit()

    # Determine class name for the AI
    school_class = db.get(Class, current_user.class_id)
    class_name = school_class.name if school_class else str(current_user.class_id)

    try:
        generated = ai.generate_questions(req.stoff, class_name, req.subject)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    new_questions = []
    for q in generated:
        question = Question(
            class_id=current_user.class_id,
            subject=req.subject,
            text=q.get("text", ""),
            type=q.get("type", "open"),
            options=q.get("options"),
            correct_answer_index=q.get("correct_answer_index"),
            sample_solution=q.get("sample_solution"),
            is_archived=False,
        )
        db.add(question)
        new_questions.append(question)

    db.commit()
    return {"created": len(new_questions), "subject": req.subject}


@app.get("/admin/stats", tags=["admin"])
def admin_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_class_admin),
):
    """Return progress statistics for all students in the class."""
    if current_user.class_id is None:
        raise HTTPException(status_code=400, detail="Admin has no class assigned")

    students = (
        db.query(User)
        .filter(User.class_id == current_user.class_id, User.role == "student")
        .all()
    )

    result = []
    for student in students:
        progress_rows = (
            db.query(Progress).filter(Progress.user_id == student.id).all()
        )
        scored = [p.score for p in progress_rows if p.score is not None]
        answered = len(progress_rows)
        avg_score = sum(scored) / len(scored) if scored else None
        result.append(
            {
                "user_id": student.id,
                "username": student.username,
                "answered": answered,
                "avg_score": round(avg_score, 1) if avg_score is not None else None,
            }
        )
    return result


@app.get("/admin/questions", tags=["admin"])
def admin_questions(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_class_admin),
):
    """Return active and archived questions for the admin's class."""
    if current_user.class_id is None:
        raise HTTPException(status_code=400, detail="Admin has no class assigned")

    questions = (
        db.query(Question)
        .filter(Question.class_id == current_user.class_id)
        .order_by(Question.created_at.desc())
        .all()
    )
    return [
        {
            "id": q.id,
            "subject": q.subject,
            "text": q.text,
            "type": q.type,
            "is_archived": q.is_archived,
            "created_at": q.created_at.isoformat(),
        }
        for q in questions
    ]


@app.patch("/admin/questions/{question_id}/unarchive", tags=["admin"])
def unarchive_question(
    question_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_class_admin),
):
    """Restore an archived question."""
    question = db.get(Question, question_id)
    if question is None or question.class_id != current_user.class_id:
        raise HTTPException(status_code=404, detail="Question not found")
    question.is_archived = False
    db.commit()
    return {"id": question.id, "is_archived": False}


# ---------------------------------------------------------------------------
# Super-Admin routes
# ---------------------------------------------------------------------------


@app.get("/admin/users", tags=["super-admin"])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """List all users (super-admin only)."""
    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "class_id": u.class_id,
        }
        for u in users
    ]


class UpdateRoleRequest(BaseModel):
    role: str


@app.patch("/admin/users/{user_id}/role", tags=["super-admin"])
def update_user_role(
    user_id: int,
    req: UpdateRoleRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Change a user's role (super-admin only)."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    allowed = {"admin", "class_admin", "student"}
    if req.role not in allowed:
        raise HTTPException(status_code=400, detail=f"Role must be one of {allowed}")
    user.role = req.role
    db.commit()
    return {"id": user.id, "role": user.role}


@app.delete("/admin/users/{user_id}", tags=["super-admin"])
def ban_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Delete (ban) a user (super-admin only)."""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot ban yourself")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"detail": "User banned"}


# ---------------------------------------------------------------------------
# Student routes
# ---------------------------------------------------------------------------


@app.get("/questions/active", tags=["student"])
def get_active_questions(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student),
):
    """Return non-archived questions for the current user's class."""
    if current_user.class_id is None:
        raise HTTPException(status_code=400, detail="User has no class assigned")

    questions = (
        db.query(Question)
        .filter(
            Question.class_id == current_user.class_id,
            Question.is_archived.is_(False),
        )
        .order_by(Question.created_at.desc())
        .all()
    )
    return [
        {
            "id": q.id,
            "subject": q.subject,
            "text": q.text,
            "type": q.type,
            "options": q.options,
        }
        for q in questions
    ]


@app.post("/submit", tags=["student"])
def submit_answer(
    req: SubmitAnswerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student),
):
    """Submit an answer; open questions are graded by the AI immediately."""
    question = db.get(Question, req.question_id)
    if question is None or question.class_id != current_user.class_id:
        raise HTTPException(status_code=404, detail="Question not found")

    score: Optional[float] = None
    ai_feedback: Optional[str] = None

    if question.type == "mc":
        # Validate against correct_answer_index
        try:
            given_index = int(req.given_answer)
        except (ValueError, TypeError):
            given_index = -1
        score = 100.0 if given_index == question.correct_answer_index else 0.0
        ai_feedback = (
            "Richtig!" if score == 100.0 else "Leider falsch."
        )
    elif question.type == "open":
        if not question.sample_solution:
            raise HTTPException(
                status_code=500, detail="No sample solution available for grading"
            )
        try:
            result = ai.grade_answer(
                question.text, question.sample_solution, req.given_answer
            )
            score = result["score"]
            ai_feedback = result["feedback"]
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Upsert progress record
    progress = (
        db.query(Progress)
        .filter(
            Progress.user_id == current_user.id,
            Progress.question_id == req.question_id,
        )
        .first()
    )
    if progress is None:
        progress = Progress(user_id=current_user.id, question_id=req.question_id)
        db.add(progress)

    progress.given_answer = req.given_answer
    progress.score = score
    progress.ai_feedback = ai_feedback
    db.commit()

    return {
        "score": score,
        "feedback": ai_feedback,
    }


@app.get("/my-progress", tags=["student"])
def my_progress(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student),
):
    """Return the current student's progress records."""
    rows = db.query(Progress).filter(Progress.user_id == current_user.id).all()
    return [
        {
            "question_id": p.question_id,
            "given_answer": p.given_answer,
            "score": p.score,
            "ai_feedback": p.ai_feedback,
        }
        for p in rows
    ]
