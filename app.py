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

    # --- ESTRATEGIA 1: Extraer de 'dataLayer' (Fuente más confiable de visualización) ---
    # dataLayer = [{ 'currentTemperature':'17ºC', ... }];
    data_layer_match = re.search(r'dataLayer\s*=\s*\[(\{.*?\})\];', str(soup), re.DOTALL)
    dl_found = False
    
    if data_layer_match:
        try:
            # Es JS, no JSON estricto (comillas simples). Usamos regex para sacar campos.
            dl_text = data_layer_match.group(1)
            
            # Temperatura
            temp_m = re.search(r"'currentTemperature'\s*:\s*'([^']*)'", dl_text)
            if temp_m: 
                weather_data['temperatura'] = temp_m.group(1).replace('ºC', '°').replace('C', '')
                dl_found = True
            
            # Ubicación (intentar sacar de provincia/ciudad)
            prov_m = re.search(r"'province'\s*:\s*'([^']*)'", dl_text)
            city_m = re.search(r"'productCity'\s*:\s*'([^']*)'", dl_text) # ej: ecuador/chimborazo/riobamba
            
            if city_m:
                parts = city_m.group(1).split('/')
                if len(parts) >= 3:
                     city = parts[2].replace('-', ' ').upper()
                     prov = parts[1].replace('-', ' ').upper()
                     weather_data['ubicacion'] = f"{city}, {prov}"
            
            # Descripción
            desc_m = re.search(r"'weatherForecast'\s*:\s*'([^']*)'", dl_text)
            if desc_m:
                raw_desc = desc_m.group(1)
                # Mapeo simple
                desc_map = {
                    'Cloudy': 'Nublado', 'Partly cloudy': 'Parcialmente nublado',
                    'Sunny': 'Soleado', 'Clear': 'Despejado', 'Rain': 'Lluvia'
                }
                weather_data['descripcion'] = desc_map.get(raw_desc, raw_desc)
                
            # Otros datos del dataLayer
            hum_m = re.search(r"'humidity'\s*:\s*'([^']*)'", dl_text)
            if hum_m: weather_data['humedad'] = f"{hum_m.group(1)}%"

            wind_m = re.search(r"'windSpeed'\s*:\s*'([^']*)'", dl_text)
            if wind_m: weather_data['viento'] = wind_m.group(1)
            
            press_m = re.search(r"'pressure'\s*:\s*'([^']*)'", dl_text)
            if press_m: weather_data['presion'] = f"{press_m.group(1)} hPa"
            
            uv_m = re.search(r"'uv_radiation'\s*:\s*'([^']*)'", dl_text)
            if uv_m: weather_data['radiacionUv'] = uv_m.group(1)
            
            # Probabilidad de precipitación (puede dar pista sobre nubes)
            precip_m = re.search(r"'precipitationProbability'\s*:\s*'([^']*)'", dl_text)
            if precip_m: 
                precip_val = precip_m.group(1)
                # Si es alta probabilidad, asumir mucha cobertura de nubes
                try:
                    if int(precip_val) > 70:
                        weather_data['nubes'] = f"{precip_val}%"
                except:
                    pass

        except Exception as e:
            print(f"Error parseando dataLayer: {e}")

    # --- ESTRATEGIA 2: Extraer del JSON sticky banner (Fallback) ---
    sticky_banner = soup.find('div', id='sticky-banner')
    if sticky_banner and sticky_banner.has_attr('data-key-values') and not weather_data.get('temperatura'):
        try:
            json_data = json.loads(sticky_banner['data-key-values'])
            # ... (código existente simplificado)
            if 'temp_c' in json_data: weather_data['temperatura'] = f"{json_data['temp_c']}°"
            # ... Resto de campos solo si no están ya
        except:
             pass

    # --- ESTRATEGIA 3: Scraping de Texto (Nubes, Sensación, arreglos finales) ---
    full_text = soup.get_text(separator=' ', strip=True)

    # Sensación Térmica
    if 'sensacion' not in weather_data:
        sensacion_match = re.search(r'(?:Sensación|Feels like).*?(\d+)', full_text, re.IGNORECASE)
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
