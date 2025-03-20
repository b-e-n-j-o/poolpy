import azure.functions as func
import logging
import json

app = func.FunctionApp()

@app.route(route="vapi-webhook", auth_level=func.AuthLevel.FUNCTION)
async def vapi_webhook(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Requête VAPI reçue')
    
    # Récupérer le corps de la requête
    try:
        req_body = req.get_json()
        logging.info(f"Corps de la requête: {json.dumps(req_body, indent=2)}")
    except ValueError:
        req_body = req.get_body().decode('utf-8')
        logging.info(f"Corps brut de la requête: {req_body}")
    
    # Retourner le même contenu comme réponse
    return func.HttpResponse(
        body=json.dumps({
            "received": req_body,
            "message": "Webhook reçu avec succès"
        }),
        mimetype="application/json",
        status_code=200
    )