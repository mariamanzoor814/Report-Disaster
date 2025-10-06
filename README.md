# 🌍 Report Disasters

**Author:** Maria Manzoor  
**Framework:** Streamlit + Firebase  
**Deployment:** Streamlit Cloud & Firebase Cloud

---

## 🔎 Overview
**Report Disasters** is a single-page Streamlit web app that enables people to instantly report disasters such as floods, fires, and roadblocks.  
All reports appear in real time on a shared interactive map using Firebase Firestore as the live database.

---

## ⚙️ Core Features
- **User Accounts:** Email/password sign-in with credentials stored in Firestore (bcrypt hashed).  
- **Submit Reports:** Type, location (auto-detect or map click), description, optional photo upload.  
- **Instant Updates:** Real-time sync through Firebase Firestore.  
- **Map View & Filters:** Interactive pins, type filters (fire, flood, roadblock).  
- **Notifications:** Firebase Cloud Messaging for browser push alerts.  
- **Simple Feed:** Recent reports with delete option for the author.

---

## 🛠 Tech Stack
| Layer | Tool | Purpose |
|-------|------|----------|
| Frontend + Backend | **Streamlit** | One Python app (no Flask needed) |
| Database | **Firebase Firestore** | Real-time storage |
| Authentication | **Custom (Firestore + bcrypt)** | Email/password login |
| Storage | **Firebase Storage** | User-uploaded photos |
| Notifications | **Firebase Cloud Messaging** | Browser push alerts |
| Maps | **folium / streamlit-folium** | Interactive map rendering |
| Hosting | **Streamlit Cloud** | Free HTTPS deployment |

---

## 🧩 Project Structure
app.py # Main Streamlit app
firebase-messaging-sw.js # Push notification service worker
requirements.txt # Python dependencies
README.md # Project documentation

## Create a virtual environment
python -m venv .venv
.venv\Scripts\activate   # on Windows
source .venv/bin/activate  # on macOS/Linux

## Install dependencies
pip install -r requirements.txt

## Set up Firebase
Create a Firebase project → enable Firestore, Storage, and Cloud Messaging.

Download the service account JSON file and name it serviceAccount.json.

Do NOT commit this file to GitHub. (Add it to .gitignore)

## Run the app
streamlit run app.py

