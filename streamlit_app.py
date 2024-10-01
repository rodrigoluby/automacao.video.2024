import streamlit as st
import ffmpeg
import os
import xml.etree.ElementTree as ET
import zipfile
import boto3
from botocore.exceptions import NoCredentialsError
from dotenv import load_dotenv

# Carrega as variáveis de ambiente do .env
load_dotenv()

# Função para processar o arquivo de mídia usando ffmpeg
def process_media(file):
    probe = ffmpeg.probe(file)
    return probe

# Função para gerar thumbnails a cada minuto
def generate_thumbnails(media_file, duration):
    thumbnails_dir = "thumbnails"
    os.makedirs(thumbnails_dir, exist_ok=True)
    
    thumbnail_paths = []
    for i in range(0, int(duration), 60):
        thumbnail_path = os.path.join(thumbnails_dir, f"thumbnail_{i}.png")
        (
            ffmpeg
            .input(media_file, ss=i)
            .output(thumbnail_path, vframes=1)
            .run(overwrite_output=True)
        )
        thumbnail_paths.append(thumbnail_path)
    
    return thumbnail_paths

# Função para gerar o XML ADI 1.1 com base nos campos e dados extraídos
def generate_adi_xml(fields, media_data):
    adi = ET.Element('ADI', version='1.1')
    asset = ET.SubElement(adi, 'Asset', Asset_Class='title')
    
    title = ET.SubElement(asset, 'Metadata')
    ET.SubElement(title, 'AMS', Provider_ID='provider_id', Asset_Name=fields['Title'],
                  Version_Major='1', Version_Minor='1', Product='VOD')
    
    for key, value in fields.items():
        ET.SubElement(title, 'App_Data', Name=key, Value=value)
    
    # Inclui os dados do FFmpeg no XML
    format_data = media_data.get('format', {})
    ET.SubElement(title, 'App_Data', Name='Duration', Value=str(format_data.get('duration', '')))
    ET.SubElement(title, 'App_Data', Name='Bitrate', Value=str(format_data.get('bit_rate', '')))
    ET.SubElement(title, 'App_Data', Name='Format', Value=format_data.get('format_name', ''))

    # Adicionando informações de streams (vídeo, áudio, etc.)
    for stream in media_data.get('streams', []):
        stream_type = stream.get('codec_type', 'unknown')
        ET.SubElement(title, 'App_Data', Name=f'{stream_type}_Codec', Value=stream.get('codec_name', ''))
        ET.SubElement(title, 'App_Data', Name=f'{stream_type}_Width', Value=str(stream.get('width', '')) if stream_type == 'video' else '')
        ET.SubElement(title, 'App_Data', Name=f'{stream_type}_Height', Value=str(stream.get('height', '')) if stream_type == 'video' else '')
        ET.SubElement(title, 'App_Data', Name=f'{stream_type}_Channels', Value=str(stream.get('channels', '')) if stream_type == 'audio' else '')
    
    tree = ET.ElementTree(adi)
    return ET.tostring(adi, encoding='utf8', method='xml').decode()

# Função para zipar os arquivos enviados
def zip_files(adi_xml, media_file, thumbnail_files):
    zip_filename = "output.zip"
    with zipfile.ZipFile(zip_filename, "w") as zipf:
        zipf.writestr("adi.xml", adi_xml)
        zipf.write(media_file, os.path.basename(media_file))
        for thumbnail in thumbnail_files:
            zipf.write(thumbnail, os.path.basename(thumbnail))
    return zip_filename

# Função para enviar o arquivo para o S3
def upload_to_s3(file_name):
    # Carrega as variáveis de ambiente
    bucket = os.getenv('AWS_S3_BUCKET_NAME')
    aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
    aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
    region = os.getenv('AWS_REGION')

    # Cria o cliente S3 com as credenciais do .env
    s3 = boto3.client('s3',
                      aws_access_key_id=aws_access_key,
                      aws_secret_access_key=aws_secret_key,
                      region_name=region)

    object_name = file_name
    try:
        s3.upload_file(file_name, bucket, object_name)
        return f"https://{bucket}.s3.{region}.amazonaws.com/{object_name}"
    except FileNotFoundError:
        st.error("O arquivo não foi encontrado.")
        return None
    except NoCredentialsError:
        st.error("Credenciais AWS não encontradas.")
        return None

# Interface do Streamlit
st.title("Automatização de vídeos")

# Inputs para o formulário baseado no XML
fields = {}
fields['Title'] = st.text_input("Título", "(ex: Episódio 01)")
fields['Original_Title'] = st.text_input("Título Original", "(ex: Episódio 01)")
fields['Summary_Short'] = st.text_input("Resumo Curto", "(ex: Resumo curto do episódio)")
fields['Summary_Long'] = st.text_area("Resumo Longo", "(ex: Resumo longo do episódio)")

# Upload do arquivo de mídia e thumbnail
media_file = st.file_uploader("Escolha o arquivo de mídia", type=["mp4", "mkv", "avi"])

if media_file:
    # Salvando o arquivo localmente
    media_path = os.path.join("temp", media_file.name)
    
    with open(media_path, "wb") as f:
        f.write(media_file.getbuffer())

    st.success("Arquivo carregado com sucesso!")

    # Processar mídia com ffmpeg
    media_data = process_media(media_path)
    st.json(media_data)

    # Extrair a duração do vídeo
    duration = float(media_data['format']['duration'])

    # Gerar thumbnails a cada 1 minuto
    thumbnail_files = generate_thumbnails(media_path, duration)

    st.success(f"Thumbnails gerados: {len(thumbnail_files)}")

    # Gerar o XML ADI 1.1
    adi_xml = generate_adi_xml(fields, media_data)
    st.text_area("ADI XML", adi_xml)

    # Zipa os arquivos (media, thumbnails e ADI XML)
    zip_filename = zip_files(adi_xml, media_path, thumbnail_files)

    # Upload automático do arquivo zip para o S3
    s3_url = upload_to_s3(zip_filename)
    if s3_url:
        st.success(f"Arquivo enviado para o S3! [Baixar ZIP]({s3_url})")
