"""커플 다이어리 | 실행: streamlit run app.py

GitHub에 app.py와 requirements.txt 업로드 → Streamlit Community Cloud 배포.
휴대폰에서 배포 URL을 열거나 앱 안의 QR을 스캔하세요.

영구 저장(온라인 사용 시 필수): Supabase 프로젝트의 SQL Editor에서 아래 SQL 실행.
create table public.couple_diary_store (
  room_key text primary key,
  payload jsonb not null,
  revision integer not null default 0
);
alter table public.couple_diary_store enable row level security;
-- 브라우저에는 키를 전달하지 않습니다. 서버에서만 service_role 키를 사용합니다.

Streamlit 앱 Settings → Secrets:
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "YOUR_SERVER_ONLY_SERVICE_ROLE_KEY"
APP_URL = "https://YOUR_APP.streamlit.app"

Secrets가 없으면 로컬 SQLite 체험 모드. Cloud에서는 로컬 파일이 소실될 수 있습니다.
연결 코드는 두 사람만 아는 충분히 긴 임의의 문구를 쓰세요.
연결 코드를 아는 사람이 입장할 수 있는 간단한 공유 앱입니다.
실서비스 수준의 본인 인증/암호화 저장을 제공하지 않으므로 민감한 정보는 쓰지 마세요.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import qrcode
import requests
import streamlit as st

KST = timezone(timedelta(hours=9))
MOODS = ["😊 행복", "🥰 설렘", "😌 평온", "😢 슬픔", "😤 속상", "😴 피곤"]


def setting(key: str) -> str:
    try:
        return str(st.secrets.get(key, os.getenv(key, ""))).strip()
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return os.getenv(key, "").strip()


class Conflict(Exception):
    pass


class Store:
    def __init__(self, url="", key="", db_path="couple_diary.sqlite3"):
        self.url = url.rstrip("/")
        self.key = key
        self.remote = bool(url and key)
        self.db_path = db_path
        if not self.remote:
            with sqlite3.connect(self.db_path) as con:
                con.execute("CREATE TABLE IF NOT EXISTS rooms (room_key TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL)")

    def request(self, method, **kwargs):
        headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json", "Prefer": "return=representation"}
        response = requests.request(method, f"{self.url}/rest/v1/couple_diary_store", headers=headers, timeout=15, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else []

    def load(self, room):
        if self.remote:
            rows = self.request("GET", params={"room_key": f"eq.{room}", "select": "payload,revision"})
            return (rows[0]["payload"], rows[0]["revision"]) if rows else (None, None)
        with sqlite3.connect(self.db_path) as con:
            row = con.execute("SELECT payload,revision FROM rooms WHERE room_key=?", (room,)).fetchone()
        return (json.loads(row[0]), row[1]) if row else (None, None)

    def save(self, room, data, revision):
        if self.remote:
            try:
                if revision is None:
                    self.request("POST", json={"room_key": room, "payload": data, "revision": 0})
                else:
                    rows = self.request("PATCH", params={"room_key": f"eq.{room}", "revision": f"eq.{revision}"}, json={"payload": data, "revision": revision + 1})
                    if not rows:
                        raise Conflict()
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 409:
                    raise Conflict() from exc
                raise
            return
        with sqlite3.connect(self.db_path) as con:
            if revision is None:
                try:
                    con.execute("INSERT INTO rooms VALUES (?,?,0)", (room, json.dumps(data, ensure_ascii=False)))
                except sqlite3.IntegrityError as exc:
                    raise Conflict() from exc
            else:
                cur = con.execute("UPDATE rooms SET payload=?,revision=revision+1 WHERE room_key=? AND revision=?", (json.dumps(data, ensure_ascii=False), room, revision))
                if cur.rowcount != 1:
                    raise Conflict()


def derive_pin(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000).hex()


def verify_pin(pin, member):
    return secrets.compare_digest(derive_pin(pin, member["salt"]), member["pin_hash"])


def now():
    return datetime.now(KST).isoformat(timespec="seconds")


def entry_key(day, role):
    return f"{day}:{role}"


def put_entry(data, day, role, mood, text):
    """수정하면 상대방의 기존 읽음 표시는 해제합니다."""
    key = entry_key(day, role)
    old = data["entries"].get(key)
    if old and old["text"] == text and old["mood"] == mood:
        return
    data["entries"][key] = {"mood": mood, "text": text, "updated_at": now(), "read_at": None}


def main():
    st.set_page_config(page_title="둘의 하루", page_icon="💌", layout="centered", initial_sidebar_state="collapsed")
    st.markdown("""<style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap');
    html,body,[class*="css"],[data-testid="stApp"] {font-family:'Noto Sans KR',sans-serif;}
    [data-testid="stAppViewContainer"] {background:linear-gradient(150deg,#fff9f8,#fff4f2 60%,#f8f4ff);}
    .block-container {max-width:430px;padding:0.65rem 0.8rem 0.35rem!important;}
    [data-testid="stHeader"],footer {display:none;}
    [data-testid="stVerticalBlock"] {gap:0.33rem;}
    h1 {font-size:1.45rem!important;margin:0!important;padding:0!important;color:#714957;}
    p {margin-bottom:0.15rem;} .stCaption {font-size:0.73rem;}
    [data-testid="stTextArea"] textarea {font-size:0.86rem;line-height:1.4;min-height:78px!important;}
    [data-testid="stTextInput"] input {min-height:36px;}
    [data-testid="stButton"] button,[data-testid="stFormSubmitButton"] button {min-height:35px;border-radius:12px;}
    [data-testid="stForm"] {padding:0.55rem;border-radius:16px;background:#ffffffaa;border:1px solid #f0dce0;}
    [data-testid="stAlert"] {padding:0.4rem 0.6rem;}
    [data-testid="stDateInput"] {max-width:100%;}
    @media(max-height:690px) {.block-container{padding-top:0.3rem!important;} [data-testid="stVerticalBlock"]{gap:0.2rem;}}
    </style>""", unsafe_allow_html=True)
    st.title("💌 둘의 하루")
    url, key = setting("SUPABASE_URL"), setting("SUPABASE_SERVICE_ROLE_KEY")
    if bool(url) != bool(key):
        st.error("Secrets에 SUPABASE_URL과 SUPABASE_SERVICE_ROLE_KEY를 모두 설정해 주세요.")
        st.stop()
    try:
        store = Store(url, key)
    except Exception:
        st.error("저장소를 준비하지 못했어요. 설정을 확인해 주세요.")
        st.stop()
    if not store.remote:
        st.caption("🧪 로컬 체험 · 온라인 영구 저장은 Supabase 설정 필요")
    if "auth" not in st.session_state:
        st.caption("오늘의 기분과 짧은 일기, 우리 둘만 나눠요.")
        with st.form("login"):
            room_code = st.text_input("우리 연결 코드", type="password", placeholder="둘만 아는 12자 이상의 문구", max_chars=100)
            cols = st.columns(2)
            role = cols[0].selectbox("내 자리", ["A", "B"], format_func=lambda x: "🌷 첫 번째 사람" if x == "A" else "🌿 두 번째 사람")
            nickname = cols[1].text_input("내 이름", max_chars=12, placeholder="예: 봄이")
            pin = st.text_input("내 비밀번호", type="password", placeholder="첫 입장 때 등록 · 8자 이상", max_chars=100)
            submit = st.form_submit_button("우리 다이어리 열기", use_container_width=True, type="primary")
        st.caption("같은 연결 코드 + 서로 다른 자리(A/B)를 선택하세요. 비밀번호는 각자 등록하며 잊으면 복구할 수 없어요.")
        if submit:
            room_code = room_code.strip()
            if len(room_code) < 12 or len(pin) < 8 or not nickname.strip():
                st.error("연결 코드 12자 이상, 비밀번호 8자 이상, 이름을 입력해 주세요.")
                st.stop()
            room = hashlib.sha256(room_code.encode()).hexdigest()
            try:
                data, rev = store.load(room)
                if data is None:
                    data = {"members": {}, "entries": {}}
                member = data["members"].get(role)
                if member:
                    if not verify_pin(pin, member):
                        st.error("자리 또는 비밀번호가 맞지 않아요.")
                        st.stop()
                else:
                    salt = secrets.token_hex(16)
                    data["members"][role] = {"name": nickname.strip(), "salt": salt, "pin_hash": derive_pin(pin, salt)}
                    store.save(room, data, rev)
                st.session_state.auth = {"room": room, "role": role}
                st.rerun()
            except Conflict:
                st.warning("상대가 동시에 입장했어요. 한 번 더 눌러주세요.")
            except (requests.RequestException, sqlite3.Error, ValueError, KeyError):
                st.error("저장소 연결에 실패했어요. Supabase 테이블과 Secrets를 확인해 주세요.")
        st.stop()

    auth = st.session_state.auth
    room, role = auth["room"], auth["role"]
    other = "B" if role == "A" else "A"
    try:
        data, revision = store.load(room)
        if not data or role not in data["members"]:
            del st.session_state.auth
            st.rerun()
    except (requests.RequestException, sqlite3.Error, ValueError, KeyError):
        st.error("일기를 불러오지 못했어요. 잠시 후 새로고침해 주세요.")
        if st.button("다시 불러오기"):
            st.rerun()
        st.stop()
    c1, c2, c3 = st.columns([5, 1, 1])
    day = c1.date_input("날짜", value=datetime.now(KST).date(), max_value=datetime.now(KST).date(), label_visibility="collapsed").isoformat()
    with c2.popover("QR", use_container_width=True):
        app_url = setting("APP_URL")
        if app_url.startswith("https://"):
            buf = io.BytesIO()
            qrcode.make(app_url).save(buf, format="PNG")
            st.image(buf.getvalue(), caption="휴대폰 카메라로 스캔")
            st.caption("QR에는 앱 주소만 들어 있어요. 연결 코드와 비밀번호는 별도로 입력합니다.")
        else:
            st.caption('Secrets에 APP_URL = "https://배포주소"를 설정하세요.')
        if st.button("로그아웃", use_container_width=True):
            st.session_state.clear()
            st.rerun()
    if c3.button("↻", help="상대방의 최신 일기와 읽음 상태를 불러와요", use_container_width=True):
        st.rerun()
    mine = data["entries"].get(entry_key(day, role))
    theirs = data["entries"].get(entry_key(day, other))
    my_name = data["members"][role]["name"]
    partner = data["members"].get(other, {}).get("name", "상대방")
    st.caption(f"{my_name}의 하루 · " + ("💗 상대가 확인했어요" if mine and mine.get("read_at") else "✉️ 아직 확인 전" if mine else "오늘의 마음을 남겨보세요"))
    with st.form(f"write_{day}_{role}"):
        mood = st.selectbox("오늘의 기분", MOODS, index=MOODS.index(mine["mood"]) if mine and mine["mood"] in MOODS else 0, label_visibility="collapsed")
        text = st.text_area("나의 짧은 일기", value=mine["text"] if mine else "", height=82, max_chars=180, placeholder="오늘 어떤 하루였어? (최대 180자)", label_visibility="collapsed")
        saved = st.form_submit_button("💌 일기 수정" if mine else "💌 마음 보내기", type="primary", use_container_width=True)
    if saved:
        if not text.strip():
            st.warning("한 줄이라도 마음을 적어주세요.")
        else:
            put_entry(data, day, role, mood, text.strip())
            try:
                store.save(room, data, revision)
                st.toast("마음을 저장했어요 💗")
                st.rerun()
            except Conflict:
                st.warning("상대의 변경사항이 있어요. 작성한 글을 복사한 뒤 ↻로 새로고침하고 다시 저장하세요.")
            except (requests.RequestException, sqlite3.Error):
                st.error("저장하지 못했어요. 입력한 내용을 복사해 두고 다시 시도해 주세요.")
    st.caption(f"🌿 {partner}의 하루" + (f" · {theirs['mood']}" if theirs else ""))
    # 고정 높이 카드: 긴 글은 이 카드 안에서만 스크롤됩니다. HTML에 사용자 글을 넣지 않습니다.
    with st.container(height=105, border=True):
        st.text(theirs["text"] if theirs else "아직 도착한 일기가 없어요. 조금만 기다려 주세요 💭")
    checked = st.checkbox("💗 확인했어요", value=bool(theirs and theirs.get("read_at")), disabled=not bool(theirs), key=f"read_{day}_{role}_{theirs.get('updated_at') if theirs else 'empty'}")
    if theirs and checked != bool(theirs.get("read_at")):
        theirs["read_at"] = now() if checked else None
        try:
            store.save(room, data, revision)
            st.rerun()
        except Conflict:
            st.warning("일기가 변경됐어요. ↻를 누른 뒤 다시 확인해 주세요.")
        except (requests.RequestException, sqlite3.Error):
            st.error("읽음 상태를 저장하지 못했어요. ↻를 누르고 다시 시도해 주세요.")
    st.caption("하루 한 편 · 수정하면 읽음 해제 · 최신 상태는 ↻")


if __name__ == "__main__":
    main()
