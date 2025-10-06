# app.py — Full Disaster Report app (Login, Register, Dashboard w/ map, Feed, Notifications, Delete)
import os
import time
import uuid
import base64
import json
import math
from datetime import datetime
import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, storage
from streamlit_folium import st_folium
import folium
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
import bcrypt


# optional autorefresh helper
try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    def st_autorefresh(interval, limit, key):
        return None

# ---------------- Config ----------------
st.set_page_config(page_title="Report Disasters", layout="wide", initial_sidebar_state="collapsed")

# ---------- Firebase init ----------
try:
    if not firebase_admin._apps:
        if "serviceAccount" in st.secrets:  # ✅ For Streamlit Cloud
            cred = credentials.Certificate(dict(st.secrets["serviceAccount"]))
            firebase_admin.initialize_app(cred)
        # else:  # ✅ Local fallback (for dev)
        #     SERVICE_ACCOUNT_PATH = "serviceAccount.json"
        #     if not os.path.exists(SERVICE_ACCOUNT_PATH):
        #         st.error("Missing serviceAccount.json or [serviceAccount] in secrets.toml")
        #         st.stop()
        #     cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
        #     firebase_admin.initialize_app(cred)

    db = firestore.client()
    try:
        bucket = storage.bucket()
    except Exception:
        bucket = None

except Exception as e:
    st.error(f"🔥 Firebase initialization failed: {e}")
    st.stop()

USERS_COLLECTION = "app_users"
INCIDENTS_COLLECTION = "incidents"

# ---------------- Helpers ----------------
def hash_password(pw: str) -> bytes:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt())

def check_password(pw: str, hashed: bytes) -> bool:
    return bcrypt.checkpw(pw.encode("utf-8"), hashed)

def create_user_in_firestore(email: str, password: str, username: str):
    email = email.strip().lower()
    username = username.strip() if username else ""
    if '@' not in email or len(password) < 8 or len(username) < 2:
        return False, "Provide a valid email, username (min 2 chars) and password >= 8 chars."
    doc_ref = db.collection(USERS_COLLECTION).document(email)
    if doc_ref.get().exists:
        return False, "Email already registered"
    hashed = hash_password(password)
    doc_ref.set({
        "email": email,
        "username": username,
        "password_hash": base64.b64encode(hashed).decode("utf-8"),
        "last_seen_ms": int(time.time()*1000),
        "created": firestore.SERVER_TIMESTAMP
    })
    return True, None

def authenticate_user(email: str, password: str):
    email = email.strip().lower()
    doc = db.collection(USERS_COLLECTION).document(email).get()
    if not doc.exists:
        return False, "No account with this email"
    data = doc.to_dict()
    hashed_b64 = data.get("password_hash")
    if not hashed_b64:
        return False, "Account corrupted"
    hashed = base64.b64decode(hashed_b64.encode("utf-8"))
    if check_password(password, hashed):
        return True, data.get("username") or email.split("@")[0]
    return False, "Invalid password"

def get_user_doc(email):
    try:
        if not email:
            return None
        doc = db.collection(USERS_COLLECTION).document(email.strip().lower()).get()
        return doc.to_dict() if doc.exists else None
    except Exception:
        return None

def set_user_last_seen(email, ms=None):
    if not email:
        return
    try:
        if ms is None:
            ms = int(time.time()*1000)
        db.collection(USERS_COLLECTION).document(email.strip().lower()).set({"last_seen_ms": ms}, merge=True)
    except Exception:
        pass

def set_user_home_location(email, lat, lng):
    if not email:
        return
    try:
        db.collection(USERS_COLLECTION).document(email.strip().lower()).set({"home_lat": float(lat), "home_lng": float(lng)}, merge=True)
    except Exception:
        pass

def reverse_geocode(lat, lng):
    try:
        geolocator = Nominatim(user_agent="report-disasters")
        loc = geolocator.reverse((lat, lng), timeout=10, language="en", addressdetails=True)
        if loc and loc.raw:
            addr = loc.raw.get("address", {})
            country = addr.get("country")
            region = addr.get("state") or addr.get("region") or addr.get("county")
            display = loc.address
            return country, region, display
    except Exception:
        return None, None, None
    return None, None, None

def save_incident(uid_email, username, inc_type, description, lat, lng, level="Normal", photo_bytes=None, photo_name=None):
    created_ms = int(time.time()*1000)
    country, region, display_addr = reverse_geocode(lat, lng)
    doc = {
        "uid": (uid_email or "anonymous").strip().lower(),
        "username": username or (uid_email or "anonymous").split("@")[0],
        "type": inc_type,
        "description": description,
        "level": level,
        "country": country,
        "region": region,
        "display_address": display_addr,
        "location": firestore.GeoPoint(float(lat), float(lng)),
        "created": firestore.SERVER_TIMESTAMP,
        "created_ms": created_ms,
        "source": "streamlit"
    }
    if photo_bytes and bucket:
        ext = (photo_name.split(".")[-1] if photo_name and "." in photo_name else "jpg")
        dest = f"images/{(uid_email or 'anon')}_{int(time.time())}_{uuid.uuid4().hex}.{ext}"
        blob = bucket.blob(dest)
        blob.upload_from_string(photo_bytes, content_type=f"image/{ext}")
        try:
            blob.make_public()
            doc["photo_url"] = blob.public_url
        except Exception:
            doc["photo_url"] = None
    db.collection(INCIDENTS_COLLECTION).add(doc)

def geocode_address(q):
    if not q: return None
    try:
        geolocator = Nominatim(user_agent="report-disasters")
        r = geolocator.geocode(q, timeout=10)
        if r:
            return r.latitude, r.longitude, r.address
    except GeocoderTimedOut:
        return None
    except Exception:
        return None
    return None

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.asin(math.sqrt(a))

