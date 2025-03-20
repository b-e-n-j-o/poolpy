import os
import azure.functions as func
import logging
import json

app = func.FunctionApp()

def extract_call_features(data):
    try:
        features = {
            "call_metadata": {
                "call_id": data["message"]["call"]["id"],
                "start_time": data["message"]["startedAt"],
                "end_time": data["message"]["endedAt"],
                "duration": {
                    "seconds": data["message"]["durationSeconds"],
                    "minutes": data["message"]["durationMinutes"]
                },
                "end_reason": data["message"]["endedReason"]
            },

            "conversation_content": {
                "summary": data["message"]["analysis"]["summary"],
                "full_transcript": data["message"]["transcript"],
                "structured_exchanges": data["message"]["analysis"]["structuredData"]["transcript"],
                "media": {
                    "recording_url": data["message"]["recordingUrl"],
                    "stereo_recording_url": data["message"]["stereoRecordingUrl"]
                }
            },

            "technical_settings": {
                "model": {
                    "provider": data["message"]["assistant"]["model"]["provider"],
                    "model_name": data["message"]["assistant"]["model"]["model"],
                    "temperature": data["message"]["assistant"]["model"]["temperature"]
                },
                "transcriber": {
                    "provider": data["message"]["assistant"]["transcriber"]["provider"],
                    "model": data["message"]["assistant"]["transcriber"]["model"],
                    "language": data["message"]["assistant"]["transcriber"]["language"]
                },
                "voice": {
                    "provider": data["message"]["costs"][2]["voice"]["provider"],
                    "voice_id": data["message"]["costs"][2]["voice"]["voiceId"],
                    "model": data["message"]["costs"][2]["voice"]["model"]
                }
            },

            "performance_metrics": {
                "tokens": {
                    "prompt": data["message"]["costBreakdown"]["llmPromptTokens"],
                    "completion": data["message"]["costBreakdown"]["llmCompletionTokens"],
                    "total": data["message"]["costBreakdown"]["llmPromptTokens"] + 
                            data["message"]["costBreakdown"]["llmCompletionTokens"]
                },
                "costs": {
                    "total": data["message"]["costBreakdown"]["total"],
                    "breakdown": {
                        "stt": data["message"]["costBreakdown"]["stt"],
                        "llm": data["message"]["costBreakdown"]["llm"],
                        "tts": data["message"]["costBreakdown"]["tts"],
                        "vapi": data["message"]["costBreakdown"]["vapi"]
                    }
                }
            },

            "conversation_analysis": {
                "messages_flow": [
                    {
                        "role": msg["role"],
                        "content": msg["message"],
                        "timestamp": msg["time"],
                        "seconds_from_start": msg.get("secondsFromStart", None),
                        "duration": msg.get("duration", None)
                    }
                    for msg in data["message"]["messages"]
                    if msg["role"] in ["user", "bot"]
                ],
                "user_messages": [
                    {
                        "content": msg["message"],
                        "timestamp": msg["time"]
                    }
                    for msg in data["message"]["messages"]
                    if msg["role"] == "user"
                ]
            }
        }
        return features
    except KeyError as e:
        return {
            "error": f"Erreur lors de l'extraction des features: champ manquant {str(e)}",
            "status": "error"
        }

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
        # Afficher la dernière donnée traitée
        return func.HttpResponse(
            body=json.dumps({
                "dernière_requête_traitée": last_received_data or "Aucune donnée reçue"
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=200
        )
    
    # Pour les requêtes POST
    try:
        req_body = req.get_json()
        # Prétraitement des données avec extract_call_features
        processed_data = extract_call_features(req_body)
        last_received_data = processed_data  # Sauvegarder les données traitées
        logging.info(f"Données traitées: {json.dumps(processed_data, indent=2)}")
        
        return func.HttpResponse(
            body=json.dumps({
                "processed_data": processed_data,
                "message": "Données traitées avec succès"
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=200
        )
    except ValueError as e:
        return func.HttpResponse(
            body=json.dumps({
                "error": "Format JSON invalide",
                "details": str(e)
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=400
        )
    except Exception as e:
        return func.HttpResponse(
            body=json.dumps({
                "error": "Erreur lors du traitement",
                "details": str(e)
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=500
        )

# // test //