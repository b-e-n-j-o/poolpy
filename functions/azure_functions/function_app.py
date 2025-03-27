import os
import azure.functions as func
import logging
import json
import traceback
import requests
from datetime import datetime
from supabase import create_client


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

# Modifier les variables globales
last_received_data = None
last_routing_status = None
last_operation_details = {
    "timestamp": None,
    "phone_number": None,
    "user_exists": None,
    "routing_decision": None,
    "operation_result": None,
    "supabase_check": {
        "success": False,
        "error": None
    }
}

def check_user_exists(phone_number):
    """
    Vérifie si l'utilisateur existe dans Supabase
    """
    global last_operation_details
    
    try:
        supabase_url = os.environ.get("SUPABASE_URL_DEV")
        supabase_key = os.environ.get("SUPABASE_KEY_DEV")
        supabase = create_client(supabase_url, supabase_key)
        
        user_response = supabase.table("users").select("id").eq("phone_number", phone_number).execute()
        exists = len(user_response.data) > 0
        
        last_operation_details["supabase_check"] = {
            "success": True,
            "error": None
        }
        
        return exists
    except Exception as e:
        last_operation_details["supabase_check"] = {
            "success": False,
            "error": str(e)
        }
        raise e

def route_transcript(processed_data):
    """
    Route le transcript vers le bon service selon l'existence de l'utilisateur
    """
    global last_operation_details
    
    logging.info("[DEBUG] Début de route_transcript")  # Nouveau log
    phone_number = processed_data.get("call_metadata", {}).get("customer_phone")
    logging.info(f"[DEBUG] Numéro de téléphone extrait: {phone_number}")  # Nouveau log
    
    last_operation_details.update({
        "timestamp": datetime.now().isoformat(),
        "phone_number": phone_number,
        "user_exists": None,
        "routing_decision": None,
        "operation_result": None,
        "debug_info": {
            "profile_updater_url": None,
            "headers": None
        }
    })
    
    if not phone_number:
        last_operation_details["operation_result"] = "Erreur: numéro de téléphone manquant"
        logging.error("Numéro de téléphone manquant")
        return False, "Numéro de téléphone manquant"
    
    try:
        logging.info("[DEBUG] Vérification existence utilisateur")  # Nouveau log
        user_exists = check_user_exists(phone_number)
        logging.info(f"[DEBUG] Résultat existence utilisateur: {user_exists}")  # Nouveau log
        last_operation_details["user_exists"] = user_exists
        
        if user_exists:
            logging.info("[DEBUG] Utilisateur existant - préparation appel profile-updater")  # Nouveau log
            last_operation_details["routing_decision"] = "profile_updater"
            try:
                profile_updater_url = os.environ.get("PROFILE_UPDATER_URL")
                
                if not profile_updater_url:
                    error_msg = "Variable d'environnement PROFILE_UPDATER_URL non configurée"
                    last_operation_details["debug_info"] = {
                        "profile_updater_url": None,
                        "error": error_msg
                    }
                    logging.error(f"[CONFIG] {error_msg}")
                    return False, error_msg
                    
                # Retirer le @ si présent au début de l'URL
                if profile_updater_url.startswith('@'):
                    profile_updater_url = profile_updater_url[1:]
                    
                headers = {"Content-Type": "application/json"}
                
                last_operation_details["debug_info"] = {
                    "profile_updater_url": profile_updater_url,
                    "headers": headers,
                    "data_sample": json.dumps(processed_data)[:200]
                }
                
                logging.info(f"[HTTP] Envoi vers profile-updater")
                logging.info(f"[HTTP] URL: {profile_updater_url}")
                logging.info(f"[HTTP] Headers: {headers}")
                
                response = requests.post(
                    profile_updater_url,
                    json=processed_data,
                    headers=headers,
                    timeout=30
                )
                
                # Logger la réponse immédiatement
                logging.info(f"[HTTP] Status code: {response.status_code}")
                logging.info(f"[HTTP] Response headers: {dict(response.headers)}")
                logging.info(f"[HTTP] Response body: {response.text[:200]}")
                
                if response.status_code >= 200 and response.status_code < 300:
                    success_msg = f"Profil mis à jour pour {phone_number}"
                    last_operation_details["operation_result"] = success_msg
                    return True, success_msg
                else:
                    error_msg = f"Erreur {response.status_code}: {response.text[:200]}"
                    last_operation_details["operation_result"] = error_msg
                    return False, error_msg
                    
            except Exception as e:
                error_msg = f"Erreur lors de l'envoi au profile-updater: {str(e)}"
                last_operation_details["operation_result"] = error_msg
                return False, error_msg
        else:
            last_operation_details["routing_decision"] = "profile_generator"
            # Pour les nouveaux utilisateurs, utiliser la fonction de création de profil
            success, message = send_to_processor(processed_data)
            last_operation_details["operation_result"] = message
            return success, message
            
    except Exception as e:
        error_msg = f"Erreur lors du routage: {str(e)}"
        last_operation_details["operation_result"] = error_msg
        return False, error_msg

@app.route(route="vapi-webhook", auth_level=func.AuthLevel.ANONYMOUS)
async def vapi_webhook(req: func.HttpRequest) -> func.HttpResponse:
    global last_received_data, last_routing_status, last_operation_details
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
                "dernière_requête": {
                    "données": last_received_data or "Aucune donnée reçue",
                    "statut_routage": last_routing_status or "Aucun routage effectué"
                },
                "détails_opération": {
                    "horodatage": last_operation_details["timestamp"],
                    "numéro_téléphone": last_operation_details["phone_number"],
                    "vérification_utilisateur": {
                        "existe": last_operation_details["user_exists"],
                        "statut_supabase": last_operation_details["supabase_check"]
                    },
                    "routage": {
                        "décision": last_operation_details["routing_decision"],
                        "résultat": last_operation_details["operation_result"]
                    },
                    "debug": last_operation_details.get("debug_info", {})
                }
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
            "type": "utilisateur_existant" if last_operation_details["routing_decision"] == "profile_updater" else "nouvel_utilisateur",
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
    