# Notification helper (JS)
def _send_browser_notifications(items):
    """
    items: list of dicts with keys: title, body, optional 'level' (e.g. "Dangerous","Warning","Normal","Peace")
    This builds a small SVG data-URI icon colored by level and sends notifications via the browser Notification API.
    """
    if not items:
        return

    def _level_color(lv):
        if not lv:
            return "#2563EB"
        lv_l = str(lv).strip().lower()
        if lv_l in ("dangerous", "danger", "high"):
            return "#ef4444"
        if lv_l in ("warning",):
            return "#f59e0b"
        if lv_l in ("peace", "normal", "ok", "safe"):
            return "#10b981"
        return "#2563EB"

    enriched = []
    for it in items:
        title = it.get("title", "")
        body = it.get("body", "")
        level = it.get("level", None)
        color = _level_color(level)

        symbol = "•"
        if level:
            lv = str(level).lower()
            if "danger" in lv:
                symbol = "❗"
            elif "warn" in lv:
                symbol = "⚠️"
            elif lv in ("normal", "peace", "ok", "safe"):
                symbol = "✓"

        svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='128' height='128'>
  <rect rx='20' width='100%' height='100%' fill='{color}'/>
  <text x='50%' y='58%' text-anchor='middle' font-size='68' font-family='Segoe UI, Arial, sans-serif' fill='white'>{symbol}</text>
