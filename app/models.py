from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user")  # user | admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """Elke zoekopdracht/actie van elke gebruiker — zichtbaar voor admins."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user: Mapped["User"] = relationship()


class CompanyList(Base):
    """Opgeslagen longlist (bv. alle bedrijven met NACE 62010 in Limburg)."""

    __tablename__ = "company_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    criteria: Mapped[str] = mapped_column(Text, default="")  # JSON van de zoekcriteria
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    owner: Mapped["User"] = relationship()
    items: Mapped[list["CompanyListItem"]] = relationship(
        back_populates="company_list", cascade="all, delete-orphan"
    )


class CompanyListItem(Base):
    __tablename__ = "company_list_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("company_lists.id"), index=True)
    enterprise_number: Mapped[str] = mapped_column(String(15), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    zipcode: Mapped[str] = mapped_column(String(10), default="")
    municipality: Mapped[str] = mapped_column(String(100), default="")
    nace_code: Mapped[str] = mapped_column(String(10), default="")
    nbb_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|fetched|error|none
    nbb_error: Mapped[str] = mapped_column(Text, default="")  # laatste foutreden bij ophalen
    nbb_note: Mapped[str] = mapped_column(Text, default="")  # info, geen fout (bv. referenties zonder document bij de NBB)
    nbb_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    company_list: Mapped["CompanyList"] = relationship(back_populates="items")


class NbbDeposit(Base):
    """Metadata van een bij de NBB neergelegd document (jaarrekening e.d.)."""

    __tablename__ = "nbb_deposits"

    id: Mapped[int] = mapped_column(primary_key=True)
    enterprise_number: Mapped[str] = mapped_column(String(15), index=True)
    reference: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    deposit_date: Mapped[str] = mapped_column(String(20), default="")
    exercise_end: Mapped[str] = mapped_column(String(20), default="")
    model_type: Mapped[str] = mapped_column(String(50), default="")
    data_format: Mapped[str] = mapped_column(String(20), default="")  # XBRL|PDF|...
    file_path: Mapped[str] = mapped_column(String(500), default="")
    raw_json: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Prompt(Base):
    """Door gebruikers geschreven prompt die dagelijks op de nieuwe neerleggingen draait."""

    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    prompt_text: Mapped[str] = mapped_column(Text)
    recipients: Mapped[str] = mapped_column(Text, default="")  # komma-gescheiden e-mails
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    owner: Mapped["User"] = relationship()
    runs: Mapped[list["PromptRun"]] = relationship(
        back_populates="prompt", cascade="all, delete-orphan"
    )


class PromptRun(Base):
    __tablename__ = "prompt_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_id: Mapped[int] = mapped_column(ForeignKey("prompts.id"), index=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok|error
    result_text: Mapped[str] = mapped_column(Text, default="")
    emailed: Mapped[bool] = mapped_column(Boolean, default=False)

    prompt: Mapped["Prompt"] = relationship(back_populates="runs")


class ScreeningPipeline(Base):
    """Eén gebouwde screening-pipeline: op basis van de thesis-criteria
    (kind='thesis') of van een door de gebruiker gebouwde longlist
    (kind='list'). De artefacten staan in marts/pipelines/<sleutel>/."""

    __tablename__ = "screening_pipelines"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # 'thesis' | 'list-<id>'
    kind: Mapped[str] = mapped_column(String(10), default="thesis")        # thesis | list
    list_id: Mapped[int | None] = mapped_column(ForeignKey("company_lists.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    thesis_name: Mapped[str] = mapped_column(String(120), default="")  # gebruikte criteria-set
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    scored_count: Mapped[int] = mapped_column(Integer, default=0)
    built_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    company_list: Mapped["CompanyList"] = relationship()


class Analysis(Base):
    """Bewaard screeningresultaat: pipeline-one-pager of individuele screening.
    Elke run die inhoudelijk verschilt van de vorige wordt als nieuwe rij
    bewaard, zodat de historiek per bedrijf blijft bestaan."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    enterprise_number: Mapped[str] = mapped_column(String(15), index=True)
    company_name: Mapped[str] = mapped_column(String(255), default="")
    kind: Mapped[str] = mapped_column(String(20), default="pipeline")  # pipeline | individueel
    report_md: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    created_by: Mapped["User"] = relationship()


class Setting(Base):
    """Sleutel/waarde-instellingen, o.a. laatst verwerkte KBO-extract en daily extract."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
