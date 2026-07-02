"""
Bu script'i bir kez çalıştırın: token.json oluşturulacak.
Google Cloud Console'da hesabınızı test kullanıcısı olarak ekledikten sonra
açılan browser penceresinde giriş yapın.
"""
from google_auth_oauthlib.flow import InstalledAppFlow
import json, os

SCOPES = ['https://www.googleapis.com/auth/drive']
flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
creds = flow.run_local_server(port=8765)
with open('token.json', 'w') as f:
    f.write(creds.to_json())
print('token.json oluşturuldu:', list(json.load(open('token.json')).keys()))
