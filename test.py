import json

with open("serviceAccount.json", "r") as f:
    data = json.load(f)

# Dumps to single line string with \n properly escaped
escaped = json.dumps(data)

print('FIREBASE_SERVICE_ACCOUNT = """' + escaped + '"""')
