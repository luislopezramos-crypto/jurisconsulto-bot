from google.cloud import discoveryengine_v1 as discoveryengine

def consultar_base_legal(query_text: str):
    project_id = "1788990201092"
    location = "global"
    data_store_id = "bibliotecaconstitucionalyparlamentaria_1788990201092"
    
    client = discoveryengine.SearchServiceClient()
    
    serving_config = client.serving_config_path(
        project=project_id,
        location=location,
        data_store=data_store_id,
        serving_config="default_config",
    )
    
    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=query_text,
        page_size=5,
    )
    
    response = client.search(request)
    
    # Extraer el texto de los fragmentos encontrados en los PDFs/DOCX
    resultados = []
    for result in response.results:
        data = result.document.derived_struct_data
        resultados.append(data)
        
    return resultados
