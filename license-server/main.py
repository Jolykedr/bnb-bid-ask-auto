"""
License Server - FastAPI application.

Endpoints:
    POST /activate     - First-time activation (binds HWID)
    POST /validate     - Periodic license check
    POST /admin/keys   - Create new license key
    GET  /admin/keys   - List all keys
    POST /admin/reset-hwid  - Reset HWID binding
    POST /admin/block       - Block a key
    POST /admin/extend      - Extend expiry
"""

import hashlib
import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import ADMIN_SECRET, RATE_LIMIT_VALIDATE, RATE_LIMIT_ACTIVATE
from models import LicenseKey, get_db, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("license-server")

app = FastAPI(title="License Server", docs_url=None, redoc_url=None)


# --- Rate Limiting (in-memory, simple) ---

_rate_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(key: str, max_per_minute: int):
    now = time.time()
    window = now - 60
    hits = _rate_store[key]
    # Cleanup old entries
    _rate_store[key] = [t for t in hits if t > window]
    if len(_rate_store[key]) >= max_per_minute:
        raise HTTPException(status_code=429, detail="Too many requests")
    _rate_store[key].append(now)


# --- Helpers ---

def hash_key(license_key: str) -> str:
    return hashlib.sha256(license_key.strip().encode()).hexdigest()


