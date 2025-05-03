from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.chat_logic import handle_user_message, check_inactive_sessions
from app.models import ChatRequest, ChatResponse
import os
import threading
import time

from app.chat_logic import (handle_user_message, check_inactive_sessions, 
                           chat_history_store, is_session_inactive, last_activity_times)
from langchain_core.messages import HumanMessage

app = FastAPI(
    title="Jackie API",
    description="API de chat pour Jackie, le connecteur social IA",
    version="1.0.0"
)

DEFAULT_PHONE_NUMBER = "+33686796460"

print("AZURE_OPENAI_DEPLOYMENT_NAME =", os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"))

@app.get("/")
def root():
    return {"message": "Jackie API is running!"}

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    phone_number = request.phone_number or DEFAULT_PHONE_NUMBER
    response = await handle_user_message(phone_number, request.message)
    return ChatResponse(response=response)

@app.post("/chat/raw")
async def chat_raw_endpoint(request: Request):
    body = await request.json()
    phone_number = body.get("phone_number")
    message = body.get("message")

    if not phone_number or not message:
        return JSONResponse(
            status_code=400,
            content={"error": "Missing phone_number or message"}
        )

    response = await handle_user_message(phone_number, message)
    return {"response": response}

def background_session_checker():
    while True:
        time.sleep(10)  # Vérifie toutes les 10 secondes
        check_inactive_sessions()

@app.on_event("startup")
def start_background_tasks():
    thread = threading.Thread(target=background_session_checker, daemon=True)
    thread.start() 

@app.get("/monitor/active-sessions")
def monitor_active_sessions():
    # Liste des sessions actives
    active_sessions = []
    for session_id, history in chat_history_store.items():
        if not is_session_inactive(session_id):
            active_sessions.append({
                "session_id": session_id,
                "phone_number": history.phone_number,
                "messages_count": len(history.session_messages),
                "messages": [
                    {
                        "type": "user" if isinstance(msg, HumanMessage) else "ai",
                        "content": msg.content
                    }
                    for msg in history.session_messages
                ],
                "last_activity": last_activity_times[session_id].isoformat() if session_id in last_activity_times else None
            })
    
    return {
        "count": len(active_sessions),
        "sessions": active_sessions
    }