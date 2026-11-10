import os
import json
import io
import re
import html
from fastapi import FastAPI, Request, Response, BackgroundTasks
import requests
from google.cloud import discoveryengine_v1 as discoveryengine
from google.cloud import storage
from google.oauth2 import service_account
import anthropic
from pypdf import PdfReader

# Librerías para generación de PDF y Marca de Agua
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

app = FastAPI(title="Jurisconsulto Bot API")

# Variables de entorno
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "1788990201092")
DATA_STORE_ID = os.environ.get("DATA_STORE_ID", "bibliotecaconstitucionalyparlamentaria_1788990201092")
LOCATION = "global"
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

credentials = None
SECRET_FILE_PATH = "/etc/secrets/gcp-key.json"

# Carga y validación de credenciales de GCP al inicio
if os.path.exists(SECRET_FILE_PATH):
    try:
        credentials = service_account.Credentials.from_service_account_file(SECRET_FILE_PATH)
    except Exception as e:
        print(f"Error al cargar archivo secreto GCP desde {SECRET_FILE_PATH}: {e}")
else:
    gcp_creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if gcp_creds_json:
        try:
            creds_clean = gcp_creds_json.replace("\\n", "\n")
            creds_dict = json.loads(creds_clean, strict=False)
            credentials = service_account.Credentials.from_service_account_info(creds_dict)
        except Exception as e:
            print(f"Error al cargar credenciales JSON de entorno: {e}")


class NumberedCanvas(canvas.Canvas):
    """Canvas personalizado para la marca de agua limpia de Jurisconsulto."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_watermark()
            super().showPage()
        super().save()

    def draw_watermark(self):
        self.saveState()
        self.setFont("Helvetica-Bold", 30)
        self.setFillColorRGB(0.85, 0.85, 0.85, alpha=0.3)
        self.translate(300, 400)
        self.rotate(45)
        self.drawCentredString(0, 0, "Respuesta dada por Jurisconsulto")
        self.restoreState()


def preparar_texto_para_reportlab(texto: str) -> str:
    """Remueve emojis e iconos no tipográficos y escapa entidades HTML (<, >, &) para ReportLab."""
    texto_sin_emojis = re.sub(r'[\U00010000-\U0010FFFF]', '', texto)
    return html.escape(texto_sin_emojis)


def crear_pdf_dictamen(texto_dictamen: str) -> bytes:
    """Genera un PDF limpio con marca de agua y formateo seguro de párrafos."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=45,
        leftMargin=45,
        topMargin=55,
        bottomMargin=55
    )
    styles = getSampleStyleSheet()
    style_body = ParagraphStyle('BodyTextCustom', parent=styles['Normal'], fontSize=10.5, leading=14.5, spaceAfter=8)
    
    story = []
    for linea in texto_dictamen.split("\n"):
        linea_limpia = linea.strip()
        if linea_limpia:
            linea_escapada = preparar_texto_para_reportlab(linea_limpia)
            story.append(Paragraph(linea_escapada, style_body))
        else:
            story.append(Spacer(1, 6))
            
    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer.getvalue()


def identificar_documentos_en_vertex(query_text: str) -> list:
    """Busca en Vertex AI enriqueciendo la consulta para capturar gacetas, resoluciones de la CC y precedentes."""
    try:
        client = discoveryengine.SearchServiceClient(credentials=credentials) if credentials else discoveryengine.SearchServiceClient()
        
        serving_config = client.serving_config_path(
            project=PROJECT_ID,
            location=LOCATION,
            data_store=DATA_STORE_ID,
            serving_config="default_config",
        )
        
        # Enriquecimiento de la consulta para forzar la recuperación de jurisprudencia y precedentes
        query_enriquecida = f"{query_text} jurisprudencia Corte de Constitucionalidad expedientes precedentes parlamentarios"
        
        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=query_enriquecida,
            page_size=8,
        )
        
        response = client.search(request)
        documentos_uris = []
        
        for result in response.results:
            derived_data = result.document.derived_struct_data
            doc_link = derived_data.get("link") or derived_data.get("gcs_uri") or ""
            if doc_link:
                documentos_uris.append(doc_link)
                
        return documentos_uris
    except Exception as e:
        print(f"Error identificando documentos en Vertex: {e}")
        return []


