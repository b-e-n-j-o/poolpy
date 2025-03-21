import json
import time
import tiktoken
from typing import Dict, Any, List, Tuple, Optional
from tqdm import tqdm
import os
from dotenv import load_dotenv

class TranscriptAnalyzer:
    """
    Agent d'analyse de transcript qui extrait les informations structurées à partir
    d'une conversation pour les adapter au schéma de base de données cible.
    """
    
    def __init__(self, openai_api_key):
        """
        Initialise l'analyseur de transcript.
        
        Args:
            openai_api_key: Clé API OpenAI
        """
        import openai
        self.client = openai.Client(api_key=openai_api_key)
        self.model = "gpt-4o-mini"  # On utilise GPT-4o-mini
        self.tokenizer = tiktoken.encoding_for_model("gpt-4o-mini")
        self.total_input_tokens = 0
        self.total_output_tokens = 0
    
    def count_tokens(self, text: str) -> int:
        """Compte le nombre de tokens dans un texte."""
        return len(self.tokenizer.encode(text))
    
    def log_prompt_stats(self, prompt: str, response: str):
        """Affiche les statistiques d'utilisation des tokens."""
        input_tokens = self.count_tokens(prompt)
        output_tokens = self.count_tokens(response)
        
        # Cumuler les tokens
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        
        print(f"\n=== Statistiques d'utilisation ===")
        print(f"Tokens en entrée: {input_tokens}")
        print(f"Tokens en sortie: {output_tokens}")
        print(f"Total tokens: {input_tokens + output_tokens}")
        print("================================")
    
    def format_transcript_for_analysis(self, vapi_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transforme les données du format VAPI au format attendu par l'analyseur.
        """
        # Extraire les informations de base
        call_id = vapi_data["call_metadata"]["call_id"]
        
        # Extraire le nom de l'utilisateur des échanges (premier message de l'utilisateur)
        user_name = None
        for exchange in vapi_data["conversation_content"]["structured_exchanges"]:
            if exchange["speaker"] == "user" and "m'appelle" in exchange["text"]:
                # Extraction basique du nom depuis la présentation
                parts = exchange["text"].split("m'appelle")
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
            "user_name": user_name,  # Ajout du nom d'utilisateur extrait
            "timestamp": vapi_data["call_metadata"]["start_time"],
            "transcript": transcript,
            "metadata": vapi_data.get("metadata", {})  # Utilise les métadonnées si disponibles
        }
        
        return formatted_data
    
    def extract_personal_profile(self, transcript_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extrait les informations pour le profil personnel de l'utilisateur.
        
        Args:
            transcript_data: Données formatées du transcript
            
        Returns:
            Dict contenant les informations du profil personnel selon le schéma de BDD
        """
        print("  Préparation des données du transcript...")
        user_name = transcript_data.get("user_name", "")
        transcript = transcript_data.get("transcript", [])
        
        print("  Extraction des messages utilisateur...")
        user_messages = [msg.get("text", "") for msg in transcript if msg.get("speaker") == "user"]
        user_text = "\n".join(user_messages)
        
        # Reconstitution de la conversation complète au format lisible
        conversation = []
        for msg in transcript:
            speaker = "Assistant" if msg.get("speaker") == "agent" else "Utilisateur"
            conversation.append(f"{speaker}: {msg.get('text', '')}")
        
        conversation_text = "\n".join(conversation)
        
        # Création du prompt pour l'extraction du profil personnel
        prompt = f"""
        Analyse la conversation suivante entre un assistant vocal et un utilisateur:

        {conversation_text}

        À partir de cette conversation, extrait les informations pour créer un profil personnel de l'utilisateur selon le schéma de base de données suivant.
        Utilise UNIQUEMENT les informations disponibles dans la conversation. Si une information n'est pas mentionnée, utilise null.

        Réponds au format JSON strict avec les champs suivants correspondant à notre table personal_profiles:

        {{
          "user_id": "{user_name.lower()}",  # ID de l'utilisateur (obligatoire)
          "age": null,  # âge estimé (int)
          "gender": null,  # genre (string: "homme", "femme", "non-binaire", "autre")
          "location": null,  # localisation (string, ex: "Paris 11ème")
          "occupation": null,  # profession (string)
          "relationship_status": null,  # statut relationnel (string)
          "personality_attributes": {{}},  # traits de personnalité avec scores de 1 à 10 (objet)
          "interests": {{}},  # centres d'intérêt avec scores de 1 à 10 (objet)
          "distinctive_qualities": {{}}  # qualités distinctives avec descriptions (objet)
        }}

        Important: Ta réponse doit être uniquement un objet JSON valide sans aucun texte avant ou après.
        Ne répond pas avec des explications, uniquement le JSON brut.
        """
        
        print("\n=== Prompt pour l'extraction du profil personnel ===")
        print(prompt)
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "Tu es un ami bienveillant et intuitif qui a un vrai talent pour comprendre les gens et faire des présentations qui créent des connexions authentiques. Tu sais naturellement capter l'essence de ce que recherchent les gens dans leurs relations et l'exprimer de façon chaleureuse et sincère, comme lors d'une conversation entre amis."},
                {"role": "user", "content": prompt}
            ]
        )
        
        response_content = response.choices[0].message.content
        self.log_prompt_stats(prompt, response_content)
        
        # Extraction et traitement du JSON
        try:
            personal_profile = json.loads(response_content)
            print("  Données extraites avec succès")
            
            # Ajouter les timestamps
            current_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            personal_profile["created_at"] = current_time
            personal_profile["updated_at"] = current_time
            
            return personal_profile
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  ⚠️ Erreur lors du parsing du profil personnel: {e}")
            return {
                "user_id": user_name.lower(),
                "error": "Échec de l'extraction",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }
    
    def extract_desired_profile(self, transcript_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extrait les informations pour le profil recherché par l'utilisateur.
        
        Args:
            transcript_data: Données formatées du transcript
            
        Returns:
            Dict contenant les informations du profil recherché selon le schéma de BDD
        """
        user_name = transcript_data.get("user_name", "")
        transcript = transcript_data.get("transcript", [])
        
        # Reconstitution de la conversation complète au format lisible
        conversation = []
        for msg in transcript:
            speaker = "Assistant" if msg.get("speaker") == "agent" else "Utilisateur"
            conversation.append(f"{speaker}: {msg.get('text', '')}")
        
        conversation_text = "\n".join(conversation)
        
        # Création du prompt pour l'extraction du profil recherché
        prompt = f"""
        Analyse la conversation suivante entre un assistant vocal et un utilisateur:

        {conversation_text}

        À partir de cette conversation, extrait les informations sur ce que l'utilisateur recherche chez d'autres personnes selon le schéma de base de données suivant.
        Utilise UNIQUEMENT les informations disponibles dans la conversation. Si une information n'est pas mentionnée, utilise null.

        Réponds au format JSON strict avec les champs suivants correspondant à notre table desired_profiles:

        {{
          "user_id": "{user_name.lower()}",  # ID de l'utilisateur (obligatoire)
          "relationship_type": null,  # type de relation recherchée (string: "amitié", "amour", "professionnel")
          "age_range": null,  # tranche d'âge préférée [min, max] (array of int)
          "gender_preference": null,  # préférence de genre (string ou array of strings)
          "location_preference": null,  # préférence géographique (string)
          "essential_qualities": {{}},  # qualités essentielles recherchées avec scores de 1 à 10 (objet)
          "desired_qualities": {{}},  # qualités souhaitables mais non essentielles (objet)
          "dealbreakers": {{}},  # points de non-négociation avec descriptions (objet)
          "similarity_preferences": {{}}  # préférences similarité vs complémentarité (objet)
        }}

        Important: Ta réponse doit être uniquement un objet JSON valide sans aucun texte avant ou après.
        Ne répond pas avec des explications, uniquement le JSON brut.
        """
        
        print("\n=== Prompt pour l'extraction du profil recherché ===")
        print(prompt)
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "Tu es un ami bienveillant et intuitif qui a un vrai talent pour comprendre les gens et faire des présentations qui créent des connexions authentiques. Tu sais naturellement capter l'essence de ce que recherchent les gens dans leurs relations et l'exprimer de façon chaleureuse et sincère, comme lors d'une conversation entre amis."},
                {"role": "user", "content": prompt}
            ]
        )
        
        response_content = response.choices[0].message.content
        self.log_prompt_stats(prompt, response_content)
        
        # Extraction et traitement du JSON
        try:
            desired_profile = json.loads(response_content)
            
            # Ajouter les timestamps
            current_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            desired_profile["created_at"] = current_time
            desired_profile["updated_at"] = current_time
            
            return desired_profile
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Erreur lors du parsing du profil recherché: {e}")
            return {
                "user_id": user_name.lower(),
                "error": "Échec de l'extraction",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
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
        
        Args:
            personal_profile: Profil personnel extrait
            transcript_data: Données complètes du transcript
            
        Returns:
            Résumé narratif du profil personnel
        """
        user_name = transcript_data.get("user_name", "")
        
        # Création du prompt pour la génération du résumé narratif
        user_messages = self.get_user_messages(transcript_data)
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
- Aussi inclus si besoin des éléments très précis qui peut parfois rapprocher les gens comme le style de musique ou quelque chose de préféré chez la personne
- Ne généralise pas certains traits ou certains points, essaie de decrire {user_name} le mieux possible, le but de l'analyse est de connaitre {user_name} et de savoir précisément qui il est.


Le résumé doit donner une image précise de qui est cette personne, ce qui la caractérise, 
et comment elle interagit avec les autres, en te basant uniquement sur les données disponibles.
"""
        
        print("\n=== Prompt pour la génération du résumé personnel ===")
        print(prompt)
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "Tu es un ami proche qui a un don pour parler des gens avec bienveillance et authenticité. Tu sais capter ce qui rend chaque personne unique et spéciale, et le partager de façon naturelle et engageante, comme lors d'une conversation sincère entre amis."},
                {"role": "user", "content": prompt}
            ]
        )
        
        response_content = response.choices[0].message.content
        self.log_prompt_stats(prompt, response_content)
        
        return response_content.strip()
    
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
        - Ne généralise pas certains traits ou certains points, essaie de decrire ce que {user_name} rechecrhe le mieux possible, le but de l'analyse est de connaitre {user_name} et de connaitre ses attentes

        Le résumé doit sonner comme une conversation naturelle où tu expliques à un ami 
        le genre de personnes avec qui {user_name} pourrait vraiment bien s'entendre.
        
        Important : Évite le style "recherche" ou "critères". Garde un ton chaleureux et authentique,
        comme si tu présentais naturellement deux personnes qui pourraient bien s'entendre.
        """
        
        print("\n=== Prompt pour la génération du résumé des préférences ===")
        print(prompt)
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "Tu es un ami intuitif qui a un don pour comprendre ce que les gens recherchent vraiment dans leurs relations. Tu sais lire entre les lignes et capter les aspirations profondes des gens, au-delà des simples critères. Tu as le talent de présenter ces attentes de façon naturelle et bienveillante."},
                {"role": "user", "content": prompt}
            ]
        )
        
        response_content = response.choices[0].message.content
        self.log_prompt_stats(prompt, response_content)
        
        return response_content.strip()
    
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
        print("\n=== Début du traitement du transcript ===")
        print(f"Traitement pour l'utilisateur: {transcript_data.get('user_name', 'Inconnu')}")
        
        # Création d'une barre de progression pour les 4 étapes principales
        steps = ['Extraction profil personnel', 'Extraction profil recherché', 
                 'Génération résumé personnel', 'Génération résumé préférences']
        
        results = {}
        with tqdm(total=len(steps), desc="Progression de l'analyse") as pbar:
            # Extraction du profil personnel
            pbar.set_description("Extraction du profil personnel")
            personal_profile = self.extract_personal_profile(transcript_data)
            pbar.update(1)
            
            # Extraction du profil recherché
            pbar.set_description("Extraction du profil recherché")
            desired_profile = self.extract_desired_profile(transcript_data)
            pbar.update(1)
            
            # Génération du résumé narratif personnel
            pbar.set_description("Génération du résumé personnel")
            personal_narrative = self.generate_personal_narrative(personal_profile, transcript_data)
            personal_profile["narrative_summary"] = personal_narrative
            pbar.update(1)
            
            # Génération du résumé narratif des préférences
            pbar.set_description("Génération du résumé des préférences")
            desired_narrative = self.generate_desired_narrative(desired_profile, transcript_data)
            desired_profile["narrative_summary"] = desired_narrative
            pbar.update(1)
        
        print("\n=== Traitement terminé avec succès ===")
        
        print("\n=== Statistiques finales d'utilisation des tokens ===")
        print(f"Total tokens en entrée: {self.total_input_tokens}")
        print(f"Total tokens en sortie: {self.total_output_tokens}")
        print(f"Total tokens global: {self.total_input_tokens + self.total_output_tokens}")
        print("================================================")

        return {
            "conversation_id": transcript_data.get("conversation_id", ""),
            "user_id": transcript_data.get("user_name", "").lower(),
            "personal_profile": personal_profile,
            "desired_profile": desired_profile,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "token_usage": {
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "total_tokens": self.total_input_tokens + self.total_output_tokens
            }
        }

# Charger les variables d'environnement
load_dotenv()

# Récupérer la clé API OpenAI
openai_api_key = os.getenv('OPENAI_API_KEY')

# Créer une instance de l'analyseur
analyzer = TranscriptAnalyzer(openai_api_key)

# Charger le transcript (ajustez le chemin selon votre environnement)
path = '../tests/transcript_optimized.json'  # Assurez-vous que c'est le bon chemin

try:
    with open(path, 'r', encoding='utf-8') as f:
        transcript_data = json.load(f)
except Exception as e:
    print(f"Erreur lors de la lecture du fichier : {e}")
    exit(1)

# Formater les données
formatted_data = analyzer.format_transcript_for_analysis(transcript_data)

# Vérifier que les données sont correctement formatées
print(f"Analyse pour l'utilisateur : {formatted_data.get('user_name', 'Inconnu')}")
print(f"Nombre d'échanges : {len(formatted_data.get('transcript', []))}")

# Analyser le transcript
results = analyzer.process_transcript(formatted_data)

# Sauvegarder les résultats
with open('analysis_results.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)