import azure.functions as func
import json
from datetime import datetime
import os
from supabase import create_client
import openai
import logging

app = func.FunctionApp()

# Configuration Supabase
supabase_url = os.environ.get("SUPABASE_URL_DEV")
supabase_key = os.environ.get("SUPABASE_KEY_DEV")
supabase = create_client(supabase_url, supabase_key)

# Configuration OpenAI
openai.api_key = os.environ.get("AZURE_OPENAI_KEY")
openai.api_type = "azure"
openai.api_base = os.environ.get("AZURE_OPENAI_ENDPOINT")
openai.api_version = "2024-02-01"
LLM_MODEL = "gpt-4o-mini"

# Ajouter cette variable globale au début du fichier avec les autres configurations
last_processing_data = {
    "last_update": None,
    "last_profiles_updated": None,
    "last_conversation_stored": None,
    "recent_updates": []  # Garder un historique des dernières mises à jour
}

@app.function_name(name="hello")
@app.route(route="hello", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
def hello_function(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"message": "Bonjour, la fonction marche !"}),
        mimetype="application/json"
    )

@app.function_name(name="test_supabase")
@app.route(route="test-supabase", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
def test_supabase(req: func.HttpRequest) -> func.HttpResponse:
    try:
        # Test simple de connexion à Supabase
        response = supabase.table("users").select("count").execute()
        return func.HttpResponse(
            json.dumps({
                "status": "success",
                "message": "Connexion Supabase OK",
                "data": response.data
            }),
            mimetype="application/json"
        )
    except Exception as e:
        logging.error(f"Erreur Supabase: {str(e)}")
        return func.HttpResponse(
            json.dumps({
                "status": "error",
                "message": str(e)
            }),
            status_code=500,
            mimetype="application/json"
        )

@app.function_name(name="test_openai")
@app.route(route="test-openai", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
def test_openai(req: func.HttpRequest) -> func.HttpResponse:
    try:
        # Test simple d'OpenAI
        response = openai.ChatCompletion.create(
            engine=LLM_MODEL,
            messages=[
                {"role": "user", "content": "Dis bonjour"}
            ],
            temperature=0.2,
            max_tokens=100
        )
        return func.HttpResponse(
            json.dumps({
                "status": "success",
                "message": "Connexion OpenAI OK",
                "response": response.choices[0].message.content
            }),
            mimetype="application/json"
        )
    except Exception as e:
        logging.error(f"Erreur OpenAI: {str(e)}")
        return func.HttpResponse(
            json.dumps({
                "status": "error",
                "message": str(e)
            }),
            status_code=500,
            mimetype="application/json"
        )

@app.function_name(name="profile_update")
@app.route(route="profile-update", auth_level=func.AuthLevel.ANONYMOUS, methods=["POST"])
def profile_updater(req: func.HttpRequest) -> func.HttpResponse:
    global last_processing_data
    try:
        logging.info("Début du traitement de la requête")
        logging.info(f"Headers reçus: {dict(req.headers)}")  # Debug des headers
        
        # Récupérer le corps de la requête
        data = req.get_json()
        logging.info(f"Données reçues: {json.dumps(data, ensure_ascii=False)[:200]}...")  # Debug des données
        
        # Extraire le numéro de téléphone directement des métadonnées
        phone_number = data.get("call_metadata", {}).get("customer_phone")
        logging.info(f"Numéro de téléphone extrait: {phone_number}")  # Debug du numéro
        
        structured_exchanges = data.get("conversation_content", {}).get("structured_exchanges", [])
        
        if not phone_number:
            return func.HttpResponse(
                json.dumps({
                    "error": "Numéro de téléphone non trouvé",
                    "data_received": {
                        "call_metadata": data.get("call_metadata"),
                        "data_structure": str(type(data))
                    }
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        # Récupérer les profils
        user_response = supabase.table("users").select("id").eq("phone_number", phone_number).execute()
        if not user_response.data:
            return func.HttpResponse(
                json.dumps({"error": "Utilisateur non trouvé"}),
                status_code=404,
                mimetype="application/json"
            )
        
        user_id = user_response.data[0]["id"]
        
        # Récupérer les profils existants
        personal_response = supabase.table("personal_profiles").select("*").eq("user_id", user_id).execute()
        desired_response = supabase.table("desired_profiles").select("*").eq("user_id", user_id).execute()
        
        personal_profile = personal_response.data[0] if personal_response.data else None
        desired_profile = desired_response.data[0] if desired_response.data else None
        
        # Analyser avec OpenAI
        conversation_text = "\n".join([
            f"{exchange['speaker']}: {exchange['text']}" 
            for exchange in structured_exchanges
        ])
        
        # Analyser les changements potentiels
        personal_update_result = analyze_personal_profile(personal_profile, conversation_text)
        desired_update_result = analyze_desired_profile(desired_profile, conversation_text)
        
        # Préparer les mises à jour
        profile_updates = {
            "personal_profile_changes": [],
            "desired_profile_changes": []
        }

        if personal_update_result["update_needed"]:
            # Enlever les champs spéciaux que Supabase ne devrait pas mettre à jour
            updated_profile = personal_update_result["updated_profile"]
            if "id" in updated_profile:
                del updated_profile["id"]
            if "created_at" in updated_profile:
                del updated_profile["created_at"]
            
            supabase.table("personal_profiles").update(updated_profile).eq("user_id", user_id).execute()
            profile_updates["personal_profile_changes"] = personal_update_result["reasoning"]["modifications"]

        if desired_update_result["update_needed"]:
            supabase.table("desired_profiles").update(
                desired_update_result["updated_profile"]
            ).eq("user_id", user_id).execute()
            profile_updates["desired_profile_changes"] = desired_update_result["reasoning"]["modifications"]

        # Stocker la conversation
        supabase.table("conversations").insert({
            "user_id": user_id,
            "conversation_type": "history",  # Ajout de ce champ
            "content": structured_exchanges,    # Au lieu de 'transcript'
            "profile_updates": profile_updates,  # Au lieu de 'updates_applied'
            "created_at": "now()"
            # "updated_at" sera probablement géré automatiquement
        }).execute()
        
        # Après les mises à jour des profils, avant le return
        last_processing_data.update({
            "last_update": datetime.now().isoformat(),
            "last_profiles_updated": {
                "user_id": user_id,
                "personal_profile_updated": personal_update_result["update_needed"],
                "desired_profile_updated": desired_update_result["update_needed"]
            },
            "last_conversation_stored": {
                "timestamp": datetime.now().isoformat(),
                "exchange_count": len(structured_exchanges)
            }
        })

        # Garder un historique des dernières mises à jour
        last_processing_data["recent_updates"].append({
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "updates": profile_updates
        })
        
        # Limiter la taille de l'historique à 5 entrées
        if len(last_processing_data["recent_updates"]) > 5:
            last_processing_data["recent_updates"] = last_processing_data["recent_updates"][-5:]

        return func.HttpResponse(
            json.dumps({
                "status": "success",
                "updates": profile_updates
            }),
            mimetype="application/json"
        )
        
    except Exception as e:
        logging.error(f"Erreur: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )

def analyze_personal_profile(current_profile, conversation_text):
    system_prompt = f"""Tu es un expert en analyse psychologique chargé d'évaluer et mettre à jour le profil d'un utilisateur.
    
    Ta mission est d'analyser la nouvelle conversation et de mettre à jour le profil existant en JSON :
    - Conservant toutes les informations pertinentes du profil actuel qui ne sont pas contredites
    - Ajoutant les nouvelles informations découvertes
    - Modifiant les éléments qui ont évolué
    - Supprimant les éléments qui ne sont plus valides

    Points d'attention particuliers :
    - Traits de personnalité spécifiques et nuancés
    - Centres d'intérêt précis (ex: styles de musique particuliers, activités favorites)
    - Éléments distinctifs qui définissent la personne
    - Façon d'interagir avec les autres
    - Valeurs et convictions personnelles
    - Expériences de vie significatives
    - Préférences et habitudes quotidiennes
    - Aspirations et objectifs

    RÈGLES IMPORTANTES :
    - Ne fais pas de généralisations
    - Reste fidèle aux informations disponibles sans inventer
    - Conserve les détails précis qui rendent le profil unique
    - Justifie chaque modification avec une citation exacte de la conversation
    - Adopte un ton objectif mais bienveillant
    
    Format de réponse attendu en JSON :
    {{
        "update_needed": boolean,
        "updated_profile": {{
            // Profil complet et détaillé, incluant anciennes et nouvelles informations
        }},
        "reasoning": {{
            "modifications": [
                {{
                    "aspect": "nom_aspect",
                    "ancien": "description_précédente",
                    "nouveau": "nouvelle_description",
                    "justification": "citation_conversation"
                }}
            ],
            "explications": "analyse détaillée des changements et de leur pertinence"
        }}
    }}
    """

    user_prompt = f"""
    PROFIL ACTUEL:
    {json.dumps(current_profile, indent=2)}

    NOUVELLE CONVERSATION:
    {conversation_text}

    Analyse cette conversation et mets à jour le profil en conservant sa richesse et sa précision. Réponds en format JSON.
    """
    
    client = openai.AzureOpenAI(
        api_key=os.environ.get("AZURE_OPENAI_KEY"),
        api_version=openai.api_version,
        azure_endpoint=openai.api_base
    )
    
    response = client.chat.completions.create(
        model=LLM_MODEL,
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
    system_prompt = f"""Tu es un expert en analyse des préférences relationnelles chargé d'évaluer et mettre à jour les critères de recherche d'un utilisateur.
    
    Ta mission est d'analyser la nouvelle conversation et de mettre à jour le profil recherché en JSON :
    - Conservant tous les critères pertinents qui ne sont pas contredits
    - Ajoutant les nouveaux critères découverts
    - Modifiant les préférences qui ont évolué
    - Supprimant les critères qui ne sont plus valides

    Points d'attention particuliers :
    - Type de relation précisément recherchée
    - Caractéristiques spécifiques souhaitées (pas de généralités)
    - Critères non négociables détaillés
    - Valeurs essentielles à partager
    - Modes de vie et habitudes compatibles
    - Centres d'intérêt à partager
    - Vision de la relation et projets communs
    - Attentes en termes d'interaction et de communication

    RÈGLES IMPORTANTES :
    - Capture les nuances et les priorités exprimées
    - Reste fidèle aux souhaits exprimés sans interprétation excessive
    - Conserve les détails précis qui définissent les attentes
    - Justifie chaque modification avec une citation exacte
    - Maintiens un équilibre entre aspirations et réalisme
    
    Format de réponse attendu en JSON :
    {{
        "update_needed": boolean,
        "updated_profile": {{
            // Profil recherché complet et détaillé
        }},
        "reasoning": {{
            "modifications": [
                {{
                    "critère": "nom_critère",
                    "ancien": "description_précédente",
                    "nouveau": "nouvelle_description",
                    "justification": "citation_conversation"
                }}
            ],
            "explications": "analyse détaillée des évolutions dans les critères de recherche"
        }}
    }}
    """
    
    user_prompt = f"""
    PROFIL RECHERCHÉ ACTUEL:
    {json.dumps(current_profile, indent=2)}
    
    NOUVELLE CONVERSATION:
    {conversation_text}
    """
    
    client = openai.AzureOpenAI(
        api_key=os.environ.get("AZURE_OPENAI_KEY"),
        api_version=openai.api_version,
        azure_endpoint=openai.api_base
    )
    
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2,
        max_tokens=2000,
        response_format={"type": "json_object"}
    )
    
    return json.loads(response.choices[0].message.content)

@app.function_name(name="monitor")
@app.route(route="monitor", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
def monitor(req: func.HttpRequest) -> func.HttpResponse:
    try:
        # Récupérer les dernières conversations stockées
        recent_conversations = supabase.table("conversations")\
            .select("*")\
            .order("created_at", desc=True)\
            .limit(5)\
            .execute()

        # Récupérer les dernières mises à jour de profils
        recent_profile_updates = supabase.table("personal_profiles")\
            .select("id, user_id, updated_at")\
            .order("updated_at", desc=True)\
            .limit(5)\
            .execute()

        monitoring_data = {
            "status": "running",
            "last_processing": last_processing_data,
            "database_status": {
                "recent_conversations": recent_conversations.data if recent_conversations.data else [],
                "recent_profile_updates": recent_profile_updates.data if recent_profile_updates.data else [],
            },
            "service_status": {
                "supabase": "connected" if recent_conversations.data is not None else "error",
                "openai": LLM_MODEL,
            }
        }

        return func.HttpResponse(
            json.dumps(monitoring_data, indent=2, default=str),
            mimetype="application/json"
        )
    except Exception as e:
        logging.error(f"Erreur monitoring: {str(e)}")
        return func.HttpResponse(
            json.dumps({
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }),
            status_code=500,
            mimetype="application/json"
        )