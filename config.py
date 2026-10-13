"""Central configuration, loaded from environment variables / .env file."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- Claude (AI suggestions) ---
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
    analysis_interval_sec: float = float(os.getenv("ANALYSIS_INTERVAL_SEC", "8"))

    # --- Azure Speech (live transcription with speaker separation) ---
    speech_key: str = os.getenv("AZURE_SPEECH_KEY", "")
    speech_region: str = os.getenv("AZURE_SPEECH_REGION", "eastus")
    speech_language: str = os.getenv("AZURE_SPEECH_LANGUAGE", "en-US")
    # Blank = default microphone. For a softphone, set this to the
    # loopback / virtual audio device that carries both sides of the call.
    audio_input_device: str = os.getenv("AUDIO_INPUT_DEVICE", "")

    # --- Microsoft Dynamics 365 / Dataverse ---
    dynamics_url: str = os.getenv("DYNAMICS_URL", "").rstrip("/")  # e.g. https://yourorg.crm.dynamics.com
    azure_tenant_id: str = os.getenv("AZURE_TENANT_ID", "")
    azure_client_id: str = os.getenv("AZURE_CLIENT_ID", "")
    azure_client_secret: str = os.getenv("AZURE_CLIENT_SECRET", "")
    auto_create_case: bool = _bool("AUTO_CREATE_CASE", True)
    attach_transcript_note: bool = _bool("ATTACH_TRANSCRIPT_NOTE", True)

    # --- Knowledge base ---
    kb_folder: str = os.getenv("KB_FOLDER", "kb")
    sync_dynamics_kb: bool = _bool("SYNC_DYNAMICS_KB", True)

    @property
    def dynamics_configured(self) -> bool:
        return all([self.dynamics_url, self.azure_tenant_id, self.azure_client_id, self.azure_client_secret])


settings = Settings()
