from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Defaults that are safe in dev but MUST be overridden in production.
_DEFAULT_JWT_SECRET = "change-me"
_DEFAULT_ADMIN_EMAIL = "admin@anvil.dev"
_DEFAULT_ADMIN_PASSWORD = "admin"  # noqa: S105 — dev-only default, blocked in prod


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # Deployment env: "development" (default) | "production"
    anvil_env: str = Field(
        default="development",
        validation_alias=AliasChoices("ANVIL_ENV", "anvil_env"),
    )

    database_url: str = "postgresql+psycopg://anvil:anvil@localhost:5432/anvil"
    redis_url: str = "redis://localhost:6379/0"
    ml_service_url: str = "http://localhost:8001"

    storage_provider: str = "local"
    storage_bucket: str = "anvil"
    storage_root: str = "./data"  # for local provider

    jwt_secret: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    # Access TTL — short by design; refresh tokens cover long sessions.
    jwt_expires_minutes: int = Field(
        default=30,
        validation_alias=AliasChoices("JWT_EXPIRES_MINUTES", "jwt_expires_minutes"),
    )
    # Refresh TTL — long-lived, rotated on each /refresh.
    refresh_expires_days: int = Field(
        default=30,
        validation_alias=AliasChoices("REFRESH_EXPIRES_DAYS", "refresh_expires_days"),
    )
    # Grace window: a refresh token used <N seconds ago is treated as a benign
    # concurrent refresh (multi-tab race), not as theft. Within this window the
    # second refresh attempt returns a benign 409 and the family stays alive.
    refresh_reuse_grace_seconds: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "REFRESH_REUSE_GRACE_SECONDS", "refresh_reuse_grace_seconds"
        ),
    )

    # --- Cookie auth ---
    # SameSite for auth cookies. "lax" is correct for same-site SPA+API.
    # Use "none" (forces Secure) only if frontend and API are on different
    # eTLD+1 sites; in that case ALSO add a CSRF token (custom-header check
    # alone is insufficient with SameSite=None).
    cookie_samesite: str = Field(
        default="lax",
        validation_alias=AliasChoices("COOKIE_SAMESITE", "cookie_samesite"),
    )
    # If your frontend and API share an eTLD+1 (e.g. anvil.example +
    # api.anvil.example) set this to ".anvil.example" so cookies are sent
    # to both. Leave empty for host-only cookies (recommended for localhost).
    cookie_domain: str = Field(
        default="",
        validation_alias=AliasChoices("COOKIE_DOMAIN", "cookie_domain"),
    )

    # Bootstrap admin (defaults preserve existing dev/CI scripts). To skip
    # auto-creation entirely in any environment, set ANVIL_ADMIN_BOOTSTRAP=false.
    # In production the default password is rejected (see model_validator).
    admin_bootstrap: bool = Field(
        default=True,
        validation_alias=AliasChoices("ANVIL_ADMIN_BOOTSTRAP", "admin_bootstrap"),
    )
    admin_email: str = Field(
        default=_DEFAULT_ADMIN_EMAIL,
        validation_alias=AliasChoices("ANVIL_ADMIN_EMAIL", "admin_email"),
    )
    admin_password: str = Field(
        default=_DEFAULT_ADMIN_PASSWORD,
        validation_alias=AliasChoices("ANVIL_ADMIN_PASSWORD", "admin_password"),
    )

    # Whether `/api/auth/signup` is open. Set false to lock down a deployment.
    allow_signup: bool = True

    # Default role assigned to new self-signup AND OAuth-created users.
    # Defaults to "viewer" (safest for OSS/shared deployments) — admin promotes
    # users explicitly. Set to "labeler" for trusted single-team workspaces.
    default_signup_role: str = Field(
        default="viewer",
        validation_alias=AliasChoices("ANVIL_DEFAULT_SIGNUP_ROLE", "default_signup_role"),
    )

    # Frontend base URL. Used as the strict redirect target after OAuth.
    frontend_url: str = "http://localhost:5173"

    # Public base URL where the API is reachable; used to build provider
    # redirect_uri values. Must match what's registered with each provider.
    oauth_redirect_base: str = "http://localhost:8000"

    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""

    cors_origins: str = "http://localhost:5173"

    # --- Rate limiting (in-process; single-replica only) ---
    rate_limit_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("RATE_LIMIT_ENABLED", "rate_limit_enabled"),
    )
    rate_limit_login_per_min: int = Field(
        default=10,
        validation_alias=AliasChoices("RATE_LIMIT_LOGIN_PER_MIN", "rate_limit_login_per_min"),
    )
    # Counts only *failed* logins per email — protects users from password
    # spraying without giving attackers an account-lockout primitive.
    rate_limit_login_email_per_min: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "RATE_LIMIT_LOGIN_EMAIL_PER_MIN", "rate_limit_login_email_per_min"
        ),
    )
    rate_limit_signup_per_hour: int = Field(
        default=5,
        validation_alias=AliasChoices("RATE_LIMIT_SIGNUP_PER_HOUR", "rate_limit_signup_per_hour"),
    )
    rate_limit_oauth_callback_per_min: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "RATE_LIMIT_OAUTH_CALLBACK_PER_MIN", "rate_limit_oauth_callback_per_min"
        ),
    )
    rate_limit_password_reset_ip_per_hour: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "RATE_LIMIT_PASSWORD_RESET_IP_PER_HOUR",
            "rate_limit_password_reset_ip_per_hour",
        ),
    )
    rate_limit_password_reset_email_per_hour: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "RATE_LIMIT_PASSWORD_RESET_EMAIL_PER_HOUR",
            "rate_limit_password_reset_email_per_hour",
        ),
    )

    # Email backend: "log" (writes the message to stdout — dev default) or
    # "smtp" (production). SMTP knobs are read from EMAIL_SMTP_* env vars at
    # send time so dev/CI doesn't need an SMTP server reachable.
    email_backend: str = Field(
        default="log",
        validation_alias=AliasChoices("EMAIL_BACKEND", "email_backend"),
    )
    email_from: str = Field(
        default="no-reply@anvil.local",
        validation_alias=AliasChoices("EMAIL_FROM", "email_from"),
    )
    email_smtp_host: str = Field(
        default="",
        validation_alias=AliasChoices("EMAIL_SMTP_HOST", "email_smtp_host"),
    )
    email_smtp_port: int = Field(
        default=587,
        validation_alias=AliasChoices("EMAIL_SMTP_PORT", "email_smtp_port"),
    )
    email_smtp_user: str = Field(
        default="",
        validation_alias=AliasChoices("EMAIL_SMTP_USER", "email_smtp_user"),
    )
    email_smtp_password: str = Field(
        default="",
        validation_alias=AliasChoices("EMAIL_SMTP_PASSWORD", "email_smtp_password"),
    )
    email_smtp_starttls: bool = Field(
        default=True,
        validation_alias=AliasChoices("EMAIL_SMTP_STARTTLS", "email_smtp_starttls"),
    )
    password_reset_ttl_minutes: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "PASSWORD_RESET_TTL_MINUTES", "password_reset_ttl_minutes"
        ),
    )

    # If true, trust X-Forwarded-For (leftmost) for client IP. Off by default
    # so attackers can't spoof their IP to bypass rate limits.
    trusted_proxy: bool = Field(
        default=False,
        validation_alias=AliasChoices("TRUSTED_PROXY", "trusted_proxy"),
    )

    @property
    def is_production(self) -> bool:
        return self.anvil_env.lower() == "production"

    @property
    def google_oauth_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def github_oauth_enabled(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret)

    @model_validator(mode="after")
    def _enforce_production_guardrails(self) -> "Settings":
        if not self.is_production:
            return self
        problems: list[str] = []
        if self.jwt_secret == _DEFAULT_JWT_SECRET or len(self.jwt_secret) < 32:
            problems.append(
                "JWT_SECRET must be set to a strong (>=32 char) random string in production"
            )
        # Only validate admin_password when admin auto-bootstrap is enabled.
        if self.admin_bootstrap and self.admin_password in ("", _DEFAULT_ADMIN_PASSWORD):
            problems.append(
                "ANVIL_ADMIN_PASSWORD must be a non-default strong password in production "
                "(or set ANVIL_ADMIN_BOOTSTRAP=false to skip admin auto-creation)"
            )
        if problems:
            raise ValueError(
                "Anvil refused to start due to insecure production config:\n  - "
                + "\n  - ".join(problems)
            )
        return self


settings = Settings()
