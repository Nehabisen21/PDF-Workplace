import base64
import hashlib
import hmac
import io
import logging
import mimetypes
import os
import re
import threading
import time
import zipfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from authlib.integrations.base_client import OAuthError
from authlib.integrations.base_client.errors import MismatchingStateError
from authlib.integrations.starlette_client import OAuth
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
import pymupdf
from pydantic import BaseModel, Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from .db import Base, engine, get_db
from .conversions import convert_documents
from .models import Folder, Invitation, Job, SavedSignature, StoredDocument, Team, User
from .pdf_ops import (
    MAX_PDF_PAGES,
    MAX_RENDERED_PIXELS,
    MAX_UPLOAD_BYTES,
    MAX_TOTAL_UPLOAD_BYTES,
    SUPPORTED_OPERATIONS,
    cv2,
    _get_form_fields,
    process_pdf,
)
from .signing import (
    MAX_CERTIFICATE_BYTES,
    apply_certificate_signature,
    apply_personal_signature,
    decode_signature_image,
)


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("paperwork")

SESSION_SECRET = os.getenv("SESSION_SECRET", "")
if len(SESSION_SECRET) < 32:
    raise RuntimeError("SESSION_SECRET must be set to a random value of at least 32 characters.")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
TENANT_ID = os.getenv("ENTRA_TENANT_ID", "").strip()
CLIENT_ID = os.getenv("ENTRA_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("ENTRA_CLIENT_SECRET", "").strip()
STORAGE_ENCRYPTION_KEY = os.getenv("STORAGE_ENCRYPTION_KEY", "").strip()
MAX_STORAGE_BYTES_PER_USER = int(os.getenv("MAX_STORAGE_BYTES_PER_USER", str(1024 * 1024 * 1024)))
MAX_FOLDERS_PER_USER = 100
MAX_FOLDER_DOCUMENTS = 100
MAX_FOLDER_OUTPUT_BYTES = 200 * 1024 * 1024
MAX_SAVED_SIGNATURES_PER_USER = 20
INITIAL_ADMIN_EMAILS = frozenset(
    email.strip().casefold()
    for email in os.getenv("INITIAL_ADMIN_EMAILS", "").split(",")
    if email.strip()
)
LOCAL_ADMIN_USERNAME = os.getenv("LOCAL_ADMIN_USERNAME", "").strip()
LOCAL_ADMIN_EMAIL = os.getenv("LOCAL_ADMIN_EMAIL", "").strip().casefold()
_LOCAL_ADMIN_PASSWORD = os.getenv("LOCAL_ADMIN_PASSWORD", "")
if _LOCAL_ADMIN_PASSWORD and len(_LOCAL_ADMIN_PASSWORD) < 12:
    raise RuntimeError("LOCAL_ADMIN_PASSWORD must contain at least 12 characters.")
LOCAL_ADMIN_PASSWORD_SALT = os.urandom(16)
LOCAL_ADMIN_PASSWORD_DIGEST = (
    hashlib.scrypt(
        _LOCAL_ADMIN_PASSWORD.encode("utf-8"),
        salt=LOCAL_ADMIN_PASSWORD_SALT,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )
    if _LOCAL_ADMIN_PASSWORD
    else b""
)
del _LOCAL_ADMIN_PASSWORD
LOCAL_ADMIN_MAX_ATTEMPTS = 5
LOCAL_ADMIN_LOCKOUT_SECONDS = 15 * 60
_local_admin_attempts: OrderedDict[str, tuple[int, float, float]] = OrderedDict()
_local_admin_attempts_lock = threading.Lock()
ADMIN_ROLES = frozenset({"org_owner", "admin"})
USER_ROLES = frozenset({"org_owner", "admin", "team_lead", "member"})
EDITOR_OPERATIONS = frozenset(
    {
        "crop",
        "rotate",
        "reorder",
        "extract",
        "watermark",
        "stamp",
        "redact",
        "flatten",
        "unlock_forms",
        "remove_pages",
        "remove_blank_pages",
        "remove_annotations",
        "remove_images",
        "remove_certificate_sign",
        "auto_form_detection",
        "add_text",
        "add_image",
        "annotate",
        "form_editor",
        "adjust_colors",
        "overlay",
        "color_transform",
        "personal_signature",
        "digital_signature",
    }
)
PROCESSOR_OPERATIONS = frozenset(SUPPORTED_OPERATIONS - EDITOR_OPERATIONS)
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", str(Path(__file__).resolve().parents[2] / "frontend")))


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class UserRoleUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(org_owner|admin|team_lead|member)$")
    is_active: bool | None = None
    team_id: int | None = Field(default=None, ge=1)
    can_edit: bool | None = None
    can_process: bool | None = None


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class InvitationCreate(BaseModel):
    emails: list[str] = Field(min_length=1)
    team_id: int = Field(ge=1)
    role: str = Field(default="member", pattern="^(org_owner|admin|team_lead|member)$")
    can_edit: bool = True
    can_process: bool = True


@asynccontextmanager
async def lifespan(_: FastAPI):
    _migrate_user_admin_columns()
    Base.metadata.create_all(bind=engine)
    _ensure_default_team()
    _bootstrap_configured_owners()
    if not INITIAL_ADMIN_EMAILS:
        logger.warning(
            "INITIAL_ADMIN_EMAILS is empty; user administration requires an active "
            "org_owner and no owner is bootstrapped automatically."
        )
    yield


def _migrate_user_admin_columns(bind=None) -> None:
    bind = bind or engine
    Team.__table__.create(bind, checkfirst=True)
    if not inspect(bind).has_table("users"):
        return
    columns = {column["name"] for column in inspect(bind).get_columns("users")}
    statements = []
    if "role" not in columns:
        statements.append(
            "ALTER TABLE users ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'member'"
        )
    if "is_active" not in columns:
        statements.append(
            "ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE"
        )
    if "last_login_at" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP WITH TIME ZONE")
    if "team_id" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN team_id INTEGER REFERENCES teams(id)")
    if "can_edit" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN can_edit BOOLEAN NOT NULL DEFAULT TRUE")
    if "can_process" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN can_process BOOLEAN NOT NULL DEFAULT TRUE")
    statements.extend(
        [
            "CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)",
            "CREATE INDEX IF NOT EXISTS ix_users_role_active ON users (role, is_active)",
            "CREATE INDEX IF NOT EXISTS ix_users_team_id ON users (team_id)",
        ]
    )
    if statements:
        with bind.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))


def _ensure_default_team(bind=None) -> None:
    with Session(bind or engine) as db:
        team = db.query(Team).filter_by(name="Default").one_or_none()
        if team is None:
            team = Team(name="Default")
            db.add(team)
            db.flush()
        db.query(User).filter(User.team_id.is_(None)).update(
            {User.team_id: team.id}, synchronize_session=False
        )
        db.commit()


