"""Mô hình dữ liệu: 1 phụ huynh : n trẻ : m thiết bị. Mọi mốc thời gian là epoch (giây, UTC)."""
from __future__ import annotations

import time
from typing import Optional

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, LargeBinary, String, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .security import Vault


def now() -> int:
    return int(time.time())


class EncText(TypeDecorator):
    """Chuỗi được mã hoá AES-GCM khi lưu."""
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else Vault.seal(value.encode())

    def process_result_value(self, value, dialect):
        return None if value is None else Vault.open(value).decode()


class EncBytes(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else Vault.seal(value)

    def process_result_value(self, value, dialect):
        return None if value is None else Vault.open(value)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    pw_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="parent")  # parent | child_view | admin
    child_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # chỉ với child_view
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # phụ huynh tạo tài khoản child_view
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[int] = mapped_column(BigInteger, default=0)
    disabled: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class Child(Base):
    __tablename__ = "children"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    age: Mapped[int] = mapped_column(Integer, default=9)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    fingerprint_hash: Mapped[str] = mapped_column(String(64), default="")
    refresh_hash: Mapped[str] = mapped_column(String(64), default="")      # chỉ lưu băm refresh token
    policy_key: Mapped[bytes] = mapped_column(EncBytes)                    # khóa HMAC chính sách (AES-GCM)
    status: Mapped[str] = mapped_column(String(12), default="active")      # active | revoked
    enrolled_at: Mapped[int] = mapped_column(BigInteger, default=now)
    consent_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    consent_at: Mapped[int] = mapped_column(BigInteger, default=0)
    last_seen: Mapped[int] = mapped_column(BigInteger, default=0)
    state: Mapped[str] = mapped_column(String(12), default="ok")            # ok | grace | locked | suspended
    ui_ok: Mapped[bool] = mapped_column(default=True)
    agent_version: Mapped[str] = mapped_column(String(20), default="")
    used_today_sec: Mapped[int] = mapped_column(Integer, default=0)
    remaining_sec: Mapped[int] = mapped_column(Integer, default=0)
    policy_applied: Mapped[int] = mapped_column(Integer, default=0)


class EnrollCode(Base):
    __tablename__ = "enroll_codes"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    child_id: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    consent_at: Mapped[int] = mapped_column(BigInteger)
    used: Mapped[bool] = mapped_column(default=False)


class PolicyRow(Base):
    __tablename__ = "policies"
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    doc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now)
    updated_by: Mapped[str] = mapped_column(String(255), default="system")


class Command(Base):
    __tablename__ = "commands"
    __table_args__ = {"sqlite_autoincrement": True}   # id lệnh không bao giờ tái sử dụng (Agent khử trùng theo id)
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(20))
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10), default="pending")      # pending | sent | acked
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)
    acked_at: Mapped[int] = mapped_column(BigInteger, default=0)


class ChildRequest(Base):
    __tablename__ = "child_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(Integer, index=True)
    device_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(12))                           # add-time | false-block
    subject: Mapped[str] = mapped_column(String(120), default="")
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[Optional[str]] = mapped_column(EncText, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")      # pending | approved | denied
    response: Mapped[Optional[str]] = mapped_column(EncText, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)
    decided_at: Mapped[int] = mapped_column(BigInteger, default=0)
    decided_by: Mapped[str] = mapped_column(String(255), default="")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    idem_key: Mapped[str] = mapped_column(String(36), unique=True)
    ts: Mapped[int] = mapped_column(BigInteger)
    device_id: Mapped[str] = mapped_column(String(36))
    child_id: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(120), default="")
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    policy_id: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (Index("ix_events_child_ts", "child_id", "ts"),)


class DailyUsage(Base):
    __tablename__ = "daily_usage"
    device_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    child_id: Mapped[int] = mapped_column(Integer, index=True)
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    groups: Mapped[str] = mapped_column(Text, default="{}")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[int] = mapped_column(BigInteger, default=now)
    actor: Mapped[str] = mapped_column(String(255))
    actor_ip: Mapped[str] = mapped_column(String(64), default="")
    child_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(40))
    field: Mapped[str] = mapped_column(String(60), default="")
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")


class WebSession(Base):
    __tablename__ = "web_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)
    expires_at: Mapped[int] = mapped_column(BigInteger)
