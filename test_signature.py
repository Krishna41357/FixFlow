# test_signature.py — run locally
import hmac, hashlib

secret = "your-webhook-secret-from-env"
payload = b'{"action":"opened","pull_request":{"number":1}}'

sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
print("Send this as X-Hub-Signature-256:", sig)