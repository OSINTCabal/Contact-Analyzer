from __future__ import annotations

import base64
import json
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests
import websocket


class CDPError(RuntimeError):
    pass


class CDPTab:
    def __init__(self, target_id: str, ws_url: str, browser_endpoint: str):
        self.target_id = target_id
        self.browser_endpoint = browser_endpoint.rstrip("/")
        self.ws = websocket.create_connection(
            ws_url,
            timeout=30,
            enable_multithread=True,
            suppress_origin=True,
        )
        self._next_id = 1
        self._pending: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self._lock = threading.RLock()
        self._closed = False
        self._responses: dict[str, dict[str, Any]] = {}
        self._finished: set[str] = set()
        self._processed: set[str] = set()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call(
            "Network.enable",
            {
                "maxTotalBufferSize": 100_000_000,
                "maxResourceBufferSize": 25_000_000,
                "maxPostDataSize": 5_000_000,
            },
        )

    def _read_loop(self) -> None:
        try:
            while not self._closed:
                raw = self.ws.recv()
                if not raw:
                    break
                message = json.loads(raw)
                msg_id = message.get("id")
                if msg_id is not None:
                    with self._lock:
                        pending = self._pending.get(msg_id)
                    if pending:
                        pending[1].update(message)
                        pending[0].set()
                    continue

                method = message.get("method")
                params = message.get("params") or {}
                if method == "Network.responseReceived":
                    request_id = params.get("requestId")
                    if request_id:
                        with self._lock:
                            self._responses[request_id] = params
                elif method == "Network.loadingFinished":
                    request_id = params.get("requestId")
                    if request_id:
                        with self._lock:
                            self._finished.add(request_id)
        except Exception:
            pass

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 60) -> dict[str, Any]:
        with self._lock:
            msg_id = self._next_id
            self._next_id += 1
            event = threading.Event()
            result: dict[str, Any] = {}
            self._pending[msg_id] = (event, result)
        payload: dict[str, Any] = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        self.ws.send(json.dumps(payload))
        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(msg_id, None)
            raise TimeoutError(f"CDP timeout calling {method}")
        with self._lock:
            self._pending.pop(msg_id, None)
        if "error" in result:
            raise CDPError(f"{method}: {result['error']}")
        return result.get("result", {})

    def evaluate(self, expression: str, await_promise: bool = False, timeout: float = 60) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
                "userGesture": True,
            },
            timeout=timeout,
        )
        if "exceptionDetails" in result:
            description = result["exceptionDetails"].get("exception", {}).get("description")
            raise CDPError(description or str(result["exceptionDetails"]))
        return result.get("result", {}).get("value")

    def navigate(
        self,
        url: str,
        settle_seconds: float = 3.0,
        timeout: float = 60,
    ) -> None:
        self.call("Page.navigate", {"url": url}, timeout=timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                state = self.evaluate(
                    "document.readyState",
                    timeout=max(0.25, min(10, deadline - time.time())),
                )
            except Exception:
                time.sleep(0.25)
                continue
            if state in {"interactive", "complete"}:
                break
            time.sleep(0.25)
        time.sleep(settle_seconds)

    def current_url(self) -> str:
        return str(self.evaluate("location.href") or "")

    def title(self) -> str:
        return str(self.evaluate("document.title") or "")

    def clear_network_capture(self) -> None:
        with self._lock:
            self._responses.clear()
            self._finished.clear()
            self._processed.clear()

    def drain_json_responses(self, keywords: tuple[str, ...]) -> list[tuple[str, Any]]:
        lowered = tuple(x.casefold() for x in keywords)
        with self._lock:
            candidates = [
                (request_id, self._responses[request_id])
                for request_id in self._finished
                if request_id in self._responses and request_id not in self._processed
            ]
        output: list[tuple[str, Any]] = []
        for request_id, params in candidates:
            response = params.get("response") or {}
            url = str(response.get("url") or "")
            mime = str(response.get("mimeType") or "").casefold()
            resource_type = str(params.get("type") or "")
            if lowered and not any(keyword in url.casefold() for keyword in lowered):
                with self._lock:
                    self._processed.add(request_id)
                continue
            if resource_type not in {"XHR", "Fetch"} and "json" not in mime:
                with self._lock:
                    self._processed.add(request_id)
                continue
            try:
                body = self.call("Network.getResponseBody", {"requestId": request_id}, timeout=15)
                raw = body.get("body") or ""
                if body.get("base64Encoded"):
                    raw = base64.b64decode(raw).decode("utf-8", errors="replace")
                parsed = json.loads(raw)
                output.append((url, parsed))
            except Exception:
                pass
            finally:
                with self._lock:
                    self._processed.add(request_id)
        return output

    def screenshot(self, path: Path) -> None:
        result = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(result["data"]))

    def save_html(self, path: Path) -> None:
        html = self.evaluate("document.documentElement.outerHTML") or ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(html), encoding="utf-8")

    def close(self, close_target: bool = True) -> None:
        self._closed = True
        try:
            self.ws.close()
        except Exception:
            pass
        if close_target:
            try:
                requests.get(f"{self.browser_endpoint}/json/close/{self.target_id}", timeout=5)
            except Exception:
                pass


class CDPBrowser:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint.rstrip("/")

    def check(self) -> dict[str, Any]:
        response = requests.get(f"{self.endpoint}/json/version", timeout=5)
        response.raise_for_status()
        return response.json()

    def new_tab(self, url: str = "about:blank") -> CDPTab:
        encoded = urllib.parse.quote(url, safe="")
        response = requests.put(f"{self.endpoint}/json/new?{encoded}", timeout=10)
        response.raise_for_status()
        data = response.json()
        return CDPTab(data["id"], data["webSocketDebuggerUrl"], self.endpoint)
