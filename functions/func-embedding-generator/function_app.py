import azure.functions as func
import logging
import json
import os
import time
from openai import AzureOpenAI
from supabase import create_client, Client
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timezone

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Configuration
SUPABASE_URL = os.environ["SUPABASE_URL_DEV"]
SUPABASE_KEY = os.environ["SUPABASE_KEY_DEV"]
AZURE_OPENAI_KEY = os.environ["AZURE_OPENAI_KEY"]
AZURE_OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536  # Dimensionnalité pour text-embedding-3-small

# Initialisation des clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client = AzureOpenAI(
    api_key=AZURE_OPENAI_KEY,
    api_version="2024-02-01",
    azure_endpoint=AZURE_OPENAI_ENDPOINT
)

def generate_embedding(text: str) -> Optional[List[float]]:
    """Génère un embedding à partir du texte."""
    if not text or text.strip() == "":
        logging.warning("Texte vide, impossible de générer un embedding.")
        return None
    
    try:
        # Nettoyage et préparation du texte
        cleaned_text = text.strip()
        
        # Appel à l'API Azure OpenAI
        start_time = time.time()
        response = openai_client.embeddings.create(
            input=cleaned_text,
            model=EMBEDDING_MODEL
        )
        duration = time.time() - start_time
        
        if response.data and len(response.data) > 0:
            logging.info(f"Embedding généré en {duration:.2f} secondes")
            return response.data[0].embedding
        else:
            logging.error("Réponse d'embedding vide de l'API")
            return None
    except Exception as e:
        logging.error(f"Erreur lors de la génération de l'embedding: {str(e)}")
        return None

