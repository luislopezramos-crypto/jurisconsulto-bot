import os
import json
import urllib.request
from fastapi import FastAPI, Request, Response

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "Jurisconsulto2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Jurisconsulto Bot is running"}

@app.get("/webhook")
def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Verification failed", status_code=403)

@app.post("/webhook")
async def handle_webhook(request: Request):
    data = await request.json()
    
    try:
        entry = data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if messages:
            msg = messages[0]
            from_number = msg.get("from")
            text_body = msg.get("text", {}).get("body", "")

            reply_text = f"Hola, recibí tu consulta: '{text_body}'. Estamos procesando la respuesta jurídica."
            send_whatsapp_message(from_number, reply_text)

    except Exception as e:
        print("Error procesando el mensaje:", e)

    return {"status": "success"}

def send_whatsapp_message(to_number: str, message_text: str):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print("Falta WHATSAPP_TOKEN o PHONE_NUMBER_ID en las variables de entorno.")
        return

    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message_text}
    }
    
    try:
        req = urllib.request.Request(
            url, 
            data=json.dumps(payload).encode('utf-8'), 
            headers=headers, 
            method='POST'
        )
        with urllib.request.urlopen(req) as response:
            print("Mensaje enviado con éxito:", response.read().decode('utf-8'))
    except Exception as e:
        print("Error al enviar mensaje por WhatsApp:", e)
