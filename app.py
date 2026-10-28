import streamlit as st
import extra_streamlit_components as stx
from github import Github, GithubException
import json
import uuid
import time
from datetime import datetime
import base64
from io import BytesIO

# ============================================================
# CẤU HÌNH
# ============================================================
REPO_NAME = st.secrets.get("GITHUB_REPO", "")
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN", "")
DATA_DIR = "print_data"
ADMIN_SYSTEM_PASSWORD = "Tp27111510"
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB
ALLOWED_EXTENSIONS = [
    "jpg", "jpeg", "png", "gif", "bmp", "webp", "svg",  # ảnh
    "txt", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv",  # văn bản
    "zip", "rar", "7z", "tar", "gz"  # nén
]

# ============================================================
# KHỞI TẠO GITHUB CLIENT
# ============================================================
@st.cache_resource
def get_github_repo():
    """Kết nối GitHub repo, cache để không tạo lại mỗi lần chạy."""
    if not GITHUB_TOKEN or not REPO_NAME:
        st.error("Chưa cấu hình GITHUB_TOKEN hoặc GITHUB_REPO trong secrets.")
        st.stop()
    g = Github(GITHUB_TOKEN)
    return g.get_repo(REPO_NAME)

def gh_read_file(path):
    """Đọc file từ GitHub. Trả về (content, sha) hoặc (None, None) nếu chưa có."""
    repo = get_github_repo()
    try:
        contents = repo.get_contents(path)
        return contents.decoded_content, contents.sha
    except GithubException as e:
        if e.status == 404:
            return None, None
        raise

def gh_write_file(path, content_bytes, commit_message, existing_sha=None):
    """Ghi file lên GitHub. Tự động tạo hoặc update."""
    repo = get_github_repo()
    if existing_sha:
        repo.update_file(path, commit_message, content_bytes, existing_sha)
    else:
        try:
            repo.create_file(path, commit_message, content_bytes)
        except GithubException as e:
            if e.status == 422:  # file đã tồn tại
                existing = repo.get_contents(path)
                repo.update_file(path, commit_message, content_bytes, existing.sha)
            else:
                raise

def gh_delete_file(path, commit_message):
    """Xóa file khỏi GitHub."""
    repo = get_github_repo()
    try:
        contents = repo.get_contents(path)
        repo.delete_file(path, commit_message, contents.sha)
        return True
    except GithubException:
        return False

def gh_ensure_dir(dir_path):
    """Đảm bảo thư mục tồn tại bằng cách tạo file .gitkeep."""
    repo = get_github_repo()
    try:
        repo.get_contents(dir_path)
    except GithubException as e:
        if e.status == 404:
            repo.create_file(
                f"{dir_path}/.gitkeep",
                f"Tạo thư mục {dir_path}",
                b""
            )

# ============================================================
# QUẢN LÝ DỮ LIỆU (JSON trên GitHub)
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
    """Khởi tạo các file dữ liệu nếu chưa có."""
    gh_ensure_dir(DATA_DIR)
    
    # requests.json
    raw, _ = gh_read_file(f"{DATA_DIR}/requests.json")
    if raw is None:
        save_json(f"{DATA_DIR}/requests.json", {"requests": []}, "Khởi tạo requests.json")
    
    # admin_config.json
    raw, _ = gh_read_file(f"{DATA_DIR}/admin_config.json")
    if raw is None:
        save_json(f"{DATA_DIR}/admin_config.json", {
            "photo_password_hash": None,  # sẽ set khi admin photo đăng nhập lần đầu
            "server_closed": False,
            "server_closed_reason": ""
        }, "Khởi tạo admin_config.json")
    
    # visits.json
    raw, _ = gh_read_file(f"{DATA_DIR}/visits.json")
    if raw is None:
        save_json(f"{DATA_DIR}/visits.json", {"total_visits": 0, "visits": []}, "Khởi tạo visits.json")

