from flask import Flask, jsonify, request
import requests
import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import os

app = Flask(__name__)

def parse_weather_clima_com(html_content, timezone_offset=-5):
    """
    Parsea datos de clima.com
    timezone_offset: Diferencia de horas respecto a UTC
    Para Ecuador (Riobamba): -5
    """
    weather_data = {}
    
    # Obtener hora actual del sistema y ajustar por zona horaria
    now_utc = datetime.utcnow()
    now = now_utc + timedelta(hours=timezone_offset)
    
    weather_data['horaActualizacion'] = now.strftime('%H:%M:%S')
    weather_data['fechaActualizacion'] = now.strftime('%d/%m/%Y')
    weather_data['timestamp'] = int(now_utc.timestamp())
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Extraer ubicación del título o header
    title = soup.find('title')
    if title:
        # Ejemplo: "Clima en Riobamba, Chimborazo"
        title_text = title.get_text()
        if 'Riobamba' in title_text:
            weather_data['ubicacion'] = 'Riobamba, Chimborazo'
    
    # Extraer temperatura (buscar números con °)
    temp_pattern = re.search(r'(\d{1,2})\s*°\s*C', html_content)
    if temp_pattern:
        weather_data['temperatura'] = temp_pattern.group(1) + '°C'
    
    # Extraer descripción del clima
    desc_patterns = [
        'Despejado', 'Despejada', 'Clear',
        'Nublado', 'Cloudy', 'Nubes',
        'Lluvia', 'Rain', 'Rainy',
        'Llovizna', 'Drizzle',
        'Tormenta', 'Storm', 'Thunderstorm',
        'Parcialmente nublado', 'Partly Cloudy',
        'Cubierto', 'Overcast'
    ]
    for desc in desc_patterns:
        if desc.lower() in html_content.lower():
            weather_data['descripcion'] = desc
            break
    
    # Extraer sensación térmica (Feels like, Se siente, etc)
    sensacion_pattern = re.search(
        r'(?:Se\s+siente|Feels\s+like|Sensación)[:\s]+(\d{1,2})\s*°', 
        html_content, 
        re.IGNORECASE
    )
    if sensacion_pattern:
        weather_data['sensacion'] = sensacion_pattern.group(1) + '°C'
    
    # Extraer viento
    viento_pattern = re.search(r'(\d{1,3})\s*(?:km/h|Km/h|KM/H)', html_content)
    if viento_pattern:
        weather_data['viento'] = viento_pattern.group(1) + ' km/h'
    
    # Extraer humedad
    humedad_pattern = re.search(
        r'(?:Humedad|Humidity)[:\s]+(\d{1,3})\s*%', 
        html_content, 
        re.IGNORECASE
    )
    if humedad_pattern:
        weather_data['humedad'] = humedad_pattern.group(1) + '%'
    
    # Extraer nubes
    nubes_pattern = re.search(
        r'(?:Nubes|Clouds)[:\s]+(\d{1,3})\s*%', 
        html_content, 
        re.IGNORECASE
    )
    if nubes_pattern:
        weather_data['nubes'] = nubes_pattern.group(1) + '%'
    
    # Extraer presión
    presion_pattern = re.search(
        r'(?:Presión|Pressure)[:\s]+(\d{4})\s*(?:mb|hPa|Pa)', 
        html_content, 
        re.IGNORECASE
    )
    if presion_pattern:
        weather_data['presion'] = presion_pattern.group(1) + ' hPa'
    
    # Extraer radiación UV
    radiacion_pattern = re.search(
        r'(?:Radiación\s*UV|UV\s+Index)[:\s]+(\d{1,2}(?:\.\d)?)', 
        html_content, 
        re.IGNORECASE
    )
    if radiacion_pattern:
        weather_data['radiacionUv'] = radiacion_pattern.group(1)
    
    # Extraer hora de actualización
    hora_pattern = re.search(
        r'(?:Actualizado|Updated)[:\s]+(\d{1,2}):(\d{2})', 
        html_content, 
        re.IGNORECASE
    )
    if hora_pattern:
        hora_str = f"{hora_pattern.group(1)}:{hora_pattern.group(2)}"
        weather_data['horaClima'] = hora_str
        
        # Calcular diferencia de minutos
        try:
            hora_partes = hora_str.split(':')
            hora_clima = now.replace(
                hour=int(hora_partes[0]), 
                minute=int(hora_partes[1]), 
                second=0, 
                microsecond=0
            )
            diferencia_segundos = (now - hora_clima).total_seconds()
            diferencia_minutos = abs(diferencia_segundos / 60)
            
            # Si la diferencia es muy grande, ajustar
            if diferencia_minutos > 720:
                diferencia_minutos = abs(diferencia_minutos - 1440)
            
            weather_data['minutosDesdeActualizacion'] = int(diferencia_minutos)
            weather_data['esEnTiempoReal'] = diferencia_minutos < 30
            
            if diferencia_minutos < 30:
                weather_data['estadoDato'] = 'EN TIEMPO REAL'
            else:
                weather_data['estadoDato'] = f'DESACTUALIZADO ({int(diferencia_minutos)} min)'
        except:
            weather_data['esEnTiempoReal'] = False
            weather_data['estadoDato'] = 'ERROR AL CALCULAR HORA'
    else:
        weather_data['esEnTiempoReal'] = False
        weather_data['estadoDato'] = 'SIN HORA DETECTADA'
    
    return weather_data

def scrape_weather(url):
    """Scrapeada el contenido HTML de la URL"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9',
            'Referer': 'https://www.google.com/'
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"Error scraping: {str(e)}")
        return None

@app.route('/', methods=['GET'])
def health():
    """Health check"""
    return jsonify({
        'status': 'API running', 
        'endpoints': {
            'GET /clima': 'Scrapea clima.com',
            'POST /clima/parse': 'Parsea HTML proporcionado'
        }
    }), 200

@app.route('/clima', methods=['GET'])
def get_clima():
    """
    Obtiene datos del clima de Riobamba desde clima.com
    
    Query params:
    - url: URL a scrapear (opcional, default: Riobamba)
    - ejemplo: /clima o /clima?url=https://www.clima.com/ecuador/chimborazo/riobamba
    """
    url = request.args.get('url', 'https://www.clima.com/ecuador/chimborazo/riobamba')
    
    print(f"Scraping: {url}")
    
    # Scrapear el contenido
    html_content = scrape_weather(url)
    
    if not html_content:
        return jsonify({
            'error': 'No se pudo obtener el contenido de la URL',
            'url': url,
            'status': 'error'
        }), 500
    
    # Parsear datos
    weather_data = parse_weather_clima_com(html_content)
    
    return jsonify(weather_data), 200

@app.route('/clima/parse', methods=['POST'])
def parse_clima():
    """
    Parsea datos de clima desde contenido HTML proporcionado
    
    Body (JSON):
    {
        "html": "<contenido HTML aquí>"
    }
    """
    data = request.get_json()
    
    if not data or 'html' not in data:
        return jsonify({
            'error': 'Falta parámetro "html" en el body'
        }), 400
    
    html_content = data['html']
    weather_data = parse_weather_clima_com(html_content)
    
    return jsonify(weather_data), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