def extraer_texto_pdf_gcs(uri_gcs: str) -> str:
    """Descarga el PDF binario desde GCS preservando la ruta completa y extrae su texto con pypdf."""
    try:
        storage_client = storage.Client(credentials=credentials)
        
        if uri_gcs.startswith("gs://"):
            partes = uri_gcs.replace("gs://", "").split("/", 1)
            bucket_name = partes[0]
            blob_path = partes[1] if len(partes) > 1 else ""
        else:
            bucket_name = GCS_BUCKET_NAME
            blob_path = uri_gcs

        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        
        pdf_bytes = blob.download_as_bytes()
        pdf_file = io.BytesIO(pdf_bytes)
        reader = PdfReader(pdf_file)
        
        texto_extraido = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                texto_extraido.append(t)
                
        resultado_final = "\n".join(texto_extraido).strip()
        
        if not resultado_final:
            print(f"⚠️ ADVERTENCIA: El documento '{uri_gcs}' no contiene capa de texto extraíble (posible PDF escaneado).")
            
        return resultado_final
    except Exception as e:
        print(f"Error extrayendo texto del PDF ({uri_gcs}): {e}")
        return ""


def recortar_contexto_por_articulos(texto: str, limite_max: int) -> str:
    """Realiza cortes respetando saltos de párrafo para no truncar la norma ni la jurisprudencia a la mitad."""
    if len(texto) <= limite_max:
        return texto
    
    corte_parrafo = texto[:limite_max].rfind("\n\n")
    if corte_parrafo != -1:
        return texto[:corte_parrafo]
    
    corte_linea = texto[:limite_max].rfind("\n")
    if corte_linea != -1:
        return texto[:corte_linea]
        
    return texto[:limite_max]


def generar_dictamen_con_claude(pregunta_usuario: str, contexto_documental_completo: str) -> str:
    if not ANTHROPIC_API_KEY:
        return "Error: No se encuentra configurada la variable ANTHROPIC_API_KEY en Render."

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    system_prompt = """Eres 'Jurisconsulto', un consultor y analista experto en Derecho Constitucional y Parlamentario guatemalteco.

REGLAS ESTRUCTURALES Y RIGOR DE ANÁLISIS JURÍDICO MANDATORIAS:
1. RESPONDE BASÁNDOTE EXCLUSIVAMENTE EN EL TEXTO DOCUMENTAL PROVISTO EN LA BASE DE REFERENCIA.
2. TU DICTAMEN DEBE DIVIDIRSE Y CITAR OBLIGATORIAMENTE TRES EJES DE FUNDAMENTACIÓN:
   A) MARCO NORMATIVO APLICABLE: Cita explícita de artículos, numerales e incisos (CPRG, LOOL, etc.).
   B) JURISPRUDENCIA Y DOCTRINA DE LA CORTE DE CONSTITUCIONALIDAD: Cita explícita y obligatoria de NÚMEROS DE EXPEDIENTE, SENTENCIAS, GACETAS o criterios doctrinales de la CC presentes en los archivos.
   C) PRECEDENTES PARLAMENTARIOS Y DICTÁMENES: Cita explícita de los precedentes del Congreso (ej. Precedente 1-2017, 2-2017) y acuerdos legislativos relacionados.
3. Si la base documental provista contiene resoluciones, gacetas o expedientes de la CC sobre el tema, ES MANDATORIO INTEGRARLOS Y CITARLOS dentro del análisis.
4. No incluyas advertencias genéricas sobre la búsqueda ni justificaciones sobre la herramienta.
5. Finaliza proponiendo únicamente UNA temática o pregunta de seguimiento guiada con el icono 🔍."""

    prompt = (
        f"DOCUMENTACIÓN NORMATIVA Y JURISPRUDENCIAL DE REFERENCIA SELECCIONADA:\n{contexto_documental_completo}\n\n"
        f"CONSULTA DEL USUARIO:\n{pregunta_usuario}\n\n"
        f"Dictamen Jurídico Integrado:"
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3500,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}]
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
    payload = {"messaging_product": "whatsapp", "to": telefono, "type": "text", "text": {"body": texto}}
    requests.post(url, json=payload, headers=headers)


