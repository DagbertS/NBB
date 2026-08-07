"""Maak de eerste beheerder aan: python scripts/create_admin.py <email> [wachtwoord]"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("Gebruik: python scripts/create_admin.py <email> [wachtwoord]")
        sys.exit(1)
    email = sys.argv[1].strip().lower()
    password = sys.argv[2] if len(sys.argv) > 2 else getpass.getpass("Wachtwoord: ")

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            existing.password_hash = hash_password(password)
            existing.role = "admin"
            existing.is_active = True
            print(f"Bestaande gebruiker {email} is nu admin (wachtwoord bijgewerkt).")
        else:
            db.add(User(email=email, password_hash=hash_password(password), role="admin"))
            print(f"Admin {email} aangemaakt.")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
