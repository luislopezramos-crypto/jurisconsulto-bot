def identificar_documentos_en_vertex(query_text: str) -> list:
    """Busca en Vertex AI forzando la recuperación de leyes, jurisprudencia CC y precedentes."""
    try:
        client = discoveryengine.SearchServiceClient(credentials=credentials) if credentials else discoveryengine.SearchServiceClient()
        
        serving_config = client.serving_config_path(
            project=PROJECT_ID,
            location=LOCATION,
            data_store=DATA_STORE_ID,
            serving_config="default_config",
        )
        
        # Ampliamos la búsqueda y reformulamos internamente para capturar gacetas y resoluciones
        query_enriquecida = f"{query_text} jurisprudencia Corte de Constitucionalidad expedientes precedentes parlamentarios"
        
        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=query_enriquecida,
            page_size=8, # Traemos más documentos para asegurar que entren las resoluciones de la CC
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

def generar_dictamen_con_claude(pregunta_usuario: str, contexto_documental_completo: str) -> str:
    if not ANTHROPIC_API_KEY:
        return "Error: No se encuentra configurada la variable ANTHROPIC_API_KEY en Render."

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    system_prompt = (
        "Eres 'Jurisconsulto', un consultor y analista experto en Derecho Constitucional y Parlamentario guatemalteco.\n\n"
        "REGLAS OBLIGATORIAS DE SUSTANCIA Y CONTENIDO JURÍDICO:\n"
        "1. RESPONDE BASÁNDOTE EXCLUSIVAMENTE EN EL TEXTO DOCUMENTAL PROVISTO EN LA BASE DE REFERENCIA.\n"
        "2. TU DICTAMEN DEBE ESTRUCTURARSE OBLIGATORIAMENTE EN TRES EJES DE FUNDAMENTACIÓN:\n"
        "   A) MARCO NORMATIVO APLICABLE: Cita de forma explícita artículos, numerales e incisos (CPRG, LOOL, etc.).\n"
        "   B) JURISPRUDENCIA Y DOCTRINA DE LA CORTE DE CONSTITUCIONALIDAD: Es OBLIGATORIO citar expresamente los NÚMEROS DE EXPEDIENTE, SENTENCIAS, GACETAS o criterios doctrinales de la CC presentes en los documentos.\n"
        "   C) PRECEDENTES PARLAMENTARIOS Y DICTÁMENES: Cita los precedentes del Congreso (ej. Precedente 1-2017, 2-2017, etc.) y acuerdos legislativos relacionados que aparezcan en los textos.\n"
        "3. Si la base documental provista contiene sentencias o expedientes de la CC sobre el tema, NO PUEDES OMITIRLOS. Deben ser integrados y analizados dentro del dictamen.\n"
        "4. No incluyas advertencias genéricas sobre la herramienta.\n"
        "5. Finaliza proponiendo únicamente UNA temática o pregunta de seguimiento guiada con el icono 🔍."
    )

    prompt = (
        f"DOCUMENTACIÓN ESTRUCTURAL Y JURISPRUDENCIAL DE REFERENCIA SELECCIONADA:\n{contexto_documental_completo}\n\n"
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
