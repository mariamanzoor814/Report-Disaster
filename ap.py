import streamlit as st
import firebase_admin
from firebase_admin import credentials, auth, firestore

# -------------------- Firebase Init --------------------
if not firebase_admin._apps:
    cred = credentials.Certificate("serviceAccount.json")
    firebase_admin.initialize_app(cred)
db = firestore.client()

# -------------------- Page --------------------
def render_login_page():
    st.markdown("<h2 style='color:#5A67D8;text-align:center;'>Login</h2>", unsafe_allow_html=True)

    # CSS to align inputs and button
    st.markdown(
        """
        <style>
        div.stTextInput > div > input, div.stButton > button {
            width: 300px;
            margin: 0 auto;
            display: block;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    with st.form("login_form"):
        email = st.text_input("Email", placeholder="Enter your email")
        password = st.text_input("Password", type="password", placeholder="Enter your password")
        submitted = st.form_submit_button("Login")

    if submitted:
        try:
            # Authenticate user (simplified for demo)
            user = auth.get_user_by_email(email)
            st.success(f"Welcome back, {user.email}!")

            # ---- Inject JavaScript for location + notifications ----
            st.markdown(
                """
                <script>
                async function requestPermissions() {
                    // Request geolocation
                    if (navigator.geolocation) {
                        navigator.geolocation.getCurrentPosition(
                            (pos) => {
                                const coords = pos.coords.latitude + "," + pos.coords.longitude;
                                window.parent.postMessage({isStreamlitMessage:true, type:"location", data:coords}, "*");
                            },
                            (err) => { alert("Location denied: " + err.message); }
                        );
                    }

                    // Request notification permission
                    if ("Notification" in window) {
                        let perm = await Notification.requestPermission();
                        if (perm === "granted") {
                            new Notification("✅ Notifications enabled!");
                        }
                    }
                }
                requestPermissions();
                </script>
                """,
                unsafe_allow_html=True
            )

        except Exception as e:
            st.error(f"Login failed: {e}")