def record_visit():
    """Ghi nhận lượt truy cập (chỉ gọi một lần mỗi session)."""
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
# QUẢN LÝ COOKIES
# ============================================================
def get_cookie_manager():
    return stx.CookieManager(key="cookie_manager")

def get_admin_session_from_cookie():
    """Đọc cookie để xác định admin đã đăng nhập chưa."""
    cm = get_cookie_manager()
    cookies = cm.get_all()
    if cookies is None:
        return None, None
    role = cookies.get("print_admin_role")
    token = cookies.get("print_admin_token")
    if role and token:
        # Validate token khớp với session_id đã lưu
        sessions = load_json(f"{DATA_DIR}/admin_sessions.json", {"sessions": {}})
        if token in sessions.get("sessions", {}):
            return role, token
    return None, None

def set_admin_cookie(role, token):
    cm = get_cookie_manager()
    cm.set("print_admin_role", role, expires_at=datetime.now().replace(hour=23, minute=59, second=59) if False else None)
    # Set với max_age 30 ngày
    import datetime as dt
    expires = dt.datetime.now() + dt.timedelta(days=30)
    cm.set("print_admin_role", role, expires_at=expires)
    cm.set("print_admin_token", token, expires_at=expires)

def clear_admin_cookie():
    cm = get_cookie_manager()
    cm.delete("print_admin_role")
    cm.delete("print_admin_token")

def create_admin_session(role):
    """Tạo token session mới cho admin."""
    token = str(uuid.uuid4())
    sessions = load_json(f"{DATA_DIR}/admin_sessions.json", {"sessions": {}})
    sessions["sessions"][token] = {
        "role": role,
        "created_at": datetime.now().isoformat()
    }
    save_json(f"{DATA_DIR}/admin_sessions.json", sessions, f"Tạo session admin {role}")
    set_admin_cookie(role, token)
    return token

# ============================================================
# MÃ HÓA MẬT KHẨU ĐƠN GIẢN (dùng cho admin photo)
# ============================================================
import hashlib

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ============================================================
# GIAO DIỆN CHÍNH
# ============================================================
def main():
    st.set_page_config(page_title="Dịch vụ in tài liệu", page_icon="🖨️", layout="wide")
    
    # Session ID cho mỗi tab
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = str(uuid.uuid4())
    
    # Khởi tạo dữ liệu lần đầu
    if "data_initialized" not in st.session_state:
        init_data_files()
        st.session_state["data_initialized"] = True
    
    # Ghi nhận lượt truy cập
    record_visit()
    
    # Đọc cấu hình hệ thống
    admin_config = load_json(f"{DATA_DIR}/admin_config.json", {
        "photo_password_hash": None,
        "server_closed": False,
        "server_closed_reason": ""
    })
    
    # ========================================================
    # KIỂM TRA SERVER ĐÓNG
    # ========================================================
    # Admin hệ thống vẫn có thể truy cập để mở lại
    current_role, _ = get_admin_session_from_cookie()
    if admin_config.get("server_closed", False) and current_role != "system_admin":
        st.error(f"🚫 **Dịch vụ in đã ngừng.**\n\nLý do: {admin_config.get('server_closed_reason', 'Không có lý do')}")
        st.stop()
    
    # ========================================================
    # XÁC ĐỊNH VAI TRÒ
    # ========================================================
    role, _ = get_admin_session_from_cookie()
    
    # Nếu chưa có cookie admin, hiển thị form đăng nhập
    if role is None:
        show_login_form(admin_config)
        return
    
    # Đã đăng nhập admin
    if role == "print_admin":
        show_print_admin_dashboard()
    elif role == "system_admin":
        show_system_admin_dashboard(admin_config)
    else:
        clear_admin_cookie()
        st.rerun()

