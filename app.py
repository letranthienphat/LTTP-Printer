import streamlit as st
import extra_streamlit_components as stx
from github import Github, GithubException
import json
import uuid
import time
from datetime import datetime, timedelta
import base64
import bcrypt
import binascii

# ============================================================
# CẤU HÌNH & SECRETS (chỉ đọc từ st.secrets, không fallback)
# ============================================================
REPO_NAME = st.secrets.get("GITHUB_REPO")
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN")
SYSTEM_ADMIN_PASSWORD = st.secrets.get("SYSTEM_ADMIN_PASSWORD")

if not REPO_NAME or not GITHUB_TOKEN or not SYSTEM_ADMIN_PASSWORD:
    st.error("Thiếu cấu hình trong st.secrets: cần GITHUB_REPO, GITHUB_TOKEN, SYSTEM_ADMIN_PASSWORD.")
    st.stop()

DATA_DIR = "print_data"
MAX_FILE_SIZE = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = [
    "jpg", "jpeg", "png", "gif", "bmp", "webp", "svg",
    "txt", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv",
    "zip", "rar", "7z", "tar", "gz"
]

# ============================================================
# GITHUB CLIENT
# ============================================================
@st.cache_resource
def get_github_repo():
    g = Github(GITHUB_TOKEN)
    return g.get_repo(REPO_NAME)

def gh_read_file(path):
    repo = get_github_repo()
    try:
        contents = repo.get_contents(path)
        return contents.decoded_content, contents.sha
    except GithubException as e:
        if e.status == 404:
            return None, None
        raise

def gh_write_file(path, content_bytes, commit_message, existing_sha=None):
    repo = get_github_repo()
    if existing_sha:
        repo.update_file(path, commit_message, content_bytes, existing_sha)
    else:
        try:
            repo.create_file(path, commit_message, content_bytes)
        except GithubException as e:
            if e.status == 422:
                existing = repo.get_contents(path)
                repo.update_file(path, commit_message, content_bytes, existing.sha)
            else:
                raise

def gh_delete_file(path, commit_message):
    repo = get_github_repo()
    try:
        contents = repo.get_contents(path)
        repo.delete_file(path, commit_message, contents.sha)
        return True
    except GithubException:
        return False

def gh_ensure_dir(dir_path):
    repo = get_github_repo()
    try:
        repo.get_contents(dir_path)
    except GithubException as e:
        if e.status == 404:
            repo.create_file(f"{dir_path}/.gitkeep", f"Tạo thư mục {dir_path}", b"")

# ============================================================
# JSON HELPERS
# ============================================================
def load_json(path, default=None):
    if default is None:
        default = {}
    raw, _ = gh_read_file(path)
    if raw is None:
        return default
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return default

def save_json(path, data, commit_message):
    content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    _, sha = gh_read_file(path)
    gh_write_file(path, content, commit_message, sha)

def init_data_files():
    gh_ensure_dir(DATA_DIR)
    if gh_read_file(f"{DATA_DIR}/requests.json")[0] is None:
        save_json(f"{DATA_DIR}/requests.json", {"requests": []}, "Khởi tạo requests.json")
    if gh_read_file(f"{DATA_DIR}/admin_config.json")[0] is None:
        save_json(f"{DATA_DIR}/admin_config.json", {
            "photo_password_hash": None,
            "server_closed": False,
            "server_closed_reason": ""
        }, "Khởi tạo admin_config.json")
    if gh_read_file(f"{DATA_DIR}/visits.json")[0] is None:
        save_json(f"{DATA_DIR}/visits.json", {"total_visits": 0, "visits": []}, "Khởi tạo visits.json")
    if gh_read_file(f"{DATA_DIR}/admin_sessions.json")[0] is None:
        save_json(f"{DATA_DIR}/admin_sessions.json", {"sessions": {}}, "Khởi tạo admin_sessions.json")

def record_visit():
    if "visit_recorded" not in st.session_state:
        data = load_json(f"{DATA_DIR}/visits.json", {"total_visits": 0, "visits": []})
        data["total_visits"] = data.get("total_visits", 0) + 1
        data["visits"].append({
            "timestamp": datetime.now().isoformat(),
            "session_id": st.session_state.get("session_id", "unknown")
        })
        save_json(f"{DATA_DIR}/visits.json", data, "Ghi nhận lượt truy cập")
        st.session_state["visit_recorded"] = True

