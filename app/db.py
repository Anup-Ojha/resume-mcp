import os
from typing import Optional, Dict, Any
from supabase import create_client, Client
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

class User(BaseModel):
    id: str
    phone: str
    name: Optional[str] = None

class ResumeSession(BaseModel):
    id: str
    user_id: str
    session_type: str
    status: str
    pdf_filename: Optional[str] = None

class SupabaseDB:
    def __init__(self):
        self.url = os.getenv("SUPABASE_URL")
        self.key = os.getenv("SUPABASE_SERVICE_KEY")
        if self.url and self.key:
            self.client: Client = create_client(self.url, self.key)
            logger.info("Supabase client initialized")
        else:
            self.client = None
            logger.warning("Supabase credentials missing. DB features disabled.")

    def get_or_create_user(self, phone: str, name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not self.client: return None
        
        # Try to find user
        response = self.client.table("users").select("*").eq("phone", phone).execute()
        if response.data:
            return response.data[0]
        
        # Create new user
        user_data = {"phone": phone}
        if name:
            user_data["name"] = name
            
        response = self.client.table("users").insert(user_data).execute()
        return response.data[0] if response.data else None

    def create_session(self, user_id: str, session_type: str, raw_input: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.client: return None
        
        session_data = {
            "user_id": user_id,
            "session_type": session_type,
            "raw_input": raw_input,
            "status": "pending"
        }
        
        response = self.client.table("resume_sessions").insert(session_data).execute()
        return response.data[0] if response.data else None

    def update_session(self, session_id: str, pdf_filename: str, status: str = "done") -> bool:
        if not self.client: return False

        response = self.client.table("resume_sessions").update({
            "pdf_filename": pdf_filename,
            "status": status,
            "updated_at": "now()"
        }).eq("id", session_id).execute()

        return len(response.data) > 0

    # ── Telegram users ────────────────────────────────────────────────────────

    def get_or_create_telegram_user(
        self,
        telegram_id: int,
        first_name: Optional[str] = None,
        username: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.client: return None
        tid = str(telegram_id)
        resp = self.client.table("telegram_users").select("*").eq("telegram_id", tid).execute()
        if resp.data:
            return resp.data[0]
        row = {"telegram_id": tid}
        if first_name: row["first_name"] = first_name
        if username:   row["username"]   = username
        resp = self.client.table("telegram_users").insert(row).execute()
        return resp.data[0] if resp.data else None

    # ── Google tokens ─────────────────────────────────────────────────────────

    def save_google_tokens(
        self,
        telegram_id: str,
        access_token: str,
        refresh_token: Optional[str],
        token_expiry: str,
        scopes: str,
        google_id: str,
        email: str,
        full_name: str,
        avatar_url: Optional[str] = None,
    ) -> bool:
        if not self.client: return False
        # Update telegram_users with Google profile
        self.client.table("telegram_users").update({
            "google_id":     google_id,
            "google_email":  email,
            "google_name":   full_name,
            "google_avatar": avatar_url,
            "last_seen_at":  "now()",
        }).eq("telegram_id", telegram_id).execute()

        # Upsert tokens
        row: Dict[str, Any] = {
            "telegram_user_id": telegram_id,
            "access_token":  access_token,
            "token_expiry":  token_expiry,
            "scopes":        scopes,
            "updated_at":    "now()",
        }
        if refresh_token:
            row["refresh_token"] = refresh_token

        resp = self.client.table("google_tokens").upsert(
            row, on_conflict="telegram_user_id"
        ).execute()
        return bool(resp.data)

    def get_google_tokens(self, telegram_id: str) -> Optional[Dict[str, Any]]:
        if not self.client: return None
        resp = self.client.table("google_tokens").select("*").eq(
            "telegram_user_id", telegram_id
        ).execute()
        return resp.data[0] if resp.data else None

    def get_telegram_user(self, telegram_id: str) -> Optional[Dict[str, Any]]:
        if not self.client: return None
        resp = self.client.table("telegram_users").select("*").eq(
            "telegram_id", telegram_id
        ).execute()
        return resp.data[0] if resp.data else None

    def delete_google_tokens(self, telegram_id: str) -> bool:
        if not self.client: return False
        resp = self.client.table("google_tokens").delete().eq(
            "telegram_user_id", telegram_id
        ).execute()
        return bool(resp.data)

    def update_access_token(
        self,
        telegram_id: str,
        new_token: str,
        new_expiry: str,
    ) -> bool:
        if not self.client: return False
        resp = self.client.table("google_tokens").update({
            "access_token": new_token,
            "token_expiry": new_expiry,
            "updated_at":   "now()",
        }).eq("telegram_user_id", telegram_id).execute()
        return bool(resp.data)


db = SupabaseDB()
