"""数据源凭据加密、密钥管理与错误脱敏。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.schemas.common import AppError

# 连接串中常见的密码片段；命中后替换为 ***，避免错误信息泄漏凭据。
_PASSWORD_PATTERN = re.compile(r"(?i)(password|passwd|pwd)=[^\s;&]+")


def _read_key_file(path: Path) -> bytes | None:
    """读取密钥文件；文件不存在或为空返回 None。"""
    try:
        raw = path.read_bytes().strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AppError(500, "SOURCE_SECRET_KEY_UNAVAILABLE", "数据源密钥文件不可读") from exc
    if not raw:
        return None
    try:
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            os.chmod(path, 0o600)
    except OSError:
        pass
    return raw


def _create_key_file(path: Path) -> bytes:
    """以 0600 权限原子创建密钥文件；并发创建时回退读取已有文件。"""
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        existing = _read_key_file(path)
        if existing:
            return existing
        raise AppError(500, "SOURCE_SECRET_KEY_UNAVAILABLE", "数据源密钥文件为空或不可读")
    except OSError as exc:
        raise AppError(500, "SOURCE_SECRET_KEY_UNAVAILABLE", "数据源密钥文件创建失败") from exc
    try:
        os.write(fd, key)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def _load_or_create_key() -> bytes:
    """密钥优先级：SOURCE_SECRET_KEY 环境变量 → 本地 .source_key 文件。"""
    configured = (settings.source_secret_key or "").strip()
    if configured:
        return configured.encode("utf-8")
    path = settings.source_key_path
    existing = _read_key_file(path)
    if existing:
        return existing
    return _create_key_file(path)


def _fernet() -> Fernet:
    try:
        return Fernet(_load_or_create_key())
    except AppError:
        raise
    except Exception as exc:  # 非法密钥格式
        raise AppError(500, "SOURCE_SECRET_KEY_UNAVAILABLE", "数据源密钥格式无效") from exc


def encrypt_secret(plaintext: str) -> str:
    """加密明文凭据，返回 Fernet token。"""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    """解密 Fernet token；失败时返回固定错误，不暴露 token。"""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise AppError(422, "SOURCE_SECRET_INVALID", "数据源凭据无法解密，请删除后重建") from exc


def sanitize_error(message: str, secrets: Iterable[str] = ()) -> str:
    """脱敏错误信息：替换明文密码/常见密码参数并截断。"""
    text = str(message or "")
    for secret in secrets:
        value = str(secret or "")
        if value:
            text = text.replace(value, "***")
    text = _PASSWORD_PATTERN.sub(lambda match: f"{match.group(1)}=***", text)
    return text[:300]