# ============================================================
# FORM ĐĂNG NHẬP / NHẬP TÊN
# ============================================================
def show_login_form(admin_config):
    st.title("🖨️ Dịch vụ in tài liệu")
    st.caption("Vui lòng nhập tên của bạn để tiếp tục.")
    
    name = st.text_input("Tên của bạn", placeholder="Nhập tên...")
    
    if name:
        name_lower = name.strip().lower()
        
        if name_lower == "photo":
            # Admin in - cần mật khẩu
            st.subheader("🔐 Đăng nhập Admin In")
            password = st.text_input("Mật khẩu", type="password", key="photo_pass")
            
            if password:
                stored_hash = admin_config.get("photo_password_hash")
                
                if stored_hash is None:
                    # Lần đầu: cài đặt mật khẩu
                    st.info("Đây là lần đầu đăng nhập. Mật khẩu bạn nhập sẽ được đặt làm mật khẩu admin in.")
                    if st.button("Cài đặt mật khẩu", type="primary"):
                        admin_config["photo_password_hash"] = hash_password(password)
                        save_json(f"{DATA_DIR}/admin_config.json", admin_config, "Cài đặt mật khẩu admin in lần đầu")
                        create_admin_session("print_admin")
                        st.success("Đã cài đặt mật khẩu! Đang chuyển hướng...")
                        time.sleep(1)
                        st.rerun()
                else:
                    # Đã có mật khẩu: kiểm tra
                    if st.button("Đăng nhập", type="primary"):
                        if hash_password(password) == stored_hash:
                            create_admin_session("print_admin")
                            st.success("Đăng nhập thành công!")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("Mật khẩu không đúng.")
        
        elif name_lower == "admin":
            # Admin hệ thống
            st.subheader("🔐 Đăng nhập Admin Hệ Thống")
            password = st.text_input("Mật khẩu", type="password", key="sys_pass")
            
            if password:
                if st.button("Đăng nhập", type="primary"):
                    if password == ADMIN_SYSTEM_PASSWORD:
                        create_admin_session("system_admin")
                        st.success("Đăng nhập thành công!")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("Mật khẩu không đúng.")
        
        else:
            # User thường
            if st.button("Tiếp tục", type="primary"):
                st.session_state["user_name"] = name.strip()
                st.session_state["role"] = "user"
                st.rerun()
    
    # Footer
    st.markdown("---")
    st.caption("⚠️ Không gửi các file quan trọng như đề thi nếu lo ngại tính an toàn và bảo mật.")

