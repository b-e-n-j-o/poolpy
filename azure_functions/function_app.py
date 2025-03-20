import os
import azure.functions as func
import logging
import json

app = func.FunctionApp()

# Variable globale pour stocker la dernière requête
last_received_data = None

@app.route(route="vapi-webhook", auth_level=func.AuthLevel.ANONYMOUS)
async def vapi_webhook(req: func.HttpRequest) -> func.HttpResponse:
    global last_received_data
    logging.info('Requête VAPI reçue')
    
    # Récupérer le secret depuis les variables d'environnement
    expected_secret = os.environ.get('VAPI_WEBHOOK_SECRET')
    vapi_secret = req.headers.get('X-VAPI-SECRET')

    # Vérification pour les requêtes POST
    if req.method == "POST":
        if not expected_secret or vapi_secret != expected_secret.lower():
            return func.HttpResponse("Non autorisé", status_code=401)
    
    if req.method == "GET":
        # Afficher la dernière donnée reçue
        return func.HttpResponse(
            body=json.dumps({
                "dernière_requête_reçue": last_received_data or "Aucune donnée reçue"
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=200
        )
    
    # Pour les requêtes POST
    try:
        req_body = req.get_json()
        last_received_data = req_body  # Sauvegarder la dernière requête
        logging.info(f"Corps de la requête: {json.dumps(req_body, indent=2)}")
    except ValueError:
        req_body = req.get_body().decode('utf-8')
        last_received_data = req_body
        logging.info(f"Corps brut de la requête: {req_body}")
    
    return func.HttpResponse(
        body=json.dumps({
            "received": req_body,
            "message": "Webhook reçu avec succès"
        }, ensure_ascii=False, indent=2),
        mimetype="application/json",
        status_code=200
    )

# // test //