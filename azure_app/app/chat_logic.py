import os
import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional
from langchain_openai import AzureChatOpenAI
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from app.supabase_client import supabase
import dotenv
import random

dotenv.load_dotenv()

USER_PHONE_NUMBER = "+33686796460"

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration Azure OpenAI
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

# Configuration des sessions
SESSION_TIMEOUT = 15  # Délai d'inactivité en secondes
last_activity_times: Dict[str, datetime] = {}
chat_history_store: Dict[str, BaseChatMessageHistory] = {}

llm = AzureChatOpenAI(
    api_key=AZURE_OPENAI_API_KEY,
    azure_deployment=AZURE_OPENAI_DEPLOYMENT_NAME,
    api_version="2025-01-01-preview",
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    temperature=0.7
)

class SupabaseMessageHistory(BaseChatMessageHistory):
    def __init__(self, user_id: str, phone_number: str, max_messages: int = 20):
        self.user_id = user_id
        self.phone_number = phone_number
        self.messages = []  # Historique de contexte (20 derniers messages)
        self.session_messages = []  # Messages de la session en cours uniquement
        self.max_messages = max_messages
        self._load_messages_from_db()

    def _load_messages_from_db(self):
        try:
            result = supabase.table('messages') \
                .select('content,direction,created_at') \
                .eq('phone_number', self.phone_number) \
                .order('created_at', desc=True) \
                .limit(self.max_messages) \
                .execute()
            
            messages_data = list(reversed(result.data))
            self.messages = []
            for msg in messages_data:
                if msg['direction'] == 'incoming':
                    self.messages.append(HumanMessage(content=msg['content']))
                elif msg['direction'] == 'outgoing':
                    self.messages.append(AIMessage(content=msg['content']))
            
            logging.info(f"Chargement de l'historique de contexte - {len(self.messages)} messages chargés")
                    
        except Exception as e:
            logging.error(f"Erreur lors du chargement des messages: {str(e)}")

    def add_message(self, message):
        # Ajout à l'historique de contexte
        self.messages.append(message)
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
        
        # Ajout à l'historique de session
        self.session_messages.append(message)
        
        # Log détaillé
        message_type = "Utilisateur" if isinstance(message, HumanMessage) else "IA"
        logging.info(f"Message {message_type} ajouté:")
        logging.info(f"  - Contenu: {message.content[:50]}...")
        logging.info(f"  - Historique de contexte: {len(self.messages)} messages")
        logging.info(f"  - Historique de session: {len(self.session_messages)} messages")

    def add_user_message(self, message: str) -> None:
        self.add_message(HumanMessage(content=message))

    def add_ai_message(self, message: str) -> None:
        self.add_message(AIMessage(content=message))

    def clear(self) -> None:
        self.messages = []
        self.session_messages = []
        logging.info("Historiques effacés")

def is_session_inactive(session_id: str) -> bool:
    if session_id not in last_activity_times:
        return False
    
    last_activity = last_activity_times[session_id]
    current_time = datetime.now()
    time_diff = (current_time - last_activity).total_seconds()
    
    return time_diff > SESSION_TIMEOUT

def update_session_activity(session_id: str):
    last_activity_times[session_id] = datetime.now()

