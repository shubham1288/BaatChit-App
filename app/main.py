import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.core.rate_limiter import message_rate_limiter
from app.core.security import decode_access_token
from app.db.database import Base, engine, get_db, SessionLocal
from app.db import models  # noqa: F401
from app.routes.auth_routes import router as auth_router
from app.routes.conversation_routes import router as conv_router
from app.routes.group_routes import router as group_router
from app.routes.user_routes import router as user_router
from app.services.group_service import (
    get_member_ids,
    is_member,
    send_group_message,
)
from app.services.private_chat_service import (
    create_private_message,
    mark_delivered,
)
from app.services.user_service import get_user_by_username, get_user_by_id
from app.websocket.connection_manager import manager
from app.core.crypto import decrypt_content

setup_logging()
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


# ─────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s [%s]", settings.app_name, settings.environment)
    Base.metadata.create_all(bind=engine)
    yield
    logger.info("Shutting down %s", settings.app_name)


# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    description="Production-ready WhatsApp-like chat backend",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files & templates
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Routers
app.include_router(auth_router)
app.include_router(user_router)
app.include_router(conv_router)
app.include_router(group_router)


# ─────────────────────────────────────────────
# PAGE ROUTES
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def chat_page(request: Request):
    return templates.TemplateResponse(request, "chat.html")


