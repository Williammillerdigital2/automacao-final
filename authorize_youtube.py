import pickle
from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_SECRET_FILE = 'client_secret.json'
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
credentials = flow.run_local_server(port=0)

with open('token.pickle', 'wb') as token:
    pickle.dump(credentials, token)

print("\nArquivo 'token.pickle' gerado com sucesso!")