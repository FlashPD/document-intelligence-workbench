"""One owned serial worker; durable claims remain the authority across restarts."""

from __future__ import annotations

import threading
import uuid

from .worker import process_one


class WorkerSupervisor:
    def __init__(self, store, *, model_config=None, processor=process_one, cleanup=None):
        self.store, self.model_config = store, model_config
        self.processor, self.cleanup = processor, cleanup
        self.stop = threading.Event()
        self.wakeup = threading.Event()
        self.last_error = None
        self.worker_id = "background-" + uuid.uuid4().hex
        self.thread = threading.Thread(target=self._run, name="docwork-background-worker", daemon=True)

    def start(self):
        self.thread.start()

    def notify(self):
        self.wakeup.set()

    def close(self):
        self.stop.set()
        self.notify()
        if self.thread.is_alive():
            self.thread.join()

    def status(self):
        return {"running": self.thread.is_alive(), "last_error": self.last_error}

    def _run(self):
        while not self.stop.is_set():
            try:
                self.store.recover_stops(cleanup=self.cleanup)
                self.store.run_deletions(cleanup=self.cleanup)
                document = self.processor(self.store, self.worker_id, model_config=self.model_config,
                                          honor_job_profile=True, stop_event=self.stop)
                self.last_error = None
                if document:
                    continue
            except Exception as exc:
                # Runtime status must not expose document text, credentials or paths.
                self.last_error = getattr(exc, "code", "WORKER_ERROR")
            self.wakeup.wait(.25)
            self.wakeup.clear()
