import os
import uuid
import secrets
import hashlib
import datetime
import threading
import time
from typing import Optional

_lock = threading.Lock()
_token_cache_lock = threading.Lock()
OTP_TTL_SECONDS = 90
SESSION_TTL_DAYS = 30
PENDING_REG_TTL = 600

_LAST_ACTIVE_INTERVAL = 300
_MAX_TOKEN_CACHE = 1000
_token_last_saved: dict = {}


def _prune_token_cache() -> None:
    if len(_token_last_saved) > _MAX_TOKEN_CACHE:
        oldest = sorted(_token_last_saved.items(), key=lambda x: x[1])
        for token, _ in oldest[: _MAX_TOKEN_CACHE // 5]:
            _token_last_saved.pop(token, None)


def _db_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'database.json')


def _load() -> dict:
    from services.supabase_db import load_database
    return load_database()


def _save(db: dict) -> None:
    from services.supabase_db import save_database
    save_database(db)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _otp_code() -> str:
    return str(secrets.randbelow(900000) + 100000)


def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


def get_account_by_email(email: str) -> Optional[dict]:
    db = _load()
    return next((a for a in db.get('accounts', []) if a['email'].lower() == email.lower()), None)


def get_account_by_telegram(chat_id: int) -> Optional[dict]:
    db = _load()
    for a in db.get('accounts', []):
        if any(s.get('chat_id') == chat_id for s in a.get('sessions', [])):
            return a
    return None


def get_account_by_restaurant(rest_id: str) -> Optional[dict]:
    db = _load()
    return next((a for a in db.get('accounts', []) if a.get('restaurant_id') == rest_id), None)


def get_sessions_for_account(email: str) -> list:
    db = _load()
    account = next((a for a in db.get('accounts', []) if a['email'].lower() == email.lower()), None)
    if not account:
        return []
    now = _utcnow().replace(tzinfo=None)
    active = []
    expired_ids = []
    for s in account.get('sessions', []):
        if s.get('is_primary'):
            active.append(s)
            continue
        expires = s.get('expires_at')
        if expires:
            try:
                if (
                    datetime.datetime.fromisoformat(str(expires).replace("Z", "+00:00")).replace(
                        tzinfo=None
                    )
                    < now
                ):
                    expired_ids.append(s.get('session_id'))
                    continue
            except (ValueError, TypeError):
                pass
        active.append(s)
    if len(active) != len(account.get('sessions', [])):
        account['sessions'] = active
        _save(db)
        if expired_ids:
            try:
                from services.supabase_db import _sb
                if _sb:
                    _sb.table("sessions").delete().in_("session_id", expired_ids).execute()
            except Exception:
                pass
    return active


def get_any_token_for_email(email: str) -> Optional[str]:
    sessions = get_sessions_for_account(email)
    web = [s for s in sessions if s.get('type') == 'web' and s.get('session_id')]
    return web[0]['session_id'] if web else None


def email_registered(email: str) -> bool:
    return get_account_by_email(email) is not None


def telegram_registered(chat_id: int) -> bool:
    return get_account_by_telegram(chat_id) is not None


def restaurant_has_account(rest_id: str) -> bool:
    return get_account_by_restaurant(rest_id) is not None


def create_account(
    email: str, restaurant_id: str, telegram_chat_id: int, telegram_username: str
) -> dict:
    with _lock:
        db = _load()
        if any(a['email'].lower() == email.lower() for a in db.get('accounts', [])):
            raise ValueError('Email already registered.')
        for a in db.get('accounts', []):
            if any(s.get('chat_id') == telegram_chat_id for s in a.get('sessions', [])):
                raise ValueError('This Telegram account is already linked to another restaurant.')
        if any(a.get('restaurant_id') == restaurant_id for a in db.get('accounts', [])):
            raise ValueError('This restaurant already has an account.')
        account = {
            'email': email.lower(),
            'restaurant_id': restaurant_id,
            'created_at': _now_iso(),
            'sessions': [
                {
                    'session_id': str(uuid.uuid4()),
                    'type': 'telegram',
                    'chat_id': telegram_chat_id,
                    'telegram_username': telegram_username,
                    'is_primary': True,
                    'linked_at': _now_iso(),
                    'last_active': _now_iso(),
                    'expires_at': None,
                }
            ],
        }
        db.setdefault('accounts', []).append(account)
        for r in db.get('restaurants', []):
            if r['id'] == restaurant_id:
                r['telegram_chat_id'] = telegram_chat_id
                r['telegram_username'] = telegram_username
                break
        _save(db)
        return account


def add_web_session(email: str, label: str = 'Web browser') -> str:
    with _lock:
        db = _load()
        account = next(
            (a for a in db.get('accounts', []) if a['email'].lower() == email.lower()), None
        )
        if not account:
            raise ValueError('Account not found.')
        token = str(uuid.uuid4())
        expires = (
            _utcnow() + datetime.timedelta(days=SESSION_TTL_DAYS)
        ).isoformat()
        session_data = {
            'session_id': token,
            'type': 'web',
            'chat_id': None,
            'label': label,
            'is_primary': False,
            'linked_at': _now_iso(),
            'last_active': _now_iso(),
            'expires_at': expires,
        }
        account.setdefault('sessions', []).append(session_data)
        _save(db)
        _sync_push_session_to_supabase(email, session_data)
        return token


