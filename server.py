"""FastAPI server: runs the call session and streams transcript, AI suggestions
and ticket status to the agent's browser over a WebSocket."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .assistant import CallAssistant
from .config import settings
from .kb import KnowledgeBase
from .transcriber import AzureCallTranscriber, ScriptedTranscriber

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("call-assist")

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Lenovo Call Assist")
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")

# ---------- shared state ----------
kb = KnowledgeBase()
dynamics = None
assistant: CallAssistant | None = None
clients: set[WebSocket] = set()


async def broadcast(msg: dict) -> None:
    for ws in list(clients):
        try:
            await ws.send_json(msg)
        except Exception:
            clients.discard(ws)


class CallSession:
    def __init__(self) -> None:
        self.lines: list[dict] = []
        self.analysis: dict | None = None
        self.case: dict | None = None
        self.contact_id: str | None = None
        self.transcriber = None
        self._dirty = False
        self._worker: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.started = time.time()

    @property
    def transcript(self) -> str:
        return "\n".join(f"{l['speaker']}: {l['text']}" for l in self.lines)

    async def on_text(self, speaker: str, text: str, final: bool) -> None:
        await broadcast({"type": "partial" if not final else "line", "speaker": speaker, "text": text})
        if final:
            self.lines.append({"speaker": speaker, "text": text, "t": round(time.time() - self.started, 1)})
            self._dirty = True

    async def start(self, mode: str, script: str | None) -> None:
        loop = asyncio.get_running_loop()
        if mode == "demo":
            self.transcriber = ScriptedTranscriber(loop, self.on_text, script or str(ROOT / "samples" / "demo_call.txt"))
        else:
            self.transcriber = AzureCallTranscriber(loop, self.on_text)
        await self.transcriber.start()
        self._worker = asyncio.create_task(self._analysis_loop())

    async def stop(self) -> None:
        if self.transcriber:
            await self.transcriber.stop()
        if self._worker:
            self._worker.cancel()
        await self.run_analysis(force=True)
        await self.finalize_ticket()

    # ---------- AI ----------
    async def _analysis_loop(self) -> None:
        while True:
            await asyncio.sleep(settings.analysis_interval_sec)
            if self._dirty:
                await self.run_analysis()

    async def run_analysis(self, force: bool = False) -> None:
        if not assistant or not self.lines or (not self._dirty and not force):
            return
        async with self._lock:
            self._dirty = False
            try:
                result, articles = await assistant.analyze(self.transcript, self.analysis)
            except Exception as e:  # keep the call going if one request fails
                log.exception("Analysis failed")
                await broadcast({"type": "error", "message": f"AI analysis failed: {e}"})
                return
            self.analysis = result
            await broadcast({
                "type": "analysis",
                "analysis": result,
                "articles": [{"id": a.id, "title": a.title, "source": a.source} for a in articles],
            })
            if settings.auto_create_case:
                await self.sync_ticket()

    # ---------- ticketing ----------
    def _ready_for_ticket(self) -> bool:
        a = self.analysis or {}
        c = a.get("customer") or {}
        has_name = bool(c.get("last_name") or c.get("full_name"))
        return bool(a.get("reason_for_call")) and has_name and bool(c.get("email") or c.get("phone"))

    def _description(self) -> str:
        a = self.analysis or {}
        d = a.get("device") or {}
        parts = [a.get("issue_summary", "")]
        dev = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in d.items() if v)
        if dev:
            parts.append(f"Device — {dev}")
        if a.get("parts_needed"):
            parts.append("Suggested parts — " + "; ".join(
                f"{p['part']}{' (' + p['part_number'] + ')' if p.get('part_number') else ''} [{p['likelihood']}]"
                for p in a["parts_needed"]))
        parts.append("Created automatically by Call Assist from the live call.")
        return "\n\n".join(p for p in parts if p)

    async def sync_ticket(self, manual: bool = False) -> None:
        if not (manual or self._ready_for_ticket()):
            return
        a = self.analysis or {}
        if dynamics is None:
            if not self.case:
                self.case = {"incidentid": "dry-run", "ticketnumber": "DRY-RUN"}
                await broadcast({"type": "ticket", "status": "dry_run", "case": self.case,
                                 "customer": a.get("customer"), "title": a.get("reason_for_call"),
                                 "description": self._description()})
            return
        try:
            if not self.case:
                self.contact_id = await asyncio.to_thread(dynamics.upsert_contact, a.get("customer") or {})
                serial = (a.get("device") or {}).get("serial_number")
                self.case = await asyncio.to_thread(
                    dynamics.create_case, self.contact_id, a.get("reason_for_call") or "Phone support call",
                    self._description(), serial)
                status = "created"
            else:
                await asyncio.to_thread(dynamics.update_case, self.case["incidentid"], self._description())
                status = "updated"
            await broadcast({"type": "ticket", "status": status, "case": self.case,
                             "customer": a.get("customer"), "title": a.get("reason_for_call")})
        except Exception as e:
            log.exception("Dynamics sync failed")
            await broadcast({"type": "error", "message": f"Dynamics sync failed: {e}"})

    async def finalize_ticket(self) -> None:
        if not self.case or dynamics is None or self.case["incidentid"] == "dry-run":
            return
        try:
            a = self.analysis or {}
            steps = "\n".join(f"- {s['step']}" for s in a.get("troubleshooting_steps", []))
            await asyncio.to_thread(dynamics.add_note, self.case["incidentid"], "AI troubleshooting summary", steps or "n/a")
            if settings.attach_transcript_note:
                await asyncio.to_thread(dynamics.add_note, self.case["incidentid"], "Call transcript", self.transcript)
            await broadcast({"type": "ticket", "status": "finalized", "case": self.case})
        except Exception as e:
            await broadcast({"type": "error", "message": f"Could not add notes: {e}"})


session: CallSession | None = None


# ---------- startup ----------
@app.on_event("startup")
async def startup() -> None:
    global dynamics, assistant
    n = kb.load_folder(str(ROOT / settings.kb_folder))
    log.info("Loaded %d local KB articles", n)
    if settings.dynamics_configured:
        from .dynamics import DynamicsClient
        try:
            dynamics = DynamicsClient()
            if settings.sync_dynamics_kb:
                arts = await asyncio.to_thread(dynamics.fetch_knowledge_articles)
                kb.add_articles(arts)
                log.info("Loaded %d Dynamics knowledge articles", len(arts))
        except Exception:
            log.exception("Dynamics unavailable — running ticketing in dry-run mode")
            dynamics = None
    else:
        log.warning("Dynamics not configured — tickets will be shown as dry-run only")
    try:
        assistant = CallAssistant(kb)
    except RuntimeError as e:
        log.error("%s — AI suggestions disabled", e)


# ---------- API ----------
class StartReq(BaseModel):
    mode: str = "demo"  # "demo" or "live"
    script: str | None = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.get("/api/status")
async def status() -> dict:
    return {"kb_articles": len(kb.articles), "dynamics": dynamics is not None,
            "ai": assistant is not None, "in_call": session is not None}


@app.post("/api/call/start")
async def start_call(req: StartReq) -> dict:
    global session
    if session:
        await session.stop()
    session = CallSession()
    await session.start(req.mode, req.script)
    await broadcast({"type": "call", "status": "started", "mode": req.mode})
    return {"ok": True}


@app.post("/api/call/stop")
async def stop_call() -> dict:
    global session
    if session:
        await session.stop()
        await broadcast({"type": "call", "status": "ended"})
        session = None
    return {"ok": True}


@app.post("/api/ticket/create")
async def create_ticket() -> dict:
    if not session or not session.analysis:
        return {"ok": False, "error": "No call analysis yet"}
    await session.sync_ticket(manual=True)
    return {"ok": True, "case": session.case}


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