# ============================================================
# COOKIE MANAGER (singleton, gọi 1 lần mỗi rerun)
# ============================================================
def get_cookie_manager():
    if "cookie_manager" not in st.session_state:
        st.session_state.cookie_manager = stx.CookieManager(key="print_cookie_mgr")
    return st.session_state.cookie_manager

def read_admin_cookie_once():
    """Đọc cookie 1 lần, cache trong st.session_state của rerun hiện tại."""
    cm = get_cookie_manager()
    cookies = cm.get_all()
    if cookies is None:
        return None, None
    return cookies.get("print_admin_role"), cookies.get("print_admin_token")

def set_admin_cookie(role, token):
    cm = get_cookie_manager()
    expires = datetime.now() + timedelta(days=30)
    cm.set("print_admin_role", role, expires_at=expires, key="set_role")
    cm.set("print_admin_token", token, expires_at=expires, key="set_token")

def clear_admin_cookie():
    cm = get_cookie_manager()
    cm.delete("print_admin_role", key="del_role")
    cm.delete("print_admin_token", key="del_token")

def create_admin_session(role):
    token = str(uuid.uuid4())
    sessions = load_json(f"{DATA_DIR}/admin_sessions.json", {"sessions": {}})
    sessions["sessions"][token] = {
        "role": role,
        "created_at": datetime.now().isoformat()
    }
    save_json(f"{DATA_DIR}/admin_sessions.json", sessions, f"Tạo session admin {role}")
    set_admin_cookie(role, token)
    return token

def resolve_current_role():
    """Trả về role hiện tại dựa trên cookie. Gọi đúng 1 lần mỗi rerun."""
    if "resolved_role" in st.session_state:
        return st.session_state["resolved_role"]
    role, token = read_admin_cookie_once()
    resolved = None
    if role and token:
        sessions = load_json(f"{DATA_DIR}/admin_sessions.json", {"sessions": {}})
        if token in sessions.get("sessions", {}):
            resolved = role
    st.session_state["resolved_role"] = resolved
    return resolved

def clear_resolved_role():
    st.session_state.pop("resolved_role", None)

# ============================================================
# PASSWORD (BCRYPT)
# ============================================================
def hash_password(password: str) -> str:
    return binascii.hexlify(bcrypt.hashpw(password.encode(), bcrypt.gensalt())).decode()

def verify_password(password: str, stored_hex: str) -> bool:
    if not stored_hex:
        return False
    try:
        return bcrypt.checkpw(password.encode(), binascii.unhexlify(stored_hex.encode()))
    except (binascii.Error, ValueError):
        return False

# ============================================================
# MAIN
# ============================================================
def main():
    st.set_page_config(page_title="Dịch vụ in tài liệu", page_icon="🖨️", layout="wide")

    if "session_id" not in st.session_state:
        st.session_state["session_id"] = str(uuid.uuid4())

    if "data_initialized" not in st.session_state:
        init_data_files()
        st.session_state["data_initialized"] = True

    record_visit()

    admin_config = load_json(f"{DATA_DIR}/admin_config.json", {
        "photo_password_hash": None,
        "server_closed": False,
        "server_closed_reason": ""
    })

    current_role = resolve_current_role()

    if admin_config.get("server_closed", False) and current_role != "system_admin":
        st.error(f"🚫 **Dịch vụ in đã ngừng.**\n\nLý do: {admin_config.get('server_closed_reason', 'Không có lý do')}")
        st.stop()

    if current_role is None:
        show_login_form(admin_config)
        return

    if current_role == "print_admin":
        show_print_admin_dashboard()
    elif current_role == "system_admin":
        show_system_admin_dashboard(admin_config)
    else:
        clear_admin_cookie()
        clear_resolved_role()
        st.rerun()

