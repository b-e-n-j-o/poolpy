from supabase import create_client
import os
import json
import openai
import azure.functions as func
import logging
from datetime import datetime

# Configuration
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_API_KEY")
openai.api_key = os.environ.get("OPENAI_API_KEY")
openai.api_type = "azure"
openai.api_base = os.environ.get("AZURE_OPENAI_ENDPOINT")
openai.api_version = "2024-02-01"
LLM_MODEL = "gpt-4o-mini"

supabase = create_client(supabase_url, supabase_key)

# Variable globale pour stocker les informations des derniers traitements
last_processed_data = {
    "last_update_time": None,
    "last_phone_number": None,
    "updates_performed": []
}

def main(message: func.ServiceBusMessage):
    global last_processed_data
    try:
        # Désérialiser le message
        message_body = message.get_body().decode('utf-8')
        transcript_data = json.loads(message_body)
        
        # Extraire les échanges structurés
        structured_exchanges = transcript_data.get("conversation_content", {}).get("structured_exchanges", [])
        # Extraire le numéro de téléphone (à adapter selon votre format)
        phone_number = extract_phone_number(structured_exchanges)
        
        # Récupérer les profils existants
        personal_profile, desired_profile = get_user_profiles(phone_number)
        
        if not personal_profile:
            return func.HttpResponse(
                json.dumps({"error": f"Aucun profil trouvé pour {phone_number}"}),
                status_code=404
            )
        
        # Construire le texte de la conversation pour le LLM
        conversation_text = "\n".join([f"{exchange['speaker']}: {exchange['text']}" for exchange in structured_exchanges])
        
        # Analyser et mettre à jour les profils
        personal_update_result = analyze_personal_profile(personal_profile, conversation_text)
        desired_update_result = analyze_desired_profile(desired_profile, conversation_text)
        
        # Appliquer les mises à jour si nécessaire
        update_results = {}
        
        if personal_update_result["update_needed"]:
            update_personal_profile(phone_number, personal_update_result["updated_profile"])
            update_results["personal_profile"] = personal_update_result["reasoning"]
        
        if desired_update_result["update_needed"]:
            update_desired_profile(phone_number, desired_update_result["updated_profile"])
            update_results["desired_profile"] = desired_update_result["reasoning"]
        
        # Mise à jour des infos de monitoring
        last_processed_data["last_update_time"] = datetime.now().isoformat()
        last_processed_data["last_phone_number"] = phone_number
        
        if personal_update_result["update_needed"] or desired_update_result["update_needed"]:
            last_processed_data["updates_performed"].append({
                "timestamp": datetime.now().isoformat(),
                "phone_number": phone_number,
                "personal_updated": personal_update_result["update_needed"],
                "desired_updated": desired_update_result["update_needed"]
            })
            # Limiter la liste à 10 éléments
            if len(last_processed_data["updates_performed"]) > 10:
                last_processed_data["updates_performed"] = last_processed_data["updates_performed"][-10:]
        
        # Stocker la conversation
        store_conversation(phone_number, structured_exchanges)
        
        return func.HttpResponse(
            json.dumps({"success": True, "updates": update_results}),
            status_code=200
        )
    
    except Exception as e:
        logging.error(f"Erreur: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500
        )

def extract_phone_number(structured_exchanges):
    try:
        # Récupérer le numéro depuis la requête Vapi avec customer.number
        if isinstance(structured_exchanges, dict):
            return structured_exchanges.get("customer", {}).get("number")
        
        logging.warning("Structure de données inattendue pour extraire le numéro de téléphone")
        return None
        
    except Exception as e:
        logging.error(f"Erreur lors de l'extraction du numéro de téléphone: {str(e)}")
        return None

def get_user_profiles(phone_number):
    # Récupérer l'ID utilisateur
    user_response = supabase.table("users").select("id").eq("phone_number", phone_number).execute()
    if len(user_response.data) == 0:
        return None, None
    
    user_id = user_response.data[0]["id"]
    
    # Récupérer le profil personnel complet
    personal_response = supabase.table("personal_profiles").select("*").eq("user_id", user_id).execute()
    personal_profile = personal_response.data[0] if personal_response.data else None
    
    # Récupérer le profil recherché complet
    desired_response = supabase.table("desired_profiles").select("*").eq("user_id", user_id).execute()
    desired_profile = desired_response.data[0] if desired_response.data else None
    
    return personal_profile, desired_profile

