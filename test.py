import json, toml

with open("serviceAccount.json") as f:
    data = json.load(f)

with open(".streamlit/secrets.toml", "w") as f:
    toml.dump({"FIREBASE_SERVICE_ACCOUNT": data}, f)