# ============================================================
# LOGIN
# ============================================================
def show_login_form(admin_config):
    st.title("🖨️ Dịch vụ in tài liệu")
    st.caption("Vui lòng nhập tên của bạn để tiếp tục.")

    name = st.text_input("Tên của bạn", placeholder="Nhập tên...", key="login_name")

    if name:
        name_lower = name.strip().lower()

        if name_lower == "photo":
            st.subheader("🔐 Đăng nhập Admin In")
            password = st.text_input("Mật khẩu", type="password", key="photo_pass")

            if password:
                stored_hash = admin_config.get("photo_password_hash")
                if stored_hash is None:
                    st.info("Lần đầu đăng nhập: mật khẩu bạn nhập sẽ được đặt làm mật khẩu admin in.")
                    if st.button("Cài đặt mật khẩu", type="primary", key="set_pwd_btn"):
                        admin_config["photo_password_hash"] = hash_password(password)
                        save_json(f"{DATA_DIR}/admin_config.json", admin_config, "Cài đặt mật khẩu admin in")
                        create_admin_session("print_admin")
                        clear_resolved_role()
                        st.rerun()
                else:
                    if st.button("Đăng nhập", type="primary", key="login_photo_btn"):
                        if verify_password(password, stored_hash):
                            create_admin_session("print_admin")
                            clear_resolved_role()
                            st.rerun()
                        else:
                            st.error("Mật khẩu không đúng.")

        elif name_lower == "admin":
            st.subheader("🔐 Đăng nhập Admin Hệ Thống")
            password = st.text_input("Mật khẩu", type="password", key="sys_pass")

            if password:
                if st.button("Đăng nhập", type="primary", key="login_sys_btn"):
                    if password == SYSTEM_ADMIN_PASSWORD:
                        create_admin_session("system_admin")
                        clear_resolved_role()
                        st.rerun()
                    else:
                        st.error("Mật khẩu không đúng.")
        else:
            if st.button("Tiếp tục", type="primary", key="continue_user_btn"):
                st.session_state["user_name"] = name.strip()
                st.session_state["role"] = "user"
                st.rerun()

    st.markdown("---")
    st.caption("⚠️ Không gửi các file quan trọng như đề thi nếu lo ngại tính an toàn và bảo mật.")

# ============================================================
# USER SCREEN
# ============================================================
def show_user_screen():
    user_name = st.session_state.get("user_name", "")
    st.title("🖨️ Gửi yêu cầu in")
    st.write(f"Xin chào, **{user_name}**!")

    requests_data = load_json(f"{DATA_DIR}/requests.json", {"requests": []})
    user_replies = [r for r in requests_data.get("requests", [])
                    if r.get("user_name") == user_name and r.get("admin_reply")]

    if user_replies:
        st.subheader("📬 Phản hồi từ Admin")
        for req in user_replies:
            if not req.get("reply_read", False):
                with st.container(border=True):
                    st.write(f"**Yêu cầu:** {req.get('file_name', 'N/A')}")
                    st.write(f"**Trạng thái:** {req.get('status', 'pending')}")
                    st.write(f"**Phản hồi:** {req.get('admin_reply', '')}")
                    if st.button("Đã đọc", key=f"read_{req['id']}"):
                        for r in requests_data["requests"]:
                            if r["id"] == req["id"]:
                                r["reply_read"] = True
                        save_json(f"{DATA_DIR}/requests.json", requests_data, "Đánh dấu đã đọc")
                        st.rerun()

    st.markdown("---")

    with st.form("print_request_form", clear_on_submit=True):
        uploaded_file = st.file_uploader(
            "Chọn file cần in",
            type=ALLOWED_EXTENSIONS,
            help="Hỗ trợ ảnh, văn bản, file nén. Tối đa 25MB mỗi file.",
            key="uploader"
        )
        note = st.text_area("Yêu cầu khác (không bắt buộc)", placeholder="Ví dụ: In 2 bản, in màu...", key="note")
        submitted = st.form_submit_button("📤 Gửi yêu cầu", type="primary")

        if submitted:
            if uploaded_file is None:
                st.error("Vui lòng chọn file.")
            elif uploaded_file.size > MAX_FILE_SIZE:
                st.error(f"File quá lớn. Tối đa 25MB, file của bạn là {uploaded_file.size / 1024 / 1024:.1f}MB.")
            else:
                file_bytes = uploaded_file.getvalue()
                request_id = str(uuid.uuid4())[:8]
                file_data = {
                    "id": request_id,
                    "filename": uploaded_file.name,
                    "content_b64": base64.b64encode(file_bytes).decode()
                }
                save_json(f"{DATA_DIR}/files/{request_id}.json", file_data, f"Upload file {uploaded_file.name}")

                request_meta = {
                    "id": request_id,
                    "user_name": user_name,
                    "session_id": st.session_state.get("session_id"),
                    "file_name": uploaded_file.name,
                    "file_path": f"{DATA_DIR}/files/{request_id}.json",
                    "file_size": uploaded_file.size,
                    "note": note.strip(),
                    "status": "pending",
                    "admin_reply": None,
                    "reply_read": False,
                    "created_at": datetime.now().isoformat(),
                    "confirmed_at": None
                }
                requests_data = load_json(f"{DATA_DIR}/requests.json", {"requests": []})
                requests_data["requests"].append(request_meta)
                save_json(f"{DATA_DIR}/requests.json", requests_data, f"Yêu cầu in mới từ {user_name}")

                st.success(f"✅ Đã gửi yêu cầu in file **{uploaded_file.name}**.")
                # Không rerun ở đây để giữ thông báo thành công; user có thể tự làm mới
                # (form đã clear_on_submit nên đã reset)

    if st.button("🚪 Đăng xuất", key="logout_user"):
        for k in ["user_name", "role", "session_id", "visit_recorded"]:
            st.session_state.pop(k, None)
        st.rerun()

