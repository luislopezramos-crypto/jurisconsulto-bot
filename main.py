import os
import requests
from fastapi import FastAPI, Request, Response
from anthropic import Anthropic
from google.genai import client

app = FastAPI()

# Variables de entorno
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mi_token_de_verificacion")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Inicialización de cliente Anthropic
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ID de tu cuaderno de trabajo sobre temas parlamentarios
NOTEBOOK_ID = "notebooks/a8e97b09-299f-443e-bc41-56abf9779730"

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
        # 1. Recuperar los textos normativos y doctrinales del cuaderno de NotebookLM
        chunks_response = client.notebooklm.retrieve_relevant_chunks(
            name=NOTEBOOK_ID,
            query=consulta_usuario
        )
        
        textos_recuperados = ""
        if hasattr(chunks_response, 'source_contexts'):
            for source in chunks_response.source_contexts:
                for chunk in source.chunks:
                    textos_recuperados += chunk.content.pieces[0].text + "\n\n"

        if not textos_recuperados.strip():
            return "Disculpa, no encontré información sobre este tema en las fuentes del cuaderno."

        # 2. Prompt con el estilo directo y profesional de un consultor
        prompt_sistema = (
            "Eres un consultor experto en derecho constitucional y parlamentario guatemalteco. "
            "Responde a la consulta del usuario de forma clara, directa, profesional y conversacional (sin formato de dictamen formal).\n\n"
            "REGLAS OBLIGATORIAS:\n"
            "1. Basa tu respuesta ESTRICTAMENTE en las leyes, artículos y jurisprudencia de la CC provistos en el texto de referencia adjunto.\n"
            "2. Fundamenta siempre citando explícitamente los artículos, leyes o expedientes de la CC mencionados en el texto.\n"
            "3. Mantén un tono de respuesta directo y natural para un chat de WhatsApp.\n\n"
            f"TEXTO DE REFERENCIA DEL CUADERNO:\n{textos_recuperados}"
        )

        # 3. Generar la respuesta utilizando el modelo activo
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=prompt_sistema,
            messages=[{"role": "user", "content": consulta_usuario}]
        )
        
        return response.content[0].text

    except Exception as e:
        print(f"Error al generar la respuesta: {e}")
        return "Ocurrió un error al procesar la consulta con el cuaderno. Por favor intenta de nuevo."

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