def store_embedding(
    user_id: str,
    profile_type: str,
    profile_id: str,
    embedding: List[float]
) -> Dict[str, Any]:
    """Stocke l'embedding dans Supabase."""
    try:
        # Vérifier si un embedding existe déjà pour ce profil
        existing = supabase.table("profile_embeddings").select("id").eq("profile_id", profile_id).eq("profile_type", profile_type).execute()
        
        result = {}
        
        if existing.data and len(existing.data) > 0:
            # Mettre à jour l'embedding existant
            supabase.table("profile_embeddings").update({
                "embedding": embedding,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", existing.data[0]["id"]).execute()
            result = {"status": "updated", "id": existing.data[0]["id"]}
        else:
            # Créer un nouvel embedding
            insert_response = supabase.table("profile_embeddings").insert({
                "user_id": user_id,
                "profile_type": profile_type,
                "profile_id": profile_id,
                "embedding": embedding
            }).execute()
            
            if insert_response.data and len(insert_response.data) > 0:
                result = {"status": "created", "id": insert_response.data[0]["id"]}
            else:
                result = {"status": "error", "message": "Échec de l'insertion"}
        
        return result
    except Exception as e:
        logging.error(f"Erreur lors du stockage de l'embedding: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.route(route="generate", methods=["POST"])
def generate_profile_embedding(req: func.HttpRequest) -> func.HttpResponse:
    """
    Génère et stocke un embedding pour un profil utilisateur.
    
    Body attendu:
    {
        "user_id": "uuid-string",
        "profile_type": "personal" | "desired",
        "profile_id": "uuid-string",
        "text": "Contenu du profil à embedder"
    }
    """
    try:
        req_body = req.get_json()
        
        # Validation des données d'entrée
        required_fields = ["user_id", "profile_type", "profile_id", "text"]
        for field in required_fields:
            if field not in req_body:
                return func.HttpResponse(
                    json.dumps({"error": f"Le champ '{field}' est requis"}),
                    mimetype="application/json",
                    status_code=400
                )
        
        user_id = str(req_body["user_id"])
        profile_type = req_body["profile_type"]
        profile_id = str(req_body["profile_id"])
        text = req_body["text"]
        
        # Validation du type de profil
        if profile_type not in ["personal", "desired"]:
            return func.HttpResponse(
                json.dumps({"error": "Le type de profil doit être 'personal' ou 'desired'"}),
                mimetype="application/json",
                status_code=400
            )
        
        # Génération de l'embedding
        start_time = time.time()
        embedding = generate_embedding(text)
        
        if not embedding:
            return func.HttpResponse(
                json.dumps({"error": "Impossible de générer l'embedding"}),
                mimetype="application/json",
                status_code=500
            )
        
        # Stockage de l'embedding
        storage_result = store_embedding(user_id, profile_type, profile_id, embedding)
        
        # Préparation de la réponse
        processing_time = time.time() - start_time
        response = {
            "user_id": user_id,
            "profile_type": profile_type,
            "profile_id": profile_id,
            "embedding_status": storage_result["status"],
            "dimensions": len(embedding),
            "processing_time_seconds": round(processing_time, 3)
        }
        
        # Ajouter l'ID si disponible
        if "id" in storage_result:
            response["embedding_id"] = storage_result["id"]
        
        return func.HttpResponse(
            json.dumps(response),
            mimetype="application/json",
            status_code=200
        )
    except Exception as e:
        logging.exception("Erreur lors du traitement de la requête")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            mimetype="application/json",
            status_code=500
        )

@app.route(route="generate-for-user", methods=["POST"])
def generate_user_embeddings(req: func.HttpRequest) -> func.HttpResponse:
    """
    Génère et stocke les embeddings pour tous les profils d'un utilisateur.
    
    Body attendu:
    {
        "user_id": "uuid-string"
    }
    """
    try:
        req_body = req.get_json()
        
        if "user_id" not in req_body:
            return func.HttpResponse(
                json.dumps({"error": "Le champ 'user_id' est requis"}),
                mimetype="application/json",
                status_code=400
            )
        
        user_id = str(req_body["user_id"])
        
        # Récupérer les profils de l'utilisateur
        personal_profile = supabase.table("personal_profiles").select("*").eq("user_id", user_id).limit(1).execute()
        desired_profile = supabase.table("desired_profiles").select("*").eq("user_id", user_id).limit(1).execute()
        
        results = {
            "user_id": user_id,
            "personal_profile": {"status": "not_found"},
            "desired_profile": {"status": "not_found"}
        }
        
        # Traitement du profil personnel
        if personal_profile.data and len(personal_profile.data) > 0:
            profile = personal_profile.data[0]
            
            # Obtenir le texte du profil - ajustez selon votre structure de données
            profile_text = profile.get("personal_profile", "")
            if not profile_text:
                # Fallback si le champ spécifique n'existe pas, construire à partir d'autres champs
                profile_text = f"Nom: {profile.get('name', '')}, Âge: {profile.get('age', '')}, " \
                              f"Localisation: {profile.get('location', '')}, Bio: {profile.get('bio', '')}, " \
                              f"Activités: {profile.get('hobbies_activities', '')}"
            
            # Générer et stocker l'embedding
            embedding = generate_embedding(profile_text)
            if embedding:
                storage_result = store_embedding(user_id, "personal", profile["id"], embedding)
                results["personal_profile"] = {
                    "status": storage_result["status"],
                    "profile_id": profile["id"],
                    "dimensions": len(embedding)
                }
                if "id" in storage_result:
                    results["personal_profile"]["embedding_id"] = storage_result["id"]
            else:
                results["personal_profile"] = {
                    "status": "error",
                    "message": "Impossible de générer l'embedding"
                }
        
        # Traitement du profil désiré (similaire au profil personnel)
        if desired_profile.data and len(desired_profile.data) > 0:
            profile = desired_profile.data[0]
            
            profile_text = profile.get("desired_profile", "")
            if not profile_text:
                profile_text = f"Âge recherché: {profile.get('age', '')}, " \
                              f"Localisation: {profile.get('location_preference', '')}, " \
                              f"Description: {profile.get('bio', '')}, " \
                              f"Activités: {profile.get('hobbies_activities', '')}"
            
            embedding = generate_embedding(profile_text)
            if embedding:
                storage_result = store_embedding(user_id, "desired", profile["id"], embedding)
                results["desired_profile"] = {
                    "status": storage_result["status"],
                    "profile_id": profile["id"],
                    "dimensions": len(embedding)
                }
                if "id" in storage_result:
                    results["desired_profile"]["embedding_id"] = storage_result["id"]
            else:
                results["desired_profile"] = {
                    "status": "error",
                    "message": "Impossible de générer l'embedding"
                }
        
        return func.HttpResponse(
            json.dumps(results),
            mimetype="application/json",
            status_code=200
        )
    except Exception as e:
        logging.exception("Erreur lors du traitement de la requête")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            mimetype="application/json",
            status_code=500
        )

@app.route(route="batch", methods=["POST"])
def batch_generate_embeddings(req: func.HttpRequest) -> func.HttpResponse:
    """
    Génère et stocke des embeddings pour plusieurs utilisateurs.
    
    Body attendu:
    {
        "user_ids": ["uuid-string-1", "uuid-string-2", "uuid-string-3"],
        "limit": 10  // Optionnel
    }
    """
    try:
        req_body = req.get_json()
        
        if "user_ids" not in req_body or not isinstance(req_body["user_ids"], list):
            return func.HttpResponse(
                json.dumps({"error": "Le champ 'user_ids' est requis et doit être une liste"}),
                mimetype="application/json",
                status_code=400
            )
        
        user_ids = [str(uid) for uid in req_body["user_ids"]]
        limit = req_body.get("limit", len(user_ids))
        
        results = {
            "total_users": len(user_ids),
            "processed_count": 0,
            "successful_count": 0,
            "error_count": 0,
            "results": []
        }
        
        # Traiter chaque utilisateur jusqu'à la limite
        for i, user_id in enumerate(user_ids[:limit]):
            if i >= limit:
                break
                
            try:
                # Appeler l'endpoint de génération pour un utilisateur
                user_result = json.loads(generate_user_embeddings(
                    func.HttpRequest(
                        method="POST",
                        body=json.dumps({"user_id": user_id}).encode(),
                        url="/api/generate-for-user"
                    )
                ).get_body())
                
                results["processed_count"] += 1
                
                # Vérifier si l'opération a réussi
                if "error" not in user_result:
                    results["successful_count"] += 1
                    results["results"].append({
                        "user_id": user_id,
                        "status": "success",
                        "details": user_result
                    })
                else:
                    results["error_count"] += 1
                    results["results"].append({
                        "user_id": user_id,
                        "status": "error",
                        "message": user_result["error"]
                    })
            except Exception as e:
                results["error_count"] += 1
                results["processed_count"] += 1
                results["results"].append({
                    "user_id": user_id,
                    "status": "error",
                    "message": str(e)
                })
        
        return func.HttpResponse(
            json.dumps(results),
            mimetype="application/json",
            status_code=200
        )
    except Exception as e:
        logging.exception("Erreur lors du traitement de la requête batch")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            mimetype="application/json",
            status_code=500
        )

