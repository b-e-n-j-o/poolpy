import os
import json
import logging
import azure.functions as func
import traceback
from openai import AzureOpenAI
from typing import Dict, Any, List, Tuple, Optional
from tqdm import tqdm
import time
import openai

# Import du module d'intégration Supabase
from supabase_integration import store_profiles_to_supabase as store_profiles

app = func.FunctionApp()

# Au début de la fonction profile_generator
logging.info("Vérification des variables d'environnement Supabase...")
supabase_url = os.environ.get("SUPABASE_URL_DEV")
supabase_key = os.environ.get("SUPABASE_KEY_DEV")
if not supabase_url or not supabase_key:
    logging.warning("Variables d'environnement Supabase manquantes")

@app.route(route="health", auth_level=func.AuthLevel.ANONYMOUS)
async def health_check(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps({"status": "ok", "message": "Fonction en ligne"}),
        mimetype="application/json",
        status_code=200
    )

class TranscriptAnalyzer:
    """
    Agent d'analyse de transcript qui extrait les informations structurées à partir
    d'une conversation pour les adapter au schéma de base de données cible.
    """
    
    def __init__(self, azure_openai_client, deployment_id: str):
        """
        Initialise l'analyseur de transcript.
        
        Args:
            azure_openai_client: Client Azure OpenAI déjà initialisé
            deployment_id: ID du déploiement Azure OpenAI
        """
        self.client = azure_openai_client
        self.model_name = deployment_id
        self.total_input_tokens = 0
        self.total_output_tokens = 0
    
    def count_tokens(self, text: str) -> int:
        """Estime approximativement le nombre de tokens dans un texte."""
        # Estimation grossière: environ 4 caractères par token en moyenne
        return len(text) // 4
    
    def log_prompt_stats(self, prompt: str, response: str):
        """Version simplifiée des statistiques d'utilisation des tokens."""
        # Estimation approximative des tokens
        input_tokens = self.count_tokens(prompt)
        output_tokens = self.count_tokens(response)
        
        # Cumuler les tokens
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        
        logging.info(f"Tokens estimés en entrée: ~{input_tokens}")
        logging.info(f"Tokens estimés en sortie: ~{output_tokens}")
        logging.info(f"Total tokens estimés: ~{input_tokens + output_tokens}")
    
    def format_transcript_for_analysis(self, vapi_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transforme les données du format VAPI au format attendu par l'analyseur.
        """
        # Extraire les informations de base
        call_id = vapi_data["call_metadata"]["call_id"]
        customer_phone = vapi_data["call_metadata"].get("customer_phone")  # Récupération du numéro de téléphone
        
        # Extraire le nom de l'utilisateur des échanges (premier message de l'utilisateur)
        user_name = None
        for exchange in vapi_data["conversation_content"]["structured_exchanges"]:
            if exchange["speaker"] == "user":
                # Extraction basique du nom depuis la présentation
                text = exchange["text"].lower()
                if "m'appelle" in text:
                    parts = text.split("m'appelle")
                    if len(parts) > 1:
                        user_name = parts[1].strip().rstrip(',.!?').split()[0]
                        break
                elif "my name is" in text:
                    parts = text.split("my name is")
                    if len(parts) > 1:
                        user_name = parts[1].strip().rstrip(',.!?').split()[0]
                        break
        
        # Si nom non trouvé, utiliser un nom par défaut
        if not user_name:
            user_name = "Utilisateur"
        
        # Structurer les échanges de la conversation
        transcript = []
        for exchange in vapi_data["conversation_content"]["structured_exchanges"]:
            speaker = exchange["speaker"]  # Déjà au bon format "agent" ou "user"
            transcript.append({
                "speaker": speaker,
                "text": exchange["text"],
                "timestamp": exchange["timestamp"]
            })
        
        # Créer le format attendu par les méthodes d'analyse
        formatted_data = {
            "conversation_id": call_id,
            "user_name": user_name,
            "timestamp": vapi_data["call_metadata"]["start_time"],
            "call_metadata": {
                "customer_phone": customer_phone  # Ajout du numéro de téléphone dans les métadonnées
            },
            "transcript": transcript,
            "metadata": {
                "detected_languages": [vapi_data["technical_settings"]["transcriber"]["language"]]
            }
        }
        
        return formatted_data
    
    def extract_personal_profile(self, transcript_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extrait les informations pour le profil personnel de l'utilisateur.
        """
        logging.info("Préparation des données du transcript...")
        user_name = transcript_data.get("user_name", "")
        transcript = transcript_data.get("transcript", [])
        
        conversation = []
        for msg in transcript:
            speaker = "Assistant" if msg.get("speaker") == "agent" else "Utilisateur"
            conversation.append(f"{speaker}: {msg.get('text', '')}")
        
        conversation_text = "\n".join(conversation)
        
        # Mise à jour du prompt pour correspondre à la nouvelle structure
        prompt = f"""
        Analyse la conversation suivante entre un assistant vocal et un utilisateur:

        {conversation_text}

        À partir de cette conversation, extrait les informations pour créer un profil personnel de l'utilisateur.
        Pour le champ relationship_looked_for, tu peux choisir UN ou PLUSIEURS types parmi :
        - FRIENDSHIP : recherche d'amis, connexions sociales, nouvelles rencontres amicales
        - ROMANTIC : recherche de relations amoureuses, rencontres sentimentales
        - ACTIVITY_PARTNER : recherche de partenaires pour des activités spécifiques (sport, loisirs, sorties)
        - PROFESSIONAL : networking, connexions professionnelles, collaborations business
        - OTHER : si d'autres types de relations sont mentionnés

        Réponds au format JSON strict avec les champs suivants:

        {{
          "name": "{user_name}",
          "age": null,
          "location": null,
          "phone_number": null,
          "relationship_looked_for": {{
            "types": ["FRIENDSHIP", "ROMANTIC", "ACTIVITY_PARTNER", ...],  // Un ou plusieurs types possibles
            "primary_type": "TYPE_PRINCIPAL",  // Le type qui semble prioritaire
            "descriptions": {{                 // Description pour chaque type sélectionné
              "FRIENDSHIP": "Description des attentes amicales",
              "ROMANTIC": "Description des attentes amoureuses",
              ...
            }},
            "additional_context": []
          }},
          "hobbies_activities": {{
            "hobbies": [],
            "activities": [],
            "passions": []
          }},
          "key_traits": {{
            "personality": [],
            "lifestyle": [],
            "values": []
          }}
        }}

        Important: 
        - Le champ 'types' peut contenir plusieurs valeurs de l'énumération
        - Chaque type sélectionné doit avoir sa description dans 'descriptions'
        - 'primary_type' doit être le type qui semble le plus important pour l'utilisateur
        """
        
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "Tu es un ami bienveillant et intuitif qui a un vrai talent pour comprendre les gens et faire des présentations qui créent des connexions authentiques. Tu sais naturellement capter l'essence de ce que recherchent les gens dans leurs relations et l'exprimer de façon chaleureuse et sincère, comme lors d'une conversation entre amis."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )
            
            response_content = response.choices[0].message.content
            self.log_prompt_stats(prompt, response_content)
            
            return json.loads(response_content)
        except Exception as e:
            logging.error(f"Erreur lors de l'extraction du profil personnel: {str(e)}")
            return {
                "name": user_name,
                "error": str(e)
            }
    
    def extract_desired_profile(self, transcript_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extrait les informations pour le profil recherché par l'utilisateur.
        """
        user_name = transcript_data.get("user_name", "")
        transcript = transcript_data.get("transcript", [])
        
        conversation = []
        for msg in transcript:
            speaker = "Assistant" if msg.get("speaker") == "agent" else "Utilisateur"
            conversation.append(f"{speaker}: {msg.get('text', '')}")
        
        conversation_text = "\n".join(conversation)
        
        # Mise à jour du prompt pour correspondre à la nouvelle structure
        prompt = f"""
        Analyse la conversation suivante entre un assistant vocal et un utilisateur:

        {conversation_text}

        À partir de cette conversation, extrait les informations sur ce que l'utilisateur recherche.
        Pour le champ relationship_looked_for, choisis UNIQUEMENT parmi ces types de relations :
        - FRIENDSHIP : recherche d'amis, connexions sociales, nouvelles rencontres amicales
        - ROMANTIC : recherche de relations amoureuses, rencontres sentimentales
        - ACTIVITY_PARTNER : recherche de partenaires pour des activités spécifiques (sport, loisirs, sorties)
        - PROFESSIONAL : networking, connexions professionnelles, collaborations business
        - OTHER : si aucune des catégories ci-dessus ne correspond clairement

        Réponds au format JSON strict avec les champs suivants:

        {{
          "name": null,
          "age": null,
          "location_preference": null,
          "relationship_looked_for": {{
            "type": "FRIENDSHIP|ROMANTIC|ACTIVITY_PARTNER|PROFESSIONAL|OTHER",
            "description": "Brève description du type de relation recherchée",
            "additional_context": []
          }},
          "hobbies_activities": {{
            "interests": [],
            "preferred_activities": []
          }},
          "main_aspects": {{
            "desired_traits": [],
            "important_values": []
          }}
        }}

        Important: Le champ 'type' dans relationship_looked_for DOIT être une des valeurs énumérées ci-dessus, en majuscules.
        """
        
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "Tu es un expert en analyse de profils qui comprend parfaitement ce que les gens recherchent dans leurs relations."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )
            
            response_content = response.choices[0].message.content
            self.log_prompt_stats(prompt, response_content)
            
            return json.loads(response_content)
        except Exception as e:
            logging.error(f"Erreur lors de l'extraction du profil recherché: {str(e)}")
            return {
                "name": None,
                "error": str(e)
            }
    
    def get_user_messages(self, transcript_data: Dict[str, Any]) -> str:
        """Extrait et formate les messages de l'utilisateur avec leurs timestamps."""
        user_interactions = []
        
        for msg in transcript_data.get("transcript", []):
            if msg.get("speaker") == "user":
                timestamp = msg.get("timestamp", "")
                text = msg.get("text", "")
                user_interactions.append(f"[{timestamp}] {text}")
        
        # Joindre tous les messages avec des sauts de ligne
        chronological_conversation = "\n".join(user_interactions)
        
        return f"""
MESSAGES DE L'UTILISATEUR (par ordre chronologique):
{chronological_conversation}
"""
    
    def generate_personal_narrative(self, personal_profile: Dict[str, Any], transcript_data: Dict[str, Any]) -> str:
        """
        Génère un résumé narratif du profil personnel.
        """
        user_name = transcript_data.get("user_name", "")
        user_messages = self.get_user_messages(transcript_data)
        
        # Mise à jour du prompt pour mieux utiliser la nouvelle structure
        prompt = f"""
        Je vais te donner deux sources d'information sur {user_name} :
        1. Des informations structurées extraites de la conversation
        2. L'historique chronologique des messages de la personne

        INFORMATIONS STRUCTURÉES :
        {json.dumps(personal_profile, indent=2, ensure_ascii=False)}

        MESSAGES ORIGINAUX :
        {user_messages}

        Génère un résumé clair et informatif de ce profil en 2-3 paragraphes.

        Consignes :
        - Présente fidèlement les informations sans exagération ni invention
        - Inclus les traits de personnalité, centres d'intérêt et particularités mentionnés
        - Adopte un ton positif mais objectif
        - Organise les informations de façon logique et fluide
        - Utilise un style simple, direct et accessible
        - Aussi inclus si besoin des éléments très précis qui peuvent parfois rapprocher les gens comme le style de musique ou quelque chose de préféré chez la personne
        - Ne généralise pas certains traits ou certains points, essaie de décrire {user_name} le mieux possible, le but de l'analyse est de connaître {user_name} et de savoir précisément qui il est.

        Le résumé doit donner une image précise de qui est cette personne, ce qui la caractérise, 
        et comment elle interagit avec les autres, en te basant uniquement sur les données disponibles.
        Parle de manière à décrire {user_name} le mieux possible, et non pas de manière à faire une présentation.
        """
        
        logging.info("Envoi du prompt pour la génération du résumé personnel")
        
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "Tu es un ami proche qui a un don pour parler des gens avec bienveillance et authenticité. Tu sais capter ce qui rend chaque personne unique et spéciale, et le partager de façon naturelle et engageante, comme lors d'une conversation sincère entre amis."},
                    {"role": "user", "content": prompt}
                ]
            )
            
            response_content = response.choices[0].message.content
            self.log_prompt_stats(prompt, response_content)
            
            return response_content.strip()
        except Exception as e:
            logging.error(f"Erreur lors de la génération du résumé personnel: {str(e)}")
            return f"Erreur lors de la génération du résumé: {str(e)}"
    
    def generate_desired_narrative(self, desired_profile: Dict[str, Any], transcript_data: Dict[str, Any]) -> str:
        """
        Génère un résumé narratif du profil recherché.
        """
        user_name = transcript_data.get("user_name", "")
        
        # Récupération des messages de l'utilisateur
        user_messages = []
        for msg in transcript_data.get("transcript", []):
            if msg.get("speaker") == "user":
                timestamp = msg.get("timestamp", "")
                text = msg.get("text", "")
                user_messages.append(f"[{timestamp}] {text}")
        conversation_text = "\n".join(user_messages)
        
        # Création du prompt pour la génération du résumé narratif
        prompt = f"""
        Je vais te donner deux sources d'information sur ce que {user_name} recherche :
        1. Les informations structurées extraites de l'analyse
        2. Les messages originaux de la conversation

        INFORMATIONS STRUCTURÉES :
        {json.dumps(desired_profile, indent=2, ensure_ascii=False)}

        CONVERSATION ORIGINALE :
        {conversation_text}

        En utilisant ces deux sources, décris naturellement le genre de connexions et de personnes 
        qui pourraient vraiment correspondre à {user_name}.

        Points importants :
        - Utilise les mots et expressions employés par la personne elle-même
        - Capte les nuances et les priorités qui ressortent de la conversation
        - Note ce qui semble vraiment important ou récurrent dans ses attentes
        - Fais ressortir sa vision des relations et des connexions qu'elle souhaite
        - Intègre subtilement les critères plus factuels (âge, localisation, etc.)
        - Ne généralise pas certains traits ou certains points, essaie de décrire ce que {user_name} recherche le mieux possible
        - Le but est de vraiment comprendre les attentes spécifiques de {user_name}

        Le résumé doit sonner comme une conversation naturelle où tu expliques à un ami 
        le genre de personnes avec qui {user_name} pourrait vraiment bien s'entendre.
        
        Important : Évite le style "recherche" ou "critères". Garde un ton chaleureux et authentique,
        comme si tu présentais naturellement deux personnes qui pourraient bien s'entendre.
        """
        
        logging.info("Envoi du prompt pour la génération du résumé des préférences")
        
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "Tu es un ami intuitif qui a un don pour comprendre ce que les gens recherchent vraiment dans leurs relations. Tu sais lire entre les lignes et capter les aspirations profondes des gens, au-delà des simples critères. Tu as le talent de présenter ces attentes de façon naturelle et bienveillante."},
                    {"role": "user", "content": prompt}
                ]
            )
            
            response_content = response.choices[0].message.content
            self.log_prompt_stats(prompt, response_content)
            
            return response_content.strip()
        except Exception as e:
            logging.error(f"Erreur lors de la génération du résumé des préférences: {str(e)}")
            return f"Erreur lors de la génération du résumé: {str(e)}"
    
    def process_vapi_data(self, vapi_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Point d'entrée principal pour traiter les données VAPI brutes.
        
        Args:
            vapi_data: Données reçues de l'Azure Function webhook
            
        Returns:
            Profils générés à partir de l'analyse
        """
        # Reformater les données au format attendu par les méthodes d'analyse
        formatted_data = self.format_transcript_for_analysis(vapi_data)
        
        # Traiter le transcript formaté
        return self.process_transcript(formatted_data)
    
    def process_transcript(self, transcript_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite l'ensemble du transcript pour extraire les profils et générer les résumés.
        """
        logging.info(f"Début du traitement du transcript pour l'utilisateur: {transcript_data.get('user_name', 'Inconnu')}")
        
        try:
            # Extraction du profil personnel
            logging.info("Extraction du profil personnel")
            personal_profile = self.extract_personal_profile(transcript_data)
            
            # Extraction du profil recherché
            logging.info("Extraction du profil recherché")
            desired_profile = self.extract_desired_profile(transcript_data)
            
            # Génération du résumé narratif personnel
            logging.info("Génération du résumé personnel")
            personal_narrative = self.generate_personal_narrative(personal_profile, transcript_data)
            
            # Reformater le profil personnel selon la nouvelle structure
            personal_profile_formatted = {
                "name": transcript_data.get("user_name"),
                "age": personal_profile.get("age"),
                "location": personal_profile.get("location"),
                "bio": personal_narrative,
                "relationship_looked_for": personal_profile.get("relationship_looked_for", {}),
                "hobbies_activities": personal_profile.get("hobbies_activities", {}),
                "main_aspects": personal_profile.get("key_traits", {})
            }
            
            # Génération du résumé narratif des préférences
            logging.info("Génération du résumé des préférences")
            desired_narrative = self.generate_desired_narrative(desired_profile, transcript_data)
            
            # Reformater le profil désiré selon la nouvelle structure
            desired_profile_formatted = {
                "name": desired_profile.get("name"),
                "age": desired_profile.get("age_range"),
                "location_preference": desired_profile.get("location_preference"),
                "bio": desired_narrative,
                "relationship_looked_for": desired_profile.get("relationship_looked_for", {}),
                "hobbies_activities": desired_profile.get("hobbies_activities", {}),
                "main_aspects": desired_profile.get("main_aspects", {})
            }
            
            # Créer l'objet de réponse complet avec le transcript et le numéro de téléphone
            result = {
                "conversation_id": transcript_data.get("conversation_id", ""),
                "user_id": transcript_data.get("user_name", "").lower(),
                "phone_number": transcript_data.get("call_metadata", {}).get("customer_phone"),  # Ajout du numéro de téléphone
                "personal_profile": personal_profile_formatted,
                "desired_profile": desired_profile_formatted,
                "transcript": transcript_data.get("transcript", []),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "token_usage": {
                    "input_tokens": self.total_input_tokens,
                    "output_tokens": self.total_output_tokens,
                    "total_tokens": self.total_input_tokens + self.total_output_tokens
                }
            }
            
            # Stocker les profils dans Supabase
            try:
                storage_result = store_profiles(result)
                result["storage_status"] = storage_result
            except Exception as e:
                logging.error(f"Erreur lors du stockage des profils: {str(e)}")
                result["storage_status"] = {
                    "success": False,
                    "message": "Erreur lors du stockage des profils",
                    "details": str(e)
                }
            
            return result
            
        except Exception as e:
            logging.error(f"Erreur lors du traitement du transcript: {str(e)}")
            return {
                "error": f"Échec du traitement: {str(e)}",
                "conversation_id": transcript_data.get("conversation_id", ""),
                "user_id": transcript_data.get("user_name", "").lower(),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }

# Variable globale pour stocker le client OpenAI
openai_client = None
last_generated_profiles = None

# Initialiser le client Azure OpenAI
def get_openai_client():
    try:
        logging.info("Tentative d'initialisation du client OpenAI...")
        logging.info(f"OpenAI version: {openai.__version__}")
        
        # Récupérer les variables d'environnement
        api_key = os.environ.get("AZURE_OPENAI_KEY")
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        
        logging.info(f"Variables d'env récupérées: endpoint={endpoint is not None}, key={api_key is not None}")
        
        # Créer le client avec les variables d'environnement
        client = AzureOpenAI(
            api_key=api_key,
            api_version="2024-02-15-preview",
            azure_endpoint=endpoint
        )
        
        logging.info("Client OpenAI initialisé avec succès!")
        return client
    except Exception as e:
        logging.error(f"Erreur détaillée lors de l'initialisation du client Azure OpenAI: {str(e)}")
        logging.error(traceback.format_exc())
        raise

@app.route(route="profile-generator", auth_level=func.AuthLevel.ANONYMOUS)
async def profile_generator(req: func.HttpRequest) -> func.HttpResponse:
    global openai_client, last_generated_profiles
    logging.info('[HTTP] Requête pour la génération de profil reçue')
    logging.info(f'[HTTP] Méthode: {req.method}, URL: {req.url}')
    
    # Initialiser le client OpenAI si ce n'est pas déjà fait
    if not openai_client:
        try:
            openai_client = get_openai_client()
        except Exception as e:
            logging.error(f'[HTTP] Erreur d\'initialisation OpenAI: {str(e)}')
            return func.HttpResponse(
                body=json.dumps({
                    "error": "Erreur lors de l'initialisation du client Azure OpenAI",
                    "details": str(e)
                }, ensure_ascii=False, indent=2),
                mimetype="application/json",
                status_code=500
            )
    
    # Vérification pour les requêtes GET (retourner les derniers résultats)
    if req.method == "GET":
        logging.info('[HTTP] Traitement requête GET - retour des derniers profils')
        return func.HttpResponse(
            body=json.dumps({
                "derniers_profils_générés": last_generated_profiles or "Aucun profil généré",
                "données_entrée": req.get_json() if req.get_body() else "Aucune donnée d'entrée"
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=200
        )
    
    # Pour les requêtes POST (traiter de nouvelles données)
    try:
        logging.info('[HTTP] Traitement requête POST - début extraction JSON')
        req_body = req.get_json()
        logging.info(f'[HTTP] Données reçues: {json.dumps(req_body)[:200]}...')
        
        # Récupérer l'ID de déploiement
        deployment_id = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
        logging.info(f'[HTTP] Utilisation du modèle: {deployment_id}')
        
        # Initialiser l'analyseur de transcript
        analyzer = TranscriptAnalyzer(openai_client, deployment_id)
        
        # Analyser les données et générer les profils
        logging.info('[HTTP] Début analyse des données et génération des profils')
        profiles = analyzer.process_vapi_data(req_body)
        
        # Sauvegarder les résultats en mémoire pour les requêtes GET
        last_generated_profiles = profiles
        
        logging.info('[HTTP] Génération des profils terminée avec succès')
        return func.HttpResponse(
            body=json.dumps(profiles, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=200
        )
    except ValueError as e:
        logging.error(f'[HTTP] Erreur de format JSON: {str(e)}')
        return func.HttpResponse(
            body=json.dumps({
                "error": "Format JSON invalide",
                "details": str(e)
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=400
        )
    except Exception as e:
        logging.error(f'[HTTP] Erreur lors de la génération des profils: {str(e)}')
        logging.error(f'[HTTP] Traceback: {traceback.format_exc()}')
        return func.HttpResponse(
            body=json.dumps({
                "error": "Erreur lors de la génération des profils",
                "details": str(e),
                "traceback": traceback.format_exc()
            }, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status_code=500
        )