def require_admin(request: Request):
    if not ADMIN_SECRET:
        raise HTTPException(status_code=500, detail="ADMIN_SECRET not configured")
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {ADMIN_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")


# --- Schemas ---

class ActivateRequest(BaseModel):
    license_key: str = Field(..., min_length=10)
    hwid: str = Field(..., min_length=8, max_length=128)


class ValidateRequest(BaseModel):
    license_key: str = Field(..., min_length=10)
    hwid: str = Field(..., min_length=8, max_length=128)


class ValidateResponse(BaseModel):
    valid: bool
    expires_at: Optional[str] = None
    days_remaining: Optional[int] = None
    error: Optional[str] = None


class AdminCreateKey(BaseModel):
    user_label: str = Field(..., min_length=1, max_length=256)
    days: int = Field(30, ge=1, le=3650)
    max_devices: int = Field(1, ge=1, le=5)


class AdminResetHwid(BaseModel):
    key_prefix: str = Field(..., min_length=4)


class AdminBlockKey(BaseModel):
    key_prefix: str = Field(..., min_length=4)
    reason: str = Field("", max_length=512)


class AdminExtendKey(BaseModel):
    key_prefix: str = Field(..., min_length=4)
    days: int = Field(..., ge=1, le=3650)


# --- Client Endpoints ---

@app.post("/activate", response_model=ValidateResponse)
def activate(req: ActivateRequest, request: Request, db: Session = Depends(get_db)):
    """First-time activation: binds license key to HWID."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"activate:{client_ip}", RATE_LIMIT_ACTIVATE)

    kh = hash_key(req.license_key)
    lic = db.query(LicenseKey).filter(LicenseKey.key_hash == kh).first()

    if not lic:
        logger.warning(f"Activate: unknown key from {client_ip}")
        return ValidateResponse(valid=False, error="Invalid license key")

    if lic.is_blocked:
        return ValidateResponse(valid=False, error=f"License blocked: {lic.block_reason or 'contact support'}")

    if not lic.is_active:
        return ValidateResponse(valid=False, error="License deactivated")

    if lic.is_expired():
        return ValidateResponse(valid=False, error="License expired")

    # Check HWID binding
    if lic.hwid is not None and lic.hwid != req.hwid:
        logger.warning(f"Activate: HWID mismatch for {lic.key_prefix} from {client_ip}")
        return ValidateResponse(valid=False, error="License already activated on another device. Contact support to reset.")

    # Bind HWID
    if lic.hwid is None:
        lic.hwid = req.hwid
        logger.info(f"Activated {lic.key_prefix} for HWID {req.hwid[:16]}... from {client_ip}")

    lic.last_validated_at = datetime.utcnow()
    lic.last_ip = client_ip
    lic.validation_count += 1
    db.commit()

    days_left = (lic.expires_at - datetime.utcnow()).days
    return ValidateResponse(
        valid=True,
        expires_at=lic.expires_at.isoformat(),
        days_remaining=days_left,
    )


@app.post("/validate", response_model=ValidateResponse)
def validate(req: ValidateRequest, request: Request, db: Session = Depends(get_db)):
    """Periodic license validation."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"validate:{client_ip}", RATE_LIMIT_VALIDATE)

    kh = hash_key(req.license_key)
    lic = db.query(LicenseKey).filter(LicenseKey.key_hash == kh).first()

    if not lic:
        return ValidateResponse(valid=False, error="Invalid license key")

    if lic.is_blocked:
        return ValidateResponse(valid=False, error=f"License blocked: {lic.block_reason or 'contact support'}")

    if not lic.is_active:
        return ValidateResponse(valid=False, error="License deactivated")

    if lic.is_expired():
        return ValidateResponse(valid=False, error="License expired")

    # HWID must match (if activated)
    if lic.hwid is not None and lic.hwid != req.hwid:
        logger.warning(f"Validate: HWID mismatch for {lic.key_prefix} from {client_ip}")
        return ValidateResponse(valid=False, error="HWID mismatch. Contact support.")

    # Not yet activated — activate now
    if lic.hwid is None:
        lic.hwid = req.hwid
        logger.info(f"Auto-activated {lic.key_prefix} via validate from {client_ip}")

    lic.last_validated_at = datetime.utcnow()
    lic.last_ip = client_ip
    lic.validation_count += 1
    db.commit()

    days_left = (lic.expires_at - datetime.utcnow()).days
    return ValidateResponse(
        valid=True,
        expires_at=lic.expires_at.isoformat(),
        days_remaining=days_left,
    )


# --- Admin Endpoints ---

@app.post("/admin/keys")
def admin_create_key(
    req: AdminCreateKey,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    """Create a new license key. Returns the raw key (only shown once)."""
    import secrets

    # Generate key: LL-XXXX-XXXX-XXXX-XXXX (20 hex chars)
    raw = secrets.token_hex(10).upper()
    license_key = f"LL-{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:]}"

    from datetime import timedelta
    expires_at = datetime.utcnow() + timedelta(days=req.days)

    lic = LicenseKey(
        key_hash=hash_key(license_key),
        key_prefix=license_key[:7],  # "LL-XXXX"
        user_label=req.user_label,
        max_devices=req.max_devices,
        expires_at=expires_at,
    )
    db.add(lic)
    db.commit()

    logger.info(f"Created key {lic.key_prefix} for {req.user_label}, expires {expires_at.date()}")

    return {
        "license_key": license_key,  # Raw key — show to admin only once
        "key_prefix": lic.key_prefix,
        "user_label": req.user_label,
        "expires_at": expires_at.isoformat(),
        "days": req.days,
    }


@app.get("/admin/keys")
def admin_list_keys(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    """List all license keys (without raw key values)."""
    keys = db.query(LicenseKey).order_by(LicenseKey.created_at.desc()).all()
    result = []
    now = datetime.utcnow()
    for k in keys:
        days_left = (k.expires_at - now).days if k.expires_at > now else 0
        result.append({
            "id": k.id,
            "key_prefix": k.key_prefix,
            "user_label": k.user_label,
            "hwid": k.hwid[:16] + "..." if k.hwid else None,
            "is_active": k.is_active,
            "is_blocked": k.is_blocked,
            "block_reason": k.block_reason,
            "created_at": k.created_at.isoformat(),
            "expires_at": k.expires_at.isoformat(),
            "days_remaining": days_left,
            "last_validated_at": k.last_validated_at.isoformat() if k.last_validated_at else None,
            "last_ip": k.last_ip,
            "validation_count": k.validation_count,
        })
    return {"keys": result, "total": len(result)}


@app.post("/admin/reset-hwid")
def admin_reset_hwid(
    req: AdminResetHwid,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    """Reset HWID binding so client can activate on a new device."""
    lic = db.query(LicenseKey).filter(LicenseKey.key_prefix == req.key_prefix).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Key not found")

    old_hwid = lic.hwid
    lic.hwid = None
    db.commit()

    logger.info(f"Reset HWID for {lic.key_prefix} (was: {old_hwid})")
    return {"status": "ok", "key_prefix": lic.key_prefix, "message": "HWID reset. Client can re-activate."}


@app.post("/admin/block")
def admin_block_key(
    req: AdminBlockKey,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    """Block a license key."""
    lic = db.query(LicenseKey).filter(LicenseKey.key_prefix == req.key_prefix).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Key not found")

    lic.is_blocked = True
    lic.block_reason = req.reason or "Blocked by admin"
    db.commit()

    logger.info(f"Blocked {lic.key_prefix}: {lic.block_reason}")
    return {"status": "ok", "key_prefix": lic.key_prefix, "message": f"Key blocked: {lic.block_reason}"}


@app.post("/admin/unblock")
def admin_unblock_key(
    req: AdminResetHwid,  # same schema — just key_prefix
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    """Unblock a license key."""
    lic = db.query(LicenseKey).filter(LicenseKey.key_prefix == req.key_prefix).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Key not found")

    lic.is_blocked = False
    lic.block_reason = None
    db.commit()

    logger.info(f"Unblocked {lic.key_prefix}")
    return {"status": "ok", "key_prefix": lic.key_prefix, "message": "Key unblocked"}


@app.post("/admin/extend")
def admin_extend_key(
    req: AdminExtendKey,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    """Extend license expiration."""
    from datetime import timedelta

    lic = db.query(LicenseKey).filter(LicenseKey.key_prefix == req.key_prefix).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Key not found")

    now = datetime.utcnow()
    # If expired, extend from now; otherwise from current expiry
    base = max(lic.expires_at, now)
    lic.expires_at = base + timedelta(days=req.days)
    db.commit()

    new_days = (lic.expires_at - now).days
    logger.info(f"Extended {lic.key_prefix} by {req.days} days, now expires {lic.expires_at.date()}")
    return {
        "status": "ok",
        "key_prefix": lic.key_prefix,
        "expires_at": lic.expires_at.isoformat(),
        "days_remaining": new_days,
    }


# --- Startup ---

@app.on_event("startup")
def on_startup():
    init_db()
    logger.info(f"License server started")
    if not ADMIN_SECRET:
        logger.warning("WARNING: LICENSE_ADMIN_SECRET not set! Admin endpoints disabled.")
