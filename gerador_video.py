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
from dotenv import load_dotenv

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
from moviepy.config import change_settings

# ==============================================================================
# CONFIGURAÇÃO INICIAL
# ==============================================================================
# Carrega as variáveis de ambiente do arquivo .env
# Essencial para rodar tanto localmente quanto na VM
load_dotenv()

# Bloco de configuração do ImageMagick. No Linux (VM), ele não encontrará o caminho
# e seguirá em frente sem erro, usando a instalação padrão do sistema.
# No Windows, ele usará o caminho especificado.
try:
    # Este caminho é apenas para o seu ambiente Windows local
    if os.name == 'nt': 
        change_settings({"IMAGEMAGICK_BINARY": r"C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe"})
        print("Caminho do ImageMagick configurado para Windows.")
except Exception as e:
    print(f"Aviso: Não foi possível configurar o caminho do ImageMagick. Erro: {e}")

# Inicialização do App Flask (usado apenas para a estrutura do código)
app = Flask(__name__)

# Constantes do projeto
CELEB_FEEDS = [
    "https://www.tmz.com/rss.xml", "https://people.com/celebrity/feed/", "https://www.eonline.com/news/rss",
    "https://www.justjared.com/feed/", "https://variety.com/v/film/news/feed/",
    "https://www.hollywoodreporter.com/c/music/feed/", "https://www.vulture.com/rss/index.xml"
]
VIDEO_DIR, ARQUIVO_LOG_NOTICIAS = "videos_gerados", "noticias_postadas.log" 

# ==============================================================================
# FUNÇÕES AUXILIARES DA AUTOMAÇÃO
# ==============================================================================

def buscar_noticia_recente():
    """
    Busca as 5 notícias mais recentes de vários feeds, ordena por data,
    e seleciona a mais nova que ainda não foi postada.
    """
    print("Buscando a notícia mais recente...")
    noticias_postadas = []
    if os.path.exists(ARQUIVO_LOG_NOTICIAS):
        with open(ARQUIVO_LOG_NOTICIAS, "r", encoding='utf-8') as f:
            noticias_postadas = [line.strip() for line in f.readlines()]

    candidatas = []
    for feed_url in CELEB_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                if hasattr(entry, 'published_parsed'):
                    candidatas.append(entry)
        except Exception as e:
            print(f"Aviso: Falha ao processar feed {feed_url}: {e}")

    if not candidatas:
        raise Exception("Nenhuma notícia encontrada em nenhum feed.")

    candidatas.sort(key=lambda x: x.published_parsed, reverse=True)

    noticia_selecionada = None
    for noticia in candidatas:
        if noticia.link not in noticias_postadas:
            noticia_selecionada = noticia
            break 

    if noticia_selecionada is None:
        raise Exception("Nenhuma notícia NOVA encontrada. Todas as recentes já foram postadas.")

    titulo = noticia_selecionada.title
    link = noticia_selecionada.link
    
    imagem_url = next((c['url'] for c in getattr(noticia_selecionada, 'media_content', []) if c), 
                      next((l.href for l in getattr(noticia_selecionada, 'links', []) if 'image' in l.get('type', '')), None))

    if not imagem_url:
        raise Exception("Notícia selecionada não continha uma imagem.")
        
    print(f"Notícia selecionada: {titulo}")
    return {"titulo": titulo, "imagem_url": imagem_url, "link": link}

def otimizar_conteudo_com_gemini(titulo):
    print("Otimizando conteúdo com Google Gemini...")
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    
    model = genai.GenerativeModel('gemini-1.5-flash-latest')
    prompt = f"""Baseado na manchete de celebridade: "{titulo}", gere um conteúdo para um YouTube Short. Retorne um objeto JSON com 3 chaves: "roteiro", "titulos_sugeridos" e "hashtags".
    1. "roteiro": Roteiro curto e cativante de 15 a 20 segundos. Tom informativo e direto.
    2. "titulos_sugeridos": Gere uma lista de 3 títulos otimizados para viralizar no YouTube Shorts. Devem ser curtos, usar gatilhos de curiosidade e terminar com a hashtag #shorts.
    3. "hashtags": Gere uma lista de 10 a 15 hashtags relevantes em inglês, misturando genéricas e específicas da notícia.
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

    # Alteração para usar o arquivo de fonte local
    text_clip = TextClip(roteiro, fontsize=70, color='white', font='ARIALBD.TTF', stroke_color='black', stroke_width=3, method='label', size=(target_size[0]-100, None)).set_position(('center', 'center')).set_duration(audio_clip.duration)
    
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
# FUNÇÃO PRINCIPAL DE EXECUÇÃO
# ==============================================================================
def job_de_criacao_de_video():
    """Função principal que executa todo o fluxo de trabalho."""
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

        with open(ARQUIVO_LOG_NOTICIAS, "a", encoding='utf-8') as f: f.write(f"{noticia['link']}\n")
        
        for f in [audio_file, image_file, video_file]:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception as e:
                print(f"Aviso: Não foi possível remover o arquivo {f}. Erro: {e}")
        
        print(f"SUCESSO: Vídeo criado e postado: {titulo_yt}")

    except Exception as e:
        print(f"ERRO NO PROCESSO: {e}")

# ==============================================================================
# PONTO DE ENTRADA
# ==============================================================================
if __name__ == "__main__":
    # Este bloco foi removido pois não precisamos mais do servidor Flask.
    # A execução agora é sempre direta.
    print("--- INICIANDO EXECUÇÃO DIRETA ---")
    job_de_criacao_de_video()
    print("--- EXECUÇÃO DIRETA CONCLUÍDA ---")

