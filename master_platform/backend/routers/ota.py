from __future__ import annotations

from fastapi import APIRouter, Depends

from ..models import APIKey
from ..security.auth import require_scopes
from ..security import signing


router = APIRouter(prefix="/api/ota", tags=["ota"])


@router.get("/pubkey")
async def get_pubkey() -> dict:
    """Public, intentionally unauthenticated. Pico devices fetch this once
    at provisioning to embed the OTA verification key.
    """
    return {
        "alg": "ed25519",
        "kid": signing.signing_kid(),
        "public_key_b64": signing.public_key_b64(),
        "public_key_hex": signing.public_key_hex(),
    }


@router.post("/rotate-key", dependencies=[Depends(require_scopes("admin"))])
async def rotate_signing_key(
    _: APIKey = Depends(require_scopes("admin")),
) -> dict:
    """Rotate the OTA signing key. New key id is reported; the old public
    key remains valid for verifying previously-signed manifests."""
    new_kid = signing.rotate()
    return {
        "ok": True,
        "kid": new_kid,
        "public_key_b64": signing.public_key_b64(),
    }
