"""Seed script: creates the classes table entries and a default admin user."""

import os
import sys

from passlib.context import CryptContext
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Class, User

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://lernapp:lernapp123@localhost:3306/lernapp",
)

# All classes from 1AHIT to 5BHITM
HTL_CLASSES = [
    "1AHIT",
    "1BHIT",
    "2AHIT",
    "2BHIT",
    "3AHIT",
    "3BHIT",
    "4AHIT",
    "4BHIT",
    "5AHIT",
    "5BHIT",
    "1AHITM",
    "1BHITM",
    "2AHITM",
    "2BHITM",
    "3AHITM",
    "3BHITM",
    "4AHITM",
    "4BHITM",
    "5AHITM",
    "5BHITM",
]

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def main() -> None:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    # Seed classes
    for name in HTL_CLASSES:
        if not db.query(Class).filter(Class.name == name).first():
            db.add(Class(name=name))
            print(f"  + Class {name}")
    db.commit()

    # Seed default admin
    if not db.query(User).filter(User.username == DEFAULT_ADMIN_USERNAME).first():
        admin = User(
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=pwd_context.hash(DEFAULT_ADMIN_PASSWORD),
            role="admin",
            class_id=None,
        )
        db.add(admin)
        db.commit()
        print(f"  + Admin user '{DEFAULT_ADMIN_USERNAME}' created.")
    else:
        print(f"  Admin user '{DEFAULT_ADMIN_USERNAME}' already exists.")

    db.close()
    print("Database initialisation complete.")


if __name__ == "__main__":
    main()
