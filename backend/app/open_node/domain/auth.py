from pydantic import BaseModel, Field, SecretStr


class AdministratorCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.@-]+$")
    password: SecretStr = Field(min_length=12, max_length=1024)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=1024)


class PasswordChangeRequest(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: SecretStr = Field(min_length=12, max_length=1024)


class SessionResponse(BaseModel):
    configured: bool
    authenticated: bool = False
    username: str | None = None
    csrf_token: str | None = None
