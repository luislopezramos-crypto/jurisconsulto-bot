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

# Inicialización del cliente oficial de Anthropic
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

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
                ai_response = generar_respuesta_fundamentada(text_body)
                enviar_whatsapp(from_number, ai_response)
    except Exception as e:
        print(f"Error procesando webhook: {e}")
        
    return Response(content="OK", status_code=200)

def generar_respuesta_fundamentada(consulta_usuario: str) -> str:
    try:
        prompt_sistema = (
            "Eres un consultor experto en derecho constitucional y parlamentario guatemalteco.\n\n"
            "INSTRUCCIONES DE RESPUESTA:\n"
            "1. Responde a las dudas del usuario con claridad, precisión y de forma directa (formato natural para WhatsApp).\n"
            "2. Fundamenta SIEMPRE citando explícitamente los artículos de la Constitución Política de la República de Guatemala (CPRG), "
            "la Ley Orgánica del Organismo Legislativo (LOOL - Decreto 63-94) y los criterios/expedientes de la Corte de Constitucionalidad (CC).\n"
            "3. Cuando te pregunten sobre la creación, reforma o supresión de entidades descentralizadas o autónomas, fundamenta explícitamente "
            "con el artículo 134 de la CPRG (requisitos, voto favorable de las dos terceras partes del total de diputados) y los artículos de la LOOL aplicables."
        )

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=prompt_sistema,
            messages=[{"role": "user", "content": consulta_usuario}]
        )
        
        return response.content[0].text

    except Exception as e:
        print(f"Error al generar la respuesta: {e}")
        return "Disculpa, ocurrió un inconveniente al procesar tu consulta. Por favor intenta de nuevo."

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
