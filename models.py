"""SQLAlchemy database models for the HTL Lern-App."""

from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Class(Base):
    """Represents a school class such as 1AHIT."""

    __tablename__ = "classes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)

    users = relationship("User", back_populates="school_class")
    questions = relationship("Question", back_populates="school_class")


class User(Base):
    """Application user (admin / class_admin / student)."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(
        Enum("admin", "class_admin", "student", name="user_role"),
        nullable=False,
        default="student",
    )
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)

    school_class = relationship("Class", back_populates="users")
    progress = relationship("Progress", back_populates="user")


class Question(Base):
    """A question that can be MC or open-ended."""

    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=False)
    subject = Column(String(200), nullable=False)
    text = Column(Text, nullable=False)
    type = Column(
        Enum("mc", "open", name="question_type"),
        nullable=False,
    )
    # JSON array of option strings, only used for type='mc'
    options = Column(JSON, nullable=True)
    # Zero-based index of the correct MC option
    correct_answer_index = Column(Integer, nullable=True)
    # Used for type='open' grading
    sample_solution = Column(Text, nullable=True)
    is_archived = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    school_class = relationship("Class", back_populates="questions")
    progress = relationship("Progress", back_populates="question")


class Progress(Base):
    """Tracks a student's answer to a specific question."""

    __tablename__ = "progress"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    given_answer = Column(Text, nullable=True)
    # Score 0-100
    score = Column(Float, nullable=True)
    ai_feedback = Column(Text, nullable=True)

    user = relationship("User", back_populates="progress")
    question = relationship("Question", back_populates="progress")
