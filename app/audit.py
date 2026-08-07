from sqlalchemy.orm import Session

from .models import AuditLog


def log_action(db: Session, user_id: int | None, action: str, detail: str = "") -> None:
    db.add(AuditLog(user_id=user_id, action=action, detail=detail[:4000]))
    db.commit()
