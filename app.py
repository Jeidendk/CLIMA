from flask import Flask, jsonify, request
import requests
import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import os

app = Flask(__name__)

def parse_weather_clima_com(html_content, timezone_offset=-5):
    """
    Parsea datos de clima.com usando una combinación de data-key-values y scraping de texto
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
    
    # --- ESTRATEGIA 1: Extraer del JSON en data-key-values (Rápido y estructurado) ---
    sticky_banner = soup.find('div', id='sticky-banner')
    json_data = {}
    if sticky_banner and sticky_banner.has_attr('data-key-values'):
        try:
            json_data = json.loads(sticky_banner['data-key-values'])
            
            # Ubicación: Construir desde 'urlized' si es posible para tener "Riobamba, Chimborazo"
            # formato urlized: "ecuador/chimborazo/riobamba"
            if 'urlized' in json_data:
                parts = json_data['urlized'].split('/')
                if len(parts) >= 3:
                    city = parts[2].replace('-', ' ').upper()
                    province = parts[1].replace('-', ' ').upper()
                    weather_data['ubicacion'] = f"{city}, {province}"
            
            # Fallback ubicación simple
            if 'ubicacion' not in weather_data:
                city = json_data.get('poi_name', 'RIOBAMBA').upper()
                region = json_data.get('region', '').replace('canton-', 'CANTÓN ').replace('-', ' ').upper()
                weather_data['ubicacion'] = f"{city}, {region}"

            # Datos Básicos del JSON
            if 'temp_c' in json_data: weather_data['temperatura'] = f"{json_data['temp_c']}°"
            if 'humidity' in json_data: weather_data['humedad'] = f"{json_data['humidity']}%"
            if 'wind_speed' in json_data: weather_data['viento'] = f"{json_data['wind_speed']} km/h"
            if 'pressure' in json_data: weather_data['presion'] = f"{json_data['pressure']} hPa"
            if 'uv_radiation' in json_data: weather_data['radiacionUv'] = str(json_data['uv_radiation'])
            
            # Descripción (traducir)
            desc_map = {
                'cloudy': 'Nublado',
                'partly_cloudy': 'Parcialmente nublado',
                'sunny': 'Soleado',
                'clear': 'Despejado',
                'rain': 'Lluvia',
                'storm': 'Tormenta',
                'snow': 'Nieve',
                'fog': 'Niebla',
                'despejado': 'Despejado',
                'parcialmente nuboso': 'Parcialmente nublado',
                'cubierto': 'Nublado'
            }
            raw_desc = json_data.get('clouds_level', '') or json_data.get('precip_type', '')
            weather_data['descripcion'] = desc_map.get(raw_desc.lower(), raw_desc.capitalize())
            
        except Exception as e:
            print(f"Error parseando JSON: {e}")

    # --- ESTRATEGIA 2: Scraping de Texto para datos faltantes (Nubes, Sensación, arreglos) ---
    
    full_text = soup.get_text(separator=' ', strip=True)

    # Sensación Térmica
    if 'sensacion' not in weather_data:
        # Buscar texto "Sensación" seguido de números
        sensacion_match = re.search(r'(?:Sensación|Feels like).*?(\d+)', full_text, re.IGNORECASE)
        if sensacion_match:
            weather_data['sensacion'] = f"{sensacion_match.group(1)}°"
        elif 'temperatura' in weather_data:
             weather_data['sensacion'] = weather_data['temperatura']
    
    # Porcentaje de Nubes
    # Buscar "Nubes" seguido de un número y % en todo el texto limpio
    if 'nubes' not in weather_data:
        nubes_match = re.search(r'Nubes\s+(\d+)\s*%', full_text, re.IGNORECASE)
        if nubes_match:
            weather_data['nubes'] = f"{nubes_match.group(1)}%"
        else:
            weather_data['nubes'] = '--%'
        
    # Arreglo descripción
    if 'descripcion' not in weather_data or not weather_data['descripcion']:
         weather_data['descripcion'] = 'Nublado'

    # Estado
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
