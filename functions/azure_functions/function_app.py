import os
import azure.functions as func
import logging
import json
import traceback
import requests
from datetime import datetime
from azure.servicebus import ServiceBusClient, ServiceBusMessage

app = func.FunctionApp()

def extract_call_features(data):
    """
    Extrait et transforme les données VAPI vers le format attendu
    """
    try:
        # Vérification de la structure de base
        if "message" not in data:
            return {
                "error": "Structure JSON invalide: champ 'message' manquant",
                "status": "error"
            }

        message = data["message"]
        
        # Construction du format de sortie simplifié avec le numéro de téléphone via customer.number
        features = {
            "call_metadata": {
                "call_id": message["call"]["id"],
                "start_time": message["startedAt"],
                "customer_phone": message["customer"]["number"]  # Utilisation de customer.number comme dans la doc Vapi
            },
            "technical_settings": {
                "transcriber": {
                    "language": message["assistant"]["transcriber"]["language"]
                }
            },
            "conversation_content": {
                "structured_exchanges": []
            }
        }

        # Extraction des échanges depuis structuredData
        if "analysis" in message and "structuredData" in message["analysis"]:
            features["conversation_content"]["structured_exchanges"] = message["analysis"]["structuredData"]["transcript"]
        else:
            return {
                "error": "Données de conversation manquantes",
                "status": "error",
                "details": {
                    "message_keys": list(message.keys()),
                    "analysis_present": "analysis" in message,
                    "structuredData_present": "structuredData" in message.get("analysis", {})
                }
            }

        return features
        
    except KeyError as e:
        return {
            "error": f"Erreur lors de l'extraction des features: champ manquant {str(e)}",
            "status": "error",
            "details": {
                "received_data": {
                    "top_level_keys": list(data.keys()) if isinstance(data, dict) else "not a dict",
                    "message_keys": list(data.get("message", {}).keys()) if isinstance(data.get("message"), dict) else "not a dict"
                }
            }
        }

def send_to_processor(processed_data):
    """
    Envoie les données traitées directement à la fonction de génération de profils via HTTP
    """
    try:
        # URL de la fonction de génération de profils
        
        # Récupérer la clé d'API depuis les variables d'environnement
        function_key = os.environ.get("PROFILE_FUNCTION_KEY")        
        # Préparer les headers avec authentification
        processor_url = f"https://func-profile-generator.azurewebsites.net/api/profile-generator?code={function_key}"
        headers = {"Content-Type": "application/json"}
        
        # Log avant l'envoi pour debug
        logging.info(f"[HTTP] Envoi au processeur de profil: {processor_url}")
        logging.info(f"[HTTP] Données: {json.dumps(processed_data, ensure_ascii=False)[:200]}...")
        
        # Faire la requête POST avec un timeout approprié (30 secondes)
        response = requests.post(
            processor_url, 
            json=processed_data, 
            headers=headers,
            timeout=30
        )
        
        # Vérifier la réponse
        if response.status_code >= 200 and response.status_code < 300:
            logging.info(f"[HTTP] Requête réussie: {response.status_code}")
            
            # Tenter de parser la réponse JSON si disponible
            try:
                response_data = response.json()
                return True, f"Traitement réussi: {json.dumps(response_data, ensure_ascii=False)[:100]}..."
            except:
                return True, f"Traitement réussi avec statut: {response.status_code}"
        else:
            logging.error(f"[HTTP] Échec de la requête: {response.status_code}")
            logging.error(f"[HTTP] Réponse: {response.text[:500]}")
            return False, f"Échec du traitement: HTTP {response.status_code} - {response.text[:100]}"
            
    except requests.RequestException as e:
        logging.error(f"[HTTP] Erreur réseau: {str(e)}")
        return False, f"Erreur de communication: {str(e)}"
    except Exception as e:
        logging.error(f"[HTTP] Erreur inattendue: {str(e)}")
        logging.error(f"[HTTP] Trace: {traceback.format_exc()}")
        return False, f"Erreur: {str(e)}"

def check_user_exists(phone_number):
    """
    Vérifie si l'utilisateur existe dans Supabase
    """
    from supabase import create_client
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_API_KEY")
    supabase = create_client(supabase_url, supabase_key)
    
    user_response = supabase.table("users").select("id").eq("phone_number", phone_number).execute()
    return len(user_response.data) > 0

