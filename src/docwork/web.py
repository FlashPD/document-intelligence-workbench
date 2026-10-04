"""Loopback-only browser review prototype using the existing durable stores.

This zero-dependency interface is for local demonstrations. A random session
token and same-origin mutation checks protect against casual cross-site access.
Reviewer identity comes from the server's OS account, never document/client text.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .baseline import extract_invoice
from .access import AccessDenied, LocalAccess
from .intake import IntakeStore, processing_profile
from .local_model import LocalModelConfig
from .ocr import MAX_FILE_BYTES, tesseract_page
from .review import ReviewBlocked, ReviewConflict
from .worker import process_one
from .supervisor import WorkerSupervisor
from .storage_budget import StorageLimitExceeded, DEFAULT_ARTIFACT_BYTES, DEFAULT_DISK_RESERVE_BYTES

DOCUMENT_ID = re.compile(r"[0-9a-f]{32}")
PAGE_ROUTE = re.compile(r"/api/documents/([0-9a-f]{32})/pages/([1-9]\d*)")
EXPORT_ROUTE = re.compile(r"/api/exports/([0-9a-f]{32})/(\d+)/(json|csv)/(invoice\.json|header\.csv|line-items\.csv)")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/pilot.js": ("pilot.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
DEMO_FILES = {"clean": "clean.png", "conflicting-total": "conflicting-total.png"}


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: IntakeStore, *, token: str | None = None,
                 model_config: LocalModelConfig | None = None, model_profile: str | None = None,
                 review_pilot=None, demo_replay: dict | None = None,
                 background_processing: bool = False, processor=process_one, cleanup=None):
        if address[0] not in ("127.0.0.1", "::1"):
            raise ValueError("Review server must bind to loopback")
        self.store = store
        self.access = LocalAccess(token)
        self.token = self.access.reviewer_token
        self.model_config = model_config
        self.model_profile = model_profile
        self.review_pilot = review_pilot
        if demo_replay is not None and (model_config is not None or review_pilot is not None):
            raise ValueError("Replay uses recorded candidates without a model or timed pilot")
        self.demo_replay = demo_replay
        self.repo_root = Path(__file__).resolve().parents[2]
        if background_processing and (demo_replay is not None or review_pilot is not None):
            raise ValueError("Recorded replay and timed pilots cannot run a live worker")
        self.supervisor = None
        super().__init__(address, ReviewHandler)
        if background_processing:
            self.supervisor = WorkerSupervisor(store, model_config=model_config, processor=processor, cleanup=cleanup)
            self.supervisor.start()

    def server_close(self):
        if self.supervisor:
            self.supervisor.close()
        super().server_close()

    @property
    def origin(self) -> str:
        host = "[::1]" if self.server_address[0] == "::1" else "127.0.0.1"
        return f"http://{host}:{self.server_port}"


class ReviewHandler(BaseHTTPRequestHandler):
    server: ReviewServer

    def log_message(self, format: str, *args) -> None:
        # Avoid writing document names, field values, tokens, or query strings.
        pass

    def _send(self, status: int, body: bytes, media_type: str, *, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: object) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _principal(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        session = cookie.get("docwork_session")
        return self.server.access.authenticate(session.value if session else None,
                                               self.headers.get("Authorization"))

    def _authorized(self) -> bool:
        return self._principal() is not None

    def _review_actor(self, data: dict) -> str:
        return self.server.access.review_actor(self._principal(), data)

    def _read_body(self, maximum: int) -> bytes:
        raw = self.headers.get("Content-Length")
        if raw is None or not raw.isdecimal():
            raise ValueError("Content-Length is required")
        length = int(raw)
        if length < 1 or length > maximum:
            raise ValueError("Request body exceeds its allowed size")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("Incomplete request body")
        return body

    def _input(self) -> dict:
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise ValueError("Expected application/json")
        data = json.loads(self._read_body(64 * 1024))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    @staticmethod
    def _required(data: dict, key: str, expected: type):
        value = data.get(key)
        if type(value) is not expected:
            raise ValueError(f"{key} must be {expected.__name__}")
        return value

    @staticmethod
    def _document_route(path: str) -> tuple[str, str] | None:
        parts = path.split("/")
        if len(parts) not in (4, 5) or parts[:3] != ["", "api", "documents"] or not DOCUMENT_ID.fullmatch(parts[3]):
            return None
        return parts[3], parts[4] if len(parts) == 5 else ""

    def _handle_error(self, exc: Exception) -> None:
        if isinstance(exc, AccessDenied):
            status = HTTPStatus.FORBIDDEN
        elif isinstance(exc, StorageLimitExceeded):
            status = HTTPStatus.INSUFFICIENT_STORAGE
        elif isinstance(exc, KeyError):
            status = HTTPStatus.NOT_FOUND
        elif isinstance(exc, ReviewConflict):
            status = HTTPStatus.CONFLICT
        elif isinstance(exc, ReviewBlocked):
            status = HTTPStatus.UNPROCESSABLE_ENTITY
        elif isinstance(exc, (ValueError, json.JSONDecodeError)):
            status = HTTPStatus.BAD_REQUEST
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        message = str(exc) if status != HTTPStatus.INTERNAL_SERVER_ERROR else "Internal server error"
        self._json(status, {"error": message})

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path == "/" and not self._authorized() and self.headers.get("Authorization") is None:
            candidate = parse_qs(url.query).get("token", [""])[0]
            if candidate and secrets.compare_digest(candidate.encode(), self.server.token.encode()):
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"docwork_session={self.server.token}; HttpOnly; SameSite=Strict; Path=/")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "Open the session URL printed by docwork serve"})
            return
        try:
            self.server.access.require_route(self._principal(), "GET", url.path)
            if url.path in STATIC:
                filename, media = STATIC[url.path]
                content = (self.server.repo_root / "ui" / filename).read_bytes()
                self._send(HTTPStatus.OK, content, media)
            elif url.path == "/api/documents":
                self._json(HTTPStatus.OK, self.server.store.list_documents())
            elif url.path == "/api/runtime":
                config = self.server.model_config
                self._json(HTTPStatus.OK, {"principal": self._principal().as_dict(),
                                          "managed_model": config is not None,
                                          "model_id": config.model_id if config else None,
                                          "profile": self.server.model_profile,
                                          "demo_replay": getattr(self.server, "demo_replay", None),
                                          "review_pilot": getattr(self.server, "review_pilot", None) is not None,
                                          "background_processing": getattr(self.server, "supervisor", None) is not None,
                                          "worker": self.server.supervisor.status() if getattr(self.server, "supervisor", None) else None})
            elif url.path == "/api/storage":
                self._json(HTTPStatus.OK, self.server.store.storage_budget.inventory())
            elif url.path == "/api/deletions":
                self._json(HTTPStatus.OK, self.server.store.deletions())
            elif url.path == "/api/batches":
                self._json(HTTPStatus.OK, self.server.store.batches())
            elif re.fullmatch(r"/api/batches/[0-9a-f]{32}", url.path):
                self._json(HTTPStatus.OK, self.server.store.batch_status(url.path.rsplit("/", 1)[1]))
            elif re.fullmatch(r"/api/deletions/[0-9a-f]{32}", url.path):
                self._json(HTTPStatus.OK, self.server.store.deletion_status(url.path.rsplit("/", 1)[1]))
            elif url.path == "/api/pilot" and getattr(self.server, "review_pilot", None) is not None:
                self._json(HTTPStatus.OK, self.server.review_pilot.view())
            elif match := EXPORT_ROUTE.fullmatch(url.path):
                doc_id, revision, format, filename = match.groups()
                content, media = self.server.store.exported_file(doc_id, int(revision), format, filename)
                self._send(HTTPStatus.OK, content, media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
            elif match := PAGE_ROUTE.fullmatch(url.path):
                revision = parse_qs(url.query).get("revision", [None])[0]
                self._page(match.group(1), int(match.group(2)), revision=int(revision) if revision else None)
            elif route := self._document_route(url.path):
                doc_id, action = route
                if action == "":
                    if getattr(self.server, "review_pilot", None) is not None:
                        self.server.review_pilot.guard_view(doc_id)
                    revision = parse_qs(url.query).get("revision", [None])[0]
                    self._json(HTTPStatus.OK, self.server.store.get(doc_id, int(revision) if revision else None))
                elif action == "status":
                    self._json(HTTPStatus.OK, self.server.store.status(doc_id))
                elif action == "history":
                    self._json(HTTPStatus.OK, self.server.store.history(doc_id))
                elif action == "attempts":
                    self._json(HTTPStatus.OK, self.server.store.attempts(doc_id))
                elif action == "page":
                    self._page(doc_id, 1)
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown route"})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown route"})
        except (AccessDenied, KeyError, ValueError, OSError, ReviewConflict, ReviewBlocked) as exc:
            self._handle_error(exc)

    def _page(self, doc_id: str, number: int, *, revision: int | None = None) -> None:
        if getattr(self.server, "review_pilot", None) is not None:
            self.server.review_pilot.guard_view(doc_id)
        count = len(self.server.store.get(doc_id, revision)["pages"]) if revision else self.server.store.status(doc_id)["page_count"]
        if number > count:
            raise KeyError("Unknown page")
        try:
            page = self.server.store.page_image_path(doc_id, number, revision=revision)
        except ReviewConflict:
            status = self.server.store.status(doc_id)
            if number != 1 or status["page_image_sha256"] is not None:
                raise
            sample = next((name for name in DEMO_FILES.values()
                           if hashlib.sha256((self.server.repo_root / "samples" / name).read_bytes()).hexdigest() == status["source_sha256"]), None)
            if sample is None:
                raise KeyError("Rendered page is unavailable") from None
            page = self.server.repo_root / "samples" / sample
        self._send(HTTPStatus.OK, page.read_bytes(), "image/png")

    def _notify_worker(self):
        if getattr(self.server, "supervisor", None):
            self.server.supervisor.notify()

    def _profile(self, extractor: str):
        if extractor == "span_llm":
            config = self.server.model_config
            if config is None:
                raise ValueError("Start make dev-model to queue the pinned local model")
            return processing_profile(extractor, model_id=config.model_id,
                                      timeout_seconds=config.timeout_seconds, max_output_tokens=config.max_output_tokens)
        return processing_profile(extractor)

    def do_POST(self) -> None:
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "Session required"})
            return
        if self.headers.get("Origin") != self.server.origin:
            self._json(HTTPStatus.FORBIDDEN, {"error": "Same-origin request required"})
            return
        path = urlsplit(self.path).path
        try:
            self.server.access.require_route(self._principal(), "POST", path)
            pilot = getattr(self.server, "review_pilot", None)
            live_action = path in ("/api/upload", "/api/process-one", "/api/demo/seed", "/api/batches") or path.rsplit("/", 1)[-1] in ("cancel", "reprocess", "delete")
            if getattr(self.server, "demo_replay", None) is not None and live_action:
                raise ReviewBlocked("Replay uses recorded candidates. Start make dev for live processing.")
            elif path == "/api/pilot/start" and pilot is not None:
                data = self._input()
                self._json(HTTPStatus.CREATED, pilot.start(self._required(data, "document_id", str),
                                                          self._review_actor(data)))
            elif path == "/api/pilot/event" and pilot is not None:
                data = self._input()
                actor = self._review_actor(data)
                trial = pilot.view().get("trials", [])
                active = next((item for item in trial if item["id"] == data.get("trial_id")), None)
                if active is not None and active["actor"] != actor:
                    raise AccessDenied("Pilot trial belongs to a different reviewer")
                self._json(HTTPStatus.OK, pilot.event(self._required(data, "trial_id", str),
                                                    self._required(data, "kind", str),
                                                    self._required(data, "event_id", str)))
            elif pilot is not None and live_action:
                raise ReviewBlocked("The pilot uses its declared precomputed documents")
            elif path == "/api/batches":
                data = self._input()
                profile = self._profile(data.get("extractor", "ocr_rules"))
                batch_id = self.server.store.create_batch(data.get("count"), profile=profile)
                self._json(HTTPStatus.CREATED, self.server.store.batch_status(batch_id))
            elif path == "/api/upload":
                name = unquote(self.headers.get("X-File-Name", ""))
                media = self.headers.get("Content-Type", "")
                import io
                batch_id = self.headers.get("X-Batch-Id")
                position = self.headers.get("X-Batch-Position")
                try:
                    doc_id = self.server.store.submit(io.BytesIO(self._read_body(MAX_FILE_BYTES)), name, media,
                                                     profile=self._profile(self.headers.get("X-Extractor", "ocr_rules")),
                                                     batch_id=batch_id, batch_position=int(position) if position is not None else None)
                except (ValueError, OSError, ReviewConflict) as exc:
                    if batch_id and position is not None and position.isdecimal():
                        self.server.store.reject_batch_item(batch_id, int(position), getattr(exc, "code", "UPLOAD_REJECTED"))
                    raise
                self._notify_worker()
                self._json(HTTPStatus.CREATED, self.server.store.status(doc_id))
            elif path == "/api/process-one":
                data = self._input()
                extractor = data.get("extractor", "ocr_rules")
                if extractor not in ("ocr_rules", "span_llm"):
                    raise ValueError("Unknown extractor profile")
                if getattr(self.server, "supervisor", None):
                    if "model_endpoint" in data or "model_id" in data:
                        raise ValueError("Queued processing uses the server-owned model profile")
                    self._notify_worker()
                    self._json(HTTPStatus.ACCEPTED, {"status": "QUEUED", "worker": self.server.supervisor.status()})
                    return
                model_config = None
                if extractor == "span_llm":
                    configured = self.server.model_config
                    if configured is not None:
                        if "model_endpoint" in data or "model_id" in data:
                            raise ValueError("The managed model is selected by the server; omit endpoint overrides")
                        model_config = configured
                    else:
                        endpoint = self._required(data, "model_endpoint", str)
                        model_id = self._required(data, "model_id", str)
                        model_config = LocalModelConfig(endpoint, model_id)
                doc_id = process_one(self.server.store, "browser-worker", extractor=extractor,
                                     model_config=model_config)
                self._json(HTTPStatus.OK, self.server.store.status(doc_id) if doc_id else {"status": "IDLE"})
            elif path == "/api/demo/seed":
                fixture = self._required(self._input(), "fixture", str)
                if fixture not in DEMO_FILES:
                    raise ValueError("Unknown demo fixture")
                source = self.server.repo_root / "samples" / DEMO_FILES[fixture]
                page = tesseract_page(source)
                record = extract_invoice(page)
                doc_id = self.server.store.ingest(hashlib.sha256(source.read_bytes()).hexdigest(), source.name, page, record)
                self._json(HTTPStatus.CREATED, self.server.store.get(doc_id))
            elif route := self._document_route(path):
                doc_id, action = route
                data = self._input()
                actor = self._review_actor(data)
                if pilot is not None:
                    pilot.guard(doc_id, actor)
                if action == "edit":
                    revision = self._required(data, "revision", int)
                    field_path = self._required(data, "path", str)
                    refs = data.get("evidence_ids")
                    if refs is not None and (not isinstance(refs, list) or not all(isinstance(value, str) for value in refs)):
                        raise ValueError("evidence_ids must be a string list")
                    value = data.get("value")
                    if value is not None and not isinstance(value, str):
                        raise ValueError("value must be text or null")
                    new_revision = self.server.store.edit(doc_id, revision, field_path, value,
                                                          actor, tuple(refs) if refs is not None else None)
                    self._json(HTTPStatus.OK, self.server.store.get(doc_id, new_revision))
                elif action == "acknowledge":
                    self.server.store.acknowledge(
                        doc_id, self._required(data, "revision", int), self._required(data, "code", str),
                        self._required(data, "path", str), self._required(data, "reason", str),
                        actor,
                    )
                    self._json(HTTPStatus.OK, self.server.store.get(doc_id))
                elif action == "approve":
                    approval = self.server.store.approve(doc_id, self._required(data, "revision", int),
                                                         actor)
                    self._json(HTTPStatus.OK, approval)
                elif action == "export":
                    manifest = self.server.store.export(doc_id, self._required(data, "format", str))
                    for file in manifest["files"]:
                        file["url"] = f"/api/exports/{doc_id}/{manifest['revision']}/{manifest['format']}/{Path(file['path']).name}"
                    self._json(HTTPStatus.OK, manifest)
                elif action == "retry":
                    self.server.store.retry(doc_id)
                    self._notify_worker()
                    self._json(HTTPStatus.OK, self.server.store.status(doc_id))
                elif action == "cancel":
                    self.server.store.cancel(doc_id)
                    self._notify_worker()
                    self._json(HTTPStatus.ACCEPTED, self.server.store.status(doc_id))
                elif action == "reprocess":
                    profile = self._profile(data.get("extractor", "ocr_rules"))
                    self.server.store.reprocess(doc_id, profile=profile, reparse=data.get("reparse", False),
                                                expected_revision=self._required(data, "revision", int))
                    self._notify_worker()
                    self._json(HTTPStatus.ACCEPTED, self.server.store.status(doc_id))
                elif action == "delete":
                    result = self.server.store.request_delete(doc_id)
                    self._notify_worker()
                    self._json(HTTPStatus.ACCEPTED, result)
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown route"})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown route"})
        except (AccessDenied, KeyError, ValueError, OSError, ReviewConflict, ReviewBlocked) as exc:
            self._handle_error(exc)


def serve(database: Path, object_root: Path, port: int = 8765, *,
          model_config: LocalModelConfig | None = None, model_profile: str | None = None,
          demo_replay: dict | None = None, max_artifact_bytes: int | None = None,
          disk_reserve_bytes: int | None = None) -> None:
    store = IntakeStore(database, object_root, max_artifact_bytes=max_artifact_bytes,
                        disk_reserve_bytes=disk_reserve_bytes)
    with ReviewServer(("127.0.0.1", port), store,
                      model_config=model_config, model_profile=model_profile, demo_replay=demo_replay,
                      background_processing=demo_replay is None) as server:
        print(f"Open {server.origin}/?token={server.token}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
