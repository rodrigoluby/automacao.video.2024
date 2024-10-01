import streamlit as st
import ffmpeg
import os
import xml.etree.ElementTree as ET
import zipfile
import boto3
from botocore.exceptions import NoCredentialsError
from dotenv import load_dotenv
from io import BytesIO

# Carrega as variáveis de ambiente do .env
load_dotenv()

# Função para processar o arquivo de mídia usando ffmpeg
def process_media(file):
    probe = ffmpeg.probe(file)
    return probe

# Função para gerar thumbnails a cada minuto e salvar diretamente no S3
def generate_thumbnails(media_file, duration, bucket):
    s3 = boto3.client('s3',
                      aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                      aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                      region_name=os.getenv('AWS_REGION'))

    thumbnail_paths = []
    for i in range(0, int(duration), 60):
        thumbnail_buffer = BytesIO()
        (
            ffmpeg
            .input(media_file, ss=i)
            .output('pipe:', vframes=1, format='png')
            .run(overwrite_output=True, capture_stdout=thumbnail_buffer)
        )
        
        thumbnail_buffer.seek(0)
        thumbnail_path = f"thumbnails/thumbnail_{i}.png"
        s3.upload_fileobj(thumbnail_buffer, bucket, thumbnail_path)
        thumbnail_paths.append(thumbnail_path)
    
    return thumbnail_paths

# Função para gerar o XML ADI 1.1 com base nos campos e dados extraídos e salvar no S3
def generate_adi_xml(fields, media_data, bucket):
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
        if stream_type == 'video':
            ET.SubElement(title, 'App_Data', Name=f'{stream_type}_Width', Value=str(stream.get('width', '')))
            ET.SubElement(title, 'App_Data', Name=f'{stream_type}_Height', Value=str(stream.get('height', '')))
        elif stream_type == 'audio':
            ET.SubElement(title, 'App_Data', Name=f'{stream_type}_Channels', Value=str(stream.get('channels', '')))
    
    # Converter o XML para string e enviar para o S3
    adi_xml_str = ET.tostring(adi, encoding='utf8', method='xml').decode()
    adi_buffer = BytesIO(adi_xml_str.encode('utf-8'))
    
    s3 = boto3.client('s3',
                      aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                      aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                      region_name=os.getenv('AWS_REGION'))
    
    adi_xml_path = "adi/adi.xml"
    s3.upload_fileobj(adi_buffer, bucket, adi_xml_path)

    return adi_xml_path

# Função para zipar os arquivos diretamente no S3
def zip_files(adi_xml, media_file, thumbnail_files, bucket):
    zip_buffer = BytesIO()
    
    with zipfile.ZipFile(zip_buffer, "w") as zipf:
        # Adiciona o XML ADI no zip
        s3 = boto3.client('s3',
                          aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                          aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                          region_name=os.getenv('AWS_REGION'))

        zipf.writestr("adi.xml", adi_xml)

        # Adiciona o arquivo de mídia
        media_file.seek(0)
        zipf.writestr(os.path.basename(media_file.name), media_file.read())
        
        # Adiciona thumbnails
        for thumbnail in thumbnail_files:
            thumbnail_buffer = BytesIO()
            s3.download_fileobj(bucket, thumbnail, thumbnail_buffer)
            thumbnail_buffer.seek(0)
            zipf.writestr(os.path.basename(thumbnail), thumbnail_buffer.read())

    # Upload do arquivo zip para o S3
    zip_buffer.seek(0)
    zip_filename = "output.zip"
    s3.upload_fileobj(zip_buffer, bucket, zip_filename)

    return zip_filename

# Função para enviar o arquivo para o S3
def upload_to_s3(file_name, bucket):
    region = os.getenv('AWS_REGION')
    return f"https://{bucket}.s3.{region}.amazonaws.com/{file_name}"

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
    bucket = os.getenv('AWS_S3_BUCKET_NAME')
    
    # Processar mídia com ffmpeg
    media_data = process_media(media_file)
    st.json(media_data)

    # Extrair a duração do vídeo
    duration = float(media_data['format']['duration'])

    # Gerar thumbnails a cada 1 minuto e salvar diretamente no S3
    thumbnail_files = generate_thumbnails(media_file.name, duration, bucket)
    st.success(f"Thumbnails gerados e enviados para o S3: {len(thumbnail_files)}")

    # Gerar o XML ADI 1.1 e salvar no S3
    adi_xml = generate_adi_xml(fields, media_data, bucket)
    st.text_area("ADI XML", adi_xml)

    # Zipa os arquivos e envia diretamente para o S3
    zip_filename = zip_files(adi_xml, media_file, thumbnail_files, bucket)

    # Upload automático do arquivo zip para o S3
    s3_url = upload_to_s3(zip_filename, bucket)
    if s3_url:
        st.success(f"Arquivo enviado para o S3! [Baixar ZIP]({s3_url})")