def _apply_pending_invitation(db: Session, user: User, email: str | None) -> None:
    if not email:
        return
    invitation = (
        db.query(Invitation)
        .filter(
            func.lower(Invitation.email) == email.casefold(),
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .one_or_none()
    )
    if invitation is None:
        return
    if user.role != "org_owner" and email.casefold() not in INITIAL_ADMIN_EMAILS and not (
        _configured_local_admin() and email.casefold() == LOCAL_ADMIN_EMAIL
    ):
        user.role = invitation.role
        user.team_id = invitation.team_id
        user.can_edit = invitation.can_edit
        user.can_process = invitation.can_process
    invitation.accepted_at = datetime.now(timezone.utc)


def _bootstrap_configured_owners(bind=None) -> None:
    if not INITIAL_ADMIN_EMAILS:
        return
    with Session(bind or engine) as db:
        db.query(User).filter(func.lower(User.email).in_(INITIAL_ADMIN_EMAILS)).update(
            {User.role: "org_owner"}, synchronize_session=False
        )
        db.commit()


app = FastAPI(title="PDF Workplace", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="paperwork_session",
    max_age=8 * 60 * 60,
    same_site="lax",
    https_only=os.getenv("COOKIE_SECURE", "true").lower() == "true",
)

oauth = OAuth()
if TENANT_ID and CLIENT_ID and CLIENT_SECRET:
    oauth.register(
        name="entra",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        server_metadata_url=(
            f"https://login.microsoftonline.com/{TENANT_ID}/v2.0/"
            ".well-known/openid-configuration"
        ),
        client_kwargs={"scope": "openid profile email"},
    )


# authlib keeps a single pending OAuth state per provider in the session and
# drops the previous one on every new /auth/login request. A browser can send
# that request more than once for one user (double click on the sign-in button,
# a second tab, link prefetch, reload during the redirect). The round trip that
# comes back with the state that was dropped then fails with
# "MismatchingStateError: CSRF Warning! State not equal in request and response",
# which surfaced as an HTTP 500 on /auth/callback.
# Keep a few recent states so overlapping sign-in attempts stay independent.
_MAX_PENDING_OAUTH_STATES = 4

try:
    from authlib.integrations.starlette_client.integration import StarletteIntegration

    _authlib_set_state_data = StarletteIntegration.set_state_data

    async def _set_state_data_keep_pending(self, session, state, data):
        """Store the new state but keep the previous few pending ones too."""
        key_prefix = f"_state_{self.name}_"
        kept: dict[str, object] = {}
        if session is not None:
            kept = {
                key: value
                for key, value in list(session.items())
                if key.startswith(key_prefix)
            }
        await _authlib_set_state_data(self, session, state, data)
        if session is None:
            return
        for key, value in kept.items():
            if key not in session:
                session[key] = value
        pending = [key for key in session if key.startswith(key_prefix)]
        while len(pending) > _MAX_PENDING_OAUTH_STATES:
            session.pop(pending.pop(0), None)

    StarletteIntegration.set_state_data = _set_state_data_keep_pending
except Exception:  # pragma: no cover - never block sign-in if authlib changes
    logger.warning("Could not keep multiple OAuth states pending", exc_info=True)


def _auth_error_page(message: str, status_code: int = 400) -> HTMLResponse:
    return HTMLResponse(
        status_code=status_code,
        content=(
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Sign-in problem</title>"
            "<style>body{margin:0;min-height:100vh;display:grid;place-items:center;"
            "background:#0f172a;color:#e2e8f0;font-family:system-ui,sans-serif}"
            "main{max-width:34rem;padding:2rem;text-align:center;line-height:1.5}"
            "a{display:inline-block;margin-top:1.25rem;padding:.65rem 1.3rem;"
            "border-radius:.55rem;background:#2563eb;color:#fff;text-decoration:none}"
            "</style></head><body><main><h1>Sign-in problem</h1>"
            f"<p>{message}</p><a href=\"/auth/login\">Try signing in again</a>"
            "</main></body></html>"
        ),
    )


def _configured_identity_provider() -> bool:
    return bool(TENANT_ID and CLIENT_ID and CLIENT_SECRET)


def _current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    user = db.get(User, user_id)
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Your session has expired. Sign in again.")
    if not user.is_active:
        request.session.clear()
        raise HTTPException(status_code=403, detail="Your account has been deactivated.")
    if (
        _configured_local_admin()
        and user.email
        and user.email.casefold() == LOCAL_ADMIN_EMAIL
        and request.session.get("auth_method") != "local"
    ):
        request.session.clear()
        raise HTTPException(
            status_code=403, detail="Use the local administrator username and password to sign in."
        )
    return user


def _require_admin(user: User = Depends(_current_user)) -> User:
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user


def _require_capability(capability: str):
    def require(user: User = Depends(_current_user)) -> User:
        if not getattr(user, f"can_{capability}"):
            label = "Editor" if capability == "edit" else "Processor"
            raise HTTPException(
                status_code=403,
                detail=f"The {label} capability is required for this operation.",
            )
        return user

    return require


_require_editor = _require_capability("edit")


def _require_operation_capability(user: User, operation: str) -> None:
    if operation not in EDITOR_OPERATIONS and operation not in PROCESSOR_OPERATIONS:
        raise HTTPException(status_code=400, detail="This PDF operation is not supported.")
    capability = "edit" if operation in EDITOR_OPERATIONS else "process"
    if not getattr(user, f"can_{capability}"):
        label = "Editor" if capability == "edit" else "Processor"
        raise HTTPException(
            status_code=403,
            detail=f"The {label} capability is required for this operation.",
        )


def _storage_cipher() -> Fernet:
    if not STORAGE_ENCRYPTION_KEY:
        raise HTTPException(
            status_code=503,
            detail="Encrypted folder storage is not configured. Set STORAGE_ENCRYPTION_KEY.",
        )
    try:
        return Fernet(STORAGE_ENCRYPTION_KEY.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise HTTPException(
            status_code=503, detail="STORAGE_ENCRYPTION_KEY is invalid."
        ) from exc


def _owned_folder(db: Session, user_id: int, folder_id: int) -> Folder:
    folder = db.query(Folder).filter_by(id=folder_id, user_id=user_id).one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found.")
    return folder


def _clean_filename(filename: str | None) -> str:
    value = (filename or "document.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    value = re.sub(r"[\x00-\x1f\x7f]", "", value).strip()
    if not value.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Only PDF files can be stored in a folder.")
    if len(value) > 255:
        value = f"{value[:-4][:251]}{value[-4:]}"
    return value


def _validate_stored_pdf(data: bytes) -> None:
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if not reader.is_encrypted:
            page_count = len(reader.pages)
            if not page_count:
                raise HTTPException(status_code=400, detail="A stored PDF must contain at least one page.")
            if page_count > MAX_PDF_PAGES:
                raise HTTPException(
                    status_code=400, detail=f"A PDF cannot exceed {MAX_PDF_PAGES} pages."
                )
    except (PdfReadError, PdfStreamError) as exc:
        raise HTTPException(status_code=400, detail="An uploaded file is not a readable PDF.") from exc


async def _read_uploads(
    files: list[UploadFile], *, images_only: bool = False, allow_any: bool = False
) -> list[tuple[str, bytes]]:
    if len(files) > 20:
        raise HTTPException(status_code=413, detail="Upload no more than 20 files at a time.")
    documents: list[tuple[str, bytes]] = []
    total_size = 0
    for upload in files:
        try:
            extension = Path(upload.filename or "").suffix.lower()
            if images_only:
                if extension not in {".png", ".jpg", ".jpeg"}:
                    raise HTTPException(
                        status_code=415, detail="Only PNG and JPEG images are supported."
                    )
            elif allow_any:
                if not extension:
                    raise HTTPException(status_code=415, detail="Every file must have a format extension.")
            elif upload.content_type not in {"application/pdf", "application/octet-stream"} and (
                extension != ".pdf"
            ):
                raise HTTPException(status_code=415, detail="Only PDF files are supported.")
            if not images_only and not allow_any:
                _clean_filename(upload.filename)
            data = await upload.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413, detail="An uploaded file exceeds the per-file size limit."
                )
            total_size += len(data)
            if total_size > MAX_TOTAL_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="The total upload exceeds the size limit.")
            documents.append((upload.filename or "document.pdf", data))
        finally:
            await upload.close()
    return documents


def _folder_json(folder: Folder) -> dict[str, int | str]:
    return {"id": folder.id, "name": folder.name, "document_count": len(folder.documents)}


def _document_json(document: StoredDocument) -> dict[str, int | str]:
    return {
        "id": document.id,
        "name": document.filename,
        "size_bytes": document.size_bytes,
        "created_at": document.created_at.astimezone(timezone.utc).isoformat(),
    }


def _storage_usage(db: Session, user_id: int) -> tuple[int, int]:
    query = db.query(StoredDocument).join(Folder).filter(Folder.user_id == user_id)
    total_bytes = db.query(func.coalesce(func.sum(StoredDocument.size_bytes), 0)).join(
        Folder
    ).filter(Folder.user_id == user_id).scalar()
    return int(total_bytes or 0), int(query.count())


@app.get("/healthz")
def healthz(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


def _configured_local_admin() -> bool:
    return bool(LOCAL_ADMIN_USERNAME and LOCAL_ADMIN_EMAIL and LOCAL_ADMIN_PASSWORD_DIGEST)


@app.get("/api/auth/options")
def auth_options() -> dict[str, bool]:
    return {
        "entra_enabled": _configured_identity_provider(),
        "local_admin_enabled": _configured_local_admin(),
    }


@app.get("/auth/login")
async def login(request: Request):
    if not _configured_identity_provider():
        raise HTTPException(
            status_code=503,
            detail="Microsoft Entra sign-in is not configured. Set the ENTRA_* variables.",
        )
    pending = [key for key in request.session if key.startswith("_state_entra_")]
    logger.info("Starting Microsoft Entra sign-in (pending states: %d)", len(pending))
    return await oauth.entra.authorize_redirect(request, f"{PUBLIC_BASE_URL}/auth/callback")


@app.get("/auth/callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    if not _configured_identity_provider():
        raise HTTPException(status_code=503, detail="Microsoft Entra sign-in is not configured.")
    try:
        token = await oauth.entra.authorize_access_token(request)
    except MismatchingStateError:
        pending = [key for key in request.session if key.startswith("_state_entra_")]
        logger.warning(
            "Entra callback state mismatch (session cookie received: %s, pending states: %d, "
            "state parameter present: %s)",
            "paperwork_session" in request.cookies,
            len(pending),
            bool(request.query_params.get("state")),
        )
        return _auth_error_page(
            "Your sign-in session expired or was replaced by another sign-in attempt. "
            "Start the sign-in again from this page."
        )
    except OAuthError as exc:
        logger.warning("Entra token exchange failed: %s", exc)
        return _auth_error_page(
            "Microsoft could not complete the sign-in. Start the sign-in again from this page.",
            status_code=502,
        )
    claims = token.get("userinfo")
    if not claims:
        raise HTTPException(status_code=401, detail="The identity provider returned no user profile.")
    subject = claims.get("sub")
    issuer = claims.get("iss")
    if not isinstance(subject, str) or not isinstance(issuer, str):
        raise HTTPException(status_code=401, detail="The identity provider profile is incomplete.")

    user = db.query(User).filter_by(issuer=issuer, subject=subject).one_or_none()
    claim_emails = [
        claim for claim in (claims.get("email"), claims.get("preferred_username"))
        if isinstance(claim, str)
    ]
    if (
        _configured_local_admin()
        and (
            any(email.casefold() == LOCAL_ADMIN_EMAIL for email in claim_emails)
            or (
                user is not None
                and user.email
                and user.email.casefold() == LOCAL_ADMIN_EMAIL
            )
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="This account uses local administrator sign-in, not Entra SSO.",
        )
    email = claim_emails[0] if claim_emails else None
    if user is not None and not user.is_active:
        db.rollback()
        raise HTTPException(status_code=403, detail="Your account has been deactivated.")
    display_name = claims.get("name") or email or subject
    default_team = db.query(Team).filter_by(name="Default").one()
    if user is None:
        user = User(
            issuer=issuer,
            subject=subject,
            email=email,
            display_name=display_name,
            role="org_owner" if email and email.casefold() in INITIAL_ADMIN_EMAILS else "member",
            team_id=default_team.id,
        )
        db.add(user)
    else:
        user.email = email
        user.display_name = display_name
        if email and email.casefold() in INITIAL_ADMIN_EMAILS:
            user.role = "org_owner"
        if user.team_id is None:
            user.team_id = default_team.id
    _apply_pending_invitation(db, user, email)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["auth_method"] = "entra"
    return RedirectResponse("/", status_code=303)


def _local_admin_lockout_remaining(client_key: str) -> int:
    now = time.monotonic()
    with _local_admin_attempts_lock:
        attempt = _local_admin_attempts.get(client_key)
        if attempt is None:
            return 0
        count, window_start, locked_until = attempt
        window_end = window_start + LOCAL_ADMIN_LOCKOUT_SECONDS
        if now >= window_end:
            _local_admin_attempts.pop(client_key, None)
            return 0
        if count >= LOCAL_ADMIN_MAX_ATTEMPTS or now < locked_until:
            return max(1, int(window_end - now) + 1)
        return 0


def _record_local_admin_failure(client_key: str) -> None:
    now = time.monotonic()
    with _local_admin_attempts_lock:
        count, window_start, _ = _local_admin_attempts.get(client_key, (0, now, 0.0))
        if now >= window_start + LOCAL_ADMIN_LOCKOUT_SECONDS:
            count, window_start = 0, now
        count += 1
        locked_until = (
            window_start + LOCAL_ADMIN_LOCKOUT_SECONDS
            if count >= LOCAL_ADMIN_MAX_ATTEMPTS
            else 0.0
        )
        _local_admin_attempts[client_key] = (count, window_start, locked_until)
        _local_admin_attempts.move_to_end(client_key)
        while len(_local_admin_attempts) > 10_000:
            _local_admin_attempts.popitem(last=False)


@app.post("/auth/local-login")
def local_admin_login(
    request: Request,
    username: str = Form(max_length=255),
    password: str = Form(max_length=1024),
    db: Session = Depends(get_db),
):
    if not _configured_local_admin():
        raise HTTPException(
            status_code=503,
            detail="Local admin login is not configured. Set LOCAL_ADMIN_USERNAME, "
            "LOCAL_ADMIN_EMAIL, and LOCAL_ADMIN_PASSWORD in .env.",
        )
    client_key = request.client.host if request.client else "unknown"
    retry_after = _local_admin_lockout_remaining(client_key)
    if retry_after:
        retry_minutes = max(1, (retry_after + 59) // 60)
        minute_label = "minute" if retry_minutes == 1 else "minutes"
        raise HTTPException(
            status_code=429,
            detail=f"Too many sign-in attempts. Try again in {retry_minutes} {minute_label}.",
            headers={"Retry-After": str(retry_after)},
        )

    password_digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=LOCAL_ADMIN_PASSWORD_SALT,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )
    username_matches = hmac.compare_digest(
        username.encode("utf-8"), LOCAL_ADMIN_USERNAME.encode("utf-8")
    )
    password_matches = hmac.compare_digest(password_digest, LOCAL_ADMIN_PASSWORD_DIGEST)
    if not username_matches or not password_matches:
        _record_local_admin_failure(client_key)
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    local_matches = (
        db.query(User)
        .filter(func.lower(User.email) == LOCAL_ADMIN_EMAIL)
        .order_by(User.id)
        .limit(2)
        .all()
    )
    if len(local_matches) > 1:
        raise HTTPException(
            status_code=409,
            detail="Multiple accounts use the configured local admin email; resolve the duplicates first.",
        )
    user = local_matches[0] if local_matches else None
    default_team = db.query(Team).filter_by(name="Default").one()
    if user is None:
        user = User(
            issuer="local-admin",
            subject=LOCAL_ADMIN_USERNAME,
            email=LOCAL_ADMIN_EMAIL,
            display_name=LOCAL_ADMIN_USERNAME,
            role="org_owner",
            team_id=default_team.id,
        )
        db.add(user)
    elif not user.is_active:
        raise HTTPException(status_code=403, detail="The local administrator account is inactive.")
    else:
        user.role = "org_owner"
        if user.team_id is None:
            user.team_id = default_team.id
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    with _local_admin_attempts_lock:
        _local_admin_attempts.pop(client_key, None)
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["auth_method"] = "local"
    return RedirectResponse("/", status_code=303)


@app.get("/auth/logout")
def logout():
    return Response(
        content=(
            "<!doctype html><meta charset=utf-8><title>Sign out</title>"
            "<form method=post action=/auth/logout><button>Sign out</button></form>"
        ),
        media_type="text/html",
    )


@app.post("/auth/logout")
def logout_post(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/api/me")
def me(user: User = Depends(_current_user)) -> dict[
    str, str | int | bool | None | list[str]
]:
    return {
        "id": user.id,
        "name": user.display_name,
        "email": user.email,
        "role": user.role,
        "team_id": user.team_id,
        "team": user.team.name if user.team else None,
        "can_edit": user.can_edit,
        "can_process": user.can_process,
        "edit_operations": sorted(EDITOR_OPERATIONS),
        "process_operations": sorted(PROCESSOR_OPERATIONS),
        "vision_enabled": cv2 is not None,
    }


def _admin_user_json(user: User) -> dict[str, str | int | bool | None]:
    is_bootstrap_owner = bool(
        user.email
        and (
            user.email.casefold() in INITIAL_ADMIN_EMAILS
            or (
                _configured_local_admin()
                and user.email.casefold() == LOCAL_ADMIN_EMAIL
            )
        )
    )
    return {
        "id": user.id,
        "email": user.email,
        "name": user.display_name,
        "role": user.role,
        "team_id": user.team_id,
        "team": user.team.name if user.team else None,
        "can_edit": user.can_edit,
        "can_process": user.can_process,
        "is_active": user.is_active,
        "is_bootstrap_owner": is_bootstrap_owner,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@app.get("/api/admin/users")
def list_users(
    search: str = Query(default="", max_length=200),
    team_id: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    _: User = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> dict[str, int | list[dict[str, str | int | bool | None]]]:
    users_query = db.query(User)
    if team_id is not None:
        users_query = users_query.filter(User.team_id == team_id)
    search = search.strip()
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        users_query = users_query.filter(
            User.email.ilike(pattern, escape="\\")
            | User.display_name.ilike(pattern, escape="\\")
        )
    total = users_query.count()
    active_count = users_query.filter(User.is_active.is_(True)).count()
    users = (
        users_query.options(joinedload(User.team))
        .order_by(User.created_at.desc(), User.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "users": [_admin_user_json(user) for user in users],
        "total": total,
        "active_count": active_count,
        "offset": offset,
        "limit": limit,
    }


@app.get("/api/admin/teams")
def list_teams(
    _: User = Depends(_require_admin), db: Session = Depends(get_db)
) -> list[dict[str, int | str]]:
    teams = db.query(Team).order_by(Team.name, Team.id).all()
    member_counts = dict(
        db.query(User.team_id, func.count(User.id)).group_by(User.team_id).all()
    )
    pending_counts = dict(
        db.query(Invitation.team_id, func.count(Invitation.id))
        .filter(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))
        .group_by(Invitation.team_id)
        .all()
    )
    return [
        {
            "id": team.id,
            "name": team.name,
            "member_count": member_counts.get(team.id, 0),
            "pending_invitation_count": pending_counts.get(team.id, 0),
        }
        for team in teams
    ]


@app.post("/api/admin/teams", status_code=201)
def create_team(
    body: TeamCreate,
    _: User = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Team name cannot be blank.")
    if db.query(Team).filter(func.lower(Team.name) == name.casefold()).first():
        raise HTTPException(status_code=409, detail="A team with this name already exists.")
    team = Team(name=name)
    db.add(team)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A team with this name already exists.") from exc
    db.refresh(team)
    return {"id": team.id, "name": team.name}


@app.get("/api/admin/invitations")
def list_invitations(
    _: User = Depends(_require_admin), db: Session = Depends(get_db)
) -> list[dict[str, int | str]]:
    invitations = (
        db.query(Invitation)
        .options(joinedload(Invitation.team))
        .filter(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))
        .order_by(Invitation.created_at.desc(), Invitation.id.desc())
        .all()
    )
    return [
        {
            "id": invitation.id,
            "email": invitation.email,
            "team_id": invitation.team_id,
            "team": invitation.team.name,
            "role": invitation.role,
            "can_edit": invitation.can_edit,
            "can_process": invitation.can_process,
            "created_at": invitation.created_at.isoformat(),
        }
        for invitation in invitations
    ]


@app.post("/api/admin/invitations", status_code=201)
def create_invitations(
    body: InvitationCreate,
    admin: User = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> dict[str, int | list[dict[str, int | str]]]:
    team = db.get(Team, body.team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found.")
    if body.role in ADMIN_ROLES and admin.role != "org_owner":
        raise HTTPException(status_code=403, detail="Only an organization owner can invite admins.")

    emails = []
    for submitted_email in body.emails:
        email = submitted_email.strip().casefold()
        if len(email) > 320 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise HTTPException(status_code=422, detail="Enter valid email addresses for invitations.")
        if email in INITIAL_ADMIN_EMAILS or (
            _configured_local_admin() and email == LOCAL_ADMIN_EMAIL
        ):
            raise HTTPException(
                status_code=409, detail="Configured organization owners are managed in server settings."
            )
        if email not in emails:
            emails.append(email)

    existing_users = {
        email.casefold()
        for (email,) in db.query(User.email)
        .filter(func.lower(User.email).in_(emails))
        .all()
        if email
    }
    if existing_users:
        raise HTTPException(
            status_code=409,
            detail=f"These people already have accounts; update them in Users: {', '.join(sorted(existing_users))}",
        )

    existing_invitations = {
        invitation.email: invitation
        for invitation in db.query(Invitation).filter(Invitation.email.in_(emails)).all()
    }
    pending = [
        email
        for email, invitation in existing_invitations.items()
        if invitation.accepted_at is None and invitation.revoked_at is None
    ]
    if pending:
        raise HTTPException(
            status_code=409,
            detail=f"These invitations are already pending: {', '.join(sorted(pending))}",
        )

    created = []
    for email in emails:
        invitation = existing_invitations.get(email)
        if invitation is None:
            invitation = Invitation(email=email, invited_by_id=admin.id)
            db.add(invitation)
        invitation.team_id = team.id
        invitation.role = body.role
        invitation.can_edit = body.can_edit
        invitation.can_process = body.can_process
        invitation.invited_by_id = admin.id
        invitation.created_at = datetime.now(timezone.utc)
        invitation.accepted_at = None
        invitation.revoked_at = None
        created.append(invitation)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="One or more invitations were created concurrently."
        ) from exc
    for invitation in created:
        db.refresh(invitation)
    return {
        "created": len(created),
        "invitations": [
            {"id": invitation.id, "email": invitation.email}
            for invitation in created
        ],
    }


@app.delete("/api/admin/invitations/{invitation_id}", status_code=204)
def revoke_invitation(
    invitation_id: int,
    _: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    invitation = db.get(Invitation, invitation_id)
    if invitation is None:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    if invitation.accepted_at is not None:
        raise HTTPException(status_code=409, detail="This invitation has already been accepted.")
    if invitation.revoked_at is not None:
        raise HTTPException(status_code=409, detail="This invitation has already been revoked.")
    invitation.revoked_at = datetime.now(timezone.utc)
    db.commit()


@app.patch("/api/admin/users/{user_id}")
def update_user(
    user_id: int,
    body: UserRoleUpdate,
    admin: User = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> dict[str, str | int | bool | None]:
    if all(
        value is None
        for value in (body.role, body.is_active, body.team_id, body.can_edit, body.can_process)
    ):
        raise HTTPException(status_code=422, detail="Provide a user access setting to update.")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.id == admin.id:
        raise HTTPException(status_code=409, detail="You cannot change your own account here.")
    if target.email and (
        target.email.casefold() in INITIAL_ADMIN_EMAILS
        or (
            _configured_local_admin()
            and target.email.casefold() == LOCAL_ADMIN_EMAIL
        )
    ):
        raise HTTPException(
            status_code=409,
            detail="This owner is managed by INITIAL_ADMIN_EMAILS and cannot be changed here.",
        )
    if (
        target.role in ADMIN_ROLES or body.role in ADMIN_ROLES
    ) and admin.role != "org_owner":
        raise HTTPException(
            status_code=403, detail="Only an organization owner can manage admins."
        )
    next_role = body.role or target.role
    next_active = body.is_active if body.is_active is not None else target.is_active
    if target.role == "org_owner" and target.is_active and (
        next_role != "org_owner" or not next_active
    ):
        active_owners = db.query(User).filter_by(role="org_owner", is_active=True).count()
        if active_owners <= 1:
            raise HTTPException(
                status_code=409, detail="At least one active organization owner is required."
            )
    if body.role is not None:
        target.role = body.role
    if body.is_active is not None:
        target.is_active = body.is_active
    if body.team_id is not None:
        if db.get(Team, body.team_id) is None:
            raise HTTPException(status_code=404, detail="Team not found.")
        target.team_id = body.team_id
    if body.can_edit is not None:
        target.can_edit = body.can_edit
    if body.can_process is not None:
        target.can_process = body.can_process
    db.commit()
    db.refresh(target)
    return _admin_user_json(target)


@app.get("/api/jobs")
def recent_jobs(
    user: User = Depends(_current_user), db: Session = Depends(get_db)
) -> list[dict[str, str | int]]:
    jobs = (
        db.query(Job)
        .filter(Job.user_id == user.id)
        .order_by(Job.created_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id": job.id,
            "operation": job.operation,
            "input_count": job.input_count,
            "output_name": job.output_name,
            "created_at": job.created_at.astimezone(timezone.utc).isoformat(),
        }
        for job in jobs
    ]


@app.get("/api/usage")
def usage_summary(
    user: User = Depends(_current_user), db: Session = Depends(get_db)
) -> dict[str, int | str | None]:
    is_admin = user.role in ADMIN_ROLES
    job_query = db.query(func.count(Job.id))
    folder_query = db.query(func.count(Folder.id))
    document_query = db.query(
        func.count(StoredDocument.id),
        func.coalesce(func.sum(StoredDocument.size_bytes), 0),
    ).join(Folder)
    if not is_admin:
        job_query = job_query.filter(Job.user_id == user.id)
        folder_query = folder_query.filter(Folder.user_id == user.id)
        document_query = document_query.filter(Folder.user_id == user.id)
    document_count, stored_bytes = document_query.one()
    return {
        "scope": "organization" if is_admin else "personal",
        "completed_operations": job_query.scalar() or 0,
        "folders": folder_query.scalar() or 0,
        "stored_pdfs": document_count or 0,
        "stored_bytes": stored_bytes or 0,
        "active_users": db.query(func.count(User.id)).filter(User.is_active.is_(True)).scalar()
        if is_admin
        else None,
        "teams": db.query(func.count(Team.id)).scalar() if is_admin else None,
        "pending_invitations": db.query(func.count(Invitation.id))
        .filter(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))
        .scalar()
        if is_admin
        else None,
    }


@app.get("/api/admin/server-settings")
def server_settings(_: User = Depends(_require_admin)) -> dict[str, str | int | bool]:
    return {
        "identity_provider": "Microsoft Entra ID" if _configured_identity_provider() else "Not configured",
        "encrypted_folder_storage": bool(STORAGE_ENCRYPTION_KEY),
        "public_base_url": PUBLIC_BASE_URL,
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "max_batch_mb": MAX_TOTAL_UPLOAD_BYTES // (1024 * 1024),
        "max_pdf_pages": MAX_PDF_PAGES,
        "user_seat_limit": "Unlimited",
        "configuration_note": "Settings are read-only here. Change server environment values and restart the app.",
    }


@app.get("/api/folders")
def list_folders(
    user: User = Depends(_current_user), db: Session = Depends(get_db)
) -> list[dict[str, int | str]]:
    folders = (
        db.query(Folder)
        .filter(Folder.user_id == user.id)
        .order_by(Folder.created_at.desc(), Folder.id.desc())
        .all()
    )
    return [_folder_json(folder) for folder in folders]


@app.post("/api/folders", status_code=201)
def create_folder(
    body: FolderCreate,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    _storage_cipher()
    name = body.name.strip()
    if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
        raise HTTPException(status_code=400, detail="Enter a folder name up to 80 characters.")
    db.query(User).filter_by(id=user.id).with_for_update().one()
    if db.query(Folder).filter_by(user_id=user.id).count() >= MAX_FOLDERS_PER_USER:
        raise HTTPException(status_code=413, detail="Your account has reached the folder limit.")
    folder = Folder(user_id=user.id, name=name)
    db.add(folder)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A folder with that name already exists.") from exc
    db.refresh(folder)
    return _folder_json(folder)


@app.delete("/api/folders/{folder_id}", status_code=204)
def delete_folder(
    folder_id: int,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
):
    folder = _owned_folder(db, user.id, folder_id)
    db.delete(folder)
    db.commit()


@app.get("/api/folders/{folder_id}/documents")
def list_folder_documents(
    folder_id: int,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, int | str]]:
    folder = _owned_folder(db, user.id, folder_id)
    return [_document_json(document) for document in folder.documents]


@app.post("/api/folders/{folder_id}/documents", status_code=201)
async def upload_folder_documents(
    folder_id: int,
    files: list[UploadFile] = File(...),
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, int | str]]:
    cipher = _storage_cipher()
    folder = _owned_folder(db, user.id, folder_id)
    uploads = await _read_uploads(files)
    if not uploads:
        raise HTTPException(status_code=400, detail="Select at least one PDF to store.")
    for _, data in uploads:
        _validate_stored_pdf(data)
    db.query(User).filter_by(id=user.id).with_for_update().one()
    if len(folder.documents) + len(uploads) > MAX_FOLDER_DOCUMENTS:
        raise HTTPException(
            status_code=413, detail=f"A folder can contain no more than {MAX_FOLDER_DOCUMENTS} PDFs."
        )
    existing_bytes, _ = _storage_usage(db, user.id)
    incoming_bytes = sum(len(data) for _, data in uploads)
    if existing_bytes + incoming_bytes > MAX_STORAGE_BYTES_PER_USER:
        raise HTTPException(status_code=413, detail="Your encrypted storage quota would be exceeded.")

    documents = [
        StoredDocument(
            folder_id=folder.id,
            filename=_clean_filename(filename),
            size_bytes=len(data),
            encrypted_content=cipher.encrypt(data),
        )
        for filename, data in uploads
    ]
    db.add_all(documents)
    db.commit()
    for document in documents:
        db.refresh(document)
    return [_document_json(document) for document in documents]


@app.get("/api/folders/{folder_id}/documents/{document_id}")
def download_folder_document(
    folder_id: int,
    document_id: int,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
):
    _owned_folder(db, user.id, folder_id)
    document = (
        db.query(StoredDocument)
        .filter_by(id=document_id, folder_id=folder_id)
        .one_or_none()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    try:
        content = _storage_cipher().decrypt(document.encrypted_content)
    except InvalidToken as exc:
        logger.error("Stored document decryption failed user_id=%s document_id=%s", user.id, document.id)
        raise HTTPException(
            status_code=500, detail="Stored document could not be decrypted; verify the storage key."
        ) from exc
    encoded_name = quote(document.filename, safe="")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.delete("/api/folders/{folder_id}/documents/{document_id}", status_code=204)
def delete_folder_document(
    folder_id: int,
    document_id: int,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
):
    _owned_folder(db, user.id, folder_id)
    document = (
        db.query(StoredDocument)
        .filter_by(id=document_id, folder_id=folder_id)
        .one_or_none()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    db.delete(document)
    db.commit()


@app.post("/api/pdf/preview")
async def preview_pdf(
    files: list[UploadFile] = File(...),
    page_number: int = Form(default=1, ge=1),
    user: User = Depends(_current_user),
):
    del user
    documents = await _read_uploads(files)
    if len(documents) != 1:
        raise HTTPException(status_code=400, detail="Choose exactly one PDF to preview.")
    try:
        document = pymupdf.open(stream=documents[0][1], filetype="pdf")
    except pymupdf.FileDataError as exc:
        raise HTTPException(status_code=400, detail="The uploaded file is not a readable PDF.") from exc
    try:
        if document.needs_pass:
            raise HTTPException(
                status_code=400, detail="Unlock this PDF before selecting a crop area."
            )
        if document.page_count > MAX_PDF_PAGES:
            raise HTTPException(
                status_code=413,
                detail=f"PDF files must not exceed {MAX_PDF_PAGES} pages.",
            )
        if page_number > document.page_count:
            raise HTTPException(status_code=400, detail="The requested preview page does not exist.")
        page = document[page_number - 1]
        rect = page.rect
        if rect.is_empty or rect.is_infinite:
            raise HTTPException(status_code=400, detail="This PDF page has invalid dimensions.")
        scale = min(2, 1600 / max(rect.width, rect.height))
        scale = min(scale, (MAX_RENDERED_PIXELS / (rect.width * rect.height)) ** 0.5)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return Response(
            content=pixmap.tobytes("png"),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )
    except pymupdf.FileDataError as exc:
        raise HTTPException(status_code=400, detail="The PDF page could not be rendered.") from exc
    finally:
        document.close()


@app.post("/api/pdf/form-fields")
async def inspect_form_fields(
    files: list[UploadFile] = File(...),
    user: User = Depends(_require_editor),
):
    del user
    documents = await _read_uploads(files)
    if len(documents) != 1:
        raise HTTPException(status_code=400, detail="Choose exactly one PDF to inspect.")
    try:
        return {"fields": await run_in_threadpool(_get_form_fields, documents[0][1])}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail="The uploaded PDF could not be inspected.") from exc


@app.post("/api/folders/{folder_id}/process/{operation}")
async def process_folder(
    folder_id: int,
    operation: str,
    stamp_image: UploadFile | None = File(default=None),
    tool_image: UploadFile | None = File(default=None),
    pages: str = Form(default=""),
    groups: str = Form(default=""),
    split_mode: str = Form(default="pages"),
    split_value: str = Form(default=""),
    divider_text: str = Form(default=""),
    crop_x: str = Form(default=""),
    crop_y: str = Form(default=""),
    crop_width: str = Form(default=""),
    crop_height: str = Form(default=""),
    degrees: str = Form(default=""),
    watermark_text: str = Form(default=""),
    redact_text: str = Form(default=""),
    protect_password: str = Form(default=""),
    unlock_password: str = Form(default=""),
    form_password: str = Form(default=""),
    source_password: str = Form(default=""),
    owner_password: str = Form(default=""),
    permission_actions: str = Form(default="print,copy,edit,forms"),
    stamp_mode: str = Form(default="text"),
    stamp_text: str = Form(default=""),
    stamp_position: str = Form(default="center"),
    flatten_mode: str = Form(default="forms_annotations"),
    text: str = Form(default=""),
    annotation_text: str = Form(default=""),
    page_number: str = Form(default="1"),
    x: str = Form(default="72"),
    y: str = Form(default="72"),
    font_size: str = Form(default="12"),
    text_color: str = Form(default="#222222"),
    width: str = Form(default="144"),
    height: str = Form(default="100"),
    brightness: str = Form(default="1"),
    contrast: str = Form(default="1"),
    color_mode: str = Form(default="invert"),
    source_color: str = Form(default="#000000"),
    replacement_color: str = Form(default="#FFFFFF"),
    color_tolerance: str = Form(default="16"),
    automation_steps: str = Form(default="[]"),
    form_values: str = Form(default=""),
    scale_factor: str = Form(default="1"),
    page_size: str = Form(default="KEEP"),
    x_factor: str = Form(default="1"),
    y_factor: str = Form(default="1"),
    right_to_left: bool = Form(default=False),
    add_border: bool = Form(default=False),
    spine_location: str = Form(default="LEFT"),
    add_gutter: bool = Form(default=False),
    gutter_size: str = Form(default="0"),
    double_sided: bool = Form(default=False),
    duplex_pass: str = Form(default="BOTH"),
    flip_on_short_edge: bool = Form(default=False),
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
):
    if operation not in SUPPORTED_OPERATIONS or operation in {"images_to_pdf", "convert"}:
        raise HTTPException(status_code=400, detail="This operation is not available for PDF folders.")
    _require_operation_capability(user, operation)
    folder = _owned_folder(db, user.id, folder_id)
    stored_documents = folder.documents
    if not stored_documents:
        raise HTTPException(status_code=400, detail="Add PDFs to this folder before processing it.")
    if len(stored_documents) > 20:
        raise HTTPException(status_code=413, detail="Process no more than 20 PDFs at a time.")
    if sum(document.size_bytes for document in stored_documents) > MAX_TOTAL_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="This folder batch exceeds the processing size limit.")

    cipher = _storage_cipher()
    try:
        documents = [
            (document.filename, cipher.decrypt(document.encrypted_content))
            for document in stored_documents
        ]
    except InvalidToken as exc:
        logger.error("Stored folder decryption failed user_id=%s folder_id=%s", user.id, folder.id)
        raise HTTPException(
            status_code=500, detail="Stored files could not be decrypted; verify the storage key."
        ) from exc

    options: dict[str, str | bytes] = {
        "pages": pages,
        "groups": groups,
        "split_mode": split_mode,
        "split_value": split_value,
        "divider_text": divider_text,
        "crop_x": crop_x,
        "crop_y": crop_y,
        "crop_width": crop_width,
        "crop_height": crop_height,
        "degrees": degrees,
        "text": watermark_text,
        "redact_text": redact_text,
        "protect_password": protect_password,
        "unlock_password": unlock_password,
        "form_password": form_password,
        "source_password": source_password,
        "owner_password": owner_password,
        "permission_actions": permission_actions,
        "stamp_mode": stamp_mode,
        "stamp_text": stamp_text,
        "stamp_position": stamp_position,
        "flatten_mode": flatten_mode,
        "page_number": page_number,
        "x": x,
        "y": y,
        "font_size": font_size,
        "text_color": text_color,
        "width": width,
        "height": height,
        "brightness": brightness,
        "contrast": contrast,
        "color_mode": color_mode,
        "source_color": source_color,
        "replacement_color": replacement_color,
        "color_tolerance": color_tolerance,
        "automation_steps": automation_steps,
        "form_values": form_values,
        "scale_factor": scale_factor,
        "page_size": page_size,
        "x_factor": x_factor,
        "y_factor": y_factor,
        "right_to_left": right_to_left,
        "add_border": add_border,
        "spine_location": spine_location,
        "add_gutter": add_gutter,
        "gutter_size": gutter_size,
        "double_sided": double_sided,
        "duplex_pass": duplex_pass,
        "flip_on_short_edge": flip_on_short_edge,
    }
    options["text"] = watermark_text or text
    options["annotation_text"] = annotation_text
    if stamp_image is not None and stamp_image.filename:
        image = (await _read_uploads([stamp_image], images_only=True))[0]
        if sum(len(data) for _, data in documents) + len(image[1]) > MAX_TOTAL_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="The total upload exceeds the size limit.")
        options["stamp_image"] = image[1]
        options["stamp_image_type"] = Path(image[0]).suffix.lower().lstrip(".")
    if tool_image is not None and tool_image.filename:
        image = (await _read_uploads([tool_image], images_only=True))[0]
        if sum(len(data) for _, data in documents) + len(image[1]) > MAX_TOTAL_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="The total upload exceeds the size limit.")
        options["tool_image"] = image[1]
        options["tool_image_type"] = Path(image[0]).suffix.lower().lstrip(".")
    try:
        if operation in {"merge", "overlay"} or (
            operation == "split" and split_mode == "document_count"
        ):
            content, output_name, page_count = process_pdf(operation, documents, options)
            if len(content) > MAX_FOLDER_OUTPUT_BYTES:
                raise HTTPException(status_code=413, detail="The folder output exceeds the size limit.")
            media_type = "application/pdf" if operation in {"merge", "overlay"} else "application/zip"
        else:
            archive = io.BytesIO()
            page_count = 0
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as result:
                for index, (filename, data) in enumerate(documents, start=1):
                    content, output_name, processed_pages = process_pdf(
                        operation, [(filename, data)], options
                    )
                    if archive.tell() + len(content) > MAX_FOLDER_OUTPUT_BYTES:
                        raise HTTPException(
                            status_code=413, detail="The folder output exceeds the size limit."
                        )
                    page_count += processed_pages
                    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).stem)[:80]
                    result.writestr(f"{index:03d}-{stem or 'document'}-{output_name}", content)
            content = archive.getvalue()
            output_name = "folder-results.zip"
            media_type = "application/zip"
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.add(Job(user_id=user.id, operation=f"folder_{operation}", input_count=len(documents), output_name=output_name))
    db.commit()
    logger.info(
        "Folder operation completed operation=%s user_id=%s folder_id=%s inputs=%s pages=%s output_bytes=%s",
        operation,
        user.id,
        folder.id,
        len(documents),
        page_count,
        len(content),
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{output_name}"'},
    )


@app.post("/api/process/{operation}")
async def process(
    operation: str,
    files: list[UploadFile] = File(...),
    stamp_image: UploadFile | None = File(default=None),
    tool_image: UploadFile | None = File(default=None),
    pages: str = Form(default=""),
    groups: str = Form(default=""),
    split_mode: str = Form(default="pages"),
    split_value: str = Form(default=""),
    divider_text: str = Form(default=""),
    crop_x: str = Form(default=""),
    crop_y: str = Form(default=""),
    crop_width: str = Form(default=""),
    crop_height: str = Form(default=""),
    degrees: str = Form(default=""),
    watermark_text: str = Form(default=""),
    redact_text: str = Form(default=""),
    protect_password: str = Form(default=""),
    unlock_password: str = Form(default=""),
    form_password: str = Form(default=""),
    source_password: str = Form(default=""),
    owner_password: str = Form(default=""),
    permission_actions: str = Form(default="print,copy,edit,forms"),
    stamp_mode: str = Form(default="text"),
    stamp_text: str = Form(default=""),
    stamp_position: str = Form(default="center"),
    flatten_mode: str = Form(default="forms_annotations"),
    text: str = Form(default=""),
    annotation_text: str = Form(default=""),
    page_number: str = Form(default="1"),
    x: str = Form(default="72"),
    y: str = Form(default="72"),
    font_size: str = Form(default="12"),
    text_color: str = Form(default="#222222"),
    width: str = Form(default="144"),
    height: str = Form(default="100"),
    brightness: str = Form(default="1"),
    contrast: str = Form(default="1"),
    color_mode: str = Form(default="invert"),
    source_color: str = Form(default="#000000"),
    replacement_color: str = Form(default="#FFFFFF"),
    color_tolerance: str = Form(default="16"),
    automation_steps: str = Form(default="[]"),
    form_values: str = Form(default=""),
    source_format: str = Form(default=""),
    target_format: str = Form(default=""),
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
):
    if operation not in SUPPORTED_OPERATIONS:
        raise HTTPException(status_code=400, detail="This PDF operation is not supported.")
    _require_operation_capability(user, operation)
    documents = await _read_uploads(
        files,
        images_only=operation == "images_to_pdf",
        allow_any=operation == "convert",
    )

    options: dict[str, str | bytes] = {
        "pages": pages,
        "groups": groups,
        "split_mode": split_mode,
        "split_value": split_value,
        "divider_text": divider_text,
        "crop_x": crop_x,
        "crop_y": crop_y,
        "crop_width": crop_width,
        "crop_height": crop_height,
        "degrees": degrees,
        "text": watermark_text,
        "redact_text": redact_text,
        "protect_password": protect_password,
        "unlock_password": unlock_password,
        "form_password": form_password,
        "source_password": source_password,
        "owner_password": owner_password,
        "permission_actions": permission_actions,
        "stamp_mode": stamp_mode,
        "stamp_text": stamp_text,
        "stamp_position": stamp_position,
        "flatten_mode": flatten_mode,
        "page_number": page_number,
        "x": x,
        "y": y,
        "font_size": font_size,
        "text_color": text_color,
        "width": width,
        "height": height,
        "brightness": brightness,
        "contrast": contrast,
        "color_mode": color_mode,
        "source_color": source_color,
        "replacement_color": replacement_color,
        "color_tolerance": color_tolerance,
        "automation_steps": automation_steps,
        "form_values": form_values,
        "scale_factor": scale_factor,
        "page_size": page_size,
        "x_factor": x_factor,
        "y_factor": y_factor,
        "right_to_left": right_to_left,
        "add_border": add_border,
        "spine_location": spine_location,
        "add_gutter": add_gutter,
        "gutter_size": gutter_size,
        "double_sided": double_sided,
        "duplex_pass": duplex_pass,
        "flip_on_short_edge": flip_on_short_edge,
    }
    options["text"] = watermark_text or text
    options["annotation_text"] = annotation_text
    if stamp_image is not None and stamp_image.filename:
        image = (await _read_uploads([stamp_image], images_only=True))[0]
        if sum(len(data) for _, data in documents) + len(image[1]) > MAX_TOTAL_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="The total upload exceeds the size limit.")
        options["stamp_image"] = image[1]
        options["stamp_image_type"] = Path(image[0]).suffix.lower().lstrip(".")
    if tool_image is not None and tool_image.filename:
        image = (await _read_uploads([tool_image], images_only=True))[0]
        if sum(len(data) for _, data in documents) + len(image[1]) > MAX_TOTAL_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="The total upload exceeds the size limit.")
        options["tool_image"] = image[1]
        options["tool_image_type"] = Path(image[0]).suffix.lower().lstrip(".")
    try:
        if operation == "convert":
            content, output_name, page_count = await run_in_threadpool(
                convert_documents, documents, source_format, target_format
            )
        else:
            content, output_name, page_count = process_pdf(operation, documents, options)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.add(Job(user_id=user.id, operation=operation, input_count=len(files), output_name=output_name))
    db.commit()
    logger.info(
        "PDF operation completed operation=%s user_id=%s inputs=%s pages=%s output_bytes=%s",
        operation,
        user.id,
        len(files),
        page_count,
        len(content),
    )
    return Response(
        content=content,
        media_type=mimetypes.guess_type(output_name)[0] or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{output_name}"'},
    )


async def _read_single_signing_pdf(files: list[UploadFile]) -> bytes:
    if len(files) != 1:
        for upload in files:
            await upload.close()
        raise HTTPException(status_code=400, detail="Choose exactly one PDF to sign.")
    documents = await _read_uploads(files)
    return documents[0][1]


def _saved_signature_json(signature: SavedSignature) -> dict[str, int | str | bool]:
    return {
        "id": signature.id,
        "name": signature.name,
        "is_default": signature.is_default,
    }


def _owned_signature(db: Session, user_id: int, signature_id: int) -> SavedSignature:
    signature = db.query(SavedSignature).filter_by(
        id=signature_id, user_id=user_id
    ).one_or_none()
    if signature is None:
        raise HTTPException(status_code=404, detail="Saved signature not found.")
    return signature


def _clear_signature_default(db: Session, user_id: int) -> None:
    db.query(SavedSignature).filter_by(user_id=user_id, is_default=True).update(
        {"is_default": False}, synchronize_session=False
    )


def _decrypt_signature(cipher: Fernet, signature: SavedSignature) -> bytes:
    try:
        return cipher.decrypt(signature.encrypted_image)
    except InvalidToken as exc:
        raise HTTPException(
            status_code=503,
            detail="The saved signature cannot be decrypted. Check STORAGE_ENCRYPTION_KEY.",
        ) from exc


@app.get("/api/signatures")
def list_saved_signatures(
    user: User = Depends(_require_editor), db: Session = Depends(get_db)
) -> list[dict[str, int | str | bool]]:
    signatures = (
        db.query(SavedSignature)
        .filter_by(user_id=user.id)
        .order_by(SavedSignature.is_default.desc(), SavedSignature.name)
        .all()
    )
    return [_saved_signature_json(signature) for signature in signatures]


@app.get("/api/signatures/{signature_id}")
def get_saved_signature(
    signature_id: int,
    user: User = Depends(_require_editor),
    db: Session = Depends(get_db),
):
    signature = _owned_signature(db, user.id, signature_id)
    image = _decrypt_signature(_storage_cipher(), signature)
    payload = _saved_signature_json(signature)
    payload["signature_image"] = f"data:image/png;base64,{base64.b64encode(image).decode('ascii')}"
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})


