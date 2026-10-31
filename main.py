import os
import requests
from fastapi import FastAPI, Request, Response
from anthropic import Anthropic

app = FastAPI()

# Variables de entorno
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mi_token_de_verificacion")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Inicialización configurada para claves sk-ant-usr-...
anthropic_client = Anthropic(
    api_key=ANTHROPIC_API_KEY,
    default_headers={
        "anthropic-version": "2023-06-01"
    }
)

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), status_code=200)
    return Response(content="Error de verificación", status_code=403)

@app.post("/webhook")
async def webhook(request: Request):
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
            
            if text_body:
                # 1. Generar respuesta con Claude
                ai_response = generar_respuesta_claude(text_body)
                
                # 2. Enviar respuesta a WhatsApp
                enviar_whatsapp(from_number, ai_response)
                
    except Exception as e:
        print(f"Error procesando webhook: {e}")
        
    return Response(content="OK", status_code=200)

def generar_respuesta_claude(consulta_usuario: str) -> str:
    try:
        prompt_sistema = (
            "Eres un consultor jurídico experto en derecho constitucional y parlamentario guatemalteco. "
            "Responde a las consultas de forma clara, profesional, precisa y bien fundamentada."
        )
        response = anthropic_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=800,
            system=prompt_sistema,
            messages=[
                {"role": "user", "content": consulta_usuario}
            ]
        )
        return response.content[0].text
    except Exception as e:
        print(f"Error detallado de Anthropic: {type(e).__name__} - {str(e)}")
        return "Disculpa, ocurrió un error al procesar tu consulta jurídica. Por favor intenta de nuevo."

def enviar_whatsapp(to_number: str, message_text: str):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "text",
        "text": {"body": message_text}
    }
    requests.post(url, json=payload, headers=headers)
