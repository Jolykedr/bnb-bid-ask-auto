#!/usr/bin/env python3
"""
CLI tool for managing license keys.

Usage:
    python keygen.py create --user "client@email.com" --days 30
    python keygen.py list
    python keygen.py reset-hwid --prefix "LL-A1B2"
    python keygen.py block --prefix "LL-A1B2" --reason "Refunded"
    python keygen.py unblock --prefix "LL-A1B2"
    python keygen.py extend --prefix "LL-A1B2" --days 30

Requires LICENSE_ADMIN_SECRET env var (or in .env file).
Can work directly with DB (local mode) or via API (remote mode).
"""

import argparse
import hashlib
import os
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass


def generate_key() -> str:
    raw = secrets.token_hex(10).upper()
    return f"LL-{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:]}"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.strip().encode()).hexdigest()


def cmd_create(args):
    from models import LicenseKey, SessionLocal, init_db
    init_db()

    license_key = generate_key()
    expires_at = datetime.utcnow() + timedelta(days=args.days)

    db = SessionLocal()
    try:
        lic = LicenseKey(
            key_hash=hash_key(license_key),
            key_prefix=license_key[:7],
            user_label=args.user,
            max_devices=args.devices,
            expires_at=expires_at,
        )
        db.add(lic)
        db.commit()
    finally:
        db.close()

    print("=" * 50)
    print("LICENSE KEY CREATED")
    print("=" * 50)
    print(f"  Key:     {license_key}")
    print(f"  User:    {args.user}")
    print(f"  Expires: {expires_at.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Days:    {args.days}")
    print(f"  Devices: {args.devices}")
    print("=" * 50)
    print("IMPORTANT: Save this key! It cannot be recovered.")
    print("=" * 50)


def cmd_list(args):
    from models import LicenseKey, SessionLocal, init_db
    init_db()

    db = SessionLocal()
    try:
        keys = db.query(LicenseKey).order_by(LicenseKey.created_at.desc()).all()
        now = datetime.utcnow()

        if not keys:
            print("No license keys found.")
            return

        print(f"{'Prefix':<10} {'User':<30} {'HWID':<20} {'Expires':<12} {'Days':<6} {'Status':<10} {'Validates':<10}")
        print("-" * 100)
        for k in keys:
            days_left = (k.expires_at - now).days if k.expires_at > now else 0
            hwid_short = (k.hwid[:16] + "...") if k.hwid else "not activated"
            status = "BLOCKED" if k.is_blocked else ("EXPIRED" if k.is_expired() else "OK")
            print(f"{k.key_prefix:<10} {k.user_label:<30} {hwid_short:<20} {k.expires_at.strftime('%Y-%m-%d'):<12} {days_left:<6} {status:<10} {k.validation_count:<10}")
    finally:
        db.close()


def cmd_reset_hwid(args):
    from models import LicenseKey, SessionLocal, init_db
    init_db()

    db = SessionLocal()
    try:
        lic = db.query(LicenseKey).filter(LicenseKey.key_prefix == args.prefix).first()
        if not lic:
            print(f"Key with prefix '{args.prefix}' not found.")
            sys.exit(1)

        old_hwid = lic.hwid
        lic.hwid = None
        db.commit()
        print(f"HWID reset for {lic.key_prefix} ({lic.user_label})")
        print(f"  Old HWID: {old_hwid}")
        print(f"  Client can now re-activate on a new device.")
    finally:
        db.close()


def cmd_block(args):
    from models import LicenseKey, SessionLocal, init_db
    init_db()

    db = SessionLocal()
    try:
        lic = db.query(LicenseKey).filter(LicenseKey.key_prefix == args.prefix).first()
        if not lic:
            print(f"Key with prefix '{args.prefix}' not found.")
            sys.exit(1)

        lic.is_blocked = True
        lic.block_reason = args.reason or "Blocked by admin"
        db.commit()
        print(f"BLOCKED {lic.key_prefix} ({lic.user_label}): {lic.block_reason}")
    finally:
        db.close()


def cmd_unblock(args):
    from models import LicenseKey, SessionLocal, init_db
    init_db()

    db = SessionLocal()
    try:
        lic = db.query(LicenseKey).filter(LicenseKey.key_prefix == args.prefix).first()
        if not lic:
            print(f"Key with prefix '{args.prefix}' not found.")
            sys.exit(1)

        lic.is_blocked = False
        lic.block_reason = None
        db.commit()
        print(f"UNBLOCKED {lic.key_prefix} ({lic.user_label})")
    finally:
        db.close()


def cmd_extend(args):
    from models import LicenseKey, SessionLocal, init_db
    init_db()

    db = SessionLocal()
    try:
        lic = db.query(LicenseKey).filter(LicenseKey.key_prefix == args.prefix).first()
        if not lic:
            print(f"Key with prefix '{args.prefix}' not found.")
            sys.exit(1)

        now = datetime.utcnow()
        base = max(lic.expires_at, now)
        lic.expires_at = base + timedelta(days=args.days)
        db.commit()

        days_left = (lic.expires_at - now).days
        print(f"Extended {lic.key_prefix} ({lic.user_label}) by {args.days} days")
        print(f"  New expiry: {lic.expires_at.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"  Days remaining: {days_left}")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="License Key Management CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Create a new license key")
    p_create.add_argument("--user", "-u", required=True, help="Client email or name")
    p_create.add_argument("--days", "-d", type=int, default=30, help="Days valid (default: 30)")
    p_create.add_argument("--devices", type=int, default=1, help="Max devices (default: 1)")

    # list
    sub.add_parser("list", help="List all license keys")

    # reset-hwid
    p_reset = sub.add_parser("reset-hwid", help="Reset HWID binding")
    p_reset.add_argument("--prefix", "-p", required=True, help="Key prefix (e.g. LL-A1B2)")

    # block
    p_block = sub.add_parser("block", help="Block a license key")
    p_block.add_argument("--prefix", "-p", required=True, help="Key prefix")
    p_block.add_argument("--reason", "-r", default="", help="Block reason")

    # unblock
    p_unblock = sub.add_parser("unblock", help="Unblock a license key")
    p_unblock.add_argument("--prefix", "-p", required=True, help="Key prefix")

    # extend
    p_extend = sub.add_parser("extend", help="Extend license expiration")
    p_extend.add_argument("--prefix", "-p", required=True, help="Key prefix")
    p_extend.add_argument("--days", "-d", type=int, required=True, help="Days to add")

    args = parser.parse_args()

    commands = {
        "create": cmd_create,
        "list": cmd_list,
        "reset-hwid": cmd_reset_hwid,
        "block": cmd_block,
        "unblock": cmd_unblock,
        "extend": cmd_extend,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