@app.delete("/api/signatures/{signature_id}", status_code=204)
def delete_saved_signature(
    signature_id: int,
    user: User = Depends(_require_editor),
    db: Session = Depends(get_db),
):
    signature = _owned_signature(db, user.id, signature_id)
    db.delete(signature)
    db.commit()


@app.post("/api/sign/personal")
async def sign_personal(
    files: list[UploadFile] = File(...),
    signature_image: str = Form(default=""),
    saved_signature_id: int | None = Form(default=None, ge=1),
    save_to_signatures: bool = Form(default=False),
    signature_name: str = Form(default=""),
    make_default: bool = Form(default=False),
    page_number: int = Form(default=1, ge=1),
    position: str = Form(default="bottom-right"),
    user: User = Depends(_require_editor),
    db: Session = Depends(get_db),
):
    pdf_bytes = await _read_single_signing_pdf(files)
    cipher = None
    saved_signature = None
    if saved_signature_id is not None:
        if signature_image or save_to_signatures:
            raise HTTPException(
                status_code=400,
                detail="Use either a saved signature or a new signature image.",
            )
        cipher = _storage_cipher()
        saved_signature = _owned_signature(db, user.id, saved_signature_id)
        image_bytes = _decrypt_signature(cipher, saved_signature)
        signature_image = (
            f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"
        )
    elif not signature_image:
        raise HTTPException(status_code=400, detail="Draw or choose a signature before signing.")

    signature_name = signature_name.strip()
    if save_to_signatures:
        if saved_signature_id is not None:
            raise HTTPException(
                status_code=400, detail="A saved signature cannot be saved as a duplicate."
            )
        if not signature_name or len(signature_name) > 80:
            raise HTTPException(
                status_code=400, detail="Signature name must be between 1 and 80 characters."
            )
        cipher = _storage_cipher()
        signature_count = db.query(SavedSignature).filter_by(user_id=user.id).count()
        if signature_count >= MAX_SAVED_SIGNATURES_PER_USER:
            raise HTTPException(
                status_code=409,
                detail=f"You can save up to {MAX_SAVED_SIGNATURES_PER_USER} signatures.",
            )
        duplicate = db.query(SavedSignature).filter_by(
            user_id=user.id, name=signature_name
        ).first()
        if duplicate is not None:
            raise HTTPException(
                status_code=409, detail="A saved signature with this name already exists."
            )
    elif saved_signature_id is None and make_default:
        raise HTTPException(
            status_code=400, detail="Save the new signature before making it the default."
        )

    try:
        image_bytes = decode_signature_image(signature_image)
        content = await run_in_threadpool(
            apply_personal_signature, pdf_bytes, signature_image, page_number, position
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved_signature_id_result = None
    output_name = "signed-personal.pdf"
    try:
        if save_to_signatures:
            if make_default:
                _clear_signature_default(db, user.id)
            new_signature = SavedSignature(
                user_id=user.id,
                name=signature_name,
                encrypted_image=cipher.encrypt(image_bytes),
                is_default=make_default,
            )
            db.add(new_signature)
            db.flush()
            saved_signature_id_result = new_signature.id
        elif saved_signature is not None and saved_signature.is_default != make_default:
            if make_default:
                _clear_signature_default(db, user.id)
            saved_signature.is_default = make_default
        db.add(Job(user_id=user.id, operation="personal_signature", input_count=1, output_name=output_name))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="A saved signature with this name already exists."
        ) from exc
    logger.info(
        "Personal signature applied user_id=%s page=%s output_bytes=%s",
        user.id,
        page_number,
        len(content),
    )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{output_name}"',
            **(
                {"X-Saved-Signature-Id": str(saved_signature_id_result)}
                if saved_signature_id_result is not None
                else {}
            ),
        },
    )


