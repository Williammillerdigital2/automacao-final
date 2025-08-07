# ==============================================================================
# IMPORTAÇÕES DE BIBLIOTECAS
# ==============================================================================
import feedparser
import requests
import os
import time
import pickle
import json
import random

# Libs de Serviços e API
import google.generativeai as genai
import azure.cognitiveservices.speech as speechsdk
from flask import Flask, jsonify
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Libs de Mídia
from moviepy.editor import *
from moviepy.video.fx.all import crop
from PIL import Image, ImageEnhance, ImageOps

# ==============================================================================
# CONFIGURAÇÃO E INICIALIZAÇÃO
# ==============================================================================
app = Flask(__name__)

CELEB_FEEDS = [
    "https://www.tmz.com/rss.xml", "https://people.com/celebrity/feed/", "https://www.eonline.com/news/rss",
    "https://www.justjared.com/feed/", "https://variety.com/v/film/news/feed/",
    "https://www.hollywoodreporter.com/c/music/feed/", "https://www.vulture.com/rss/index.xml"
]
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
VIDEO_DIR, ARQUIVO_LOG_NOTICIAS = "videos_gerados", "noticias_postadas.log" 

# ==============================================================================
# FUNÇÕES AUXILIARES DA AUTOMAÇÃO
# ==============================================================================

def buscar_noticia_recente():
    print("Buscando a notícia mais recente...")
    noticias_postadas = []
    if os.path.exists(ARQUIVO_LOG_NOTICIAS):
        with open(ARQUIVO_LOG_NOTICIAS, "r") as f:
            noticias_postadas = [line.strip() for line in f.readlines()]

    noticia_mais_recente, data_mais_recente = None, None
    for feed_url in CELEB_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries: continue
            noticia_candidata = feed.entries[0]
            if noticia_candidata.link in noticias_postadas: continue
            
            if hasattr(noticia_candidata, 'published_parsed'):
                data_candidata = noticia_candidata.published_parsed
                if data_mais_recente is None or data_candidata > data_mais_recente:
                    data_mais_recente, noticia_mais_recente = data_candidata, noticia_candidata
        except Exception as e:
            print(f"Aviso: Falha ao processar feed {feed_url}: {e}")

    if noticia_mais_recente is None: raise Exception("Nenhuma notícia nova encontrada.")
    titulo, link = noticia_mais_recente.title, noticia_mais_recente.link
    
    imagem_url = next((c['url'] for c in getattr(noticia_mais_recente, 'media_content', []) if c), 
                      next((l.href for l in getattr(noticia_mais_recente, 'links', []) if 'image' in l.get('type', '')), None))

    if not imagem_url: raise Exception("Notícia não continha uma imagem.")
    print(f"Notícia selecionada: {titulo}")
    return {"titulo": titulo, "imagem_url": imagem_url, "link": link}

def otimizar_conteudo_com_gemini(titulo):
    print("Otimizando conteúdo com Google Gemini...")
    model = genai.GenerativeModel('gemini-1.5-flash-latest')
    prompt = f"""Baseado na manchete de celebridade: "{titulo}", gere um conteúdo para um YouTube Short. Retorne um objeto JSON com 3 chaves: "roteiro", "titulos_sugeridos" e "hashtags".
    1. "roteiro": Roteiro curto e cativante de 15-20 segundos.
    2. "titulos_sugeridos": 3 títulos curtos e virais para Shorts, incluindo #shorts.
    3. "hashtags": 10-15 hashtags relevantes em inglês.
    A resposta DEVE ser apenas o objeto JSON.
    """
    response = model.generate_content(prompt)
    json_response_text = response.text.strip().replace("```json", "").replace("```", "")
    try:
        return json.loads(json_response_text)
    except json.JSONDecodeError:
        raise Exception(f"Falha ao decodificar a resposta da IA. Resposta: {json_response_text}")

def gerar_audio_azure(texto, nome_arquivo):
    print("Gerando áudio com Microsoft Azure...")
    speech_key, region = os.getenv("AZURE_SPEECH_KEY"), os.getenv("AZURE_SPEECH_REGION")
    if not speech_key or not region: raise Exception("Credenciais da Azure não configuradas.")
    
    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=region)
    speech_config.speech_synthesis_voice_name = "en-US-JennyNeural"
    audio_config = speechsdk.audio.AudioOutputConfig(filename=nome_arquivo)
    
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    result = synthesizer.speak_text_async(texto).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise Exception(f"Falha ao gerar áudio: {result.cancellation_details}")
    print("Áudio salvo.")

def baixar_imagem(url, nome_arquivo):
    print("Baixando imagem...")
    response = requests.get(url, stream=True)
    response.raise_for_status()
    with open(nome_arquivo, 'wb') as f: f.write(response.content)
    print("Imagem salva.")

