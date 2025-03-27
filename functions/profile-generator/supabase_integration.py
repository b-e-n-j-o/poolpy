import uuid
import os
import json
import logging
import traceback
from datetime import datetime
from supabase import create_client

def init_supabase_client():
    """Initialise et retourne un client Supabase avec les credentials d'environnement"""
    try:
        # Récupérer les credentials depuis les variables d'environnement
        supabase_url = os.environ.get("SUPABASE_URL_DEV")
        supabase_key = os.environ.get("SUPABASE_KEY_DEV")
        
        if not supabase_url or not supabase_key:
            logging.error("Variables d'environnement SUPABASE_URL ou SUPABASE_KEY manquantes")
            return None
        
        # Créer et retourner le client
        client = create_client(supabase_url, supabase_key)
        return client
    
    except Exception as e:
        logging.error(f"Erreur lors de l'initialisation du client Supabase: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def store_profiles_to_supabase(profile_data):
    """
    Stocke les profils personnels, recherchés et la conversation dans Supabase
    
    Args:
        profile_data (dict): Les données de profil générées
        
    Returns:
        dict: Un dictionnaire avec les clés success, message et details
    """
    try:
        # Initialiser le client Supabase
        supabase = init_supabase_client()
        if not supabase:
            return {
                "success": False,
                "message": "Échec de l'initialisation du client Supabase",
                "details": "Variables d'environnement manquantes ou erreur de connexion"
            }
        
        # Extraire les données
        conversation_id = profile_data.get("conversation_id")
        user_id = profile_data.get("user_id")
        phone_number = profile_data.get("phone_number")
        personal_profile = profile_data.get("personal_profile", {})
        desired_profile = profile_data.get("desired_profile", {})
        transcript = profile_data.get("transcript", {})
        
        if not user_id:
            return {
                "success": False,
                "message": "ID utilisateur manquant dans les données de profil",
                "details": "Le champ user_id est requis"
            }
        
        # Générer un UUID basé sur user_id pour éviter les doublons
        user_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, user_id))
        
        # Insérer utilisateur s'il n'existe pas déjà
        try:
            users_data = {
                "id": user_uuid,
                "phone_number": phone_number,
                "created_at": datetime.now().isoformat(),
                "last_active": datetime.now().isoformat(),
                "status": "active",
                "onboarding_completed": True
            }
            user_result = supabase.table("users").upsert(users_data).execute()
            
            if not user_result.data or len(user_result.data) == 0:
                raise Exception(f"Échec de l'insertion utilisateur {user_id}")
                
            logging.info(f"Utilisateur {user_id} inséré avec UUID {user_uuid}")
        except Exception as user_error:
            logging.error(f"Erreur insertion utilisateur: {str(user_error)}")
            return {
                "success": False,
                "message": f"Échec insertion utilisateur: {str(user_error)}",
                "details": str(user_error)
            }
        
        # Préparer les données pour personal_profiles
        personal_profile_data = {
            "id": str(uuid.uuid4()),
            "user_id": user_uuid,
            "phone_number": personal_profile.get("phone_number", None),
            "name": personal_profile.get("name", None),
            "age": personal_profile.get("age", None),
            "location": personal_profile.get("location", None),
            "bio": personal_profile.get("bio", None),
            "relationship_looked_for": json.dumps(personal_profile.get("relationship_looked_for", {})) if personal_profile.get("relationship_looked_for") else None,
            "hobbies_activities": json.dumps(personal_profile.get("hobbies_activities", {})) if personal_profile.get("hobbies_activities") else None,
            "main_aspects": json.dumps(personal_profile.get("main_aspects", {})) if personal_profile.get("main_aspects") else None,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        # Préparer les données pour desired_profiles
        desired_profile_data = {
            "id": str(uuid.uuid4()),
            "user_id": user_uuid,
            "name": desired_profile.get("name", None),
            "age": desired_profile.get("age", None),
            "location_preference": desired_profile.get("location_preference", None),
            "bio": desired_profile.get("bio", None),
            "relationship_looked_for": json.dumps(desired_profile.get("relationship_looked_for", {})) if desired_profile.get("relationship_looked_for") else None,
            "hobbies_activities": json.dumps(desired_profile.get("hobbies_activities", {})) if desired_profile.get("hobbies_activities") else None,
            "main_aspects": json.dumps(desired_profile.get("main_aspects", {})) if desired_profile.get("main_aspects") else None,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }

        # Préparer les données pour conversations
        conversation_data = {
            "id": str(uuid.uuid4()),
            "user_id": user_uuid,
            "conversation_type": "latest",
            "content": json.dumps(transcript) if transcript else None,
            "profile_updates": json.dumps({
                "personal_profile_changes": [],
                "desired_profile_changes": []
            }),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        # Insérer dans personal_profiles
        personal_result = supabase.table("personal_profiles").upsert(personal_profile_data).execute()
        if not personal_result.data:
            raise Exception("Échec de l'insertion du profil personnel")
        
        # Insérer dans desired_profiles
        desired_result = supabase.table("desired_profiles").upsert(desired_profile_data).execute()
        if not desired_result.data:
            raise Exception("Échec de l'insertion du profil désiré")

        # Insérer dans conversations
        conversation_result = supabase.table("conversations").insert(conversation_data).execute()
        if not conversation_result.data:
            raise Exception("Échec de l'insertion de la conversation")
        
        logging.info(f"Profils et conversation stockés avec succès pour l'utilisateur {user_id} (UUID: {user_uuid})")
        return {
            "success": True, 
            "message": "Profils et conversation stockés avec succès",
            "details": {
                "personal_profile_id": personal_profile_data["id"],
                "desired_profile_id": desired_profile_data["id"],
                "conversation_id": conversation_data["id"],
                "user_uuid": user_uuid
            }
        }
    
    except Exception as e:
        error_message = f"Erreur lors du stockage des profils: {str(e)}"
        logging.error(error_message)
        logging.error(traceback.format_exc())
        return {
            "success": False,
            "message": error_message,
            "details": str(e)
        }

def convert_interests_to_array(interests_dict):
    """
    Convertit un dictionnaire d'intérêts en tableau pour Supabase
    
    Args:
        interests_dict (dict): Dictionnaire des intérêts où les clés sont les intérêts
                              et les valeurs sont les niveaux d'intérêt
    
    Returns:
        list: Liste des intérêts
    """
    if not interests_dict or not isinstance(interests_dict, dict):
        return []
    
    # Convertir en liste d'intérêts
    return list(interests_dict.keys())

def convert_to_array(value):
    """
    Convertit une valeur en tableau pour Supabase
    
    Args:
        value: La valeur à convertir en tableau
        
    Returns:
        list: La valeur convertie en tableau
    """
    if value is None:
        return []
    elif isinstance(value, list):
        return value
    else:
        return [value]

