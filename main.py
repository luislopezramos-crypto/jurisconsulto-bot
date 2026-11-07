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
    """Remueve emojis e inconos no tipográficos y escapa entidades HTML (<, >, &) para ReportLab."""
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
    
    system_prompt = (
        "Eres 'Jurisconsulto', un consultor y analista experto en Derecho Constitucional y Parlamentario guatemalteco.\n\n"
        "REGLAS ESTRUCTURALES Y RIGOR DE ANÁLISIS JURÍDICO MANDATORIAS:\n"
        "1. RESPONDE BASÁNDOTE EXCLUSIVAMENTE EN EL TEXTO DOCUMENTAL PROVISTO EN LA BASE DE REFERENCIA.\n"
        "2. TU DICTAMEN DEBE DIVIDIRSE Y CITAR OBLIGATORIAMENTE TRES EJES DE FUNDAMENTACIÓN:\n"
        "   A) MARCO NORMATIVO APLICABLE: Cita explícita de artículos, numerales e