# ============================================================
# MÀN HÌNH USER
# ============================================================
def show_user_screen():
    user_name = st.session_state.get("user_name", "")
    
    st.title("🖨️ Gửi yêu cầu in")
    st.write(f"Xin chào, **{user_name}**!")
    
    # Kiểm tra phản hồi từ admin
    requests_data = load_json(f"{DATA_DIR}/requests.json", {"requests": []})
    user_requests = [
        r for r in requests_data.get("requests", [])
        if r.get("user_name") == user_name and r.get("admin_reply")
    ]
    
    if user_requests:
        st.subheader("📬 Phản hồi từ Admin")
        for req in user_requests:
            if not req.get("reply_read", False):
                with st.container(border=True):
                    st.write(f"**Yêu cầu:** {req.get('file_name', 'N/A')}")
                    st.write(f"**Trạng thái:** {req.get('status', 'pending')}")
                    st.write(f"**Phản hồi:** {req.get('admin_reply', '')}")
                    if st.button("Đã đọc", key=f"read_{req['id']}"):
                        # Đánh dấu đã đọc
                        for r in requests_data["requests"]:
                            if r["id"] == req["id"]:
                                r["reply_read"] = True
                        save_json(f"{DATA_DIR}/requests.json", requests_data, "Đánh dấu đã đọc phản hồi")
                        st.rerun()
    
    st.markdown("---")
    
    # Form gửi yêu cầu
    with st.form("print_request_form", clear_on_submit=True):
        uploaded_file = st.file_uploader(
            "Chọn file cần in",
            type=ALLOWED_EXTENSIONS,
            help="Hỗ trợ ảnh, văn bản, file nén. Tối đa 25MB mỗi file."
        )
        
        note = st.text_area("Yêu cầu khác (không bắt buộc)", placeholder="Ví dụ: In 2 bản, in màu...")
        
        submitted = st.form_submit_button("📤 Gửi yêu cầu", type="primary")
        
        if submitted:
            if uploaded_file is None:
                st.error("Vui lòng chọn file.")
            elif uploaded_file.size > MAX_FILE_SIZE:
                st.error(f"File quá lớn. Tối đa 25MB, file của bạn là {uploaded_file.size / 1024 / 1024:.1f}MB.")
            else:
                # Lưu file lên GitHub
                file_bytes = uploaded_file.getvalue()
                ext = uploaded_file.name.split(".")[-1].lower()
                request_id = str(uuid.uuid4())[:8]
                safe_filename = f"{request_id}_{uploaded_file.name}"
                file_path = f"{DATA_DIR}/files/{safe_filename}"
                
                # Encode file
                b64_content = base64.b64encode(file_bytes).decode()
                
                # Tạo metadata
                request_meta = {
                    "id": request_id,
                    "user_name": user_name,
                    "session_id": st.session_state.get("session_id"),
                    "file_name": uploaded_file.name,
                    "file_path": file_path,
                    "file_size": uploaded_file.size,
                    "note": note.strip(),
                    "status": "pending",
                    "admin_reply": None,
                    "reply_read": False,
                    "created_at": datetime.now().isoformat(),
                    "confirmed_at": None
                }
                
                # Lưu file (base64 trong JSON để đơn giản, hoặc lưu binary riêng)
                # Ở đây lưu base64 trong một file riêng để tránh JSON quá lớn
                file_data = {
                    "id": request_id,
                    "filename": uploaded_file.name,
                    "content_b64": b64_content
                }
                save_json(f"{DATA_DIR}/files/{request_id}.json", file_data, f"Upload file {uploaded_file.name}")
                
                # Cập nhật requests
                requests_data = load_json(f"{DATA_DIR}/requests.json", {"requests": []})
                requests_data["requests"].append(request_meta)
                save_json(f"{DATA_DIR}/requests.json", requests_data, f"Yêu cầu in mới từ {user_name}")
                
                st.success(f"✅ Đã gửi yêu cầu in file **{uploaded_file.name}**. Vui lòng chờ admin xử lý.")
                st.balloons()
    
    # Nút đăng xuất
    if st.button("🚪 Đăng xuất"):
        for key in ["user_name", "role", "session_id"]:
            st.session_state.pop(key, None)
        st.rerun()

