import os
import requests
from flask import Flask, render_template, redirect, url_for, session, request, flash
from dotenv import load_dotenv
from pathlib import Path
import random
import string

# Charger les variables d'environnement
env_path = Path('.') / '.env.local'
if not env_path.exists():
    env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')

# Configuration Discord
DISCORD_CLIENT_ID = os.getenv('BOT_CLIENT_ID')
DISCORD_CLIENT_SECRET = os.getenv('BOT_CLIENT_SECRET')
BOT_ID = os.getenv('BOT_ID')
DISCORD_REDIRECT_URI = 'https://answerly-ypa0.onrender.com/callback'
DISCORD_API_URL = 'https://discord.com/api/v10'

# Configuration Supabase (via API REST directe)
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

# --- Fonctions utilitaires ---

def get_bot_guilds():
    headers = {'Authorization': f'Bot {os.getenv("BOT_TOKEN")}'}
    try:
        response = requests.get(f'{DISCORD_API_URL}/users/@me/guilds', headers=headers)
        if response.status_code == 200:
            return {g['id']: g for g in response.json()}
    except Exception as e:
        print(f"Error fetching bot guilds: {e}")
    return {}

def get_user_guilds(access_token):
    headers = {'Authorization': f'Bearer {access_token}'}
    try:
        response = requests.get(f'{DISCORD_API_URL}/users/@me/guilds', headers=headers)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Error fetching user guilds: {e}")
    return []

# --- Routes ---

@app.route('/')
def index():
    if 'access_token' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/login')
def login():
    scope = 'identify guilds'
    discord_auth_url = (
        f'https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}'
        f'&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope={scope}'
    )
    return redirect(discord_auth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return "No code provided", 400
        
    data = {
        'client_id': DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': DISCORD_REDIRECT_URI,
        'scope': 'identify guilds'
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    response = requests.post(f'{DISCORD_API_URL}/oauth2/token', data=data, headers=headers)
    
    if response.status_code == 200:
        session['access_token'] = response.json()['access_token']
        return redirect(url_for('dashboard'))
    else:
        return f"Error connecting to Discord: {response.text}", 400

@app.route('/dashboard')
def dashboard():
    if 'access_token' not in session:
        return redirect(url_for('index'))

    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    servers_list = []
    
    # On parcourt TOUS les serveurs de l'utilisateur
    for guild in user_guilds:
        permissions = int(guild.get('permissions', 0))
        # On garde seulement si l'utilisateur a la permission "Gérer le serveur" (0x20)
        if (permissions & 0x20) == 0x20:
            guild_data = guild
            # On vérifie si le bot est présent
            guild_data['bot_present'] = guild['id'] in bot_guilds
            servers_list.append(guild_data)

    return render_template('dashboard.html', servers=servers_list, bot_id=BOT_ID)

@app.route('/server/<guild_id>', methods=['GET', 'POST'])
def server(guild_id):
    if 'access_token' not in session:
        return redirect(url_for('index'))

    # --- Gestion POST (Création et Modification) ---
    if request.method == 'POST':
        question_id = request.form.get('id')
        question_text = request.form.get('question')
        answer_text = request.form.get('answer')
        
        # Vérification basique
        if not question_text or not answer_text:
            flash('Question and Answer cannot be empty.', 'error')
        else:
            if question_id:
                # --- MODIFICATION ---
                update_url = f"{SUPABASE_URL}/rest/v1/questions?id=eq.{question_id}"
                data_to_update = {'question': question_text, 'answer': answer_text}
                patch_headers = {**SUPABASE_HEADERS, "Prefer": "return=minimal"}
                
                try:
                    requests.patch(update_url, headers=patch_headers, json=data_to_update)
                    flash('Question updated successfully!', 'success')
                except Exception as e:
                    flash(f'Error updating: {str(e)}', 'error')
            else:
                # --- CRÉATION ---
                # 1. Vérifier la limite (30 questions)
                count_url = f"{SUPABASE_URL}/rest/v1/questions?guild_id=eq.{guild_id}&select=id"
                count_response = requests.get(count_url, headers=SUPABASE_HEADERS)
                current_count = len(count_response.json())
                
                if current_count >= 30:
                    flash('Limit reached: Maximum 30 questions per server.', 'error')
                else:
                    # 2. Générer un ID unique
                    def gen_id():
                        return ''.join(random.choices(string.digits, k=8))
                    
                    new_id = gen_id()
                    
                    # 3. Insérer dans Supabase
                    insert_url = f"{SUPABASE_URL}/rest/v1/questions"
                    new_data = {
                        'id': new_id,
                        'guild_id': guild_id,
                        'question': question_text,
                        'answer': answer_text,
                        'author': 'Web Dashboard', # On change l'auteur pour indiquer que ça vient du site
                        'times_sent': 0
                    }
                    
                    try:
                        requests.post(insert_url, headers=SUPABASE_HEADERS, json=new_data)
                        flash('Question created successfully!', 'success')
                    except Exception as e:
                        flash(f'Error creating: {str(e)}', 'error')

        return redirect(url_for('server', guild_id=guild_id))

    # --- Gestion GET (Affichage) ---
    select_url = f"{SUPABASE_URL}/rest/v1/questions?guild_id=eq.{guild_id}&select=*"
    questions = []
    
    try:
        response = requests.get(select_url, headers=SUPABASE_HEADERS)
        if response.status_code == 200:
            questions = response.json()
    except Exception as e:
        print(f"Supabase error: {e}")

    return render_template('server.html', questions=questions, guild_id=guild_id)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/server/<guild_id>/delete', methods=['POST'])
def delete_question(guild_id):
    if 'access_token' not in session:
        return redirect(url_for('index'))

    question_id = request.form.get('id')
    
    # URL Supabase pour supprimer la question spécifique
    delete_url = f"{SUPABASE_URL}/rest/v1/questions?id=eq.{question_id}"
    
    try:
        # On envoie une requête DELETE à Supabase
        requests.delete(delete_url, headers=SUPABASE_HEADERS)
        flash('Question deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting question: {str(e)}', 'error')

    return redirect(url_for('server', guild_id=guild_id))

if __name__ == '__main__':

    app.run(debug=True, port=5000)

