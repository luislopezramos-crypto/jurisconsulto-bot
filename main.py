import os
import json
import io
from fastapi import FastAPI, Request, Response
import requests
from google.cloud import discoveryengine_v1 as discoveryengine
from google.oauth2 import service_account
import anthropic

# Librerías para generación de PDF y Marca de Agua
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

app = FastAPI(title="Jurisconsulto Bot")

# Variables de entorno
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "1788990201092")
DATA_STORE_ID = os.environ.get("DATA_STORE_ID", "bibliotecaconstitucionalyparlamentaria_1788990201092")
LOCATION = "global"

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# Cargar credenciales de GCP desde Secret File
credentials = None
SECRET_FILE_PATH = "/etc/secrets/gcp-key.json"

if os.path.exists(SECRET_FILE_PATH):
    try:
        credentials = service_account.Credentials.from_service_account_file(SECRET_FILE_PATH)
    except Exception as e:
        print(f"Error al cargar archivo secreto GCP: {e}")


class NumberedCanvas(canvas.Canvas):
    """Canvas personalizado para dibujar la marca de agua 'Respuesta dada por Jurisconsulto'"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_watermark()
            super().showPage()
        super().save()

    def draw_watermark(self):
        self.saveState()
        self.setFont("Helvetica-Bold", 32)
        self.setFillColorRGB(0.85, 0.85, 0.85, alpha=0.4)  # Color gris tenue transparente
        self.translate(300, 400)
        self.rotate(45)
        self.drawCentredString(0, 0, "Respuesta dada por Jurisconsulto")
        self.restoreState()


def crear_pdf_dictamen(texto_dictamen: str) -> bytes:
    """Genera un archivo PDF en memoria con el texto del dictamen y la marca de agua."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=50, bottomMargin=50)
    
    styles = getSampleStyleSheet()
    style_body = ParagraphStyle('BodyTextCustom', parent=styles['Normal'], fontSize=11, leading=15, spaceAfter=10)
    
    story = []
    lineas = texto_dictamen.split("\n")
    for linea in lineas:
        if linea.strip():
            story.append(Paragraph(linea, style_body))
        else:
            story.append(Spacer(1, 8))
            
    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer.getvalue()


def buscar_contexto_vertex(query_text: str) -> str:
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
            page_size=10,
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
    if not ANTHROPIC_API_KEY:
        return "Error: No se encuentra configurada la variable ANTHROPIC_API_KEY en Render."

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    # Prompt de sistema incorporando las reglas de enfoque y sugerencia temática
    system_prompt = (
        "Eres 'Jurisconsulto', un consultor experto en Derecho Constitucional y Parlamentario guatemalteco. "
        "REGLAS ESTRUCTURALES DE RESPUESTA:\n"
        "1. Si el usuario realiza varias preguntas en un solo mensaje, CONCÉNTRATE EXCLUSIVAMENTE en responder a profundidad la PREGUNTA ESENCIAL o motivo principal de la consulta.\n"
        "2. Para las preguntas secundarias u opcionales, indícale al usuario que puede decidir explorarlas en sus siguientes consultas.\n"
        "3. Al finalizar tu respuesta, sugiere 2 o 3 temáticas o aspectos legales relacionados que el usuario podría consultar a continuación.\n"
        "4. Mantén la síntesis fluida, analítica y técnicamente impecable basada en el contexto normativo provisto, sin advertencias ni justificaciones."
    )

    prompt = (
        f"BASE DOCUMENTAL DE REFERENCIA:\n{contexto_documental}\n\n"
        f"CONSULTA DEL USUARIO:\n{pregunta_usuario}\n\n"
        f"Dictamen jurídico:"
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=system_prompt,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        return response.content[0].text
    except Exception as e:
        print(f"Error al generar dictamen con Anthropic: {e}")
        return f"Error al procesar la respuesta jurídica: {str(e)}"


def enviar_mensaje_whatsapp(telefono: str, texto: str):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        return
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "text",
        "text": {"body": texto}
    }
    requests.post(url, json=payload, headers=headers)


def enviar_documento_whatsapp(telefono: str, pdf_bytes: bytes, filename: str, caption: str):
    """Sube el PDF a la API de WhatsApp y lo envía como documento adjunto."""
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        return
        
    # 1. Subir archivo a la API de Meta
    media_url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
    headers_media = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    files = {
        'file': (filename, pdf_bytes, 'application/pdf'),
        'messaging_product': (None, 'whatsapp')
    }
    res = requests.post(media_url, headers=headers_media, files=files)
    
    if res.status_code == 200:
        media_id = res.json().get("id")
        
        # 2. Enviar el mensaje con el documento adjunto
        messages_url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
        headers_msg = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "document",
            "document": {
                "id": media_id,
                "filename": filename,
                "caption": caption
            }
        }
        requests.post(messages_url, json=payload, headers=headers_msg)


@app.get("/")
def home():
    return {"status": "ok", "service": "Jurisconsulto Bot API"}


@app.get("/webhook")
def verificar_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, status_code=200)
    return Response(content="Error de validación", status_code=403)


@app.post("/webhook")
async def recibir_mensaje(request: Request):
    try:
        data = await request.json()
        entry = data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if messages:
            msg = messages[0]
            remitente = msg.get("from")
            texto_usuario = msg.get("text", {}).get("body", "")
            
            if texto_usuario:
                contexto_legal = buscar_contexto_vertex(texto_usuario)
                dictamen = generar_dictamen_con_claude(texto_usuario, contexto_legal)
                
                # Evaluar extensión del mensaje (Límite de WhatsApp: ~1500 caracteres para texto cómodo)
                if len(dictamen) > 1500:
                    pdf_bytes = crear_pdf_dictamen(dictamen)
                    caption_msg = "Estimado usuario, el dictamen legal excede la extensión de texto estándar. Se adjunta el informe completo en formato PDF con las marcas oficiales."
                    enviar_documento_whatsapp(remitente, pdf_bytes, "Dictamen_Jurisconsulto.pdf", caption_msg)
                else:
                    enviar_mensaje_whatsapp(remitente, dictamen)
                
    except Exception as e:
        print(f"Error procesando webhook: {e}")
        
    return {"status": "success"}