@app.post("/api/sign/digital")
async def sign_digital(
    files: list[UploadFile] = File(...),
    certificate_file: UploadFile = File(...),
    certificate_password: str = Form(default=""),
    user: User = Depends(_require_editor),
    db: Session = Depends(get_db),
):
    try:
        pdf_bytes = await _read_single_signing_pdf(files)
        extension = Path(certificate_file.filename or "").suffix.lower()
        if extension not in {".p12", ".pfx"}:
            raise HTTPException(
                status_code=415, detail="Upload a .p12 or .pfx PKCS#12 certificate."
            )
        certificate_bytes = await certificate_file.read(MAX_CERTIFICATE_BYTES + 1)
    finally:
        await certificate_file.close()
    if len(certificate_bytes) > MAX_CERTIFICATE_BYTES:
        raise HTTPException(status_code=413, detail="The PKCS#12 certificate must be no larger than 5 MB.")
    try:
        content = await run_in_threadpool(
            apply_certificate_signature, pdf_bytes, certificate_bytes, certificate_password
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    output_name = "signed-digital.pdf"
    db.add(Job(user_id=user.id, operation="digital_signature", input_count=1, output_name=output_name))
    db.commit()
    logger.info(
        "Digital signature applied user_id=%s output_bytes=%s",
        user.id,
        len(content),
    )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{output_name}"'},
    )


if not FRONTEND_DIR.is_dir():
    raise RuntimeError(f"Frontend directory does not exist: {FRONTEND_DIR}")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
