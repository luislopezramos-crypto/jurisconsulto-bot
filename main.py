import os
import requests
from fastapi import FastAPI, Request, Response, BackgroundTasks
from anthropic import Anthropic
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

app = FastAPI()

# Variables de entorno
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mi_token_de_verificacion")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
NOTEBOOK_ID = "notebooks/a8e97b09-299f-443e-bc41-56abf9779730"

# Límite cómodo de caracteres para un mensaje directo de WhatsApp
MAX_WHATSAPP_CHARS = 1500

class WatermarkCanvas(canvas.Canvas):
    """Clase para dibujar la marca de agua en cada página del PDF"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def showPage(self):
        self.saveState()
        self.setFont("Helvetica-Bold", 24)
        self.setFillColorRGB(0.8, 0.8, 0.8, alpha=0.3)  # Gris claro translúcido
        self.rotate(45)
        self.drawString(200, 100, "Consulta respondida por Jurisconsultor")
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

def procesar_y_enviar(to_number: str, consulta_usuario: str):
    # Obtener respuesta del cuaderno
    respuesta_texto = consultar_cuaderno_notebooklm(consulta_usuario)
    
    # Evaluar extensión de la respuesta
    if len(respuesta_texto) <= MAX_WHATSAPP_CHARS:
        # Enviar directamente por mensaje de texto
        enviar_whatsapp_texto(to_number, respuesta_texto)
    else:
        # Generar PDF con marca de agua y enviarlo como documento
        pdf_path = generar_pdf_respuesta(respuesta_texto, consulta_usuario)
        media_id = subir_documento_whatsapp(pdf_path)
        if media_id:
            enviar_whatsapp_documento(to_number, media_id, "Consulta_Juridica_Jurisconsultor.pdf")
            enviar_whatsapp_texto(to_number, "Adjunto encontrarás el informe completo en formato PDF con la respuesta detallada a tu consulta.")
        else:
            # Fallback en texto si falla la subida de media
            enviar_whatsapp_texto(to_number, respuesta_texto[:4000])

def consultar_cuaderno_notebooklm(consulta_usuario: str) -> str:
    """
    Función para procesar la consulta contra el material del cuaderno.
    """
    try:
        # Prompt enfocado en el rigor del Art. 134 CPRG y sentencias de la CC
        prompt_sistema = (
            "Eres un consultor experto en derecho constitucional y parlamentario guatemalteco.\n"
            "Responde a la consulta del usuario utilizando rigurosamente los criterios, leyes y jurisprudencia "
            "del cuaderno de trabajo (Art. 134 CPRG, mayoría calificada de 2/3 partes, sentencias de la CC).\n"
            "No incluyas explicaciones básicas del trámite legislativo (como ir a comisión o lectura de debates)."
        )

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2500,
            system=prompt_sistema,
            messages=[{"role": "user", "content": consulta_usuario}]
        )
        return response.content[0].text
    except Exception as e:
        print(f"Error en consulta: {e}")
        return "Ocurrió un error al procesar la respuesta."

def generar_pdf_respuesta(texto_respuesta: str, consulta: str) -> str:
    pdf_filename = "/tmp/respuesta_jurisconsultor.pdf"
    doc = SimpleDocTemplate(pdf_filename, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('Titulo', parent=styles['Heading1'], fontSize=14, leading=18, spaceAfter=12)
    body_style = ParagraphStyle('Body', parent=styles['Normal'], fontSize=10, leading=14, spaceAfter=8)
    
    story = [
        Paragraph("<b>JURISCONSULTOR - CONSULTA JURÍDICA</b>", titulo_style),
        Spacer(1, 10),
        Paragraph(f"<b>Consulta:</b> {consulta}", body_style),
        Spacer(1, 15),
        Paragraph("<b>Dictamen / Respuesta Fundamentada:</b>", body_style),
        Spacer(1, 10)
    ]
    
    # Agregar párrafos
    for linea in texto_respuesta.split('\n'):
        if linea.strip():
            story.append(Paragraph(linea.strip(), body_style))
            story.append(Spacer(1, 4))
            
    doc.build(story, canvasmaker=WatermarkCanvas)
    return pdf_filename

def subir_documento_whatsapp(filepath: str) -> str:
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    files = {
        'file': ('respuesta.pdf', open(filepath, 'rb'), 'application/pdf'),
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
