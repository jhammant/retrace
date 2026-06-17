"""Permission/capability status with grant guidance."""

from __future__ import annotations

from fastapi import APIRouter

from ..native.permissions import check_all

router = APIRouter(tags=["permissions"])


@router.get("/permissions")
def permissions() -> dict:
    checks = check_all()
    required_missing = [
        name for name, c in checks.items()
        if c["required"] and c["state"] != "granted"
    ]
    return {
        "permissions": checks,
        "all_required_granted": len(required_missing) == 0,
        "required_missing": required_missing,
    }