def enviar_documento_whatsapp(telefono: str, pdf_bytes: bytes, filename: str, caption: str):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        return
    media_url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
    headers_media = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    files = {'file': (filename, pdf_bytes, 'application/pdf'), 'messaging_product': (None, 'whatsapp')}
    res = requests.post(media_url, headers=headers_media, files=files)
    
    if res.status_code == 200:
        media_id = res.json().get("id")
        messages_url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
        headers_msg = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "document",
            "document": {"id": media_id, "filename": filename, "caption": caption}
        }
        requests.post(messages_url, json=payload, headers=headers_msg)


def procesar_mensaje_en_segundo_plano(remitente: str, texto_usuario: str):
    try:
        if not credentials:
            msg_creds = "Error de configuración: No se pudieron cargar las credenciales de GCP en Render. Verifique el archivo de secretos."
            enviar_mensaje_whatsapp(remitente, msg_creds)
            return

        uris = identificar_documentos_en_vertex(texto_usuario)
        contextos = []
        caracteres_totales = 0
        LIMITE_MAX_CARACTERES = 500000  # Presupuesto de ~125,000 tokens para Sonnet 4.6

        for uri in uris:
            txt = extraer_texto_pdf_gcs(uri)
            if txt:
                if caracteres_totales + len(txt) > LIMITE_MAX_CARACTERES:
                    espacio_restante = LIMITE_MAX_CARACTERES - caracteres_totales
                    txt_recortado = recortar_contexto_por_articulos(txt, espacio_restante)
                    contextos.append(txt_recortado)
                    break
                else:
                    contextos.append(txt)
                    caracteres_totales += len(txt)

        if not contextos:
            msg_vacio = "Estimado usuario, no se localizaron fuentes documentales específicas en la biblioteca legal para responder su consulta con el rigor técnico requerido."
            enviar_mensaje_whatsapp(remitente, msg_vacio)
            return

        contexto_completo = "\n\n=== DOCUMENTO DE REFERENCIA SELECCIONADO ===\n\n".join(contextos)
        dictamen = generar_dictamen_con_claude(texto_usuario, contexto_completo)
        
        if len(dictamen) > 1500:
            pdf_bytes = crear_pdf_dictamen(dictamen)
            caption_msg = "Se adjunta el dictamen jurídico completo en formato PDF con la marca oficial de Jurisconsulto."
            enviar_documento_whatsapp(remitente, pdf_bytes, "Dictamen_Jurisconsulto.pdf", caption_msg)
        else:
            enviar_mensaje_whatsapp(remitente, dictamen)
            
    except Exception as e:
        print(f"Error procesando dictamen: {e}")
        msg_error = "Ocurrió un inconveniente técnico al procesar su solicitud. Por favor, reintente en unos momentos."
        enviar_mensaje_whatsapp(remitente, msg_error)


@app.get("/")
def home():
    return {"status": "ok", "service": "Jurisconsulto Bot API"}


@app.get("/webhook")
def verificar_webhook(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), status_code=200)
    return Response(content="Error de validación", status_code=403)


@app.post("/webhook")
async def recibir_mensaje(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
        messages = data.get("entry", [])[0].get("changes", [])[0].get("value", {}).get("messages", [])
        if messages:
            msg = messages[0]
            remitente = msg.get("from")
            texto_usuario = msg.get("text", {}).get("body", "")
            if texto_usuario:
                background_tasks.add_task(procesar_mensaje_en_segundo_plano, remitente, texto_usuario)
    except Exception as e:
        print(f"Error en webhook: {e}")
        
    return Response(content="EVENT_RECEIVED", status_code=200)