def aplicar_efeito_aleatorio(caminho_imagem):
    print("Aplicando efeito visual aleatório...")
    try:
        img = Image.open(caminho_imagem).convert("RGBA")
        efeitos = [efeito_vinheta, efeito_preto_e_branco, efeito_contraste, efeito_sepia]
        efeito_escolhido = random.choice(efeitos)
        img_modificada = efeito_escolhido(img)
        img_modificada.convert("RGB").save(caminho_imagem)
        print(f"Efeito '{efeito_escolhido.__name__}' aplicado.")
    except Exception as e:
        print(f"Aviso: Não foi possível aplicar efeito na imagem: {e}")

def efeito_vinheta(img): return Image.composite(img, Image.new('RGBA', img.size, (0,0,0,0)), Image.new('L', img.size, 255).resize(img.size))
def efeito_preto_e_branco(img): return ImageOps.grayscale(img).convert("RGBA")
def efeito_contraste(img): return ImageEnhance.Contrast(img).enhance(1.5)
def efeito_sepia(img): return Image.blend(ImageOps.grayscale(img).convert("RGBA"), Image.new('RGBA', img.size, (255, 240, 192, 0)), 0.6)

def criar_video(arquivo_imagem, arquivo_audio, arquivo_saida, roteiro):
    print("Iniciando a criação do vídeo...")
    audio_clip = AudioFileClip(arquivo_audio)
    image_clip = ImageClip(arquivo_imagem).set_duration(audio_clip.duration)
    w, h = image_clip.size
    target_ratio, target_size = 9/16, (1080, 1920)

    if (w/h) > target_ratio: image_clip = crop(image_clip, width=int(h*target_ratio), x_center=w/2)
    else: image_clip = crop(image_clip, height=int(w/target_ratio), y_center=h/2)
    
    final_clip = image_clip.resize(height=target_size[1]).resize(lambda t: 1+0.02*t).set_position(("center", "center"))

    text_clip = TextClip(roteiro, fontsize=70, color='white', font='Arial-Bold', stroke_color='black', stroke_width=3, method='label', size=(target_size[0]-100, None)).set_position(('center', 'center')).set_duration(audio_clip.duration)
    video_final = CompositeVideoClip([final_clip, text_clip], size=target_size).set_audio(audio_clip)
    video_final.write_videofile(arquivo_saida, codec='libx264', audio_codec='aac', temp_audiofile='temp-audio.m4a', remove_temp=True, fps=24)
    print("Vídeo final salvo.")

def upload_to_youtube(file_path, title, description, tags):
    print("Iniciando upload para o YouTube...")
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token: creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
        else: raise Exception("Credenciais do YouTube inválidas.")

    service = build('youtube', 'v3', credentials=creds)
    request_body = {'snippet': {'title': title, 'description': description, 'tags': tags, 'categoryId': '24'}, 'status': {'privacyStatus': 'public', 'selfDeclaredMadeForKids': False}}
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)
    service.videos().insert(part='snippet,status', body=request_body, media_body=media).execute()
    print("Upload para o YouTube concluído.")

# ==============================================================================
# ROTA PRINCIPAL DA API E LÓGICA DE EXECUÇÃO
# ==============================================================================
@app.route('/trigger-video', methods=['POST'])
def job_de_criacao_de_video():
    """Endpoint principal que o cron-job irá chamar."""
    try:
        if not os.path.exists(VIDEO_DIR): os.makedirs(VIDEO_DIR)
        noticia = buscar_noticia_recente()
        conteudo = otimizar_conteudo_com_gemini(noticia['titulo'])
        roteiro, titulo_yt, hashtags_yt = conteudo['roteiro'], conteudo['titulos_sugeridos'][0], conteudo['hashtags']
        
        base_name = os.path.join(VIDEO_DIR, f"video_{int(time.time())}")
        audio_file, image_file, video_file = f"{base_name}.mp3", f"{base_name}.jpg", f"{base_name}.mp4"

        gerar_audio_azure(roteiro, audio_file)
        baixar_imagem(noticia['imagem_url'], image_file)
        aplicar_efeito_aleatorio(image_file)
        criar_video(image_file, audio_file, video_file, roteiro)

        descricao_yt = f"{roteiro}\n\nTags: {', '.join(hashtags_yt)}"
        upload_to_youtube(video_file, titulo_yt, descricao_yt, hashtags_yt)

        with open(ARQUIVO_LOG_NOTICIAS, "a") as f: f.write(f"{noticia['link']}\n")
        for f in [audio_file, image_file, video_file]: os.remove(f)
        
        return jsonify({"status": "sucesso", "mensagem": f"Vídeo criado e postado: {titulo_yt}"}), 200
    except Exception as e:
        print(f"ERRO NO PROCESSO: {e}")
        return jsonify({"status": "erro", "mensagem": str(e)}), 500

# Ponto de entrada para o servidor Gunicorn no Render
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