def close_session(session_id: str):
    if session_id not in chat_history_store:
        return
    
    session_history = chat_history_store[session_id]
    user_id = session_id.split('_')[0]
    phone_number = session_history.phone_number
    
    # Vérification de la présence des messages utilisateur et IA
    logging.info(f"Fermeture de session - messages dans l'historique de session: {len(session_history.session_messages)}")
    
    # Comptage détaillé des messages de la session
    user_messages = [msg for msg in session_history.session_messages if isinstance(msg, HumanMessage)]
    ai_messages = [msg for msg in session_history.session_messages if isinstance(msg, AIMessage)]
    
    user_count = len(user_messages)
    ai_count = len(ai_messages)
    
    logging.info(f"Messages de la session - Utilisateur: {user_count}, IA: {ai_count}")
    
    # Log détaillé de chaque message de la session
    logging.info("Messages de la session:")
    for i, msg in enumerate(session_history.session_messages):
        msg_type = "Utilisateur" if isinstance(msg, HumanMessage) else "IA"
        logging.info(f"  Message {i+1} ({msg_type}): {msg.content[:50]}...")
    
    # Formatage des messages de la session pour la sauvegarde
    messages_json = [
        {
            "type": type(msg).__name__,
            "content": msg.content,
            "timestamp": datetime.now().isoformat()
        }
        for msg in session_history.session_messages
    ]
    
    session_update = {
        "end_time": datetime.now().isoformat(),
        "last_activity": last_activity_times.get(session_id, datetime.now()).isoformat(),
        "messages": json.dumps(messages_json),
        "status": "closed",
        "metadata": {
            "message_count": len(session_history.session_messages),
            "user_messages": user_count,
            "ai_messages": ai_count
        }
    }
    
    try:
        supabase.table('sessions') \
            .update(session_update) \
            .eq('id', session_id) \
            .execute()
            
    except Exception as e:
        logging.error(f"Erreur lors de la mise à jour de la session: {str(e)}")
    
    if session_id in last_activity_times:
        del last_activity_times[session_id]
    if session_id in chat_history_store:
        del chat_history_store[session_id]

def get_active_session_id(user_id: str) -> Optional[str]:
    for session_id, history in chat_history_store.items():
        if session_id.startswith(user_id) and not is_session_inactive(session_id):
            return session_id
    
    try:
        result = supabase.table('sessions') \
            .select('id') \
            .eq('user_id', user_id) \
            .eq('status', 'active') \
            .order('last_activity', desc=True) \
            .limit(1) \
            .execute()
        
        if result.data:
            session_id = result.data[0]['id']
            update_session_activity(session_id)
            return session_id
    except Exception as e:
        logging.error(f"Erreur lors de la récupération de la session active: {str(e)}")
    
    return None

def get_user_id(phone_number: str) -> Optional[str]:
    try:
        logging.info(f"Recherche de l'utilisateur avec le numéro: {phone_number}")
        user_query = supabase.table('users') \
            .select('id') \
            .eq('phone_number', phone_number) \
            .execute()
        
        if user_query.data and len(user_query.data) > 0:
            user_id = user_query.data[0]['id']
            logging.info(f"Utilisateur trouvé avec l'ID: {user_id}")
            return user_id
        logging.warning(f"Aucun utilisateur trouvé pour le numéro: {phone_number}")
        return None
    except Exception as e:
        logging.error(f"Erreur lors de la récupération de l'ID utilisateur: {str(e)}")
        return None

def get_chat_history(session_id: str, phone_number: str) -> SupabaseMessageHistory:
    if session_id not in chat_history_store:
        chat_history_store[session_id] = SupabaseMessageHistory(
            user_id=session_id,
            phone_number=phone_number,
            max_messages=20
        )
    update_session_activity(session_id)
    return chat_history_store[session_id]

