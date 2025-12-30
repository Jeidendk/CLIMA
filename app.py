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
    
    # CRÍTICO: Inicializar BeautifulSoup
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # --- ESTRATEGIA: Obtener hora REAL de actualización del sitio ---
    # Buscar "Actualizado 10:46" para mostrar la hora real del dato, no del fetch
    hora_real_str = now.strftime('%H:%M')
    minutos_diff = 0
    estado_dato = "EN TIEMPO REAL"
    
    actualizado_match = re.search(r'Actualizado\s+(\d{1,2}:\d{2})', soup.get_text(), re.IGNORECASE)
    if actualizado_match:
        hora_real_str = actualizado_match.group(1)
        weather_data['horaClima'] = hora_real_str
        weather_data['horaActualizacion'] = f"{hora_real_str}:00" # Estimado segs
        
        # Calcular diferencia para "minutosDesdeActualizacion"
        try:
            h, m = map(int, hora_real_str.split(':'))
            fecha_dato = now.replace(hour=h, minute=m, second=0, microsecond=0)
            # Ajuste de día si el dato es de ayer (muy raro, pero posible cerca de medianoche)
            if fecha_dato > now + timedelta(minutes=15): 
                fecha_dato -= timedelta(days=1)
                
            diff = (now - fecha_dato).total_seconds() / 60
            minutos_diff = int(max(0, diff))
            
            if minutos_diff > 60:
                estado_dato = f"DESACTUALIZADO ({minutos_diff} min)"
            else:
                 estado_dato = "EN TIEMPO REAL"
                 
            weather_data['minutosDesdeActualizacion'] = minutos_diff
            weather_data['estadoDato'] = estado_dato
        except:
            pass
    else:
        # Fallback si no se encuentra "Actualizado"
        weather_data['horaClima'] = now.strftime('%H:%M')
        weather_data['minutosDesdeActualizacion'] = 0
        weather_data['estadoDato'] = "EN TIEMPO REAL"

    # --- ESTRATEGIA 1: Extraer del JSON sticky banner (MÁS COMPLETO) ---
    sticky_banner = soup.find('div', id='sticky-banner')
    if sticky_banner and sticky_banner.has_attr('data-key-values'):
        try:
            json_data = json.loads(sticky_banner['data-key-values'])
            
            # Ubicación: desde urlized
            if 'urlized' in json_data:
                parts = json_data['urlized'].split('/')
                if len(parts) >= 3:
                     city = parts[2].replace('-', ' ').upper()
                     prov = parts[1].replace('-', ' ').upper()
                     weather_data['ubicacion'] = f"{city}, {prov}"
            
            # Temperatura
            if 'temp_c' in json_data: 
                weather_data['temperatura'] = f"{json_data['temp_c']}°"
            
            # Humedad, Presión, UV (CLAVES: estos SÍ están en data-key-values)
            if 'humidity' in json_data: 
                weather_data['humedad'] = f"{json_data['humidity']}%"
            
            if 'pressure' in json_data: 
                weather_data['presion'] = f"{json_data['pressure']} hPa"
            
            if 'uv_radiation' in json_data: 
                weather_data['radiacionUv'] = str(json_data['uv_radiation'])
            
            # Viento
            if 'wind_speed' in json_data: 
                weather_data['viento'] = f"{json_data['wind_speed']} km/h"
            
            # Descripción
            desc_map = {
                'cloudy': 'Nublado',
                'partly_cloudy': 'Parcialmente nublado',
                'sunny': 'Soleado',
                'clear': 'Despejado',
                'rain': 'Lluvia'
            }
            raw_desc = json_data.get('clouds_level', '') or json_data.get('precip_type', '')
            if raw_desc:
                weather_data['descripcion'] = desc_map.get(raw_desc.lower(), raw_desc.capitalize())
            
        except Exception as e:
            print(f"Error parseando sticky-banner JSON: {e}")

    # --- ESTRATEGIA 2: Extraer de 'dataLayer' (Fallback) ---
    data_layer_match = re.search(r'dataLayer\s*=\s*\[(\{.*?\})\];', str(soup), re.DOTALL)
    
    if data_layer_match:
        try:
            dl_text = data_layer_match.group(1)
            
            # Solo extraer si no tenemos ya
            if 'temperatura' not in weather_data:
                temp_m = re.search(r"'currentTemperature'\s*:\s*'([^']*)'", dl_text)
                if temp_m: 
                    weather_data['temperatura'] = temp_m.group(1).replace('ºC', '°').replace('C', '')
            
            if 'viento' not in weather_data:
                wind_m = re.search(r"'windSpeed'\s*:\s*'([^']*)'", dl_text)
                if wind_m: weather_data['viento'] = wind_m.group(1)
            
            if 'descripcion' not in weather_data:
                desc_m = re.search(r"'weatherForecast'\s*:\s*'([^']*)'", dl_text)
                if desc_m:
                    raw_desc = desc_m.group(1)
                    desc_map = {
                        'Cloudy': 'Nublado', 'Partly cloudy': 'Parcialmente nublado',
                        'Sunny': 'Soleado', 'Clear': 'Despejado', 'Rain': 'Lluvia'
                    }
                    weather_data['descripcion'] = desc_map.get(raw_desc, raw_desc)

        except Exception as e:
            print(f"Error parseando dataLayer: {e}")

    # --- ESTRATEGIA 3: Scraping de Texto (Nubes, Sensación, arreglos finales) ---
    full_text = soup.get_text(separator=' ', strip=True)

    # Sensación Térmica (MEJORADO: evitar capturar año)
    if 'sensacion' not in weather_data:
        # Buscar específicamente después de "Sensación" y antes de cualquier letra
        sensacion_match = re.search(r'Sensación[^\d]*?(\d{1,2})°', full_text, re.IGNORECASE)
        if sensacion_match:
            weather_data['sensacion'] = f"{sensacion_match.group(1)}°"
        elif 'temperatura' in weather_data:
             weather_data['sensacion'] = weather_data['temperatura']
    
    # Nubes
    if 'nubes' not in weather_data:
        nubes_match = re.search(r'Nubes\s+(\d+)\s*%', full_text, re.IGNORECASE)
        if nubes_match:
            weather_data['nubes'] = f"{nubes_match.group(1)}%"
        else:
            weather_data['nubes'] = '--%'
    
    # Humedad (fallback texto si no está en dataLayer)
    if 'humedad' not in weather_data:
        hum_match = re.search(r'Humedad[^\d]*?(\d{1,3})\s*%', full_text, re.IGNORECASE)
        if hum_match:
            weather_data['humedad'] = f"{hum_match.group(1)}%"
        else:
            weather_data['humedad'] = '--%'
    
    # Presión (fallback texto)
    if 'presion' not in weather_data:
        pres_match = re.search(r'Presión[^\d]*?(\d{3,4})\s*(?:hPa|mb)', full_text, re.IGNORECASE)
        if pres_match:
            weather_data['presion'] = f"{pres_match.group(1)} hPa"
        else:
            weather_data['presion'] = '--'
    
    # Radiación UV (fallback texto)
    if 'radiacionUv' not in weather_data:
        uv_match = re.search(r'(?:UV|Radiación UV)[^\d]*?(\d{1,2})', full_text, re.IGNORECASE)
        if uv_match:
            weather_data['radiacionUv'] = uv_match.group(1)
        else:
            weather_data['radiacionUv'] = '--'
            
    # Garantizar ubicación mayúsculas
    if 'ubicacion' not in weather_data:
         weather_data['ubicacion'] = "RIOBAMBA, CHIMBORAZO" # Fallback final
    elif weather_data['ubicacion'].islower() or 'canton' in weather_data['ubicacion'].lower():
         weather_data['ubicacion'] = weather_data['ubicacion'].upper()
         
    # Garantizar descripción
    if 'descripcion' not in weather_data or not weather_data['descripcion']:
         weather_data['descripcion'] = 'Nublado'

    weather_data['esEnTiempoReal'] = (minutos_diff < 60)
    
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