def analyze_personal_profile(current_profile, conversation_text):
    system_prompt = """Tu es un expert en analyse psychologique qui évalue les changements de profil utilisateur.
    Analyse cette conversation et compare-la avec le profil actuel. Identifie tout changement significatif dans:
    - Intérêts et passions
    - Traits de personnalité
    - Situation personnelle/professionnelle
    - Nouvelles informations pertinentes
    
    Ne modifie que les éléments explicitement ou implicitement mentionnés dans la conversation.
    Conserve toutes les informations du profil actuel qui restent valides.
    """
    
    user_prompt = f"""
    PROFIL PERSONNEL ACTUEL:
    {json.dumps(current_profile, indent=2)}
    
    NOUVELLE CONVERSATION:
    {conversation_text}
    
    Retourne un JSON avec:
    {{"update_needed": boolean, "updated_profile": object, "reasoning": string}}
    """
    
    response = openai.ChatCompletion.create(
        engine=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2,
        max_tokens=2000,
        response_format={"type": "json_object"}
    )
    
    return json.loads(response.choices[0].message.content)

def analyze_desired_profile(current_profile, conversation_text):
    system_prompt = """Tu es un expert en analyse des préférences relationnelles.
    Analyse cette conversation et compare-la avec le profil de recherche actuel. Identifie tout changement dans:
    - Type de relation recherchée
    - Caractéristiques souhaitées chez l'autre
    - Critères importants/dealbreakers
    - Activités partagées souhaitées
    
    Conserve toutes les informations du profil actuel qui restent valides.
    """
    
    user_prompt = f"""
    PROFIL RECHERCHÉ ACTUEL:
    {json.dumps(current_profile, indent=2)}
    
    NOUVELLE CONVERSATION:
    {conversation_text}
    
    Retourne un JSON avec:
    {{"update_needed": boolean, "updated_profile": object, "reasoning": string}}
    """
    
    response = openai.ChatCompletion.create(
        engine=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2,
        max_tokens=2000,
        response_format={"type": "json_object"}
    )
    
    return json.loads(response.choices[0].message.content)

def update_personal_profile(phone_number, updated_profile):
    # Récupérer l'ID utilisateur
    user_response = supabase.table("users").select("id").eq("phone_number", phone_number).execute()
    user_id = user_response.data[0]["id"]
    
    # Archiver l'ancien profil (optionnel - à implémenter si besoin)
    # archive_personal_profile(user_id)
    
    # Mettre à jour le profil personnel
    supabase.table("personal_profiles").update(updated_profile).eq("user_id", user_id).execute()

def update_desired_profile(phone_number, updated_profile):
    # Récupérer l'ID utilisateur
    user_response = supabase.table("users").select("id").eq("phone_number", phone_number).execute()
    user_id = user_response.data[0]["id"]
    
    # Mettre à jour le profil recherché
    supabase.table("desired_profiles").update(updated_profile).eq("user_id", user_id).execute()

def store_conversation(phone_number, structured_exchanges):
    # Récupérer l'ID utilisateur
    user_response = supabase.table("users").select("id").eq("phone_number", phone_number).execute()
    user_id = user_response.data[0]["id"]
    
    # Stocker la conversation
    supabase.table("conversations").insert({
        "user_id": user_id,
        "transcript": structured_exchanges,
        "created_at": "now()"
    }).execute()

@func.FunctionApp.route(route="status", auth_level=func.AuthLevel.ANONYMOUS)
def get_status(req: func.HttpRequest) -> func.HttpResponse:
    """
    Endpoint pour vérifier le statut de la fonction et obtenir les dernières mises à jour
    """
    return func.HttpResponse(
        body=json.dumps({
            "status": "running",
            "version": "1.0",
            "last_processing": last_processed_data
        }, indent=2),
        mimetype="application/json",
        status_code=200
    )

#test
