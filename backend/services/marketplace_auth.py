import os
from fastapi import HTTPException

try:
    from supabase import create_client, Client
    _supabase_available = True
except ImportError:
    _supabase_available = False
    Client = object  # type: ignore[misc,assignment]

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

_supabase: "Client | None" = None


def get_supabase() -> "Client":
    global _supabase
    if not _supabase_available:
        raise RuntimeError("supabase package is not installed")
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)  # type: ignore[arg-type]
    return _supabase



def register_customer(email: str, password: str, name: str, phone: str = "") -> dict:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if not name.strip():
        raise ValueError("Name is required.")

    supabase = get_supabase()
    try:
        result = supabase.auth.admin.create_user(
            {
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"name": name.strip(), "phone": phone.strip()},
            }
        )
    except Exception as e:
        msg = str(e).lower()
        if "already" in msg or "exists" in msg or "registered" in msg:
            raise ValueError("An account with this email already exists.")
        raise ValueError(f"Registration failed. Please try again.")

    if result.user is None:
        raise ValueError("Registration failed. Please try again.")

    try:
        supabase.table("customer_profiles").insert(
            {"id": result.user.id, "name": name.strip(), "phone": phone.strip()}
        ).execute()
    except Exception:
        pass

    return {"user_id": result.user.id, "email": email, "name": name.strip()}


def login_customer(email: str, password: str) -> dict:
    supabase = get_supabase()
    try:
        result = supabase.auth.sign_in_with_password({"email": email, "password": password})
    except Exception:
        raise ValueError("Invalid email or password.")

    if result.user is None or result.session is None:
        raise ValueError("Invalid email or password.")

    return {
        "access_token": result.session.access_token,
        "user_id": result.user.id,
        "email": result.user.email,
        "name": result.user.user_metadata.get("name", ""),
        "is_demo_customer": result.user.email == "customer@demo.my",
    }


def validate_customer_token(token: str) -> dict | None:
    if not token:
        return None
    supabase = get_supabase()
    try:
        result = supabase.auth.get_user(token)
        if result.user:
            return {
                "user_id": result.user.id,
                "email": result.user.email,
                "name": result.user.user_metadata.get("name", ""),
            }
    except Exception:
        return None
    return None


def delete_customer_account(user_id: str, token: str) -> None:
    supabase = get_supabase()

    user = validate_customer_token(token)
    if not user or user["user_id"] != user_id:
        raise ValueError("Unauthorized.")

    try:
        supabase.table("marketplace_orders").update(
            {"customer_id": None, "order_email": "[deleted]", "order_name": "[deleted]"}
        ).eq("customer_id", user_id).execute()
    except Exception:
        pass

    try:
        supabase.table("marketplace_reservations").delete().eq("customer_id", user_id).execute()
    except Exception:
        pass

    try:
        supabase.table("customer_profiles").delete().eq("id", user_id).execute()
    except Exception:
        pass

    supabase.auth.admin.delete_user(user_id)
