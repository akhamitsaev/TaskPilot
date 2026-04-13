"""
TaskPilot Streamlit UI
"""

import streamlit as st

# ============================================================================
# Page config must be FIRST
# ============================================================================
st.set_page_config(
    page_title="TaskPilot",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================================
# Imports
# ============================================================================
import requests
import uuid
from datetime import datetime, timedelta
import pandas as pd
import time
import os

# ============================================================================
# Configuration
# ============================================================================
API_URL = os.getenv("API_URL", "http://localhost:8000")

# ============================================================================
# Session State
# ============================================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "access_token" not in st.session_state:
    st.session_state.access_token = None
if "refresh_token" not in st.session_state:
    st.session_state.refresh_token = None
if "user_info" not in st.session_state:
    st.session_state.user_info = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "tasks" not in st.session_state:
    st.session_state.tasks = []
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = None

# ============================================================================
# Auth Functions
# ============================================================================
def login(username: str, password: str) -> bool:
    try:
        response = requests.post(
            f"{API_URL}/auth/login",
            data={"username": username, "password": password},
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            st.session_state.access_token = data["access_token"]
            st.session_state.refresh_token = data["refresh_token"]
            st.session_state.authenticated = True

            user_response = requests.get(
                f"{API_URL}/auth/me",
                headers={"Authorization": f"Bearer {st.session_state.access_token}"},
                timeout=10
            )
            if user_response.status_code == 200:
                st.session_state.user_info = user_response.json()
            return True
        return False
    except:
        return False

def register(username: str, email: str, password: str) -> tuple:
    """Register new user. Returns (success, message)"""
    try:
        group_id = "00000000-0000-0000-0000-000000000001"
        response = requests.post(
            f"{API_URL}/auth/register",
            json={
                "username": username,
                "email": email,
                "password": password,
                "group_id": group_id
            },
            timeout=10
        )
        if response.status_code == 200:
            return True, "Registration successful! You can now login."
        else:
            error = response.json().get("detail", "Unknown error")
            return False, f"Registration failed: {error}"
    except Exception as e:
        return False, f"Error: {str(e)}"

def logout():
    st.session_state.authenticated = False
    st.session_state.access_token = None
    st.session_state.refresh_token = None
    st.session_state.user_info = None
    st.session_state.messages = []
    st.session_state.tasks = []

def get_headers() -> dict:
    return {"Authorization": f"Bearer {st.session_state.access_token}"} if st.session_state.access_token else {}

# ============================================================================
# API Functions
# ============================================================================
def send_message(message: str) -> dict:
    try:
        response = requests.post(
            f"{API_URL}/chat",
            json={
                "user_id": st.session_state.user_info["user_id"],
                "group_id": st.session_state.user_info["group_id"],
                "message": message
            },
            headers=get_headers(),
            timeout=35
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        return {"success": False, "response": "⏱️ Timeout. Please try again."}
    except requests.exceptions.RequestException as e:
        return {"success": False, "response": f"❌ Error: {str(e)}"}

def fetch_tasks() -> list:
    try:
        response = requests.get(
            f"{API_URL}/tasks",
            params={
                "user_id": st.session_state.user_info["user_id"],
                "group_id": st.session_state.user_info["group_id"],
                "limit": 50
            },
            headers=get_headers(),
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        return data.get("tasks", [])
    except:
        return []

def check_system_health() -> dict:
    try:
        response = requests.get(f"{API_URL}/health/ready", timeout=5)
        return response.json()
    except:
        return {"status": "unknown"}

# ============================================================================
# Auto-refresh Logic
# ============================================================================
def auto_refresh(interval_seconds: int = 10):
    now = datetime.now()
    if st.session_state.last_refresh is None or (now - st.session_state.last_refresh).seconds >= interval_seconds:
        st.session_state.tasks = fetch_tasks()
        st.session_state.last_refresh = now

# ============================================================================
# Login Page (with Registration tab)
# ============================================================================
if not st.session_state.authenticated:
    st.title("🔐 TaskPilot")

    tab_login, tab_register = st.tabs(["Login", "Register"])

    # --- LOGIN TAB ---
    with tab_login:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            with st.form("login_form"):
                username = st.text_input("Username", placeholder="admin")
                password = st.text_input("Password", type="password", placeholder="••••••••")
                submit = st.form_submit_button("Login", use_container_width=True)

                if submit:
                    if login(username, password):
                        st.success("✅ Login successful!")
                        st.rerun()
                    else:
                        st.error("❌ Invalid credentials")

            st.info("💡 Demo credentials: admin / admin123")

    # --- REGISTRATION TAB ---
    with tab_register:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            with st.form("register_form"):
                new_username = st.text_input("Username", placeholder="Choose a username")
                new_email = st.text_input("Email", placeholder="user@example.com")
                new_password = st.text_input("Password", type="password", placeholder="••••••••")
                confirm_password = st.text_input("Confirm password", type="password", placeholder="••••••••")
                reg_submit = st.form_submit_button("Register", use_container_width=True)

                if reg_submit:
                    if not new_username or not new_email or not new_password:
                        st.error("All fields are required")
                    elif new_password != confirm_password:
                        st.error("Passwords do not match")
                    else:
                        success, msg = register(new_username, new_email, new_password)
                        if success:
                            st.success(msg)
                            # Auto-login after registration
                            if login(new_username, new_password):
                                st.success("✅ Auto-login successful!")
                                st.rerun()
                            else:
                                st.info("Registration complete. Please login manually.")
                        else:
                            st.error(msg)

    st.stop()

# ============================================================================
# Main Application (authenticated)
# ============================================================================
with st.sidebar:
    st.title(f"👤 {st.session_state.user_info.get('username', 'User')}")
    st.caption(f"Group: `{st.session_state.user_info.get('group_id', 'N/A')[:8]}...`")

    st.divider()

    if st.button("🔄 Refresh tasks", use_container_width=True):
        st.session_state.tasks = fetch_tasks()
        st.rerun()

    if st.button("🗑️ Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    if st.button("🚪 Logout", use_container_width=True, type="primary"):
        logout()
        st.rerun()

    st.divider()

    health = check_system_health()
    status = "🟢 Online" if health.get("status") == "healthy" else "🔴 Issues"
    st.markdown(f"**System status:** {status}")

    with st.expander("🔍 Details"):
        for comp, ok in health.get("components", {}).items():
            st.markdown(f"{'✅' if ok else '❌'} {comp}")

auto_refresh(10)

st.title("🤖 TaskPilot")
st.markdown("*Intelligent Task Management Assistant*")

# ============================================================================
# Tabs for Chat and Tasks (chat_input must be OUTSIDE tabs)
# ============================================================================
tab_chat, tab_tasks = st.tabs(["💬 Chat", "📋 Tasks"])

with tab_chat:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("meta"):
                st.caption(msg["meta"])

with tab_tasks:
    if not st.session_state.tasks:
        with st.spinner("Loading tasks..."):
            st.session_state.tasks = fetch_tasks()

    if st.session_state.tasks:
        col1, col2 = st.columns(2)
        with col1:
            status_filter = st.multiselect("Status", options=["new", "in_progress", "done", "blocked"], default=["new", "in_progress"])
        with col2:
            priority_filter = st.slider("Min priority", 1, 10, 1)

        filtered = [t for t in st.session_state.tasks if t["status"] in status_filter and t["priority"] >= priority_filter]

        if filtered:
            df = pd.DataFrame(filtered)
            df["priority_fmt"] = df["priority"].apply(lambda p: f"🔴 {p}" if p >= 8 else f"🟡 {p}" if p >= 5 else f"🟢 {p}")
            df["status_fmt"] = df["status"].apply(lambda s: f"🆕 {s}" if s == "new" else f"🔄 {s}" if s == "in_progress" else f"✅ {s}" if s == "done" else f"🚫 {s}")

            st.dataframe(
                df[["status_fmt", "priority_fmt", "title", "deadline", "problem"]],
                column_config={"status_fmt": "Status", "priority_fmt": "Priority", "title": "Task", "deadline": "Deadline", "problem": "Problem"},
                hide_index=True,
                use_container_width=True
            )
        else:
            st.info("No tasks match the filters")
    else:
        st.info("📭 No tasks. Create your first task via chat!")

# ============================================================================
# Chat input is placed OUTSIDE the tabs to avoid StreamlitAPIException
# ============================================================================
if prompt := st.chat_input("Type your message..."):
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("🤔 Analyzing..."):
            result = send_message(prompt)
            response_text = result.get("response", "No response")
            st.markdown(response_text)

            if result.get("is_task"):
                meta = f"✅ Task created: **{result.get('task_title')}**"
                st.caption(meta)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": response_text,
                    "meta": meta
                })
            else:
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": response_text
                })

            if result.get("is_task"):
                st.session_state.tasks = fetch_tasks()

    st.rerun()

st.divider()
st.caption(f"TaskPilot v0.2.0 | API: {API_URL} | Last refresh: {st.session_state.last_refresh.strftime('%H:%M:%S') if st.session_state.last_refresh else 'N/A'}")