# ============================================================
# MÀN HÌNH ADMIN IN
# ============================================================
def show_print_admin_dashboard():
    st.title("🖨️ Admin In - Danh sách chờ")
    
    # Nút đăng xuất
    if st.button("🚪 Đăng xuất"):
        clear_admin_cookie()
        st.rerun()
    
    st.markdown("---")
    
    requests_data = load_json(f"{DATA_DIR}/requests.json", {"requests": []})
    requests = requests_data.get("requests", [])
    
    # Phân loại
    pending = [r for r in requests if r.get("status") == "pending"]
    confirmed = [r for r in requests if r.get("status") == "confirmed"]
    rejected = [r for r in requests if r.get("status") == "rejected"]
    
    tab1, tab2, tab3 = st.tabs([f"⏳ Chờ xử lý ({len(pending)})", f"✅ Đã xác nhận ({len(confirmed)})", f"❌ Đã từ chối ({len(rejected)})"])
    
    with tab1:
        if not pending:
            st.info("Không có yêu cầu nào đang chờ.")
        for req in pending:
            with st.container(border=True):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.write(f"**File:** {req.get('file_name', 'N/A')}")
                    st.write(f"**Người gửi:** {req.get('user_name', 'N/A')}")
                    st.write(f"**Thời gian:** {req.get('created_at', '')[:19]}")
                    if req.get("note"):
                        st.write(f"**Ghi chú:** {req['note']}")
                    st.write(f"**Kích thước:** {req.get('file_size', 0) / 1024:.1f} KB")
                
                with col2:
                    # Nút tải file
                    file_data = load_json(f"{DATA_DIR}/files/{req['id']}.json")
                    if file_data:
                        file_bytes = base64.b64decode(file_data["content_b64"])
                        st.download_button(
                            "📥 Tải file",
                            data=file_bytes,
                            file_name=req.get("file_name", "file"),
                            mime="application/octet-stream",
                            key=f"dl_{req['id']}"
                        )
                
                # Các nút hành động
                st.markdown("---")
                action_col1, action_col2, action_col3 = st.columns(3)
                
                with action_col1:
                    if st.button("✅ Xác nhận in", key=f"confirm_{req['id']}"):
                        for r in requests_data["requests"]:
                            if r["id"] == req["id"]:
                                r["status"] = "confirmed"
                                r["confirmed_at"] = datetime.now().isoformat()
                        save_json(f"{DATA_DIR}/requests.json", requests_data, f"Xác nhận in {req['id']}")
                        st.success("Đã xác nhận in.")
                        st.rerun()
                
                with action_col2:
                    reply = st.text_area("Hỏi đáp thêm", key=f"ask_{req['id']}", placeholder="Nhập câu hỏi...")
                    if st.button("📨 Gửi hỏi đáp", key=f"send_ask_{req['id']}"):
                        if reply.strip():
                            for r in requests_data["requests"]:
                                if r["id"] == req["id"]:
                                    r["admin_reply"] = f"[Hỏi đáp] {reply.strip()}"
                            save_json(f"{DATA_DIR}/requests.json", requests_data, f"Hỏi đáp {req['id']}")
                            st.success("Đã gửi hỏi đáp.")
                            st.rerun()
                        else:
                            st.warning("Vui lòng nhập nội dung.")
                
                with action_col3:
                    reason = st.text_area("Lý do từ chối", key=f"reject_{req['id']}", placeholder="Nhập lý do...")
                    if st.button("❌ Từ chối in", key=f"reject_btn_{req['id']}"):
                        if reason.strip():
                            for r in requests_data["requests"]:
                                if r["id"] == req["id"]:
                                    r["status"] = "rejected"
                                    r["admin_reply"] = f"[Từ chối] {reason.strip()}"
                            save_json(f"{DATA_DIR}/requests.json", requests_data, f"Từ chối {req['id']}")
                            st.success("Đã từ chối.")
                            st.rerun()
                        else:
                            st.warning("Vui lòng nhập lý do từ chối.")
                
                # Nút xóa (có cảnh báo 5s nếu chưa xử lý)
                st.markdown("---")
                if st.button("🗑️ Xóa yêu cầu này", key=f"del_{req['id']}"):
                    if req.get("status") == "pending" and not req.get("admin_reply"):
                        st.warning("⚠️ Yêu cầu chưa được xử lý. Bạn có thể xóa sau 5 giây...")
                        time.sleep(5)
                    
                    # Xóa file metadata
                    gh_delete_file(f"{DATA_DIR}/files/{req['id']}.json", f"Xóa file {req['id']}")
                    
                    # Xóa khỏi requests
                    requests_data["requests"] = [r for r in requests_data["requests"] if r["id"] != req["id"]]
                    save_json(f"{DATA_DIR}/requests.json", requests_data, f"Xóa yêu cầu {req['id']}")
                    st.success("Đã xóa.")
                    st.rerun()
    
    with tab2:
        if not confirmed:
            st.info("Chưa có yêu cầu nào được xác nhận.")
        for req in confirmed:
            with st.container(border=True):
                st.write(f"**File:** {req.get('file_name')} | **Người gửi:** {req.get('user_name')} | **Xác nhận lúc:** {req.get('confirmed_at', '')[:19]}")
                file_data = load_json(f"{DATA_DIR}/files/{req['id']}.json")
                if file_data:
                    file_bytes = base64.b64decode(file_data["content_b64"])
                    st.download_button(
                        "📥 Tải file",
                        data=file_bytes,
                        file_name=req.get("file_name", "file"),
                        key=f"dl_conf_{req['id']}"
                    )
                if st.button("🗑️ Xóa", key=f"del_conf_{req['id']}"):
                    gh_delete_file(f"{DATA_DIR}/files/{req['id']}.json", f"Xóa file {req['id']}")
                    requests_data["requests"] = [r for r in requests_data["requests"] if r["id"] != req["id"]]
                    save_json(f"{DATA_DIR}/requests.json", requests_data, f"Xóa yêu cầu {req['id']}")
                    st.rerun()
    
    with tab3:
        if not rejected:
            st.info("Chưa có yêu cầu nào bị từ chối.")
        for req in rejected:
            with st.container(border=True):
                st.write(f"**File:** {req.get('file_name')} | **Người gửi:** {req.get('user_name')}")
                st.write(f"**Lý do:** {req.get('admin_reply', '')}")
                if st.button("🗑️ Xóa", key=f"del_rej_{req['id']}"):
                    gh_delete_file(f"{DATA_DIR}/files/{req['id']}.json", f"Xóa file {req['id']}")
                    requests_data["requests"] = [r for r in requests_data["requests"] if r["id"] != req["id"]]
                    save_json(f"{DATA_DIR}/requests.json", requests_data, f"Xóa yêu cầu {req['id']}")
                    st.rerun()