def route_transcript(processed_data):
    """
    Route le transcript vers le bon service selon l'existence de l'utilisateur
    """
    phone_number = processed_data.get("call_metadata", {}).get("customer_phone")
    
    if not phone_number:
        logging.error("Numéro de téléphone manquant")
        return False, "Numéro de téléphone manquant"
    
    # Vérifier si l'utilisateur existe
    user_exists = check_user_exists(phone_number)
    
    if user_exists:
        # Envoyer à Service Bus pour mise à jour de profil
        connection_string = os.environ.get("SERVICE_BUS_CONNECTION")
        
        with ServiceBusClient.from_connection_string(connection_string) as client:
            with client.get_queue_sender("existing-user-transcripts") as sender:
                message = ServiceBusMessage(json.dumps(processed_data))
                sender.send_messages([message])
                
        return True, "Transcript envoyé à la file d'attente de mise à jour"
    else:
        # Envoyer à la fonction de création de profil
        return send_to_processor(processed_data)

# Variable globale pour stocker la dernière requête et son statut de routage
last_received_data = None
last_routing_status = None  # Nouveau: pour stocker le statut de routage

@app.route(route="vapi-webhook", auth_level=func.AuthLevel.ANONYMOUS)
async def vapi_webhook(req: func.HttpRequest) -> func.HttpResponse:
    global last_received_data, last_routing_status
    logging.info(f"[START] Nouvelle requête VAPI reçue: {req.url}")
    
    # Récupérer le secret depuis les variables d'environnement
    expected_secret = os.environ.get('VAPI_WEBHOOK_SECRET')
    vapi_secret = req.headers.get('X-VAPI-SECRET')

    # Vérification pour les requêtes POST
    if req.method == "POST":
        if not expected_secret or vapi_secret != expected_secret.lower():
            logging.warning(f"[SECURITY] Tentative d'accès non autorisée depuis: {req.url}")
            return func.HttpResponse("Non autorisé", status_code=401)
    
    if req.method == "GET":
        logging.info("[INFO] Requête GET reçue - retour des dernières données traitées")
        return func.HttpResponse(
            body=json.dumps({
                "dernière_requête_traitée": last_received_data or "Aucune donnée reçue",
                "statut_routage": last_routing_status or "Aucun routage effectué"
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=200
        )
    
    # Pour les requêtes POST
    try:
        req_body = req.get_json()
        logging.info("[MILESTONE] Données JSON reçues et parsées avec succès")
        
        # Prétraitement des données avec extract_call_features
        processed_data = extract_call_features(req_body)
        transcript_id = processed_data.get("call_metadata", {}).get("call_id", "unknown")
        logging.info(f"[MILESTONE] Données extraites pour le transcript {transcript_id}")
        
        last_received_data = processed_data  # Sauvegarder les données traitées
        logging.info(f"[DEBUG] Données traitées: {json.dumps(processed_data, indent=2)}")
        
        # Envoi des données via route_transcript
        success, message = route_transcript(processed_data)
        
        # Stocker le statut de routage
        last_routing_status = {
            "type": "utilisateur_existant" if "file d'attente" in message else "nouvel_utilisateur",
            "détails": message
        }
        
        if success:
            logging.info(f"[SUCCESS] Traitement déclenché pour le transcript {transcript_id}")
        else:
            logging.error(f"[ERROR] Échec du traitement pour le transcript {transcript_id}")
        
        return func.HttpResponse(
            body=json.dumps({
                "processed_data": processed_data,
                "message": "Données traitées avec succès",
                "processor_status": message,
                "processor_success": success
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=200
        )
    except ValueError as e:
        logging.error(f"[ERROR] Format JSON invalide: {str(e)}")
        return func.HttpResponse(
            body=json.dumps({
                "error": "Format JSON invalide",
                "details": str(e)
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=400
        )
    except Exception as e:
        logging.error(f"[ERROR] Erreur inattendue: {str(e)}")
        logging.error(f"[ERROR] Stack trace: {traceback.format_exc()}")
        return func.HttpResponse(
            body=json.dumps({
                "error": "Erreur lors du traitement",
                "details": str(e)
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=500
        )

@app.route(route="test-processor", auth_level=func.AuthLevel.ANONYMOUS)
def test_processor(req: func.HttpRequest) -> func.HttpResponse:
    """Test l'appel direct à la fonction de traitement"""
    try:
        # Message test simple
        test_message = {
            "transcript_id": f"test-{datetime.now().timestamp()}",
            "call_metadata": {
                "call_id": f"test-call-{datetime.now().timestamp()}",
                "start_time": datetime.now().isoformat()
            },
            "conversation_content": {
                "structured_exchanges": [
                    {"speaker": "user", "text": "Test message", "timestamp": "00:00:01"}
                ]
            },
            "technical_settings": {
                "transcriber": {"language": "fr-FR"}
            }
        }
        
        # Envoyer le message
        success, message = send_to_processor(test_message)
        
        return func.HttpResponse(
            json.dumps({
                "status": "success" if success else "error",
                "sent_message": test_message,
                "processor_response": message
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=200 if success else 500
        )
    except Exception as e:
        return func.HttpResponse(
            json.dumps({
                "error": str(e),
                "traceback": traceback.format_exc()
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=500
        )
    
