import os
import json
import requests
from fastapi import FastAPI, Request, Response, BackgroundTasks
from anthropic import Anthropic
from google.oauth2 import service_account
from google.cloud import discoveryengine_v1 as discoveryengine

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

app = FastAPI()

# Variables de Entorno de Render
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mi_token_de_verificacion")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "lucid-burner-468800-q7")
DATA_STORE_ID = os.getenv("DATA_STORE_ID", "bibliotecaconstitucionalyparlamentaria_1788990201092")
GCP_CREDS_JSON = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")

anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
MAX_WHATSAPP_CHARS = 1400

def obtener_cliente_busqueda():
    if GCP_CREDS_JSON:
        creds_dict = json.loads(GCP_CREDS_JSON)
        credentials = service_account.Credentials.from_service_account_info(creds_dict)
        return discoveryengine.SearchServiceClient(credentials=credentials)
    return discoveryengine.SearchServiceClient()

class WatermarkCanvas(canvas.Canvas):
    def showPage(self):
        self.saveState()
        self.setFont("Helvetica-Bold", 24)
        self.setFillColorRGB(0.5, 0.5, 0.5, alpha=0.55)
        self.translate(300, 400)
        self.rotate(45)
        self.drawCentredString(0, 0, "Consulta respondida por Jurisconsultor")
        self.restoreState()
        super().showPage()

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), status_code=200)
    return Response(content="Error de verificación", status_code=403)

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
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
                background_tasks.add_task(procesar_y_enviar, from_number, text_body)
    except Exception as e:
        print(f"Error procesando webhook: {e}")
        
    return Response(content="OK", status_code=200)

def consultar_base_legal_gcp(query: str) -> str:
    """Busca fragmentos relevantes en el Data Store de Google Cloud (Vertex AI Search)"""
    try:
        client = obtener_cliente_busqueda()
        serving_config = client.serving_config_path(
            project=GCP_PROJECT_ID,
            location="global",
            data_store=DATA_STORE_ID,
            serving_config="default_search",
        )
        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=query,
            page_size=5,
        )
        response = client.search(request)
        
        contexto_recuperado = []
        for result in response.results:
            data = result.document.derived_struct_data
            snippets = data.get("snippets", [])
            for s in snippets:
                snippet_text = s.get("snippet", "")
                if snippet_text:
                    contexto_recuperado.append(snippet_text)
                    
        return "\n\n".join(contexto_recuperado)
    except Exception as e:
        print(f"Error consultando Data Store GCP: {e}")
        return ""

def procesar_y_enviar(to_number: str, consulta_usuario: str):
    # 1. Recuperar contexto legal dinámico de Google Drive / GCP
    contexto_legal = consultar_base_legal_gcp(consulta_usuario)
    
    # 2. Generar respuesta fundamentada con Claude (Temperatura 0.0)
    respuesta_texto = generar_respuesta_fundamentada(consulta_usuario, contexto_legal)
    
    # 3. Enviar por WhatsApp
    if len(respuesta_texto) <= MAX_WHATSAPP_CHARS:
        enviar_whatsapp_texto(to_number, respuesta_texto)
    else:
        pdf_path = generar_pdf_respuesta(respuesta_texto, consulta_usuario)
        media_id = subir_documento_whatsapp(pdf_path)
        if media_id:
            enviar_whatsapp_documento(to_number, media_id, "Consulta_Juridica_Jurisconsultor.pdf")
            enviar_whatsapp_texto(
                to_number, 
                "📄 Adjunto encontrarás el informe jurídico completo en formato PDF con el análisis detallado.\n\n"
                "💡 *Para consultar los puntos restantes, escribe el nombre o frase del tema que deseas resolver a continuación.*"
            )
        else:
            enviar_whatsapp_texto(to_number, respuesta_texto[:4000])