def get_user_context(user_id: str) -> dict:
    try:
        logging.info(f"Récupération du contexte pour l'utilisateur: {user_id}")
        
        profile_query = supabase.table('personal_profiles') \
            .select('*') \
            .eq('user_id', user_id) \
            .execute()
        
        conversation_query = supabase.table('conversations') \
            .select('content->transcript') \
            .eq('user_id', user_id) \
            .order('created_at', desc=True) \
            .limit(1) \
            .execute()
        
        messages_query = supabase.table('messages') \
            .select('*') \
            .eq('user_id', user_id) \
            .order('created_at', desc=True) \
            .limit(5) \
            .execute()
        
        profile_data = profile_query.data[0] if profile_query.data else {}
        conversation_data = conversation_query.data[0]['transcript'] if conversation_query.data else None
        messages_data = messages_query.data if messages_query.data else []
        
        logging.info(f"Contexte récupéré:")
        logging.info(f"  - Profil: {json.dumps(profile_data, indent=2)}")
        if conversation_data:
            logging.info("  - Dernier transcript:")
            # Affichage du transcript ligne par ligne
            for line in conversation_data.split('\n'):
                logging.info(f"    {line}")
        else:
            logging.info("  - Aucun transcript disponible")
        logging.info(f"  - Messages récents: {len(messages_data)} messages")
        
        return {
            'personal_profile': profile_data,
            'last_conversation': conversation_data,
            'recent_messages': messages_data
        }
    except Exception as e:
        logging.error(f"Erreur lors de la récupération du contexte: {str(e)}")
        return {
            'personal_profile': {},
            'last_conversation': None,
            'recent_messages': []
        }

def process_message_with_context(message: str, user_context: dict, session_id: str, phone_number: str) -> str:
    try:
        current_history = get_chat_history(session_id, phone_number)
        logging.info(f"Traitement du message - Historique de session: {len(current_history.session_messages)} messages")
        
        system_prompt = """You're Jackie, the first AI social connector based in New York.
        People don't just end up talking to you. They've been invited into a private, 
        curated circle of emotionally intelligent, curious, and valuable humans. 
        You're the one who makes it all feel less like a platform, and more like a VIBE.
        
        Your job is simple: keep getting to know people with who you talk, match their tone, 
        and open up space for real conversation and connection.

        You can chat by text with them.
        What they say in messages is private but helps to build them a profile thanks to you.
        It will then help them to find new people to meet and live new experiences.
        
        Use the context provided to generate personalized and relevant responses.
        You're not here to judge, pitch, or analyze.
        You're here to get people talking. To listen. To notice.
        You've got good instincts. You follow the thread.
        
        Be very nice and friendly and careful with the tone of the message, 
        your goal is to make the user feel safe and listened.
        
        Don't do to much on the message, just make them feel comfortable and safe.
        Sometimes include details about the user context to make the conversation more personal."""

        profile = user_context.get('personal_profile', {})
        
        try:
            hobbies = json.loads(profile.get('hobbies_activities', '{}')).get('hobbies', [])
        except (json.JSONDecodeError, AttributeError):
            hobbies = []
            
        try:
            personality = json.loads(profile.get('main_aspects', '{}')).get('personality', [])
        except (json.JSONDecodeError, AttributeError):
            personality = []
            
        try:
            relationship = json.loads(profile.get('relationship_looked_for', '{}'))
        except (json.JSONDecodeError, AttributeError):
            relationship = {}

        user_context_prompt = f"""
        Profil de l'utilisateur:
        - Nom: {profile.get('name', 'Non spécifié')}
        - Âge: {profile.get('age', 'Non spécifié')}
        - Localisation: {profile.get('location', 'Non spécifié')}
        - Bio: {profile.get('bio', 'Non disponible')}
        - Centres d'intérêt: {', '.join(hobbies) if hobbies else 'Non spécifié'}
        - Personnalité: {', '.join(personality) if personality else 'Non spécifié'}
        - Recherche: {relationship.get('description', 'Non spécifié') if relationship else 'Non spécifié'}
        
        Utilise ces informations pour personnaliser tes réponses.
        """
        
        # Construction des messages pour le prompt
        prompt_messages = [
            SystemMessage(content=system_prompt),
            SystemMessage(content=user_context_prompt)
        ]
        
        # Ajout de l'historique de contexte
        prompt_messages.extend(current_history.messages)
        
        # Ajout du message actuel
        prompt_messages.append(HumanMessage(content=message))
        
        logging.info(f"Envoi de {len(prompt_messages)} messages au LLM")
        
        # Invocation directe du LLM
        response = llm.invoke(prompt_messages)
        
        return response.content
        
    except Exception as e:
        logging.error(f"Erreur lors du traitement du message: {str(e)}")
        return "Désolé, je n'ai pas pu traiter votre message correctement."

