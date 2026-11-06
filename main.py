import os
import json
from fastapi import FastAPI, Request, Response
import requests
from google.cloud import discoveryengine_v1 as discoveryengine
from google.oauth2 import service_account
import anthropic

app = FastAPI(title="Jurisconsulto Bot")

# Variables de entorno
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "1788990201092")
DATA_STORE_ID = os.environ.get("DATA_STORE_ID", "bibliotecaconstitucionalyparlamentaria_1788990201092")
LOCATION = "global"

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# Cargar credenciales de GCP desde el Secret File de Render
credentials = None
SECRET_FILE_PATH = "/etc/secrets/gcp-key.json"

if os.path.exists(SECRET_FILE_PATH):
    try:
        credentials = service_account.Credentials.from_service_account_file(SECRET_FILE_PATH)
    except Exception as e:
        print(f"Error al cargar archivo secreto GCP: {e}")
else:
    gcp_creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if gcp_creds_json:
        try:
            creds_clean = gcp_creds_json.replace("\\n", "\n")
            creds_dict = json.loads(creds_clean, strict=False)
            credentials = service_account.Credentials.from_service_account_info(creds_dict)
        except Exception as e:
            print(f"Error al cargar credenciales JSON: {e}")


def buscar_contexto_vertex(query_text: str) -> str:
    """Busca en el Data Store de Vertex AI Search los fragmentos normativos relevantes."""
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
            page_size=8,
        )
        
        response = client.search(request)
        
        fragmentos = []
        for result in response.results:
            derived_data = result.document.derived_struct_data
            extrapolations = derived_data.get("extractive_segments", [])
            for seg in extrapolations:
                if seg.get("content"):
                    fragmentos.append(seg.get("content"))
            
            if not extrapolations:
                snippets = derived_data.get("snippets", [])
                for snip in snippets:
                    if snip.get("snippet"):
                        fragmentos.append(snip.get("snippet"))

        return "\n\n".join(fragmentos)

    except Exception as e:
        print(f"Error recuperando datos de Vertex AI Search: {e}")
        return ""


def generar_dictamen_con_claude(pregunta_usuario: str, contexto_documental: str) -> str:
    """
    Sintetiza la respuesta jurídica utilizando Claude (Anthropic).
    """
    if not ANTHROPIC_API_KEY:
        return "Error: No se encuentra configurada la variable ANTHROPIC_API_KEY en Render."

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    system_prompt = (
        "Eres 'Jurisconsulto', un consultor experto en Derecho Constitucional y Parlamentario guatemalteco. "
        "Tu función es responder a las consultas con solidez técnico-jurídica, fundamentación clara "
        "y lenguaje preciso, basándote en la evidencia documental provista. "
        "Si los fragmentos del contexto contienen la norma o doctrina aplicable, sintetízala y estructúrala "
        "para responder de forma exhaustiva la consulta del usuario."
    )

    prompt = (
        f"DOCUMENTACIÓN Y FUENTES LEGALES DE REFERENCIA:\n{contexto_documental if contexto_documental else 'No se encontraron fragmentos directos.'}\n\n"
        f"PREGUNTA DEL USUARIO:\n{pregunta_usuario}\n\n"
        f"Genera una respuesta analítica y fundament
