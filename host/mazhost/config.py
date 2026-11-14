"""MAZ Core configuration. Secrets and model routing stay on the laptop."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MAZ_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    token: str = "change-me-before-first-run"
    private_mode: bool = False  # MAZ_PRIVATE_MODE=1: zero outbound internet; loopback/private-LAN only
    autoupdate: bool = True  # MAZ_AUTOUPDATE=0 disables the GitHub Releases self-update loop
    update_interval_s: int = 300
    repo_dir: str = ""  # git checkout Core updates; default: the checkout containing mazhost
    bind: str = "0.0.0.0"
    port: int = 8787
    discovery_port: int = 8788  # MAZ_DISCOVERY_PORT: UDP LAN discovery for Piblo pairing; 0 = off
    remote_url: str = ""  # MAZ_REMOTE_URL; empty = auto-detect the Tailscale Funnel URL
    remote_detect: bool = True  # MAZ_REMOTE_DETECT=0 disables the background tailscale probe
    flow_cleanup_llm: bool = False  # MAZ_FLOW_CLEANUP_LLM=1: opt in to LLM cleanup of dictation (network/route)
    flow_cloud: bool = False  # MAZ_FLOW_CLOUD=1: opt in to sending dictation audio/text to Groq
    stt_provider: str = "parakeet"  # MAZ_STT_PROVIDER: parakeet (onnx-asr, local) | whisper (faster-whisper)
    stt_local_model: str = "nemo-parakeet-tdt-0.6b-v3"  # any onnx-asr model name, e.g. moonshine-base-en
    stt_quantization: str = "int8"
    whisper_model: str = "base.en"
    whisper_device: str = "cpu"
    whisper_compute: str = "int8"

    # Local-first brain. AUTO tries primary then backup locally before cloud.
    # LOCAL uses the same local chain but never spills to cloud.
    local_engine: Literal["ollama", "llamacpp"] = "ollama"
    llamacpp_url: str = "http://127.0.0.1:8080"
    llamacpp_model: str = "local"
    llamacpp_backup_url: str = ""
    llamacpp_backup_model: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    # LOCAL_FAST: the reliability-critical default. Small enough to stay fully
    # resident on a 6 GB card and answer in single-digit seconds once warm.
    ollama_model: str = "qwen3.5:4b"
    # LOCAL_SMART: optional, larger/slower. Never the AUTO reliability default.
    ollama_backup_model: str = "lfm2.5-8b-a1b-gpu:latest"
    local_model_policy: Literal["auto", "primary", "backup"] = "auto"
    # AUTO order. cloud_first (default): Groq/OpenRouter/9router, local last.
    # local_first: the local model answers first, bounded by local_first_timeout_s
    # and skipped in <300 ms when its server is down, then the free cloud chain.
    brain_order: Literal["cloud_first", "local_first"] = "cloud_first"
    local_first_timeout_s: float = Field(default=6.0, ge=1.0, le=60.0)
    ai_profile: Literal["smart", "save", "fast"] = "smart"

    # Free fast brain for conversation: Groq first (key from env/.env). Each model has its
    # own Groq rate-limit bucket (8000 tokens/min), so a 429 hops to the next model;
    # llama-3.3-70b was removed from this account (404). Each model is one AUTO stage. Empty key = stages skipped.
    groq_url: str = "https://api.groq.com/openai/v1"
    groq_key: str = ""
    groq_api_key: str = ""  # Alias for groq_key (PR #43 uses this name)
    groq_models: str = "openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b"
    # Legacy preference; explicit LOCAL always stays local. Choose AUTO for the cloud chain.
    smart_voice: bool = True
    now_path: str = "~/Desktop/Maz Works Knowledge Vault/NOW.md"

    # Last free cloud hop before local (key: MAZ_OPENROUTER_API_KEY; ~3 s, still beats local 7-14 s).
    openrouter_api_key: str = ""
    openrouter_free_model: str = "openrouter/free"

    # Dictation flow directory (PR #43)
    flow_dir: str = "~/.maz-pocket/flow"

    cloud_url: str = "https://openrouter.ai/api/v1"
    cloud_key: str = ""
    cloud_model: str = "anthropic/claude-3.5-haiku"
    # Explicit 9router route. Keeping this loopback-only ensures the Cardputer
    # never receives gateway credentials or contacts the model router itself.
    mazlatest_url: str = Field(default="http://localhost:20128/v1", max_length=200)
    mazlatest_key: str = ""
    mazlatest_model: str = Field(default="MazLatest", min_length=1, max_length=120)
    default_route: Literal["local", "auto", "cloud", "mazlatest"] = "local"
    build_id: str = Field(default="dev", min_length=1, max_length=120)

    live_end_silence_ms: int = Field(default=450, ge=100, le=3000)  # live voice: silence that ends the user's turn
    # live voice: digital gain on device mic audio before VAD + model. Cardputer ADV speech arrives at
    # rms ~100-290 (peak ~1k of 32k, measured 8 Nov 2026), far below normal speech levels.
    live_mic_gain: float = Field(default=10.0, ge=0.1, le=64.0)
    # live voice: ignore the mic this long after Piblo stops talking (speaker tail / room echo caused false turns)
    live_listen_holdoff_ms: int = Field(default=600, ge=0, le=3000)
    # live voice brain: "cloud" = LFM3 hears/speaks, OpenRouter thinks (falls back to Ollama, then LFM3);
    # "lfm" = today's direct LFM3 speech-to-speech.
    live_brain: Literal["cloud", "lfm"] = "cloud"
    live_model: str = ""  # upstream realtime model (MAZ_LIVE_MODEL); empty = server default
    live_cloud_default: str = "nemotron"  # voice alias resolved to a :free OpenRouter id until Maz switches by voice
    # tried in order when the chosen cloud model fails (429 etc.), before the slow local fallback. Measured 8 Nov 2026:
    # nemotron-3.5-lightning:free ~0.6 s first token; deepseek-v4.1-flash (cheap paid) ~0.7 s; gemma :free was 429ing.
    live_cloud_backups: list[str] = ["nvidia/nemotron-3.5-lightning:free", "deepseek/deepseek-v4.1-flash"]
    live_ollama_model: str = "qwen36-heretic:latest"
    live_memory_github: str = ""  # owner/repo:path; empty = off; needs github_token
    live_memory_file: str = ""  # "remember ..." notes; empty = <data_dir>/piblo-memory.md
    wakeword: bool = True  # MAZ_WAKEWORD=0: live voice listens without "Hey Piblo"; on but ungated if sherpa-onnx/model missing
    wakeword_model_dir: str = ""  # empty = %LOCALAPPDATA%\Piblo\wakeword-model (scripts/setup-wakeword.py)
    vault_dir: str = ""  # explicitly selected document folder; disabled by default
    vault_remote_url: str = ""  # home PC's Tailscale Core, for a travelling client
    vault_remote_token: str = ""  # stays on this PC, never in a URL or device reply
    shortcuts_file: str = ""  # owner-configured fixed names and targets JSON
    max_upload_mb: int = Field(default=12, ge=1, le=64)
    max_audio_seconds: int = Field(default=900, ge=1, le=3600)
    session_ttl_minutes: int = Field(default=120, ge=1, le=1440)
    max_turns: int = Field(default=12, ge=1, le=100)
    tts_enabled: bool = False
    tts_rate: int = Field(default=180, ge=80, le=300)
    fish_api_key: str = ""
    tts_voice: str = "bf322df2096a46f18c579d0baa36f41d"  # Fish "Adrian"
    tts_model: str = "s2.1-pro-free"
    # Host-only opt-in; firmware never receives model paths or keys.
    tts_provider: Literal["fish", "kokoro", "windows"] = "fish"
    kokoro_model_path: str = ""
    kokoro_voices_path: str = ""
    kokoro_voice: str = Field(default="am_michael", min_length=3, max_length=32)
    kokoro_speed: float = Field(default=1.05, ge=0.75, le=1.35)
    kokoro_pcm_port: int = Field(default=0, ge=0, le=65535)  # >0: serve raw PCM Kokoro for the device on this port
    kokoro_pcm_bind: str = "127.0.0.1"  # set 0.0.0.0 for the device to reach it (LAN peers only either way)
    kokoro_pcm_require_token: bool = False  # firmware #113 must send ?k=<token> before this is turned on
    data_dir: str = ""  # MAZ_DATA_DIR; empty = host/data in the install dir (voice choice, previews)
    maker_enderforge_cmd: str = ""  # Empty = Piblo Maker off; trusted local CLI command, no shell.
    maker_octoprint_url: str = ""
    maker_octoprint_key_file: str = ""  # Key stays on the paired computer, never in responses.
    buddy_db: str = ""  # MAZ_BUDDY_DB: saved alerts/approvals/history; empty = <data dir>/buddy.sqlite3, "off" = memory only
    nudge_url: str = "http://127.0.0.1:47831"
    nudge_token: str = ""
    nudge_token_file: str = "~/.agent-nudge/control-plane.key"
    nudge_cross_sync_days: int = Field(default=3, ge=1, le=30)
    device_port: str = ""
    device_baud: int = Field(default=115200, ge=1200, le=3_000_000)
    device_vid: int = 0x303A
    device_pid: int = 0x1001

    # MAZ Core: factual PC/project context and safe default actions.
    core_enabled: bool = True
    project_roots: str = ""
    obsidian_root: str = ""
    cardputer_url: str = "http://piblo.local"
    web_origins: str = "https://mazos-site.vercel.app,http://localhost:3000,http://127.0.0.1:3000"

    # Optional GitHub command bridge. This remains narrow and should not be used
    # as the authority channel for arbitrary PC execution.
    bridge_enabled: bool = False
    bridge_repo: str = ""
    github_token: str = ""
    bridge_poll_seconds: int = Field(default=15, ge=5, le=300)

    # Phone-approved authority broker. The normal MAZ bearer token may REQUEST
    # power, but approval is only available through the separately signed phone
    # control session. Grants are short-lived and independently revocable.
    control_enabled: bool = True
    control_dir: str = "~/.maz-pocket/control"
    control_phone_session_seconds: int = Field(default=43_200, ge=300, le=604_800)
    control_request_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    control_default_grant_seconds: int = Field(default=600, ge=30, le=3600)
    control_max_grant_seconds: int = Field(default=3600, ge=60, le=14_400)
    control_command_timeout_seconds: int = Field(default=300, ge=5, le=3600)
    control_max_output_chars: int = Field(default=40_000, ge=2_000, le=500_000)

    # WORK Consistency: JOB HUNT / MAZ WORKS / custom tracks. Same authenticated
    # phone-control session boundary as the rest of /control; own SQLite store.
    work_dir: str = "~/.maz-pocket/work"
    # Brain dump inbox any agent can read (MAZ_DUMPS_DIR). Not ~/nod: that is a public git clone.
    dumps_dir: str = "~/nod-inbox"

    # Claude/Codex/Hermes jobs are subprocesses on the PC. Their own CLI
    # permission bypasses are only used after the external MAZ phone broker has
    # approved PROJECT FULL or broader authority.
    agent_job_timeout_seconds: int = Field(default=1800, ge=60, le=7200)

    # Debug Capsules are intentionally bounded snapshots rather than whole-PC
    # dumps. Raw secrets are filtered before anything is presented to a model.
    debug_dir: str = "~/.maz-pocket/debug"
    debug_capsule_limit: int = Field(default=40, ge=5, le=500)
    debug_log_lines: int = Field(default=120, ge=20, le=1000)

    # Teach-by-Demonstration host-side resource defaults. Recording stays light;
    # heavier model work happens after STOP. 3 GB VRAM is the compatibility aim.
    resource_profile: Literal["auto", "eco", "balanced", "quality"] = "auto"
    vram_budget_mb: int = Field(default=3000, ge=512, le=48_000)
    teach_dir: str = "~/.maz-pocket/teach"
    teach_capture_mode: Literal["primary", "display", "window", "region"] = "primary"
    teach_display: str = "primary"
    teach_fps: int = Field(default=15, ge=2, le=30)
    teach_audio_device: str = ""
    teach_voice_profile: Literal["eco", "balanced", "quality"] = "balanced"
    teach_voice_cleanup: Literal["off", "light", "smart"] = "light"
    teach_sound_ai: bool = False
    teach_keep_video: bool = True
    skill_vault_dir: str = "~/.maz-pocket/skills"
    skill_draft_dir: str = "~/.maz-pocket/skills/drafts"

    # SUBTITLES: local video -> timestamped .srt beside it. Only files inside
    # these folders (semicolon-separated) can be read or written. Empty means
    # the user's Videos folder. Multilingual model so `auto` language works.
    media_roots: str = ""
    subtitle_model: str = "base"
    subtitle_timeout_seconds: int = Field(default=3600, ge=30, le=6 * 3600)
    vlc_path: str = ""

    @model_validator(mode="after")
    def migrate_legacy_shipped_defaults(self):
        if self.ollama_model == "gemma3:1b":
            self.ollama_model = "lfm2.5-8b-a1b-gpu:latest"
        parsed = None
        try:
            parsed = urlsplit(self.mazlatest_url)
            host = parsed.hostname or ""
            loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if (
            parsed is None
            or parsed.scheme != "http"
            or not loopback
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("mazlatest_url must be a loopback HTTP URL without embedded credentials")
        return self

    @property
    def token_configured(self) -> bool:
        return bool(self.token and self.token != "change-me-before-first-run")

    @property
    def project_root_paths(self) -> list[Path]:
        values = [p.strip() for p in self.project_roots.split(";") if p.strip()]
        if not values:
            home = Path.home()
            values = [str(home / "Desktop"), str(home / "Projects")]
        result: list[Path] = []
        for value in values:
            path = Path(value).expanduser()
            if path.exists() and path not in result:
                result.append(path)
        return result

    @property
    def media_root_paths(self) -> list[Path]:
        values = [p.strip() for p in self.media_roots.split(";") if p.strip()]
        if not values:
            values = [str(Path.home() / "Videos")]
        result: list[Path] = []
        for value in values:
            path = Path(value).expanduser().resolve()
            if path.is_dir() and path not in result:
                result.append(path)
        return result

    @property
    def web_origin_list(self) -> list[str]:
        return [x.strip() for x in self.web_origins.split(",") if x.strip()]