# ============================================================
# PRINT ADMIN DASHBOARD
# ============================================================
def show_print_admin_dashboard():
    st.title("🖨️ Admin In - Danh sách chờ")

    col_logout, _ = st.columns([1, 4])
    with col_logout:
        if st.button("🚪 Đăng xuất", key="logout_print_admin"):
            clear_admin_cookie()
            clear_resolved_role()
            st.rerun()

    st.markdown("---")
    requests_data = load_json(f"{DATA_DIR}/requests.json", {"requests": []})
    requests = requests_data.get("requests", [])

    pending = [r for r in requests if r.get("status") == "pending"]
    confirmed = [r for r in requests if r.get("status") == "confirmed"]
    rejected = [r for r in requests if r.get("status") == "rejected"]

    tab1, tab2, tab3 = st.tabs([
        f"⏳ Chờ ({len(pending)})",
        f"✅ Đã xác nhận ({len(confirmed)})",
        f"❌ Từ chối ({len(rejected)})"
    ])

    with tab1:
        if not pending:
            st.info("Không có yêu cầu chờ.")
        for req in pending:
            with st.container(border=True):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.write(f"**File:** {req.get('file_name')}")
                    st.write(f"**Người gửi:** {req.get('user_name')}")
                    st.write(f"**Thời gian:** {req.get('created_at', '')[:19]}")
                    if req.get("note"):
                        st.write(f"**Ghi chú:** {req['note']}")
                    st.write(f"**Kích thước:** {req.get('file_size', 0) / 1024:.1f} KB")
                with col2:
                    file_data = load_json(req.get("file_path"))
                    if file_data:
                        file_bytes = base64.b64decode(file_data["content_b64"])
                        st.download_button(
                            "📥 Tải file",
                            data=file_bytes,
                            file_name=req.get("file_name"),
                            key=f"dl_{req['id']}"
                        )

                st.markdown("---")
                c1, c2, c3 = st.columns(3)

                with c1:
                    if st.button("✅ Xác nhận in", key=f"confirm_{req['id']}"):
                        for r in requests_data["requests"]:
                            if r["id"] == req["id"]:
                                r["status"] = "confirmed"
                                r["confirmed_at"] = datetime.now().isoformat()
                        save_json(f"{DATA_DIR}/requests.json", requests_data, f"Xác nhận {req['id']}")
                        st.rerun()

                with c2:
                    reply = st.text_area("Hỏi đáp", key=f"ask_{req['id']}", placeholder="Nhập câu hỏi...")
                    if st.button("📨 Gửi hỏi đáp", key=f"send_ask_{req['id']}"):
                        if reply.strip():
                            for r in requests_data["requests"]:
                                if r["id"] == req["id"]:
                                    r["admin_reply"] = f"[Hỏi đáp] {reply.strip()}"
                            save_json(f"{DATA_DIR}/requests.json", requests_data, f"Hỏi đáp {req['id']}")
                            st.rerun()
                        else:
                            st.warning("Nhập nội dung hỏi đáp.")

                with c3:
                    reason = st.text_area("Lý do từ chối", key=f"reject_{req['id']}", placeholder="Nhập lý do...")
                    if st.button("❌ Từ chối", key=f"reject_btn_{req['id']}"):
                        if reason.strip():
                            for r in requests_data["requests"]:
                                if r["id"] == req["id"]:
                                    r["status"] = "rejected"
                                    r["admin_reply"] = f"[Từ chối] {reason.strip()}"
                            save_json(f"{DATA_DIR}/requests.json", requests_data, f"Từ chối {req['id']}")
                            st.rerun()
                        else:
                            st.warning("Nhập lý do từ chối.")

                st.markdown("---")
                if st.button("🗑️ Xóa yêu cầu này", key=f"del_{req['id']}"):
                    if req.get("status") == "pending" and not req.get("admin_reply"):
                        st.warning("⚠️ Yêu cầu chưa xử lý. Đợi 5 giây...")
                        time.sleep(5)
                    gh_delete_file(req.get("file_path"), f"Xóa file {req['id']}")
                    requests_data["requests"] = [r for r in requests_data["requests"] if r["id"] != req["id"]]
                    save_json(f"{DATA_DIR}/requests.json", requests_data, f"Xóa {req['id']}")
                    st.rerun()

    with tab2:
        if not confirmed:
            st.info("Chưa có yêu cầu nào được xác nhận.")
        for req in confirmed:
            with st.container(border=True):
                st.write(f"**File:** {req.get('file_name')} | **Người gửi:** {req.get('user_name')} | **Xác nhận lúc:** {req.get('confirmed_at', '')[:19]}")
                file_data = load_json(req.get("file_path"))
                if file_data:
                    file_bytes = base64.b64decode(file_data["content_b64"])
                    st.download_button(
                        "📥 Tải file",
                        data=file_bytes,
                        file_name=req.get("file_name"),
                        key=f"dl_conf_{req['id']}"
                    )
                if st.button("🗑️ Xóa", key=f"del_conf_{req['id']}"):
                    gh_delete_file(req.get("file_path"), f"Xóa file {req['id']}")
                    requests_data["requests"] = [r for r in requests_data["requests"] if r["id"] != req["id"]]
                    save_json(f"{DATA_DIR}/requests.json", requests_data, f"Xóa {req['id']}")
                    st.rerun()

    with tab3:
        if not rejected:
            st.info("Chưa có yêu cầu nào bị từ chối.")
        for req in rejected:
            with st.container(border=True):
                st.write(f"**File:** {req.get('file_name')} | **Người gửi:** {req.get('user_name')}")
                st.write(f"**Lý do:** {req.get('admin_reply', '')}")
                if st.button("🗑️ Xóa", key=f"del_rej_{req['id']}"):
                    gh_delete_file(req.get("file_path"), f"Xóa file {req['id']}")
                    requests_data["requests"] = [r for r in requests_data["requests"] if r["id"] != req["id"]]
                    save_json(f"{DATA_DIR}/requests.json", requests_data, f"Xóa {req['id']}")
                    st.rerun()