@app.route(route="health", methods=["GET"])
def health_check(req: func.HttpRequest) -> func.HttpResponse:
    """
    Vérifie l'état de santé du service de génération d'embeddings.
    """
    start_time = time.time()
    status = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": {},
        "stats": {}
    }
    
    # Vérifier la connexion à Supabase
    try:
        supabase_start = time.time()
        tables_response = supabase.table("profile_embeddings").select("count").limit(1).execute()
        supabase_time = time.time() - supabase_start
        
        if hasattr(tables_response, "data"):
            status["services"]["supabase"] = {
                "status": "connected",
                "response_time": round(supabase_time, 3)
            }
        else:
            status["services"]["supabase"] = {
                "status": "error",
                "message": "Réponse invalide de Supabase"
            }
            status["status"] = "degraded"
    except Exception as e:
        status["services"]["supabase"] = {
            "status": "error",
            "message": str(e)
        }
        status["status"] = "degraded"
    
    # Vérifier la connexion à Azure OpenAI
    try:
        openai_start = time.time()
        embedding_response = openai_client.embeddings.create(
            input="Test de connexion",
            model=EMBEDDING_MODEL
        )
        openai_time = time.time() - openai_start
        
        if embedding_response and hasattr(embedding_response, "data") and len(embedding_response.data) > 0:
            status["services"]["azure_openai"] = {
                "status": "connected",
                "model": EMBEDDING_MODEL,
                "dimensions": len(embedding_response.data[0].embedding),
                "response_time": round(openai_time, 3)
            }
        else:
            status["services"]["azure_openai"] = {
                "status": "error",
                "message": "Réponse invalide d'Azure OpenAI"
            }
            status["status"] = "degraded"
    except Exception as e:
        status["services"]["azure_openai"] = {
            "status": "error",
            "message": str(e)
        }
        status["status"] = "degraded"
    
    # Statistiques sur les embeddings
    try:
        stats_query = """
        SELECT 
            COUNT(*) as total_embeddings,
            COUNT(CASE WHEN profile_type = 'personal' THEN 1 END) as personal_embeddings,
            COUNT(CASE WHEN profile_type = 'desired' THEN 1 END) as desired_embeddings,
            COUNT(DISTINCT user_id) as unique_users,
            MAX(updated_at) as last_updated
        FROM profile_embeddings
        """
        
        stats_response = supabase.rpc("execute_sql", {"query": stats_query}).execute()
        
        if stats_response.data and len(stats_response.data) > 0:
            status["stats"] = stats_response.data[0]
    except Exception as e:
        status["stats"]["error"] = str(e)
    
    # Temps de réponse total
    status["response_time"] = round(time.time() - start_time, 3)
    
    return func.HttpResponse(
        json.dumps(status),
        mimetype="application/json",
        status_code=200
    )

# test