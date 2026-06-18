import os
import io
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

AUDIO_BUCKET = "wastewise-audio"
MODELS_BUCKET = "wastewise-models"

_supabase: Client | None = None


def _get_client() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _supabase


def _ensure_buckets() -> None:
    client = _get_client()
    existing = [b.name for b in client.storage.list_buckets()]
    for bucket in [AUDIO_BUCKET, MODELS_BUCKET]:
        if bucket not in existing:
            try:
                client.storage.create_bucket(bucket, options={"public": True})
            except Exception as e:
                print(f"[Storage] Bucket {bucket } may already exist: {e }")


def upload_audio(file_bytes: bytes, filename: str, restaurant_id: str) -> str | None:
    try:
        client = _get_client()
        path = f"{restaurant_id }/{filename }"
        client.storage.from_(AUDIO_BUCKET).upload(
            path, file_bytes, {"content-type": "audio/ogg", "upsert": "true"}
        )
        url = client.storage.from_(AUDIO_BUCKET).get_public_url(path)
        return url
    except Exception as e:
        print(f"[Storage] Audio upload failed: {e }")
        return None


def upload_model_snapshot(file_bytes: bytes, restaurant_id: str, model_name: str) -> str | None:
    try:
        client = _get_client()
        path = f"{restaurant_id }/{model_name }"
        client.storage.from_(MODELS_BUCKET).upload(
            path, file_bytes, {"content-type": "application/octet-stream", "upsert": "true"}
        )
        url = client.storage.from_(MODELS_BUCKET).get_public_url(path)
        return url
    except Exception as e:
        print(f"[Storage] Model upload failed: {e }")
        return None


def delete_restaurant_files(restaurant_id: str) -> None:
    client = _get_client()
    for bucket in [AUDIO_BUCKET, MODELS_BUCKET]:
        try:
            files = client.storage.from_(bucket).list(restaurant_id)
            if files:
                paths = [f"{restaurant_id }/{f ['name']}" for f in files]
                client.storage.from_(bucket).remove(paths)
        except Exception as e:
            print(f"[Storage] Delete failed for {bucket }/{restaurant_id }: {e }")