# ============================================================
# SYSTEM ADMIN DASHBOARD
# ============================================================
def show_system_admin_dashboard(admin_config):
    st.title("⚙️ Admin Hệ Thống")

    if st.button("🚪 Đăng xuất", key="logout_sys_admin"):
        clear_admin_cookie()
        clear_resolved_role()
        st.rerun()

    st.markdown("---")
    st.subheader("🔌 Trạng thái máy chủ")
    server_closed = admin_config.get("server_closed", False)

    if server_closed:
        st.error(f"**Máy chủ đang ĐÓNG.** Lý do: {admin_config.get('server_closed_reason', '')}")
        if st.button("✅ Mở lại máy chủ", type="primary", key="open_server"):
            admin_config["server_closed"] = False
            admin_config["server_closed_reason"] = ""
            save_json(f"{DATA_DIR}/admin_config.json", admin_config, "Mở lại máy chủ")
            st.rerun()
    else:
        st.success("**Máy chủ đang HOẠT ĐỘNG.**")
        reason = st.text_input("Lý do đóng máy chủ", placeholder="Nhập lý do...", key="close_reason")
        if st.button("🚫 Đóng máy chủ", type="secondary", key="close_server"):
            admin_config["server_closed"] = True
            admin_config["server_closed_reason"] = reason.strip() or "Không có lý do"
            save_json(f"{DATA_DIR}/admin_config.json", admin_config, "Đóng máy chủ")
            st.rerun()

    st.markdown("---")
    st.subheader("📊 Lượt truy cập")
    visits_data = load_json(f"{DATA_DIR}/visits.json", {"total_visits": 0, "visits": []})
    st.metric("Tổng lượt truy cập", visits_data.get("total_visits", 0))
    with st.expander("Chi tiết (50 gần nhất)"):
        for v in reversed(visits_data.get("visits", [])[-50:]):
            st.text(f"{v.get('timestamp', '')[:19]} | {v.get('session_id', '')[:8]}")

    st.markdown("---")
    st.caption("⚠️ Không gửi các file quan trọng như đề thi nếu lo ngại tính an toàn và bảo mật.")

# ============================================================
# ROUTER
# ============================================================
if "role" in st.session_state and st.session_state["role"] == "user" and "user_name" in st.session_state:
    show_user_screen()
else:
    main()