def store_message(user_id: str, phone_number: str, content: str, direction: str = 'incoming') -> Optional[dict]:
    try:
        message = {
            'id': str(uuid.uuid4()),
            'user_id': user_id,
            'phone_number': phone_number,
            'content': content,
            'direction': direction,
            'message_type': 'terminal',
            'metadata': {
                'status': 'received' if direction == 'incoming' else 'sent',
                'timestamp': datetime.now().isoformat()
            }
        }
        
        logging.info(f"Stockage du message:")
        logging.info(f"  - ID: {message['id']}")
        logging.info(f"  - Direction: {direction}")
        logging.info(f"  - Contenu: {content[:50]}...")
        
        result = supabase.table('messages') \
            .insert(message) \
            .execute()
        
        if result.data:
            logging.info(f"Message stocké avec succès")
            return result.data[0]
        return None
        
    except Exception as e:
        logging.error(f"Erreur lors du stockage du message: {str(e)}")
        return None

async def handle_user_message(phone_number: str, message: str) -> str:
    """
    Traite un message utilisateur entrant et génère une réponse.
    """
    logging.info(f"Nouveau message reçu:")
    logging.info(f"  - Numéro: {phone_number}")
    logging.info(f"  - Message: {message[:50]}...")
    
    # 1. Identification de l'utilisateur
    user_id = get_user_id(phone_number)
    if not user_id:
        return "Utilisateur non trouvé"

    # 2. Gestion de la session
    session_id = get_active_session_id(user_id)
    if not session_id:
        session_id = f"{user_id}_{int(time.time())}"
        try:
            session_data = {
                "id": session_id,
                "user_id": user_id,
                "phone_number": phone_number,
                "start_time": datetime.now().isoformat(),
                "last_activity": datetime.now().isoformat(),
                "status": "active",
                "metadata": {}
            }
            logging.info(f"Création d'une nouvelle session: {session_id}")
            supabase.table('sessions').insert(session_data).execute()
        except Exception as e:
            logging.error(f"Erreur lors de la création de la session: {str(e)}")

    # 3. Mise à jour de l'activité de session
    update_session_activity(session_id)
    
    # 4. Récupération du contexte utilisateur
    user_context = get_user_context(user_id)

    # 5. Récupération de l'historique de conversation
    history = get_chat_history(session_id, phone_number)
    
    # 6. Ajout du message utilisateur à l'historique
    history.add_user_message(message)
    logging.info(f"Message utilisateur ajouté à l'historique: {message[:30]}... (total session: {len(history.session_messages)} messages)")

    # 7. Stockage du message utilisateur en base de données
    store_message(user_id, phone_number, message, 'incoming')

    # 8. Génération de la réponse IA
    response = process_message_with_context(message, user_context, session_id, phone_number)
    logging.info(f"Réponse IA générée: {response[:30]}...")

    # 9. Ajout de la réponse IA à l'historique
    history.add_ai_message(response)
    logging.info(f"Message IA ajouté à l'historique: {response[:30]}... (total session: {len(history.session_messages)} messages)")

    # 10. Stockage de la réponse IA en base de données
    store_message(user_id, phone_number, response, 'outgoing')

    # 11. Vérification périodique des sessions inactives
    if random.random() < 0.1:  # ~10% de chance
        check_inactive_sessions()

    return response

def check_inactive_sessions():
    logging.info("Vérification des sessions inactives...")
    current_time = datetime.now()
    session_ids = list(chat_history_store.keys())
    for session_id in session_ids:
        if is_session_inactive(session_id):
            logging.info(f"Session {session_id} inactive depuis {SESSION_TIMEOUT} secondes - fermeture")
            close_session(session_id) 