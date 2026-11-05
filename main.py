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
MAX_WHATSAPP_CHARS = 1400

class WatermarkCanvas(canvas.Canvas):
    """Marca de agua visible y centrada en cada página del PDF"""
    def showPage(self):
        self.saveState()
        self.setFont("Helvetica-Bold", 24)
        self.setFillColorRGB(0.5, 0.5, 0.5, alpha=0.55)  # Intensidad y visibilidad reforzada
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
                # Responde 200 OK inmediatamente a WhatsApp para detener los reintentos repetidos
                background_tasks.add_task(procesar_y_enviar, from_number, text_body)
    except Exception as e:
        print(f"Error procesando webhook: {e}")
        
    return Response(content="OK", status_code=200)

def procesar_y_enviar(to_number: str, consulta_usuario: str):
    respuesta_texto = generar_respuesta_fundamentada(consulta_usuario)
    
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
                "💡 *¿Te gustaría que profundice en la jurisprudencia aplicable o que elabore un modelo/plantilla relacionado a este tema?*"
            )
        else:
            enviar_whatsapp_texto(to_number, respuesta_texto[:4000])

def generar_respuesta_fundamentada(consulta_usuario: str) -> str:
    try:
        # SYSTEM PROMPT DE RIGOR JURÍDICO (Fijado estrictamente según tus parámetros)
        prompt_sistema = (
            "Eres el asistente de Jurisconsultor, experto en derecho constitucional y parlamentario de Guatemala.\n\n"
            "REGLAS ESTRUCTURALES Y DE RIGOR JURÍDICO:\n"
            "1. FUNDAMENTACIÓN OBLIGATORIA: Toda afirmación debe citar explícitamente el artículo, cuerpo legal "
            "(CPRG, LOOL Decreto 63-94) y/o expediente/sentencia exacto de la Corte de Constitucionalidad.\n"
            "2. CLÁUSULA DE ABSTENCIÓN: Si la consulta no puede responderse directamente con la normativa o criterios "
            "constitucionales aplicables, el sistema NO DEBE ASUMIR NI ESPECULAR; debe indicar explícitamente: "
            "\"La normativa analizada no contiene disposición específica sobre el punto...\".\n"
            "3. AUDIENCIA EXPERTA: Omite explicaciones básicas del trámite legislativo ordinario (como que las iniciativas "
            "se presentan o pasan a comisión). Centra el análisis estrictamente en el fondo sustantivo de la duda.\n"
            "4. ESTRUCTURA FORENSE / TÉCNICA: Organiza la respuesta con Tesis del asunto, Análisis normativo aplicable, "
            "Conclusión y Fuentes consultadas.\n"
            "5. CIERRE INTERACTIVO: Concluye SIEMPRE proponiendo 1 o 2 preguntas de seguimiento o modelos prácticos para que el "
            "usuario continúe iterando en el chat."
        )

        # Se fija la temperatura en 0.1 para ceñir al modelo a la normativa sin especular
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            temperature=0.1,
            max_tokens=1800,
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
            story.append(Paragraph(linea.strip(), body_style))
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
