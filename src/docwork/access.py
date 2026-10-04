"""Ephemeral local capabilities; processing never grants review authority.

The operating-system account running the server is the trusted reviewer. This
is a single-user boundary, not an identity provider or a sandbox for host code.
"""
from __future__ import annotations

import os
import pwd
import secrets
from dataclasses import dataclass


class AccessDenied(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    actor: str
    role: str

    def as_dict(self) -> dict:
        return {"actor": self.actor, "role": self.role}


class LocalAccess:
    def __init__(self, reviewer_token: str | None = None):
        self.reviewer_token = reviewer_token or secrets.token_urlsafe(32)
        self.processing_token = secrets.token_urlsafe(32)
        self.reviewer = Principal(f"local:{pwd.getpwuid(os.getuid()).pw_name}", "reviewer")
        self.processor = Principal("service:processor", "processor")

    def authenticate(self, session: str | None, authorization: str | None) -> Principal | None:
        # Never upgrade a processing request via an accompanying review cookie.
        if authorization is not None:
            if secrets.compare_digest(authorization.encode(), f"Bearer {self.processing_token}".encode()):
                return self.processor
            return None
        if session is not None and secrets.compare_digest(session.encode(), self.reviewer_token.encode()):
            return self.reviewer
        return None

    @staticmethod
    def require_route(principal: Principal, method: str, path: str) -> None:
        if principal.role == "reviewer":
            return
        if principal.role == "processor" and method == "POST" and path in (
            "/api/upload", "/api/batches", "/api/process-one",
        ):
            return
        raise AccessDenied("Reviewer permission required")

    @staticmethod
    def review_actor(principal: Principal, data: dict) -> str:
        if principal.role != "reviewer":
            raise AccessDenied("Reviewer permission required")
        # Older clients may echo the established actor, but cannot choose one.
        if "actor" in data and data["actor"] != principal.actor:
            raise AccessDenied("Actor must match the server-established reviewer")
        return principal.actor
