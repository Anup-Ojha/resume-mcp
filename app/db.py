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

db = SupabaseDB()
