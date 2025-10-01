# app.py
import streamlit as st
import json, uuid
from datetime import datetime, timezone, timedelta
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from geopy.distance import geodesic
import folium

st.set_page_config(page_title="Report Disasters", layout="wide")
st.title("Report Disasters — Streamlit Reporter")

# ------------- Initialize Firebase Admin ----------------
if "fb_initialized" not in st.session_state:
    if not firebase_admin._apps:  # avoid double init
        cred = credentials.Certificate("serviceAccount.json")  # ✅ load from local file
        firebase_admin.initialize_app(cred)  # ✅ no storage bucket yet
        st.session_state["fb_initialized"] = True

# Firestore client
db = firestore.client()

# ---------------- Helpers ----------------
def verify_and_consume_session(code, max_age_minutes=30):
    doc_ref = db.collection("sessions").document(code)
    snap = doc_ref.get()
    if not snap.exists:
        return None, "Code not found"
    data = snap.to_dict()
    uid = data.get("uid")
    if not uid:
        return None, "Invalid session data"
    created = data.get("created")
    if isinstance(created, datetime):
        if datetime.now(timezone.utc) - created > timedelta(minutes=max_age_minutes):
            return None, "Session code expired"
    try:
        doc_ref.delete()
    except Exception:
        pass
    return uid, None

def create_incident(uid, inc_type, description, lat, lng, photo_file=None):
    doc = {
        "uid": uid,
        "type": inc_type,
        "description": description,
        "location": firestore.GeoPoint(lat, lng),
        "created": firestore.SERVER_TIMESTAMP
    }
    # if photo_file and bucket:
    #     data = photo_file.read()
    #     ext = photo_file.name.split('.')[-1]
    #     dest = f"images/{uuid.uuid4().hex}.{ext}"
    #     blob = bucket.blob(dest)
    #     blob.upload_from_string(data, content_type=photo_file.type)
    #     blob.make_public()
    #     doc["photo_url"] = blob.public_url
    # db.collection("incidents").add(doc)

def tokens_nearby(lat, lng, radius_km=10):
    tokens = []
    docs = db.collection("deviceTokens").stream()
    for d in docs:
        dd = d.to_dict()
        if not dd or "token" not in dd:
            continue
        loc = dd.get("lastLocation")
        if not loc:
            continue
        try:
            other = (loc.latitude, loc.longitude)
        except Exception:
            # fallback if stored differently
            other = (loc['latitude'], loc['longitude'])
        if geodesic((lat, lng), other).km <= radius_km:
            tokens.append(dd["token"])
    return tokens

def remove_device_token(token):
    docs = db.collection("deviceTokens").where("token", "==", token).stream()
    for d in docs:
        try:
            db.collection("deviceTokens").document(d.id).delete()
        except Exception:
            pass

def send_push_and_cleanup(tokens, title, body, data_payload=None):
    if not tokens:
        return {"sent":0, "failed":0}
    total_sent = 0
    total_failed = 0
    for i in range(0, len(tokens), 500):
        batch = tokens[i:i+500]
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            tokens=batch,
            data=data_payload or {}
        )
        resp = messaging.send_multicast(message)
        total_sent += resp.success_count
        total_failed += resp.failure_count
        # cleanup invalid tokens
        for idx, send_resp in enumerate(resp.responses):
            if not send_resp.success:
                token = batch[idx]
                # remove token(s) with clear invalid errors
                err = getattr(send_resp, "exception", None)
                if err:
                    err_str = str(err)
                    if "NotRegistered" in err_str or "registration-token-not-registered" in err_str or "InvalidRegistration" in err_str:
                        remove_device_token(token)
    return {"sent": total_sent, "failed": total_failed}

# ---------------- UI ----------------
st.markdown("### Step 1 — Link your device via the hosted auth & push page")
hosting_url = st.secrets.get("FIREBASE_HOSTING_URL")
if hosting_url:
    st.markdown(f"[Open hosted auth page →]({hosting_url})  (open on the device where you want push notifications)")
else:
    st.info("Set FIREBASE_HOSTING_URL in Streamlit Secrets to link to the hosted auth page.")