</svg>"""
        svg_b64 = base64.b64encode(svg.encode("utf-8")).decode("utf-8")
        icon_data = f"data:image/svg+xml;base64,{svg_b64}"

        enriched.append({"title": title, "body": body, "icon": icon_data})

    payload = json.dumps(enriched)

    notify_html = f"""
    <script>
      (async function(){{
        const items = {payload};
        try {{
          const perm = await Notification.requestPermission();
          if (perm === 'granted') {{
            for (const it of items) {{
              try {{
                const n = new Notification(it.title, {{ body: it.body, icon: it.icon }});
                setTimeout(()=>n.close(), 6000);
              }} catch(e){{ console.warn(e); }}
            }}
          }} else {{
            console.log('Notification permission:', perm);
          }}
        }} catch(e){{ console.error('notify error', e); }}
      }})();
    </script>
    """
    st.components.v1.html(notify_html, height=0)

# ---------------- Session defaults & simple cache ----------------
st.session_state.setdefault("user", None)   # dict: {email, username}
st.session_state.setdefault("page", "home") # home, login, register, dashboard, feed, account
# map center + selection defaults (fixes KeyError observed)
st.session_state.setdefault("map_center", (24.86, 67.01))
st.session_state.setdefault("selected_lat", None)
st.session_state.setdefault("selected_lng", None)
st.session_state.setdefault("last_seen_ms", int(time.time()*1000))
# simple in-session cache for incidents
st.session_state.setdefault("_inc_cache", {"ts": 0, "params": None, "data": None})
st.session_state.setdefault("map_markers_loaded", False)
st.session_state.setdefault("_inc_feed", None)
st.session_state.setdefault("feed_loaded", False)

INC_CACHE_TTL_SECONDS = 20

def _inc_cache_key(limit, order_by_field, order_desc, extra=None):
    return json.dumps({"limit": limit, "order_by_field": order_by_field, "order_desc": order_desc, "extra": extra})

def fetch_incidents(limit=200, order_by_field="created", order_desc=True, force_refresh=False):
    params_key = _inc_cache_key(limit, order_by_field, order_desc)
    now = time.time()
    cache = st.session_state.get("_inc_cache", {"ts": 0, "params": None, "data": None})
    if (not force_refresh) and cache.get("params") == params_key and (now - cache.get("ts", 0) < INC_CACHE_TTL_SECONDS) and cache.get("data") is not None:
        return cache.get("data")
    try:
        direction = firestore.Query.DESCENDING if order_desc else firestore.Query.ASCENDING
        docs = list(db.collection(INCIDENTS_COLLECTION).order_by(order_by_field, direction=direction).limit(limit).stream())
    except Exception:
        docs = []
    st.session_state["_inc_cache"] = {"ts": now, "params": params_key, "data": docs}
    return docs

# fetch page helper (keeps pagination)
def fetch_incidents_page(page_size=30, order_by_field="created_ms", order_desc=True, start_after_snapshot=None):
    try:
        collection = db.collection(INCIDENTS_COLLECTION)
        direction = firestore.Query.DESCENDING if order_desc else firestore.Query.ASCENDING
        query = collection.order_by(order_by_field, direction=direction).limit(page_size)
        if start_after_snapshot:
            query = query.start_after(start_after_snapshot)
        docs = list(query.stream())
        last_snap = docs[-1] if docs else None
        return docs, last_snap
    except Exception as e:
        print("fetch_incidents_page error:", e)
        return [], None

def get_qparam(k, default=None):
    q = st.query_params
    v = q.get(k)
    if not v:
        return default
    return v[0] if isinstance(v, (list, tuple)) else v

# Handle query params: lat/lng from detect, setPage for navbar, report viewing
qp_lat = get_qparam("lat")
qp_lng = get_qparam("lng")
if qp_lat and qp_lng:
    try:
        st.session_state.selected_lat = float(qp_lat)
        st.session_state.selected_lng = float(qp_lng)
        st.session_state.map_center = (st.session_state.selected_lat, st.session_state.selected_lng)
        st.session_state.page = "dashboard"
    except Exception:
        pass

selected_report_param = get_qparam("report", None)
setpage = get_qparam("setPage")
if setpage:
    st.session_state.page = setpage

# ---------------- Styling (unchanged) ----------------
st.markdown("""
<style>
:root {
  --bg: #F9FAFB;
  --text: #0F172A;
  --primary: #2563EB;
  --nav: #E5E7EB;
  --btn-grad: linear-gradient(90deg,#0b66ff,#0077ff);
}

html, body, .stApp {
  background: var(--bg) !important;
  color: var(--text) !important;
  font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial !important;
}

/* Navbar */
.navbar {
  background: var(--nav);
  padding: 10px 20px;
  border-radius: 12px;
  display:flex;
  align-items:center;
  gap:14px;
  margin-bottom: 18px;
  box-shadow: 0 4px 14px rgba(2,6,23,0.06);
}
.brand { font-weight:700; font-size:18px; color:var(--text); }
.nav-right { margin-left:auto; display:flex; gap:12px; align-items:center; }
.nav-btn {
  background: transparent;
  border: none;
  padding:8px 12px;
  border-radius:8px;
  font-weight:600;
  cursor:pointer;
  color: var(--text);
}
.nav-btn.active {
  background: var(--btn-grad);
  color: white;
  box-shadow: 0 8px 24px rgba(11,102,255,0.12);
}

/* Card (form container) */
.card {
  background: #fff;
  padding: 24px 28px;
  border-radius: 12px;
  box-shadow: 0 8px 30px rgba(15,23,42,0.06);
  max-width: 420px; /* 🔹 narrower width */
  margin: 60px auto; /* centered with top margin */
  box-sizing: border-box;
}

/* Inputs */
input, textarea, .stTextInput>div>input, .stTextArea>div>textarea {
  color: var(--text) !important;
  background: #ffffff !important;
  border: 1px solid #E6EEF8 !important;
  border-radius: 8px !important;
  padding: 10px 12px !important;
  height: 44px !important;
  font-size: 14px !important;
}

/* Buttons */
.stButton>button {
  background: var(--btn-grad) !important;
  color: #fff !important;
  font-weight:700 !important;
  border-radius: 8px !important;
  padding: 10px 12px !important;
  width: 100%;
  height: 44px !important;
  font-size: 15px !important;
}
.stButton>button:hover {
  opacity: 0.95 !important;
  transform: translateY(-1px);
}

/* Helper text */
.small { font-size:13px; color:#475569; text-align:center; }

/* Feed area layout (unchanged) */
.feed-card {
  padding:12px;
  border-radius:12px;
  background:#fff;
  border:1px solid #EEF2FF;
  margin-bottom:14px;
  box-shadow: 0 6px 18px rgba(2,6,23,0.04);
}

.feed-wrap {
  padding-left: 48px;
  padding-right: 48px;
  max-width: 1100px;
  margin-left: auto;
  margin-right: auto;
}
@media (max-width: 800px) {
  .feed-wrap { padding-left: 16px; padding-right: 16px; max-width: 100%; }
}
.block-container { padding-top: 0 !important; }
</style>
""", unsafe_allow_html=True)


# ---------------- Navbar renderer ----------------
def render_sidebar():
    if "page" not in st.session_state:
        st.session_state.page = "home"
    if "user" not in st.session_state:
        st.session_state.user = None

    # Strong, precise CSS targeting Streamlit sidebar buttons
    st.markdown("""
    <style>
    /* Sidebar base */
    [data-testid="stSidebar"] {
        background-color: #ebeced;
        border-right: 1px solid #E5E7EB;
        padding: 12px 12px 24px 12px;
    }

    /* Hide the default sidebar nav if present */
    [data-testid="stSidebarNav"] { display: none; }

    .sidebar-title {
        font-weight: 700;
        font-size: 20px;
        color: #0F172A;
        margin-bottom: 12px;
        padding-left: 4px;
    }

    /* Ensure wrapper divs take full width */
    [data-testid="stSidebar"] > div, 
    [data-testid="stSidebar"] > nav,
    [data-testid="stSidebar"] .css-1d391kg { width: 100% !important; }

    /* === The important part: force all sidebar buttons to identical size ===
       Target both Streamlit wrapper (.stButton) and any custom .sidebar-btn/.active-page wrappers
    */
    [data-testid="stSidebar"] .stButton > button,
    [data-testid="stSidebar"] .sidebar-btn button,
    [data-testid="stSidebar"] .active-page button {
        width: 100% !important;
        box-sizing: border-box !important;
        min-height: 44px !important;
        height: 44px !important;
        padding: 8px 12px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 3px !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        font-size: 15px !important;
        font-weight: 700 !important;
        border-radius: 8px !important;
        margin: 3px 0 !important;
        background: transparent !important;
        color: #0F172A !important;
        cursor: pointer !important;
    }

    /* Hover + active */
    [data-testid="stSidebar"] .stButton > button:hover,
    [data-testid="stSidebar"] .sidebar-btn button:hover {
        background-color: #E2E8F0 !important;
    }
    [data-testid="stSidebar"] .active-page button {
        background-color: #0F172A !important;
        color: #fff !important;
    }

    /* If icons / emoji make lines taller, cap them to same height */
    [data-testid="stSidebar"] .stButton > button > span,
    [data-testid="stSidebar"] .stButton > button > div {
        line-height: 1 !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
    }

    /* Small screens: slightly taller tap targets */
    @media (max-width: 480px) {
      [data-testid="stSidebar"] .stButton > button { height: 50px !important; min-height: 50px !important; }
    }
    </style>
    """, unsafe_allow_html=True)

    with st.sidebar:
        st.markdown("<div class='sidebar-title'>📣 Report Disasters</div>", unsafe_allow_html=True)

        if st.session_state.user:
            pages = [
                (" Home", "home"),
                (" Dashboard", "dashboard"),
                (" Report Feed", "feed"),
                (" Account", "account"),
                (" Logout", "logout")
            ]
        else:
            pages = [
                (" Home", "home"),
                (" Login", "login"),
                (" Register", "register")
            ]

        for label, target in pages:
            active_class = "active-page" if st.session_state.page == target else "sidebar-btn"
            # wrap each button in a div so our CSS can target .sidebar-btn as well
            st.markdown(f"<div class='{active_class}' style='width:100%'>", unsafe_allow_html=True)
            if st.button(label, key=f"nav_{target}"):
                if target == "logout":
                    st.session_state.user = None
                    st.session_state.page = "home"
                else:
                    st.session_state.page = target
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)


# ---------------- Alerts ----------------
def show_alert(msg, type_="info"):
    colors = {
        "info": "#DBEAFE",
        "success": "#DCFCE7",
        "warning": "#FEF9C3",
        "error": "#FEE2E2",
    }
    border = {
        "info": "#3B82F6",
        "success": "#16A34A",
        "warning": "#EAB308",
        "error": "#DC2626",
    }
    color = colors.get(type_, "#E5E7EB")
    bcol = border.get(type_, "#6B7280")
    html = f"""
    <div style='background:{color};border-left:5px solid {bcol};
        padding:10px 14px;border-radius:8px;margin:10px 0;font-size:14px;color:#111'>
        {msg}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

# ---------------- HOME PAGE ----------------
def page_home():
    
    st.markdown("""
        <style>
                .block-container {
    padding-top: 0 !important;
}
        /* ---------- HOME PAGE STYLES ---------- */
        .home-hero {
            text-align: center;
            padding: 60px 20px 40px 20px;
            background: #F9FAFB;
            border-radius: 18px;
            color: #0F172A;
            # box-shadow: 0 10px 25px rgba(0,0,0,0.15);
            animation: fadeSlideIn 0.8s ease-out forwards;

        }
        .home-hero h1 {
            font-size: 42px;
            margin-bottom: 14px;
            font-weight: 800;
            letter-spacing: -0.5px;
        }
        .home-hero p {
            font-size: 18px;
            line-height: 1.5;
            max-width: 700px;
            margin: 0 auto 24px auto;
            opacity: 0.9;
        }
        .cta-buttons {
            display: flex;
            justify-content: center;
            gap: 20px;
            flex-wrap: wrap;
        }
        .cta-buttons button {
            font-size: 16px !important;
            padding: 10px 24px;
            border-radius: 12px;
            transition: transform 0.2s ease, background 0.3s;
            border: none;
            cursor: pointer;
        }
        .cta-buttons button:hover {
            transform: translateY(-3px);
        }
        .btn-primary {
            background: #DC2626;
            color: white;
        }
        .btn-primary:hover {
            background: #B91C1C;
        }
        .btn-secondary {
            background: #2563EB;
            color: white;
        }
        .btn-secondary:hover {
            background: #1E3A8A;
        }

        .info-section {
            text-align: center;
            padding: 60px 20px;
            max-width: 800px;
            margin: 0 auto;
            animation: fadeSlideUp 1s ease-out forwards;
        }
        .info-section h2 {
            color: var(--text);
            margin-bottom: 16px;
        }
        .info-section p {
            color: #334155;
            font-size: 16px;
            line-height: 1.6;
        }

        .features {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 20px;
            padding: 40px 20px;
            max-width: 1000px;
            margin: auto;
        }
        .feature-card {
            background: white;
            border-radius: 14px;
            padding: 24px;
            box-shadow: 0 4px 14px rgba(0,0,0,0.06);
            text-align: center;
            transition: transform 0.25s ease;
        }
        .feature-card:hover {
            transform: translateY(-6px);
        }
        .feature-icon {
            font-size: 36px;
            margin-bottom: 12px;
        }

        @keyframes fadeSlideIn {
            from {opacity: 0; transform: translateY(-20px);}
            to {opacity: 1; transform: translateY(0);}
        }
        @keyframes fadeSlideUp {
            from {opacity: 0; transform: translateY(20px);}
            to {opacity: 1; transform: translateY(0);}
        }
        </style>
    """, unsafe_allow_html=True)

    # -------- Hero Section --------
    st.markdown("""
        <div class='home-hero'>
            <h1>🌍 Report Disasters — Community Safety Platform</h1>
            <p>Join a global community that helps track, report, and respond to local disasters in real-time.
            Every report matters — be part of the network that saves lives.</p>
            <div class='cta-buttons'>
    """, unsafe_allow_html=True)

    # if st.session_state.user:
    #     col1, col2 = st.columns(2, gap="small")
    #     with col1:
    #         if st.button("➕ Create a Report", key="home_report_btn"):
    #             st.session_state.page = "dashboard"
    #             st.rerun()
    #     with col2:
    #         if st.button("📰 Open Feed", key="home_feed_btn"):
    #             st.session_state.page = "feed"
    #             st.rerun()
    # else:
    #     c1, c2 = st.columns(2, gap="small")
    #     with c1:
    #         if st.button("🔐 Sign In to Start", key="home_login_btn"):
    #             st.session_state.page = "login"
    #             st.rerun()
    #     with c2:
    #         st.markdown("<button class='btn-secondary'>Browse Public Reports</button>", unsafe_allow_html=True)

    # st.markdown("</div></div>", unsafe_allow_html=True)

    # -------- Info Section --------
    st.markdown("""
        <div class='info-section'>
            <h2>Why This App?</h2>
            <p>Quickly report incidents like floods, fires, or accidents with location and photos. 
            Stay informed about nearby emergencies through instant feed updates and map markers.</p>
        </div>
    """, unsafe_allow_html=True)

    # -------- Feature Section --------
    st.markdown("""
        <div class='features'>
            <div class='feature-card'>
                <div class='feature-icon'>📸</div>
                <h3>Quick Reports</h3>
                <p>Submit incidents in seconds with automatic geolocation and optional photos.</p>
            </div>
            <div class='feature-card'>
                <div class='feature-icon'>🗺️</div>
                <h3>Interactive Map</h3>
                <p>Explore real-time disaster data visually on an integrated global map.</p>
            </div>
            <div class='feature-card'>
                <div class='feature-icon'>🔔</div>
                <h3>Instant Alerts</h3>
                <p>Receive browser notifications when a new report appears near you.</p>
            </div>
            <div class='feature-card'>
                <div class='feature-icon'>🤝</div>
                <h3>Community Driven</h3>
                <p>Help others stay safe by contributing verified, local information.</p>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # -------- Footer --------
    st.markdown("""
        <div style='text-align:center; padding:30px; color:#64748B; font-size:13px;'>
            © 2025 Report Disasters — Built with ❤️ by the community.
        </div>
    """, unsafe_allow_html=True)

# ---------------- LOGIN / REGISTER (unchanged) ----------------
def page_login():
    with st.form("login_form"):
        st.markdown("## 🔐 Sign in")
        email = st.text_input("Email", key="login_email")
        pwd = st.text_input("Password", type="password", key="login_pwd")
        submit = st.form_submit_button("Sign in")

        if submit:
            if not email or not pwd:
                st.error("Enter both email & password")
            else:
                ok, username_or_err = authenticate_user(email, pwd)
                if ok:
                    user_doc = get_user_doc(email)
                    stored_last_seen = 0
                    home_lat = home_lng = None
                    try:
                        if user_doc:
                            stored_last_seen = int(user_doc.get("last_seen_ms") or 0)
                            if user_doc.get("home_lat") is not None and user_doc.get("home_lng") is not None:
                                home_lat = float(user_doc.get("home_lat"))
                                home_lng = float(user_doc.get("home_lng"))
                    except Exception:
                        stored_last_seen = 0

                    missed_items = []
                    try:
                        try:
                            rows = db.collection(INCIDENTS_COLLECTION).where(
                                "created_ms", ">", stored_last_seen
                            ).order_by("created_ms", direction=firestore.Query.ASCENDING).limit(50).stream()
                        except Exception:
                            rows = db.collection(INCIDENTS_COLLECTION).order_by(
                                "created_ms", direction=firestore.Query.DESCENDING
                            ).limit(200).stream()
                        for rr in rows:
                            d = rr.to_dict()
                            created_ms = d.get("created_ms") or 0
                            if created_ms <= stored_last_seen:
                                continue
                            if home_lat is not None and home_lng is not None:
                                try:
                                    loc = d.get("location")
                                    if loc:
                                        dist_km = haversine(
                                            home_lat, home_lng,
                                            float(loc.latitude), float(loc.longitude)
                                        )
                                        if dist_km > 100:
                                            continue
                                except Exception:
                                    pass
                            missed_items.append({
                                "title": f"Missed: {d.get('type','Report')}",
                                "body": (d.get('description') or "")[:140]
                            })
                    except Exception:
                        missed_items = []

                    if missed_items:
                        _send_browser_notifications(missed_items)

                    try:
                        set_user_last_seen(email)
                    except Exception:
                        pass

                    st.session_state.user = {
                        "email": email.strip().lower(),
                        "username": username_or_err
                    }
                    st.success(f"Signed in as @{st.session_state.user['username']}")
                    st.session_state.page = "dashboard"
                    st.rerun()
                else:
                    st.error(username_or_err)

    st.markdown("<div class='small'>Don't have an account? <a href='?setPage=register'>Register</a></div>", unsafe_allow_html=True)


def page_register():
    with st.form("register_form"):
        st.markdown("## 🧾 Register")
        username = st.text_input("Username (visible to others)", key="reg_user")
        email = st.text_input("Email", key="reg_email")
        pwd = st.text_input("Password (min 8 chars)", type="password", key="reg_pwd")
        pwd2 = st.text_input("Confirm password", type="password", key="reg_pwd2")
        create = st.form_submit_button("Create account")

        if create:
            if not username or not email or not pwd:
                st.error("Enter username, email & password")
            elif pwd != pwd2:
                st.error("Passwords do not match")
            elif len(pwd) < 8:
                st.error("Password must be at least 8 characters")
            else:
                ok, err = create_user_in_firestore(email, pwd, username)
                if ok:
                    st.success("Account created — please sign in.")
                    st.session_state.page = "login"
                    st.rerun()
                else:
                    st.error(err)

    st.markdown("<div class='small'>Already have an account? <a href='?setPage=login'>Sign in</a></div>", unsafe_allow_html=True)
st.markdown("""
<style>
:root {
  --bg: #F0F2F5;
  --white: #FFFFFF;
  --text-color: #111827;
  --primary-color: #2563EB;
  --light-gray: #E5E7EB;
  --btn-grad: linear-gradient(90deg,#2563EB,#1D4ED8);
}

/* 🌙 Base layout */
html, body, .stApp {
  background: var(--bg) !important;
  color: var(--text-color) !important;
  font-family: "Inter", system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial !important;
}

/* ✨ Centered form with top padding */
.stForm {
  background: var(--white) !important;
  padding: 3rem 3.5rem !important;
  border-radius: 18px !important;
  box-shadow: 0 12px 36px rgba(0,0,0,0.1) !important;
  width: 540px !important;
  font-size: 16px !important;
  margin: 90px auto 0 auto !important;  /* 🔥 Adds top gap */
}

/* 📝 Input fields */
input[type="text"], input[type="password"], input[type="email"], textarea,
.stTextInput>div>div>input, .stTextArea>div>textarea {
  background: #FAFAFA !important;
  border: 1px solid var(--light-gray) !important;
  color: var(--text-color) !important;
  border-radius: 10px !important;
  padding: 14px 16px !important;
  width: 100% !important;
  font-size: 15px !important;
}
input:focus, textarea:focus {
  border-color: var(--primary-color) !important;
  box-shadow: 0 0 0 3px rgba(37,99,235,0.25) !important;
  outline: none !important;
}

/* Label color fix */
label, .stTextInput label, .stTextArea label {
  color: var(--text-color) !important;
  font-weight: 600 !important;
}

/* 🔘 Buttons (login/register + others) */
.stButton>button {
  background: #cacccf !important; /* 🩶 Default muted gray */
  color: #fff !important;  /* hides text until hover */
  font-weight: 700 !important;
  border-radius: 10px !important;
  padding: 12px 18px !important;
  width: 100% !important;
  height: 50px !important;
  font-size: 16px !important;
  border: none !important;
  margin-top: 20px !important; /* adds breathing space from inputs */
  transition: all 0.35s ease !important;
  letter-spacing: 0.5px !important;
  cursor: pointer !important;
}

/* On hover — gradient reveal */
.stButton>button:hover {
  background: var(--btn-grad) !important;
  color: #fff !important;
  transform: translateY(-2px) !important;
  box-shadow: 0 8px 20px rgba(37,99,235,0.25) !important;
}

/* 🧾 Headings */
h2, h3 {
  font-size: 26px !important;
  font-weight: 700 !important;
  text-align: center !important;
  margin-bottom: 1.5rem !important;
  color: var(--text-color) !important;
}

/* 📎 Helper text / links */
.small {
  font-size: 14px !important;
  color: #475569 !important;
  text-align: center !important;
  margin-top: 16px !important;
}
.small a {
  color: var(--primary-color) !important;
  font-weight: 600 !important;
  text-decoration: none !important;
}
.small a:hover {
  text-decoration: underline !important;
}
</style>
""", unsafe_allow_html=True)


# ---------------- DASHBOARD (unchanged map behavior) ----------------
def page_dashboard():
    if not st.session_state.user:
        st.info("Please sign in first to submit reports.")
        page_login()
        return

    left, right = st.columns([1, 1.6], gap="large")

    with left:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("### 📝 New Report")
        st.write(f"Signed in as: **@{st.session_state.user['username']}**")

        predefined = ["Flood", "Fire", "Earthquake", "Storm", "Landslide", "Roadblock", "Other"]
        inc_type = st.selectbox("Disaster type", predefined, index=0, key="ui_inc_type")
        if inc_type == "Other":
            custom_title = st.text_input("Enter custom disaster title", key="ui_custom_type")
            inc_title = custom_title.strip() if custom_title and custom_title.strip() else "Other"
        else:
            inc_title = inc_type

        level = st.selectbox("Severity", ["Peace", "Normal", "Warning", "Dangerous"], index=1, key="ui_level")
        description = st.text_area("Description", max_chars=1000, height=140, key="ui_desc")

        st.markdown("<div class='small'>Pick a location on the map (right) — click to select. Or use search / Detect my location.</div>", unsafe_allow_html=True)
        search_q = st.text_input("Search place (address / city / landmark)", key="ui_search")

        s1, s2 = st.columns([1,1])
        if s1.button("Search"):
            if search_q.strip():
                res = geocode_address(search_q.strip())
                if res:
                    lat, lng, addr = res
                    st.session_state.map_center = (lat, lng)
                    st.session_state.selected_lat = lat
                    st.session_state.selected_lng = lng
                    st.success(f"Found & selected: {addr}")
                else:
                    st.error("Not found. Try another query.")
            else:
                st.error("Enter search text.")

        detect_html = """
        <button id="detectBtn" style="padding:8px 12px;border-radius:8px;border:none;background:#0b66ff;color:white;font-weight:700;cursor:pointer">Detect my location</button>
        <script>
        const b = document.getElementById('detectBtn');
        b.addEventListener('click', function(){
            if (!navigator.geolocation) { alert('Geolocation not supported'); return; }
            navigator.geolocation.getCurrentPosition(function(p){
                var lat = p.coords.latitude;
                var lng = p.coords.longitude;
                var base = window.location.protocol + "//" + window.location.host + window.location.pathname;
                window.location.replace(base + '?lat=' + encodeURIComponent(lat) + '&lng=' + encodeURIComponent(lng) + '&setPage=dashboard');
            }, function(err){
                alert('Location error: ' + (err && err.message ? err.message : err.code));
            }, { enableHighAccuracy:true, timeout:15000 });
        });
        </script>
        """
        st.components.v1.html(detect_html, height=48)

        photo = st.file_uploader("Attach Photo (optional)", type=["jpg","jpeg","png"], key="ui_photo")

        c1, c2 = st.columns([1,1])
        if c1.button("Submit report"):
            lat = st.session_state.get("selected_lat")
            lng = st.session_state.get("selected_lng")
            if lat is None or lng is None:
                st.error("Location not selected. Click on map (right), search & press Search, or use Detect my location.")
            else:
                try:
                    photo_bytes = photo.getvalue() if photo else None
                    save_incident(st.session_state.user["email"], st.session_state.user["username"], inc_title, description or "", lat, lng, level, photo_bytes, getattr(photo, "name", None))
                    st.success("Report submitted — thank you.")
                    st.session_state.last_seen_ms = int(time.time()*1000)
                    st.session_state.selected_lat = None
                    st.session_state.selected_lng = None
                    st.session_state["_inc_cache"] = {"ts": 0, "params": None, "data": None}
                    st.session_state.feed_loaded = False
                    st.session_state.map_markers_loaded = False
                    st.session_state.page = "feed"
                    st.rerun()
                except Exception as e:
                    st.error("Submit failed: " + str(e))
        if c2.button("Report Feed"):
            st.session_state.page = "feed"
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

    # RIGHT: Map (markers load explicitly)
    with right:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("### 🗺️ Map — click to pick location")
        center = list(st.session_state.get("map_center", (24.86, 67.01)))
        m = folium.Map(location=center, zoom_start=12, control_scale=True)

        cols = st.columns([1,1])
        if cols[0].button("Load map markers"):
            st.session_state.map_markers_loaded = True
            docs = fetch_incidents(limit=200, order_by_field="created", order_desc=True, force_refresh=True)
            st.session_state["_inc_cache"] = {"ts": time.time(), "params": _inc_cache_key(200, "created", True), "data": docs}
        if cols[1].button("Clear markers cache"):
            st.session_state["_inc_cache"] = {"ts": 0, "params": None, "data": None}
            st.session_state.map_markers_loaded = False
            st.success("Marker cache cleared. Click 'Load map markers' to fetch again.")

        if st.session_state.map_markers_loaded:
            docs = st.session_state.get("_inc_cache", {}).get("data") or []
            for d in docs:
                try:
                    dd = d.to_dict()
                    loc = dd.get("location")
                    if not loc:
                        continue
                    lat = float(loc.latitude); lng = float(loc.longitude)
                    popup = f"<b>{dd.get('type')} ({dd.get('level','Normal')})</b><br/>{(dd.get('description') or '')[:200]}<br/><a href='?report={d.id}&setPage=feed'>View report</a>"
                    folium.Marker([lat, lng], popup=popup).add_to(m)
                except Exception:
                    continue

        if st.session_state.selected_lat and st.session_state.selected_lng:
            folium.CircleMarker(location=[st.session_state.selected_lat, st.session_state.selected_lng],
                                radius=9, color="#ff4d4f", fill=True, fill_color="#ff4d4f").add_to(m)
            m.location = [st.session_state.selected_lat, st.session_state.selected_lng]

        map_result = st_folium(m, width="100%", height=700, returned_objects=["last_clicked"])
        last_clicked = map_result.get("last_clicked") if isinstance(map_result, dict) else None
        if last_clicked:
            lat = last_clicked.get("lat"); lng = last_clicked.get("lng")
            if lat and lng:
                st.session_state.selected_lat = float(lat)
                st.session_state.selected_lng = float(lng)
                st.success(f"Selected: {lat:.6f}, {lng:.6f}")
        st.markdown("</div>", unsafe_allow_html=True)

# ---------------- FEED (requires login; no map) ----------------
def page_feed():
    st.markdown("""
<style>
/* feed-only override: set page gutters while feed is active */
.block-container {
  padding-left: 48px !important;
  padding-right: 48px !important;
  max-width: 1100px !important; /* optional */
}
@media (max-width: 800px) {
  .block-container { padding-left: 12px !important; padding-right: 12px !important; }
}
</style>
""", unsafe_allow_html=True)

    if not st.session_state.user:
        st.info("Please sign in to view the Feed.")
        page_login()
        return

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.markdown("## 📢 Report Feed")

    search_col1, search_col2, search_col3 = st.columns([4,2,1])
    with search_col1:
        feed_search_q = st.text_input("Search location to view nearby reports (city / address)", key="feed_search")
    with search_col2:
        radius_km = st.number_input("Radius (km)", min_value=1, max_value=500, value=50, step=5)
    # with search_col3:
    #     if st.button("Clear feed cache"):
    #         st.session_state.pop("_inc_feed", None)
    #         st.success("Feed state cleared. It will reload automatically on page open.")

    if st.button("Enable browser notifications"):
        js = """
        <script>
        (async () => {
          try {
            const perm = await Notification.requestPermission();
            if (perm === 'granted') {
              alert('Notifications enabled — you should receive alerts for new reports.');
            } else {
              alert('Notifications were not granted: ' + perm);
            }
          } catch(e) {
            alert('Notification request failed: ' + e);
          }
        })();
        </script>
        """
        st.components.v1.html(js, height=0)

    user_doc = get_user_doc(st.session_state.user["email"])
    user_home = None
    try:
        if user_doc and user_doc.get("home_lat") is not None and user_doc.get("home_lng") is not None:
            user_home = (float(user_doc.get("home_lat")), float(user_doc.get("home_lng")))
    except Exception:
        user_home = None

    if st.session_state.get("_inc_feed") is None:
        st.session_state["_inc_feed"] = {"docs": [], "last_snap": None, "finished": False, "page_size": 30}

    feed_state = st.session_state["_inc_feed"]
    page_size = feed_state.get("page_size", 30)

    if not feed_state["docs"]:
        try:
            snaps, last_snap = fetch_incidents_page(page_size=page_size, order_by_field="created_ms", order_desc=True, start_after_snapshot=None)
            normalized = []
            for s in snaps:
                try:
                    d = s.to_dict() or {}
                    d["_id"] = getattr(s, "id", None)
                    normalized.append(d)
                except Exception:
                    continue
            feed_state["docs"].extend(normalized)
            feed_state["last_snap"] = last_snap
            feed_state["finished"] = len(snaps) < page_size
            st.session_state["_inc_feed"] = feed_state
        except Exception as e:
            st.error("Failed to load feed: " + str(e))
            st.markdown("</div>", unsafe_allow_html=True)
            return

        try:
            stored_last_seen = int(user_doc.get("last_seen_ms") or 0) if user_doc else 0
            missed_items = []
            for d in feed_state["docs"]:
                created_ms = d.get("created_ms") or 0
                if created_ms > stored_last_seen:
                    missed_items.append({
                        "title": f"New: {d.get('type','Report')}",
                        "body": (d.get('description') or "")[:140],
                        "level": d.get("level")
                    })
            if missed_items:
                _send_browser_notifications(missed_items)
                set_user_last_seen(st.session_state.user["email"], int(time.time()*1000))
        except Exception:
            pass

    filter_by_map_center = st.checkbox("Filter results to current/home location (useful to limit to local area)", value=False, key="feed_filter_map")

    all_docs = feed_state["docs"]
    center_point = None
    if filter_by_map_center:
        if user_home:
            center_point = user_home
            st.markdown(f"<div class='small'>Filtering by saved home location: {center_point[0]:.5f}, {center_point[1]:.5f}</div>", unsafe_allow_html=True)
        else:
            mc = st.session_state.get("map_center")
            if mc:
                center_point = mc
                st.markdown(f"<div class='small'>Filtering by recent map center: {center_point[0]:.5f}, {center_point[1]:.5f}</div>", unsafe_allow_html=True)
            else:
                sel_lat = st.session_state.get("selected_lat")
                sel_lng = st.session_state.get("selected_lng")
                if sel_lat and sel_lng:
                    center_point = (sel_lat, sel_lng)
                    st.markdown(f"<div class='small'>Filtering by recently selected location: {center_point[0]:.5f}, {center_point[1]:.5f}</div>", unsafe_allow_html=True)
                else:
                    st.warning("No home or recent location available — cannot apply map filter. Please set a home location in Account or pick a location on the map.")
                    center_point = None

    if feed_search_q and feed_search_q.strip():
        g = geocode_address(feed_search_q.strip())
        if g:
            center_point = (g[0], g[1])
            st.markdown(f"<div class='small'>Filtering by search location: {g[2]}</div>", unsafe_allow_html=True)

    filtered = []
    for d in all_docs:
        loc = d.get("location")
        if center_point and not loc:
            continue
        if loc:
            try:
                lat = float(getattr(loc, "latitude", loc.get("latitude") if isinstance(loc, dict) else None))
                lng = float(getattr(loc, "longitude", loc.get("longitude") if isinstance(loc, dict) else None))
            except Exception:
                if center_point:
                    continue
                else:
                    lat = None; lng = None

            if center_point and lat is not None and lng is not None:
                try:
                    dist = haversine(center_point[0], center_point[1], lat, lng)
                    if dist > radius_km:
                        continue
                except Exception:
                    continue

        filtered.append(d)

    posts_to_show = filtered if filtered else all_docs

    if not posts_to_show:
        st.info("No reports match current filters / area.")
    else:
        for d in posts_to_show:
            when = d.get("created")
            if isinstance(when, datetime):
                when_str = when.strftime("%b %d, %Y %H:%M")
            else:
                try:
                    when_str = datetime.fromtimestamp((d.get("created_ms") or 0)/1000.0).strftime("%b %d, %Y %H:%M")
                except Exception:
                    when_str = str(when or "")

            location_str = d.get("display_address") or ((d.get("region") or "") + (", " + (d.get("country") or "") if d.get("country") else ""))
            username = d.get("username") or "anon"
            content = d.get("description") or ""
            photo = d.get("photo_url")
            initials = (username[:1].upper() if username else "A")

            level_label = d.get("level") or "Normal"
            level_norm = str(level_label).lower()
            if "danger" in level_norm:
                sev_color = "#ff4d4f"
                sev_text = "Danger"
            elif "warning" in level_norm:
                sev_color = "#f59e0b"
                sev_text = "Warning"
            elif level_norm in ("peace","normal","ok","safe"):
                sev_color = "#10b981"
                sev_text = level_label
            else:
                sev_color = "#2563EB"
                sev_text = level_label

            post_html = f"""
            <div class='feed-card'>
                <div class='post-header'>
                    <div class='post-avatar'>{initials}</div>
                    <div style='flex:1'>
                        <div style='display:flex;align-items:center;gap:8px'>
                            <div class='post-title'>@{username}</div>
                            <div style='margin-left:auto;font-weight:700;padding:6px 10px;border-radius:999px;background:{sev_color};color:white;font-size:12px'>{sev_text}</div>
                        </div>
                        <div class='post-meta'>{when_str} • {location_str or '—'}</div>
                    </div>
                </div>
                <div class='post-body'>{content}</div>
            """
            st.markdown(post_html, unsafe_allow_html=True)
            if photo:
                try:
                    st.image(photo, caption=None, use_column_width=True)
                except Exception:
                    pass

            if st.session_state.user and d.get("uid") == st.session_state.user["email"]:
                if st.button("Delete", key=f"delfeed_{d.get('_id')}"):
                    try:
                        db.collection(INCIDENTS_COLLECTION).document(d.get('_id')).delete()
                        st.session_state.pop("_inc_feed", None)
                        st.success("Deleted. Feed will reload on next visit.")
                        st.rerun()
                    except Exception as e:
                        st.error("Delete failed: " + str(e))

    st.markdown("<div style='text-align:center;margin-top:12px'>", unsafe_allow_html=True)
    if not feed_state.get("finished", False):
        if st.button("Load more"):
            try:
                last_snap = feed_state.get("last_snap", None)
                snaps, last_snap_new = fetch_incidents_page(page_size=page_size, order_by_field="created_ms", order_desc=True, start_after_snapshot=last_snap)
                normalized = []
                for s in snaps:
                    try:
                        d = s.to_dict() or {}
                        d["_id"] = getattr(s, "id", None)
                        normalized.append(d)
                    except Exception:
                        continue
                existing_ids = {x["_id"] for x in feed_state["docs"] if x.get("_id")}
                for nd in normalized:
                    if nd.get("_id") not in existing_ids:
                        feed_state["docs"].append(nd)
                feed_state["last_snap"] = last_snap_new
                feed_state["finished"] = len(snaps) < page_size
                st.session_state["_inc_feed"] = feed_state
                st.experimental_rerun()
            except Exception as e:
                st.error("Load more failed: " + str(e))
    else:
        st.markdown("<div class='small'>No more posts.</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# ---------------- ACCOUNT (unchanged) ----------------
def page_account():
    if not st.session_state.user:
        st.info("Sign in first.")
        page_login()
        return
    st.markdown("<div class='card' style='max-width:720px;margin:auto'>", unsafe_allow_html=True)
    st.markdown("## 👤 Account")
    st.markdown(f"**Signed in as:** @{st.session_state.user['username']}  •  {st.session_state.user['email']}")
    st.markdown("### Home location (used for area notifications)")
    user_doc = get_user_doc(st.session_state.user["email"])
    current_home = None
    if user_doc and user_doc.get("home_lat") is not None:
        current_home = (user_doc.get("home_lat"), user_doc.get("home_lng"))
        st.markdown(f"Current home location: {current_home[0]:.5f}, {current_home[1]:.5f}")
    c1, c2 = st.columns([2,1])
    with c1:
        home_addr = st.text_input("Search a place to set as your home location", key="home_search")
        if st.button("Find home location"):
            if home_addr.strip():
                res = geocode_address(home_addr.strip())
                if res:
                    lat, lng, addr = res
                    st.session_state['home_lat_tmp'] = lat
                    st.session_state['home_lng_tmp'] = lng
                    st.success(f"Found: {addr}")
                else:
                    st.error("Not found.")
    with c2:
        if st.button("Save home location"):
            lat = st.session_state.get("home_lat_tmp"); lng = st.session_state.get("home_lng_tmp")
            if lat is None or lng is None:
                st.error("Set home by searching first")
            else:
                set_user_home_location(st.session_state.user["email"], lat, lng)
                st.success("Home location saved.")
    st.markdown("</div>", unsafe_allow_html=True)

# ---------------- ROUTER ----------------
def router():
    render_sidebar()
    page = st.session_state.page

    if page == "home":
        page_home()
    elif page == "login":
        page_login()
    elif page == "register":
        page_register()
    elif page == "dashboard":
        page_dashboard()
    elif page == "feed":
        page_feed()
    elif page == "account":
        page_account()
    else:
        page_home()

router()
