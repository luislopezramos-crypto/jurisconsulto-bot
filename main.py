import os
import json
from fastapi import FastAPI, Request, Response
import requests
from google.cloud import discoveryengine_v1 as discoveryengine
from google.oauth2 import service_account

# Inicializar aplicación FastAPI (ASGI app)
app = FastAPI(title="Jurisconsulto Bot")

# Configurar credenciales de Google Cloud desde la variable de entorno de Render
gcp_creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
credentials = None

if gcp_creds_json:
    try:
        # Reemplazar saltos de línea literales escapados para reparar la clave RSA
        gcp_creds_clean = gcp_creds_json.replace("\\n", "\n")
        creds_dict = json.loads(gcp_creds_clean, strict=False)
        credentials = service_account.Credentials.from_service_account_info(creds_dict)
    except Exception as e:
        print(f"Error al cargar credenciales de GCP: {e}")

# Variables de entorno para Vertex AI y WhatsApp
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "1788990201092")
DATA_STORE_ID = os.environ.get("DATA_STORE_ID", "bibliotecaconstitucionalyparlamentaria_1788990201092")
LOCATION = "global"

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")


def consultar_vertex_search(query_text: str) -> str:
    """Consulta el Data Store de Vertex AI Search y recupera los fragmentos legales."""
    try:
        client = discoveryengine.SearchServiceClient(credentials=credentials) if credentials else discoveryengine.SearchServiceClient()
        
        serving_config = client.serving_config_path(
            project=PROJECT_ID,
            location=LOCATION,
            data_store=DATA_STORE_ID,
            serving_config="default_config",
        )
        
        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=query_text,
            page_size=5,
        )
        
        response = client.search(request)
        
        pasajes = []
        for result in response.results:
            derived_data = result.document.derived_struct_data
            
            # Intentar extraer extractive_segments
            extrapolations = derived_data.get("extractive_segments", [])
            for seg in extrapolations:
                content = seg.get("content")
                if content:
                    pasajes.append(content)
            
            # Si no hay extractive_segments, intentar extraer snippets
            if not pasajes:
                snippets = derived_data.get("snippets", [])
                for snip in snippets:
                    snippet_text = snip.get("snippet")
                    if snippet_text:
                        pasajes.append(snippet_text)

        if not pasajes:
            return "No se encontraron fragmentos específicos en la base legal para esta consulta."
            
        return "\n\n--- FRAGMENTO LEGAL ---\n\n".join(pasajes)

    except Exception as e:
        print(f"Error al consultar Vertex Search: {e}")
        return f"Error al consultar la base legal: {str(e)}"


def enviar_mensaje_whatsapp(telefono: str, texto: str):
    """Envía la respuesta al usuario a través de la API de WhatsApp Business."""
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print("Faltan variables de WhatsApp Token o Phone Number ID")
        return
        
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "text",
        "text": {"body": texto}
    }
    requests.post(url, json=payload, headers=headers)


@app.get("/")
def home():
    return {"status": "ok", "service": "Jurisconsulto Bot API"}


@app.get("/webhook")
def verificar_webhook(request: Request):
    """Verificación de Webhook para WhatsApp Business API."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, status_code=200)
    return Response(content="Error de validación", status_code=403)


app.post("/webhook")
