from flask import Flask, jsonify, request
import requests
import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import os

app = Flask(__name__)

def parse_weather_clima_com(html_content, timezone_offset=-5):
    """
    Parsea datos de clima.com usando data-key-values del sticky banner (más robusto)
    """
    import json
    weather_data = {}
    
    # Obtener hora actual
    now_utc = datetime.utcnow()
    now = now_utc + timedelta(hours=timezone_offset)
    
    weather_data['horaActualizacion'] = now.strftime('%H:%M:%S')
    weather_data['fechaActualizacion'] = now.strftime('%d/%m/%Y')
    weather_data['timestamp'] = int(now_utc.timestamp())
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. Intentar extraer del JSON en data-key-values (Método Preferido)
    sticky_banner = soup.find('div', id='sticky-banner')
    if sticky_banner and sticky_banner.has_attr('data-key-values'):
        try:
            data_str = sticky_banner['data-key-values']
            data_json = json.loads(data_str)
            
            # Ubicación
            city = data_json.get('poi_name', 'Riobamba')
            region = data_json.get('region', 'Chimborazo')
            weather_data['ubicacion'] = f"{city}, {region}"
            
            # Temperatura
            if 'temp_c' in data_json:
                weather_data['temperatura'] = f"{data_json['temp_c']}°"
            
            # Humedad
            if 'humidity' in data_json:
                weather_data['humedad'] = f"{data_json['humidity']}%"
            
            # Viento
            if 'wind_speed' in data_json:
                weather_data['viento'] = f"{data_json['wind_speed']} km/h"
                
            # Presión
            if 'pressure' in data_json:
                weather_data['presion'] = f"{data_json['pressure']} hPa"
            
            # UV
            if 'uv_radiation' in data_json:
                weather_data['radiacionUv'] = str(data_json['uv_radiation'])
                
            # Descripción (en inglés en el JSON, ej: "cloudy")
            # Mapeo simple
            desc_map = {
                'cloudy': 'Nublado',
                'partly_cloudy': 'Parcialmente nublado',
                'sunny': 'Soleado',
                'clear': 'Despejado',
                'rain': 'Lluvia',
                'storm': 'Tormenta',
                'snow': 'Nieve',
                'fog': 'Niebla'
            }
            raw_desc = data_json.get('clouds_level', '') or data_json.get('precip_type', '')
            weather_data['descripcion'] = desc_map.get(raw_desc, raw_desc.capitalize())
            
            # Sensación - A veces no está en este JSON, intentar calcular o dejar vacío
            
        except Exception as e:
            print(f"Error parseando data-key-values: {e}")

    # 2. Fallbacks y datos extra que no estén en el JSON (ej: Sensación, Descripción visual)
    
    # Si falta ubicación
    if 'ubicacion' not in weather_data:
        title = soup.find('title')
        if title:
            title_text = title.get_text()
            if 'Riobamba' in title_text:
                weather_data['ubicacion'] = 'Riobamba, Chimborazo'
    
    # Si falta temperatura (scraping clásico como backup)
    if 'temperatura' not in weather_data:
        temp_pattern = re.search(r'(\d{1,2})\s*°', html_content)
        if temp_pattern:
            weather_data['temperatura'] = temp_pattern.group(1) + '°'
            
    # Sensación térmica (generalmente visible en texto)
    if 'sensacion' not in weather_data:
        sensacion_pattern = re.search(
            r'(?:Se\s+siente|Feels\s+like|Sensación).*?(\d{1,2})', 
            html_content, 
            re.IGNORECASE
        )
        if sensacion_pattern:
            weather_data['sensacion'] = sensacion_pattern.group(1) + '°'
        elif 'temperatura' in weather_data:
             # Fallback: Usar temperatura como sensación si no hay dato
             weather_data['sensacion'] = weather_data['temperatura']

    # Descripción en español si el JSON falló o dio inglés
    if 'descripcion' not in weather_data or weather_data['descripcion'] in ['Cloudy', 'Clear', 'none']:
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

    # Estado del dato
    weather_data['esEnTiempoReal'] = True
    weather_data['estadoDato'] = 'EN TIEMPO REAL'
    weather_data['minutosDesdeActualizacion'] = 0
    weather_data['horaClima'] = now.strftime('%H:%M')
    
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