def validate_web_token(token: str) -> Optional[dict]:
    db = _load()
    now = _utcnow().replace(tzinfo=None)
    for account in db.get('accounts', []):
        for session in account.get('sessions', []):
            if session.get('session_id') == token and session.get('type') == 'web':
                if session.get('expires_at'):
                    if (
                        datetime.datetime.fromisoformat(
                            str(session['expires_at']).replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                        < now
                    ):
                        return None
                with _token_cache_lock:
                    last_saved = _token_last_saved.get(token, 0)
                    if (time.monotonic() - last_saved) > _LAST_ACTIVE_INTERVAL:
                        session['last_active'] = now.isoformat()
                        _token_last_saved[token] = time.monotonic()
                        _prune_token_cache()
                        _save(db)
                return {'email': account['email'], 'restaurant_id': account['restaurant_id']}

    from services.supabase_db import _sb

    if _sb:
        try:
            res = (
                _sb.table("sessions")
                .select("account_id, type, expires_at")
                .eq("session_id", token)
                .execute()
            )
            if res.data and res.data[0].get("type") == "web":
                sess_data = res.data[0]
                if sess_data.get("expires_at"):
                    if (
                        datetime.datetime.fromisoformat(
                            str(sess_data["expires_at"]).replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                        < now
                    ):
                        return None

                acc_res = (
                    _sb.table("accounts")
                    .select("email, restaurant_id")
                    .eq("id", sess_data["account_id"])
                    .execute()
                )
                if acc_res.data:
                    from services.supabase_db import invalidate_cache
                    invalidate_cache()
                    return {
                        'email': acc_res.data[0]['email'],
                        'restaurant_id': acc_res.data[0]['restaurant_id'],
                    }
        except Exception:
            pass

    return None


def remove_session(email: str, session_id: str) -> bool:
    with _lock:
        db = _load()
        account = next(
            (a for a in db.get('accounts', []) if a['email'].lower() == email.lower()), None
        )
        if not account:
            return False
        before = len(account.get('sessions', []))
        account['sessions'] = [
            s
            for s in account.get('sessions', [])
            if not (s['session_id'] == session_id and not s.get('is_primary'))
        ]
        changed = len(account['sessions']) < before
        if changed:
            _save(db)
            from services.supabase_db import _sb
            if _sb:
                try:
                    _sb.table("sessions").delete().eq("session_id", session_id).execute()
                except Exception:
                    pass
            return True
        return changed


def delete_account(email: str) -> bool:
    with _lock:
        db = _load()
        before = len(db.get('accounts', []))
        db['accounts'] = [a for a in db.get('accounts', []) if a['email'].lower() != email.lower()]
        changed = len(db['accounts']) < before
        if changed:
            _save(db)
        return changed


def create_otp(email: str, purpose: str) -> str:
    with _lock:
        code = _otp_code()
        db = _load()
        otps = db.setdefault('pending_otps', [])
        db['pending_otps'] = [
            o for o in otps if not (o.get('email') == email.lower() and o['purpose'] == purpose)
        ]
        db['pending_otps'].append(
            {
                'email': email.lower(),
                'code_hash': _hash_otp(code),
                'purpose': purpose,
                'created_at': _now_iso(),
                'expires_at': (
                    datetime.datetime.utcnow() + datetime.timedelta(seconds=OTP_TTL_SECONDS)
                ).isoformat(),
            }
        )
        _save(db)
        return code


def verify_otp(email: str, code: str, purpose: str) -> bool:
    with _lock:
        db = _load()
        now = _utcnow().replace(tzinfo=None)
        valid_otps = []
        for otp in db.get('pending_otps', []):
            try:
                if datetime.datetime.fromisoformat(str(otp['expires_at']).replace("Z", "+00:00")).replace(tzinfo=None) >= now:
                    valid_otps.append(otp)
            except Exception:
                pass
        if len(valid_otps) != len(db.get('pending_otps', [])):
            db['pending_otps'] = valid_otps
            _save(db)
        h = _hash_otp(code.strip())
        for i, otp in enumerate(db.get('pending_otps', [])):
            if (
                otp.get('email', '').lower() == email.lower()
                and otp.get('code_hash') == h
                and otp['purpose'] == purpose
            ):
                db['pending_otps'].pop(i)
                _save(db)
                return True
        return False


def create_telegram_otp(chat_id: int, purpose: str) -> str:
    with _lock:
        code = _otp_code()
        db = _load()
        otps = db.setdefault('pending_otps', [])
        db['pending_otps'] = [
            o for o in otps if not (o.get('chat_id') == chat_id and o['purpose'] == purpose)
        ]
        db['pending_otps'].append(
            {
                'chat_id': chat_id,
                'code_hash': _hash_otp(code),
                'purpose': purpose,
                'created_at': _now_iso(),
                'expires_at': (
                    datetime.datetime.utcnow() + datetime.timedelta(seconds=OTP_TTL_SECONDS)
                ).isoformat(),
            }
        )
        _save(db)
        return code


def verify_telegram_otp(chat_id: int, code: str, purpose: str) -> bool:
    with _lock:
        db = _load()
        now = _utcnow().replace(tzinfo=None)
        h = _hash_otp(code.strip())
        for i, otp in enumerate(db.get('pending_otps', [])):
            if (
                otp.get('chat_id') == chat_id
                and otp.get('code_hash') == h
                and otp['purpose'] == purpose
            ):
                if (
                    datetime.datetime.fromisoformat(
                        str(otp['expires_at']).replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                    < now
                ):
                    return False
                db['pending_otps'].pop(i)
                _save(db)
                return True
        return False


def create_pending_registration(email: str, telegram_username: str, restaurant_data: dict) -> str:
    with _lock:
        db = _load()
        prs = db.setdefault('pending_registrations', [])
        db['pending_registrations'] = [pr for pr in prs if pr.get('email') != email.lower()]
        verify_code = _otp_code()
        db['pending_registrations'].append(
            {
                'email': email.lower(),
                'telegram_username': telegram_username.lower().lstrip('@'),
                'restaurant_data': restaurant_data,
                'code_hash': _hash_otp(verify_code),
                'created_at': _now_iso(),
                'expires_at': (
                    datetime.datetime.utcnow() + datetime.timedelta(seconds=PENDING_REG_TTL)
                ).isoformat(),
            }
        )
        _save(db)
        return verify_code


def get_pending_registration_by_username(username: str) -> Optional[dict]:
    db = _load()
    now = datetime.datetime.utcnow().isoformat()
    return next(
        (
            pr
            for pr in db.get('pending_registrations', [])
            if pr.get('telegram_username', '').lower() == username.lower().lstrip('@')
            and pr.get('expires_at', '') > now
        ),
        None,
    )


def complete_pending_registration(email: str, chat_id: int, verify_code: str) -> Optional[dict]:
    with _lock:
        db = _load()
        now = datetime.datetime.utcnow()
        h = _hash_otp(verify_code.strip())
        pr = next(
            (
                p
                for p in db.get('pending_registrations', [])
                if p.get('email', '').lower() == email.lower()
            ),
            None,
        )
        if not pr:
            return None
        if (
            datetime.datetime.fromisoformat(str(pr['expires_at']).replace("Z", "+00:00")).replace(
                tzinfo=None
            )
            < now
        ):
            return None
        if pr.get('code_hash') != h:
            return None
        db['pending_registrations'] = [
            p for p in db['pending_registrations'] if p.get('email') != email.lower()
        ]
        _save(db)
        return pr


def pending_email_registered(email: str) -> bool:
    db = _load()
    now = datetime.datetime.utcnow().isoformat()
    return any(
        p.get("email", "").lower() == email.lower() and p.get("expires_at", "") > now
        for p in db.get("pending_registrations", [])
    )


def cancel_pending_registration(email: str) -> None:
    with _lock:
        db = _load()
        db['pending_registrations'] = [
            p for p in db.get('pending_registrations', []) if p.get('email') != email.lower()
        ]
        _save(db)


def create_approval_request(
    primary_chat_id: int, requesting_chat_id: int, requesting_username: str
) -> str:
    with _lock:
        approval_id = str(uuid.uuid4())[:8]
        db = _load()
        db.setdefault('pending_approvals', []).append(
            {
                'approval_id': approval_id,
                'primary_chat_id': primary_chat_id,
                'requesting_chat_id': requesting_chat_id,
                'requesting_username': requesting_username,
                'created_at': _now_iso(),
                'expires_at': (
                    datetime.datetime.utcnow() + datetime.timedelta(minutes=10)
                ).isoformat(),
                'status': 'pending',
            }
        )
        _save(db)
        return approval_id


def resolve_approval(approval_id: str, approved: bool) -> Optional[dict]:
    with _lock:
        db = _load()
        now = datetime.datetime.utcnow()
        for i, ap in enumerate(db.get('pending_approvals', [])):
            if ap['approval_id'] == approval_id:
                if (
                    datetime.datetime.fromisoformat(
                        str(ap['expires_at']).replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                    < now
                ):
                    db['pending_approvals'].pop(i)
                    _save(db)
                    return None
                ap['status'] = 'approved' if approved else 'rejected'
                _save(db)
                return ap
        return None


def clean_expired(db: dict) -> None:
    now = datetime.datetime.utcnow().isoformat()
    db['pending_otps'] = [o for o in db.get('pending_otps', []) if o.get('expires_at', '') > now]
    db['pending_approvals'] = [
        a for a in db.get('pending_approvals', []) if a.get('expires_at', '') > now
    ]
    db['pending_registrations'] = [
        r for r in db.get('pending_registrations', []) if r.get('expires_at', '') > now
    ]


def _sync_push_session_to_supabase(email: str, session: dict) -> None:
    try:
        from services.supabase_db import sb_upsert_session
        sb_upsert_session(email, session)
    except Exception:
        pass
