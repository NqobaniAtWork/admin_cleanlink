from pydantic import BaseModel, EmailStr

from app.models.enums import AdminRole
from app.security.roles import has_permission


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AdminSession(BaseModel):
    user_id: str
    admin_profile_id: str | None = None
    email: EmailStr | str
    role: AdminRole
    display_name: str = "Admin"
    custom_permissions: list[str] = []

    def has_permission(self, permission: str) -> bool:
        return has_permission(self.role, permission, self.custom_permissions)


class AdminCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    role: AdminRole
    permissions: list[str] = []


class AdminPermissionsUpdate(BaseModel):
    permissions: list[str] = []
    role: AdminRole | None = None