# ============================================================
# MÀN HÌNH ADMIN HỆ THỐNG
# ============================================================
def show_system_admin_dashboard(admin_config):
    st.title("⚙️ Admin Hệ Thống")
    
    if st.button("🚪 Đăng xuất"):
        clear_admin_cookie()
        st.rerun()
    
    st.markdown("---")
    
    # Trạng thái server
    st.subheader("🔌 Trạng thái máy chủ")
    
    server_closed = admin_config.get("server_closed", False)
    
    if server_closed:
        st.error(f"**Máy chủ đang ĐÓNG.** Lý do: {admin_config.get('server_closed_reason', '')}")
        if st.button("✅ Mở lại máy chủ", type="primary"):
            admin_config["server_closed"] = False
            admin_config["server_closed_reason"] = ""
            save_json(f"{DATA_DIR}/admin_config.json", admin_config, "Mở lại máy chủ")
            st.success("Đã mở lại máy chủ.")
            st.rerun()
    else:
        st.success("**Máy chủ đang HOẠT ĐỘNG.**")
        reason = st.text_input("Lý do đóng máy chủ", placeholder="Nhập lý do...")
        if st.button("🚫 Đóng máy chủ", type="secondary"):
            admin_config["server_closed"] = True
            admin_config["server_closed_reason"] = reason.strip() or "Không có lý do"
            save_json(f"{DATA_DIR}/admin_config.json", admin_config, "Đóng máy chủ")
            st.warning("Đã đóng máy chủ.")
            st.rerun()
    
    st.markdown("---")
    
    # Lượt truy cập
    st.subheader("📊 Lượt truy cập")
    visits_data = load_json(f"{DATA_DIR}/visits.json", {"total_visits": 0, "visits": []})
    st.metric("Tổng lượt truy cập", visits_data.get("total_visits", 0))
    
    with st.expander("Chi tiết lượt truy cập (50 gần nhất)"):
        recent = visits_data.get("visits", [])[-50:]
        for v in reversed(recent):
            st.text(f"{v.get('timestamp', '')[:19]} | {v.get('session_id', '')[:8]}")
    
    st.markdown("---")
    st.caption("⚠️ Không gửi các file quan trọng như đề thi nếu lo ngại tính an toàn và bảo mật.")

# ============================================================
# ROUTER CHÍNH
# ============================================================
# Kiểm tra nếu user thường đã nhập tên
if "role" in st.session_state and st.session_state["role"] == "user" and "user_name" in st.session_state:
    show_user_screen()
else:
    main()
