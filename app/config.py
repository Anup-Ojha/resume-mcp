import os
from pathlib import Path
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Application settings
    app_name: str = "LaTeX Resume Generator"
    debug: bool = True
    
    # Directory settings
    base_dir: Path = Path(__file__).parent.parent
    output_dir: Path = base_dir / "output"
    temp_dir: Path = base_dir / "temp"
    templates_dir: Path = base_dir / "templates"
    static_dir: Path = base_dir / "static"
    
    # LaTeX settings
    latex_compiler: str = "pdflatex"
    
    def get_compiler_path(self) -> str:
        """Find the LaTeX compiler path"""
        import shutil
        import os
        
        # Check system PATH first
        path = shutil.which(self.latex_compiler)
        if path:
            return path
            
        # Common MiKTeX paths on Windows
        common_paths = [
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'MiKTeX', 'miktex', 'bin', 'x64', 'pdflatex.exe'),
            os.path.join(os.environ.get('ProgramFiles', ''), 'MiKTeX', 'miktex', 'bin', 'x64', 'pdflatex.exe'),
        ]
        
        for p in common_paths:
            if os.path.exists(p):
                return p
                
        return self.latex_compiler  # Fallback to name and let subprocess error it out

    latex_timeout: int = 30  # seconds
    max_file_size: int = 10 * 1024 * 1024  # 10 MB

    
    # Google OAuth (required for Gmail integration)
    google_client_id: str = ""
    google_client_secret: str = ""
    # Public URL of this API service (used as OAuth redirect_uri)
    public_api_url: str = "http://localhost:8000"

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8000
    
    # AI settings
    gemini_api_key: str = ""

    # Supabase settings
    supabase_url: str = ""
    supabase_service_key: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"
        
    def ensure_directories(self):
        """Create necessary directories if they don't exist"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.static_dir.mkdir(parents=True, exist_ok=True)

settings = Settings()
settings.ensure_directories()