st.markdown("Paste the session code produced on the hosted page:")
col1, col2 = st.columns([2,1])
with col1:
    session_code = st.text_input("Session code")
with col2:
    if st.button("Link session"):
        if not session_code:
            st.error("Paste the 6-digit code from the hosted page.")
        else:
            uid, err = verify_and_consume_session(session_code.strip())
            if err:
                st.error(err)
            else:
                st.session_state["uid"] = uid
                st.success(f"Linked uid: {uid}")

if "uid" in st.session_state:
    st.info("Linked uid: " + st.session_state["uid"])
else:
    st.warning("No linked user. You can still submit reports (anonymous).")

st.markdown("### Step 2 — Submit a report")
with st.form("report_form"):
    inc_type = st.selectbox("Type", ["fire", "flood", "roadblock", "other"])
    description = st.text_area("Describe what you see")
    use_last = st.checkbox("Use last known device location (from hosted page)", value=True)
    if not use_last:
        lat = st.number_input("Latitude", format="%.6f")
        lng = st.number_input("Longitude", format="%.6f")
    photo = st.file_uploader("Optional photo", type=["jpg","jpeg","png"])
    submitted = st.form_submit_button("Submit report")
    if submitted:
        if use_last:
            if "uid" not in st.session_state:
                st.error("You must link a session (paste session code).")
            else:
                doc = db.collection("deviceTokens").document(st.session_state["uid"]).get()
                if not doc.exists:
                    st.error("No device token saved for this uid. Enable push on hosted page first.")
                else:
                    dd = doc.to_dict()
                    loc = dd.get("lastLocation")
                    if not loc:
                        st.error("No location saved on your device doc. Use manual entry.")
                    else:
                        try:
                            lat = loc.latitude; lng = loc.longitude
                        except Exception:
                            lat = loc['latitude']; lng = loc['longitude']
                        create_incident(st.session_state.get("uid","anonymous"), inc_type, description, lat, lng, photo)
                        st.success("Report saved.")
                        tokens = tokens_nearby(lat, lng, radius_km=10)
                        res = send_push_and_cleanup(tokens, f"New {inc_type} near you", (description or "")[:120], data_payload={"type":"incident"})
                        st.info(f"Pushed to {res['sent']} devices, {res['failed']} failed.")
        else:
            if lat is None or lng is None:
                st.error("Provide coordinates.")
            else:
                create_incident(st.session_state.get("uid","anonymous"), inc_type, description, lat, lng, photo)
                st.success("Report saved.")
                tokens = tokens_nearby(lat, lng, radius_km=10)
                res = send_push_and_cleanup(tokens, f"New {inc_type} near you", (description or "")[:120], data_payload={"type":"incident"})
                st.info(f"Pushed to {res['sent']} devices, {res['failed']} failed.")

st.markdown("### Shared map (latest incidents)")
from streamlit_autorefresh import st_autorefresh
_ = st_autorefresh(interval=7000, limit=None, key="map_refresh")

inc_docs = db.collection("incidents").order_by("created", direction=firestore.Query.DESCENDING).limit(200).stream()
incidents = []
for d in inc_docs:
    dd = d.to_dict()
    loc = dd.get("location")
    if not loc:
        continue
    incidents.append({
        "type": dd.get("type"),
        "desc": dd.get("description"),
        "lat": loc.latitude,
        "lng": loc.longitude,
        "photo": dd.get("photo_url")
    })

if incidents:
    avg_lat = sum(i['lat'] for i in incidents)/len(incidents)
    avg_lng = sum(i['lng'] for i in incidents)/len(incidents)
else:
    avg_lat, avg_lng = 24.86, 67.01  # sensible default

m = folium.Map(location=[avg_lat, avg_lng], zoom_start=12)
for inc in incidents:
    popup = f"<b>{inc['type']}</b><br/>{inc['desc'][:200]}"
    if inc.get('photo'):
        popup += f"<br/><img src='{inc['photo']}' width='200'/>"
    folium.Marker([inc['lat'], inc['lng']], popup=popup).add_to(m)

st.components.v1.html(m._repr_html_(), height=600, scrolling=True)
