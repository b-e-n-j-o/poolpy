import azure.functions as func
import json
import os
from openai import AzureOpenAI

app = func.FunctionApp()

# D'abord tester que les variables d'environnement sont bien configurées
@app.route(route="health", auth_level=func.AuthLevel.ANONYMOUS)
async def health_check(req: func.HttpRequest) -> func.HttpResponse:
    try:
        # Tester l'accès aux variables d'environnement
        api_key = os.environ.get("AZURE_OPENAI_KEY", "Non configuré")
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "Non configuré")
        
        return func.HttpResponse(
            body=json.dumps({
                "status": "ok",
                "message": "Fonction en ligne",
                "config": {
                    "openai_key": "Configuré" if api_key != "Non configuré" else "Manquant",
                    "endpoint": "Configuré" if endpoint != "Non configuré" else "Manquant"
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