@app.get("/login-page", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.get("/signup-page", response_class=HTMLResponse, include_in_schema=False)
async def signup_page(request: Request):
    return templates.TemplateResponse(request, "signup.html")


# ─────────────────────────────────────────────
# WEBSOCKET
# ─────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        protocols = websocket.headers.get("sec-websocket-protocol", "").split(",")
        token = protocols[0].strip() if protocols else None
    
    if not token:
        await websocket.close(code=1008)
        return

    username = decode_access_token(token)
    if not username:
        logger.warning("WebSocket token decode failed")
        await websocket.close(code=1008)
        return

    await websocket.accept(subprotocol=websocket.headers.get("sec-websocket-protocol"))

    db = SessionLocal()
    try:
        current_user = get_user_by_username(db, username)
        if not current_user or not current_user.is_active:
            await websocket.close(code=1008, reason="User not found or inactive")
            return

        await manager.connect(username, websocket)

        while True:
            try:
                data = await websocket.receive_json()
            except Exception as e:
                logger.debug("WebSocket receive error for %s: %s", username, e)
                break
            
            msg_type = data.get("type")
            logger.debug("Received WS message from %s: %s", username, msg_type)

            # ── PRIVATE MESSAGE ──────────────────────
            if msg_type == "private":
                receiver_username = data.get("receiver")
                content = data.get("content", "")
                
                if not receiver_username or not content or len(content) > 5000:
                    continue

                try:
                    message_rate_limiter.check(username)
                except Exception:
                    await manager.send_to_user(
                        username, {"type": "error", "detail": "Rate limit exceeded"}
                    )
                    continue

                receiver = get_user_by_username(db, receiver_username)
                if not receiver:
                    continue

                reply_to_id = data.get("reply_to_id")
                msg = create_private_message(db, current_user.id, receiver.id, content, reply_to_id)

                payload = {
                    "type": "private_message",
                    "id": msg.id,
                    "sender": username,
                    "receiver": receiver_username,
                    "content": decrypt_content(msg.content),
                    "status": msg.status.value,
                    "timestamp": msg.created_at.isoformat(),
                    "reply_to_id": msg.reply_to_id,
                    "reply_content": data.get("reply_content"),
                    "reply_sender": data.get("reply_sender"),
                }
                
                if msg.reply_to and not payload["reply_content"]:
                    payload["reply_content"] = decrypt_content(msg.reply_to.content)
                    payload["reply_sender"] = msg.reply_to.sender.username

                if manager.is_online(receiver_username):
                    await manager.send_to_user(receiver_username, payload)
                    mark_delivered(db, msg.id)
                    payload["status"] = "delivered"

                await manager.send_to_user(username, payload)

            # ── GROUP MESSAGE ────────────────────────
            elif msg_type == "group":
                group_id = data.get("group_id")
                content = data.get("content", "")
                
                if not group_id or not content or len(content) > 5000:
                    continue

                try:
                    group_id = int(group_id)
                except (TypeError, ValueError):
                    continue

                if not is_member(db, group_id, current_user.id):
                    continue

                try:
                    message_rate_limiter.check(username)
                except Exception:
                    await manager.send_to_user(
                        username, {"type": "error", "detail": "Rate limit exceeded"}
                    )
                    continue

                reply_to_id = data.get("reply_to_id")
                msg = send_group_message(db, group_id, current_user.id, content, reply_to_id)

                member_ids = get_member_ids(db, group_id)
                member_usernames = []
                for uid in member_ids:
                    u = get_user_by_id(db, uid)
                    if u:
                        member_usernames.append(u.username)

                payload = {
                    "type": "group_message",
                    "id": msg.id,
                    "group_id": group_id,
                    "sender": username,
                    "content": decrypt_content(msg.content),
                    "timestamp": msg.created_at.isoformat(),
                    "reply_to_id": msg.reply_to_id,
                    "reply_content": data.get("reply_content"),
                    "reply_sender": data.get("reply_sender"),
                }
                
                if msg.reply_to and not payload["reply_content"]:
                    payload["reply_content"] = decrypt_content(msg.reply_to.content)
                    payload["reply_sender"] = msg.reply_to.sender.username

                await manager.broadcast_to_group(member_usernames, payload)

            # ── EDIT PRIVATE ────────────────────────
            elif msg_type == "edit_private":
                msg_id = data.get("id")
                content = data.get("content", "").strip()
                if not msg_id or not content: continue

                from app.services.private_chat_service import update_private_message
                msg = update_private_message(db, msg_id, content)
                if msg and msg.sender_id == current_user.id:
                    receiver = get_user_by_id(db, msg.receiver_id)
                    payload = {
                        "type": "message_edited",
                        "id": msg.id,
                        "chat_type": "private",
                        "content": content,
                    }
                    await manager.send_to_user(username, payload)
                    if receiver:
                        await manager.send_to_user(receiver.username, payload)

            # ── DELETE PRIVATE ──────────────────────
            elif msg_type == "delete_private":
                msg_id = data.get("id")
                if not msg_id: continue

                from app.services.private_chat_service import delete_private_message
                msg = db.query(models.PrivateMessage).filter(models.PrivateMessage.id == msg_id).first()
                if msg and msg.sender_id == current_user.id:
                    receiver = get_user_by_id(db, msg.receiver_id)
                    delete_private_message(db, msg_id)
                    payload = {
                        "type": "message_deleted",
                        "id": msg_id,
                        "chat_type": "private"
                    }
                    await manager.send_to_user(username, payload)
                    if receiver:
                        await manager.send_to_user(receiver.username, payload)

            # ── EDIT GROUP ──────────────────────────
            elif msg_type == "edit_group":
                msg_id = data.get("id")
                content = data.get("content", "").strip()
                if not msg_id or not content: continue

                from app.services.group_service import update_group_message
                msg = update_group_message(db, msg_id, content)
                if msg and msg.sender_id == current_user.id:
                    member_ids = get_member_ids(db, msg.group_id)
                    member_usernames = [u.username for uid in member_ids if (u := get_user_by_id(db, uid))]
                    payload = {
                        "type": "message_edited",
                        "id": msg.id,
                        "chat_type": "group",
                        "group_id": msg.group_id,
                        "content": content,
                    }
                    await manager.broadcast_to_group(member_usernames, payload)

            # ── REACT PRIVATE ────────────────────────
            elif msg_type == "react_private":
                msg_id = data.get("id")
                emoji = data.get("emoji")
                if not msg_id or not emoji: continue

                msg = db.query(models.PrivateMessage).filter(models.PrivateMessage.id == msg_id).first()
                if msg:
                    current_reactions = dict(msg.reactions or {})
                    if current_reactions.get(username) == emoji:
                        del current_reactions[username]
                    else:
                        current_reactions[username] = emoji
                    
                    msg.reactions = current_reactions
                    db.commit()

                    receiver_usr = get_user_by_id(db, msg.receiver_id)
                    sender_usr = get_user_by_id(db, msg.sender_id)
                    payload = {
                        "type": "message_reacted",
                        "id": msg_id,
                        "chat_type": "private",
                        "reactions": current_reactions
                    }
                    
                    if sender_usr:
                        await manager.send_to_user(sender_usr.username, payload)
                    if receiver_usr and receiver_usr.username != sender_usr.username:
                        await manager.send_to_user(receiver_usr.username, payload)

            # ── REACT GROUP ──────────────────────────
            elif msg_type == "react_group":
                msg_id = data.get("id")
                emoji = data.get("emoji")
                if not msg_id or not emoji: continue

                msg = db.query(models.GroupMessage).filter(models.GroupMessage.id == msg_id).first()
                if msg:
                    current_reactions = dict(msg.reactions or {})
                    if current_reactions.get(username) == emoji:
                        del current_reactions[username]
                    else:
                        current_reactions[username] = emoji
                    
                    msg.reactions = current_reactions
                    db.commit()

                    member_ids = get_member_ids(db, msg.group_id)
                    member_usernames = [u.username for uid in member_ids if (u := get_user_by_id(db, uid))]
                    payload = {
                        "type": "message_reacted",
                        "id": msg_id,
                        "chat_type": "group",
                        "group_id": msg.group_id,
                        "reactions": current_reactions
                    }
                    await manager.broadcast_to_group(member_usernames, payload)

            # ── DELETE GROUP ────────────────────────
            elif msg_type == "delete_group":
                msg_id = data.get("id")
                if not msg_id: continue

                from app.services.group_service import delete_group_message
                msg = db.query(models.GroupMessage).filter(models.GroupMessage.id == msg_id).first()
                if msg and msg.sender_id == current_user.id:
                    group_id = msg.group_id
                    delete_group_message(db, msg_id)
                    member_ids = get_member_ids(db, group_id)
                    member_usernames = [u.username for uid in member_ids if (u := get_user_by_id(db, uid))]
                    payload = {
                        "type": "message_deleted",
                        "id": msg_id,
                        "chat_type": "group",
                        "group_id": group_id
                    }
                    await manager.broadcast_to_group(member_usernames, payload)

    except WebSocketDisconnect:
        manager.disconnect(username, websocket)
    except Exception as exc:
        logger.error("WebSocket error for %s: %s", username, exc, exc_info=True)
        manager.disconnect(username, websocket)
    finally:
        db.close()