def generar_respuesta_fundamentada(consulta_usuario: str, contexto_legal: str) -> str:
    try:
        if not contexto_legal.strip():
            return (
                "La base de datos legal no devolvió fragmentos específicos para esta consulta. "
                "Por favor verifica que la normativa esté correctamente subida e indexada en la carpeta de Google Drive."
            )

        prompt_sistema = (
            "Eres el asistente de Jurisconsultor, experto en derecho constitucional y parlamentario de Guatemala.\n\n"
            "REGLAS STRICTAS DE RESPUESTA (RIGOR JURÍDICO Y TEMPERATURA 0):\n"
            "1. Responde ÚNICAMENTE fundamentándote en el texto extraído de la base legal oficial provista a continuación.\n"
            "2. Queda ESTRICTAMENTE PROHIBIDO asumir, inventar o confundir las objeciones de los diputados (Art. 125 LOOL) con el veto presidencial (Art. 178 CPRG) salvo que el texto adjunto lo establezca explícitamente.\n"
            "3. NO utilices tablas escritas en formato Markdown (con caracteres '|'). Utiliza únicamente viñetas y listas estructuradas.\n"
            "4. Si la consulta abarca varios puntos, responde de inmediato el Primer Punto con el máximo rigor técnico. Al final, enumera los temas pendientes e indica la frase exacta que debe escribir el usuario para resolverlos.\n\n"
            f"--- FUENTE NORMATIVA Y JURISPRUDENCIAL RECUPERADA (DRIVE) ---\n{contexto_legal}\n--------------------------------------------------------------"
        )

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1800,
            temperature=0.0,  # <-- TEMPERATURA 0.0: Cero creatividad, respuestas deterministas y exactas
            system=prompt_sistema,
            messages=[{"role": "user", "content": consulta_usuario}]
        )
        return response.content[0].text
    except Exception as e:
        print(f"Error generando respuesta: {e}")
        return "Disculpa, ocurrió un error al procesar tu consulta jurídica."

def generar_pdf_respuesta(texto_respuesta: str, consulta: str) -> str:
    pdf_filename = "/tmp/respuesta_jurisconsultor.pdf"
    doc = SimpleDocTemplate(pdf_filename, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=50, bottomMargin=50)
    
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('Titulo', parent=styles['Heading1'], fontSize=14, leading=18, spaceAfter=12)
    body_style = ParagraphStyle('Body', parent=styles['Normal'], fontSize=10, leading=14, spaceAfter=8)
    
    story = [
        Paragraph("<b>JURISCONSULTOR - CONSULTA JURÍDICA</b>", titulo_style),
        Spacer(1, 10),
        Paragraph(f"<b>Consulta:</b> {consulta}", body_style),
        Spacer(1, 15),
        Paragraph("<b>Respuesta Fundamentada:</b>", body_style),
        Spacer(1, 10)
    ]
    
    for linea in texto_respuesta.split('\n'):
        if linea.strip():
            linea_limpia = linea.replace('#', '').strip()
            story.append(Paragraph(linea_limpia, body_style))
            story.append(Spacer(1, 4))
            
    doc.build(story, canvasmaker=WatermarkCanvas)
    return pdf_filename

def subir_documento_whatsapp(filepath: str) -> str:
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    files = {
        'file': ('Consulta_Juridica_Jurisconsultor.pdf', open(filepath, 'rb'), 'application/pdf'),
        'messaging_product': (None, 'whatsapp')
    }
    r = requests.post(url, headers=headers, files=files)
    if r.status_code == 200:
        return r.json().get("id")
    return None

def enviar_whatsapp_texto(to_number: str, message_text: str):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "text",
        "text": {"body": message_text}
    }
    requests.post(url, json=payload, headers=headers)

def enviar_whatsapp_documento(to_number: str, media_id: str, filename: str):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "document",
        "document": {
            "id": media_id,
            "filename": filename,
            "caption": "Respuesta jurídica fundamentada - Jurisconsultor"
        }
    }
    requests.post(url, json=payload, headers=headers)
