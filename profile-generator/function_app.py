import azure.functions as func
import json
import os
import logging
from openai import AzureOpenAI

app = func.FunctionApp()
openai_client = None

def get_openai_client():
    try:
        api_key = os.environ["AZURE_OPENAI_KEY"]
        endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
        
        client = AzureOpenAI(
            api_key=api_key,
            api_version="2024-02-15-preview",
            azure_endpoint=endpoint
        )
        return client
    except Exception as e:
        logging.error(f"Erreur lors de l'initialisation du client Azure OpenAI: {str(e)}")
        raise

@app.route(route="health", auth_level=func.AuthLevel.ANONYMOUS)
async def health_check(req: func.HttpRequest) -> func.HttpResponse:
    try:
        # Tester l'accès aux variables d'environnement
        api_key = os.environ.get("AZURE_OPENAI_KEY", "Non configuré")
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "Non configuré")
        deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "Non configuré")
        
        return func.HttpResponse(
            body=json.dumps({
                "status": "ok",
                "message": "Fonction en ligne",
                "config": {
                    "openai_key": "Configuré" if api_key != "Non configuré" else "Manquant",
                    "endpoint": "Configuré" if endpoint != "Non configuré" else "Manquant",
                    "deployment": "Configuré" if deployment != "Non configuré" else "Manquant"
                }
            }),
            mimetype="application/json",
            status_code=200
        )
    except Exception as e:
        return func.HttpResponse(
            body=json.dumps({
                "status": "error",
                "message": str(e)
            }),
            mimetype="application/json",
            status_code=500
        )

@app.route(route="profile-generator", auth_level=func.AuthLevel.ANONYMOUS)
async def profile_generator(req: func.HttpRequest) -> func.HttpResponse:
    global openai_client
    logging.info('Requête pour la génération de profil reçue')
    
    # Test simple pour commencer
    return func.HttpResponse(
        body=json.dumps({
            "status": "ok",
            "message": "Endpoint profile-generator fonctionnel"
        }),
        mimetype="application/json",
        status_code=200
    )