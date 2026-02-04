#!/usr/bin/env python3
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import json
import os
import time
import threading
import uuid

DEVICES = {}
CONFIG_FILE = "prana_config.json"
HISTORY_FILE = "prana_history.json"
HUMIDITY_TIME_FILE = "humidity_time.json"
WEATHER_HISTORY_FILE = "weather_history.json"
CLIENT_ID_FILE = "client_id.txt"
history_data = {}
humidity_time = {}
weather_history = []

WEATHER_API_KEY = "dd4519dc05e2f5c7757e7c758e2c0bb8"
FIREBASE_URL = "https://air-quality-monitor-9485c-default-rtdb.europe-west1.firebasedatabase.app"
FIREBASE_ENABLED = True
CLIENT_ID = ""
VERSION = "26"
PROPERTY_NAME = ""
PROPERTY_NAME_FILE = "property_name.txt"
KNOWN_IPS = ["192.168.40.250", "192.168.40.112", "192.168.40.130", "192.168.40.143"]

def get_property_name():
    global PROPERTY_NAME
    if os.path.exists(PROPERTY_NAME_FILE):
        with open(PROPERTY_NAME_FILE, "r") as f:
            PROPERTY_NAME = f.read().strip()
    if not PROPERTY_NAME:
        PROPERTY_NAME = input("Enter property name (e.g. 27 Verulam Ave, Purley): ").strip()
        if not PROPERTY_NAME: PROPERTY_NAME = "My Property"
        with open(PROPERTY_NAME_FILE, "w") as f:
            f.write(PROPERTY_NAME)
    return PROPERTY_NAME

def get_client_id():
    global CLIENT_ID
    if os.path.exists(CLIENT_ID_FILE):
        with open(CLIENT_ID_FILE, "r") as f:
            CLIENT_ID = f.read().strip()
    else:
        CLIENT_ID = str(uuid.uuid4())[:8]
        with open(CLIENT_ID_FILE, "w") as f:
            f.write(CLIENT_ID)
    return CLIENT_ID

def load_config():
    global DEVICES
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                DEVICES = json.load(f)
        except: pass

def save_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump(DEVICES, f)

def load_history():
    global history_data, humidity_time, weather_history
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                history_data = json.load(f)
            for ip in history_data:
                history_data[ip] = history_data[ip][-360:]
        except: pass
    if os.path.exists(HUMIDITY_TIME_FILE):
        try:
            with open(HUMIDITY_TIME_FILE, "r") as f:
                humidity_time = json.load(f)
        except: pass
    if os.path.exists(WEATHER_HISTORY_FILE):
        try:
            with open(WEATHER_HISTORY_FILE, "r") as f:
                weather_history = json.load(f)
            weather_history = weather_history[-168:]
        except: pass

def save_history():
    with open(HISTORY_FILE, "w") as f:
        json.dump(history_data, f)
    with open(HUMIDITY_TIME_FILE, "w") as f:
        json.dump(humidity_time, f)
    with open(WEATHER_HISTORY_FILE, "w") as f:
        json.dump(weather_history, f)

def save_weather_point(temp, humidity, pressure):
    global weather_history
    weather_history.append({"time": int(time.time()), "temp": temp, "humidity": humidity, "pressure": pressure})
    weather_history = weather_history[-168:]
    save_history()

last_firebase_sync = 0

def sync_to_firebase(data):
    global last_firebase_sync
    if not FIREBASE_ENABLED or not FIREBASE_URL: return
    now = time.time()
    if now - last_firebase_sync < 30: return
    last_firebase_sync = now
    try:
        devices_map = {}
        names_map = {}
        for d in data:
            ip = d.get("ip","")
            slim = {k: v for k, v in d.items() if k != "recent_history"}
            devices_map[ip.replace(".","-")] = slim
            names_map[ip.replace(".","-")] = d.get("name", ip)
        payload = {
            "property_name": PROPERTY_NAME,
            "last_seen": int(time.time()),
            "devices": devices_map,
            "names": names_map,
            "device_count": len(data),
            "online_count": len([d for d in data if d.get("online")]),
            "version": VERSION
        }
        if weather_history:
            latest_w = weather_history[-1]
            payload["weather"] = latest_w
        url = FIREBASE_URL + "/clients/" + CLIENT_ID + ".json"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="PUT")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=10)
        print("[Firebase] Synced OK - " + str(len(data)) + " devices")
    except Exception as e:
        print("[Firebase] Error: " + str(e))

def add_to_history(ip, data):
    global history_data, humidity_time
    if ip not in history_data: history_data[ip] = []
    now = int(time.time())
    h = data.get("humidity", 0)
    if ip not in humidity_time: humidity_time[ip] = {"start": None, "minutes": 0}
    if h > 65:
        if humidity_time[ip]["start"] is None: humidity_time[ip]["start"] = now
        else:
            humidity_time[ip]["minutes"] = min(1440, humidity_time[ip]["minutes"] + (now - humidity_time[ip]["start"]) / 60)
            humidity_time[ip]["start"] = now
    else:
        if humidity_time[ip]["start"]: 
            humidity_time[ip]["start"] = None
            humidity_time[ip]["minutes"] = max(0, humidity_time[ip]["minutes"] - 30)
    history_data[ip].append({
        "time": now, 
        "temp": data.get("inside_temperature", 0) / 10, 
        "outside_temp": data.get("outside_temperature", 0) / 10, 
        "humidity": h, 
        "co2": data.get("co2", 0), 
        "voc": data.get("voc", 0), 
        "pressure": data.get("air_pressure", 0)
    })
    history_data[ip] = history_data[ip][-360:]
    save_history()

def get_device_data():
    results = []
    for ip, device in list(DEVICES.items()):
        try:
            req = urllib.request.Request("http://" + ip + "/getState")
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read())
                data["name"] = device["name"]
                data["ip"] = ip
                data["online"] = True
                data["humidity_minutes"] = humidity_time.get(ip, {}).get("minutes", 0)
                data["recent_history"] = history_data.get(ip, [])[-30:]
                add_to_history(ip, data)
                results.append(data)
        except:
            results.append({"name": device["name"], "ip": ip, "online": False})
    sync_to_firebase(results)
    return results

def quick_scan():
    found = []
    for ip in KNOWN_IPS:
        try:
            req = urllib.request.Request("http://" + ip + "/getState")
            with urllib.request.urlopen(req, timeout=1) as response:
                if "inside_temperature" in json.loads(response.read()):
                    if ip not in DEVICES: DEVICES[ip] = {"name": "Room " + str(len(DEVICES)+1), "ip": ip}
                    found.append(ip)
                    print("  + " + ip)
        except: pass
    save_config()
    return found

def full_scan():
    found = []
    for ip_end in range(1, 255):
        ip = "192.168.40." + str(ip_end)
        try:
            req = urllib.request.Request("http://" + ip + "/getState")
            with urllib.request.urlopen(req, timeout=0.15) as response:
                if "inside_temperature" in json.loads(response.read()):
                    if ip not in DEVICES: DEVICES[ip] = {"name": "Room " + str(len(DEVICES)+1), "ip": ip}
                    found.append(ip)
        except: pass
    save_config()
    return found

def add_device_ip(ip):
    try:
        req = urllib.request.Request("http://" + ip + "/getState")
        with urllib.request.urlopen(req, timeout=2) as response:
            if "inside_temperature" in json.loads(response.read()):
                if ip not in DEVICES: 
                    DEVICES[ip] = {"name": "Room " + str(len(DEVICES)+1), "ip": ip}
                    save_config()
                return True
    except: pass
    return False

def auto_discovery_thread():
    while True:
        time.sleep(300)
        quick_scan()
        get_device_data()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/data":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(get_device_data()).encode())
        elif self.path == "/api/scan":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            found = full_scan()
            self.wfile.write(json.dumps({"found": len(found), "devices": list(DEVICES.keys())}).encode())
        elif self.path.startswith("/api/add_device"):
            ip = self.path.split("ip=")[-1] if "ip=" in self.path else None
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"success": add_device_ip(ip) if ip else False}).encode())
        elif self.path == "/api/history_all":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"devices": history_data, "weather": weather_history, "names": {ip: d["name"] for ip, d in DEVICES.items()}}).encode())
        elif self.path.startswith("/api/save_weather"):
            try:
                p = {x.split("=")[0]: x.split("=")[1] for x in self.path.split("?")[1].split("&")}
                save_weather_point(float(p.get("temp", 0)), float(p.get("humidity", 0)), float(p.get("pressure", 0)))
            except: pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        elif self.path.startswith("/api/rename"):
            try:
                parts = {p.split("=")[0]: urllib.request.unquote(p.split("=")[1]) for p in self.path.split("?")[1].split("&")}
                if parts.get("ip") in DEVICES: 
                    DEVICES[parts["ip"]]["name"] = parts.get("name", "")
                    save_config()
            except: pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        elif self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            html = r'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Air Quality</title>
<style>
:root{--bg:#0a0a0a;--card:#141414;--border:#222;--text:#fff;--dim:#888;--good:#22c55e;--warn:#eab308;--bad:#ef4444;--info:#3b82f6}
body.light{--bg:#f5f5f7;--card:#ffffff;--border:#d2d2d7;--text:#1d1d1f;--dim:#86868b}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;font-size:16px;transition:background 0.3s,color 0.3s}
.header{position:fixed;top:0;left:0;right:0;height:56px;display:flex;justify-content:space-between;align-items:center;padding:0 20px;background:var(--bg);border-bottom:1px solid var(--border);z-index:100}
.logo{font-size:20px;font-weight:600}
.btns{display:flex;gap:10px}
.btn{background:var(--card);border:1px solid var(--border);color:var(--dim);width:44px;height:44px;border-radius:8px;cursor:pointer;font-size:18px}
.top{position:fixed;top:56px;left:0;right:0;display:flex;flex-wrap:wrap;gap:20px;padding:16px 20px;background:var(--card);border-bottom:1px solid var(--border);z-index:99}
.wg{display:flex;gap:24px;flex-wrap:wrap}
.wi{display:flex;align-items:center;gap:8px}
.wv{font-size:20px;font-weight:600}
.wl{font-size:12px;color:var(--dim)}
.ws{font-size:12px;padding:3px 8px;border-radius:4px;margin-left:6px}
.ws-good{background:rgba(34,197,94,0.15);color:var(--good)}
.ws-warn{background:rgba(234,179,8,0.15);color:var(--warn)}
.ins{display:flex;gap:10px;flex-wrap:wrap;margin-left:auto}
.in{padding:8px 16px;border-radius:6px;font-size:14px;font-weight:500}
.in-good{background:rgba(34,197,94,0.1);color:var(--good);border:1px solid rgba(34,197,94,0.2)}
.in-warn{background:rgba(234,179,8,0.1);color:var(--warn);border:1px solid rgba(234,179,8,0.2)}
.in-bad{background:rgba(239,68,68,0.1);color:var(--bad);border:1px solid rgba(239,68,68,0.2)}
.main{padding:140px 20px 20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.room{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:20px}
.rh{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.rn{font-size:20px;font-weight:600;cursor:pointer}
.rb{font-size:13px;padding:4px 10px;border-radius:6px}
.rb-on{background:rgba(34,197,94,0.15);color:var(--good)}
.rb-off{background:rgba(239,68,68,0.15);color:var(--bad)}
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.m{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:14px;text-align:center}
.mv{font-size:32px;font-weight:300}
.ml{font-size:11px;color:var(--dim);text-transform:uppercase;margin-top:4px}
.ms{font-size:12px;font-weight:600;margin-top:6px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px}
.st{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px;text-align:center}
.stl{font-size:11px;color:var(--dim)}
.stv{font-size:16px;font-weight:600;margin-top:4px}
.air{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px}
.ab{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:14px;text-align:center}
.al{font-size:11px;color:var(--dim)}
.av{font-size:28px;font-weight:600;color:var(--good);margin-top:4px}
.au{font-size:12px;color:var(--dim)}
.ab-off .av{color:var(--dim)}
.modes{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
.mo{font-size:12px;padding:6px 14px;border-radius:6px;background:var(--bg);border:1px solid var(--border);color:var(--dim)}
.mo-on{background:rgba(34,197,94,0.15);border-color:rgba(34,197,94,0.3);color:var(--good)}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.m,.st,.ab,.mo{position:relative;cursor:pointer;transition:all 0.2s}
.m.editing::after,.st.editing::after,.ab.editing::after,.mo.editing::after{content:"×";position:absolute;top:4px;right:6px;width:22px;height:22px;background:var(--bad);color:#fff;border-radius:50%;font-size:16px;line-height:22px;text-align:center;cursor:pointer;z-index:10}
.m.editing,.st.editing,.ab.editing,.mo.editing{outline:2px solid var(--bad);outline-offset:-2px}
.metrics.flex-grow .m{flex:1}
.stats.flex-grow .st{flex:1}
.air.flex-grow .ab{flex:1}
.modes.flex-grow .mo{flex:1}
.metrics.has-hidden,.stats.has-hidden,.air.has-hidden{display:flex;flex-wrap:wrap}
.metrics.has-hidden .m,.stats.has-hidden .st{min-width:100px}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.8);z-index:200;justify-content:center;align-items:center;padding:20px}
.modal.show{display:flex}
.mbox{background:var(--card);border:1px solid var(--border);border-radius:10px;width:100%;max-width:540px;max-height:85vh;overflow:auto}
.mh{padding:20px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}
.mt{font-size:18px;font-weight:600}
.mc{background:var(--bg);border:1px solid var(--border);color:var(--text);width:36px;height:36px;border-radius:8px;cursor:pointer;font-size:18px}
.mb{padding:20px}
.cs{margin-bottom:20px}
.ct{font-size:15px;font-weight:600;margin-bottom:10px}
.ca{height:80px;background:var(--bg);border:1px solid var(--border);border-radius:6px}
.ca canvas{width:100%!important;height:100%!important}
.ci{display:flex;justify-content:space-between;font-size:11px;color:var(--dim);margin-top:6px}
.leg{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px}
.legi{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--dim)}
.legd{width:14px;height:4px;border-radius:2px}
.htab{padding:8px 14px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--dim);cursor:pointer;font-size:13px}
.htab-on{background:var(--info);border-color:var(--info);color:#fff}
.tabContent{display:block}
.anaBox{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:12px}
.anaTitle{font-size:12px;color:var(--dim);text-transform:uppercase;margin-bottom:8px}
.anaVal{font-size:28px;font-weight:600}
.anaSub{font-size:12px;color:var(--dim);margin-top:6px}
.insight{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:10px}
.insight-warn{border-left:3px solid var(--warn)}
.insight-good{border-left:3px solid var(--good)}
.insight-bad{border-left:3px solid var(--bad)}
.insightTitle{font-weight:600;margin-bottom:4px}
.insightText{font-size:13px;color:var(--dim)}
@media(max-width:700px){.top{flex-direction:column}.ins{margin-left:0}.grid{grid-template-columns:1fr}.metrics,.stats{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<div class="header">
<div class="logo">Air Quality <span style="font-size:11px;color:var(--dim);font-weight:400">v27</span></div>
<div style="display:flex;align-items:center;gap:12px">
<div id="fireStatus" style="display:flex;align-items:center;gap:6px;padding:6px 12px;background:rgba(34,197,94,0.15);border-radius:6px;font-size:12px;color:var(--good)"><span style="display:inline-block;width:8px;height:8px;background:var(--good);border-radius:50%;animation:pulse2 2s infinite"></span>Fire Alarm Active</div>
<div class="btns">
<button class="btn" id="resetBtn" onclick="resetHidden()" title="Restore hidden items" style="font-size:14px">↺</button>
<button class="btn" id="refBtn" onclick="location.reload()" title="Refresh">↻</button>
<button class="btn" id="themeBtn" onclick="toggleTheme()" title="Toggle light/dark mode">🌙</button>
<button class="btn" id="hBtn" title="History">H</button>
<button class="btn" id="aBtn" title="Add device">+</button>
</div>
</div>
</div>
<style>@keyframes pulse2{0%,100%{opacity:1}50%{opacity:0.5}}</style>
<div class="top">
<div class="wg" id="wg">
<div class="wi"><span class="wv" id="wT">--</span><span class="wl">Outside</span></div>
<div class="wi"><span class="wv" id="wH">--%</span><span class="wl">Humidity</span></div>
<div class="wi"><span class="wv" id="wP">--</span><span class="wl">hPa</span><span class="ws" id="wPS">--</span></div>
<div class="wi"><span class="wv" id="wUV">--</span><span class="wl">UV</span></div>
<div class="wi"><span class="wv" id="wAQI">--</span><span class="wl">Air Quality</span></div>
<div class="wi"><span class="wv" id="wWind">--</span><span class="wl">Wind</span></div>
<div class="wi"><span class="wv" id="wSun">--</span><span class="wl">Sunset</span></div>
<div class="wi"><span class="wv" id="wCarbon">--</span><span class="wl">CO2/kWh</span><span class="ws" id="wCarbonS">--</span></div>
<div class="wi" style="cursor:pointer" onclick="showLocModal()" title="Click to change location"><span id="wL" style="text-decoration:underline;color:var(--info)">Set Location</span><span class="wl">📍</span></div>
</div>
<div class="ins" id="ins"></div>
</div>
<div class="modal" id="locModal"><div class="mbox" style="max-width:360px"><div class="mh"><span class="mt">Set Location</span><button class="mc" onclick="document.getElementById('locModal').classList.remove('show')">X</button></div><div class="mb">
<div style="margin-bottom:16px"><input type="text" id="locInput" placeholder="Enter city name (e.g. London, Paris, New York)" style="width:100%;padding:14px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:16px"></div>
<button onclick="saveLocation()" style="width:100%;padding:14px;background:var(--info);border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer;font-size:16px;margin-bottom:10px">Save Location</button>
<button onclick="useMyLocation()" style="width:100%;padding:14px;background:var(--card);border:1px solid var(--border);border-radius:8px;color:var(--text);cursor:pointer;font-size:14px">📍 Use My Location (GPS)</button>
<div id="locStatus" style="margin-top:12px;font-size:13px;color:var(--dim);text-align:center"></div>
</div></div></div>
<div class="main"><div class="grid" id="grid">Loading...</div></div>
<div class="modal" id="hModal"><div class="mbox" style="max-width:600px"><div class="mh"><span class="mt">History & Analytics</span><button class="mc" id="hClose">X</button></div><div class="mb">
<div style="margin-bottom:16px"><select id="roomSelect" style="width:100%;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:16px;cursor:pointer"></select></div>
<div id="histTabs" style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap">
<button class="htab htab-on" data-tab="charts">Charts</button>
<button class="htab" data-tab="room">Room Info</button>
<button class="htab" data-tab="heating">Heating</button>
<button class="htab" data-tab="events">Events</button>
<button class="htab" data-tab="insights">Insights</button>
</div>
<div id="tabCharts" class="tabContent">
<div class="cs"><div class="ct">Temperature</div><div class="ca"><canvas id="cT"></canvas></div><div class="ci"><span id="tT"></span><span id="tR"></span></div></div>
<div class="cs"><div class="ct">Humidity</div><div class="ca"><canvas id="cH"></canvas></div><div class="ci"><span id="hT"></span><span id="hR"></span></div></div>
<div class="cs"><div class="ct">CO2</div><div class="ca"><canvas id="cC"></canvas></div><div class="ci"><span id="cT2"></span><span id="cR"></span></div></div>
<div class="cs"><div class="ct">VOC</div><div class="ca"><canvas id="cV"></canvas></div><div class="ci"><span id="vT"></span><span id="vR"></span></div></div>
</div>
<div id="tabRoom" class="tabContent" style="display:none">
<div class="anaBox"><div class="anaTitle">Room Size</div><div style="display:flex;gap:10px;align-items:center"><input type="number" id="roomSize" placeholder="e.g. 25" style="width:100px;padding:10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:16px"><span>m²</span><button onclick="saveRoomSize()" style="padding:10px 16px;background:var(--info);border:none;border-radius:6px;color:#fff;cursor:pointer">Save</button></div></div>
<div class="anaBox"><div class="anaTitle">Calculated Volume</div><div class="anaVal" id="roomVol">-- m³</div><div class="anaSub">Assuming 2.5m ceiling height</div></div>
<div class="anaBox"><div class="anaTitle">Air Changes per Hour</div><div class="anaVal" id="airChanges">--</div><div class="anaSub">Based on current ventilation speed</div></div>
<div class="anaBox"><div class="anaTitle">Full Room Refresh</div><div class="anaVal" id="refreshTime">-- min</div><div class="anaSub">Time to completely exchange room air</div></div>
</div>
<div id="tabHeating" class="tabContent" style="display:none">
<div class="anaBox"><div class="anaTitle">Heating Today</div><div class="anaVal" id="heatToday">-- hrs</div></div>
<div class="anaBox"><div class="anaTitle">Heating This Week</div><div class="anaVal" id="heatWeek">-- hrs</div></div>
<div class="anaBox"><div class="anaTitle">Heat Loss Rate</div><div class="anaVal" id="heatLoss">-- °C/hr</div><div class="anaSub">How fast room cools when heating OFF</div></div>
<div class="anaBox"><div class="anaTitle">Heating Timeline</div><div class="ca" style="height:60px;background:var(--bg)"><canvas id="cHeat"></canvas></div><div class="anaSub" style="margin-top:6px"><span style="color:var(--warn)">■</span> Heating ON</div></div>
</div>
<div id="tabEvents" class="tabContent" style="display:none">
<div class="anaBox"><div class="anaTitle">Showers Detected</div><div class="anaVal" id="showerCount">--</div><div class="anaSub">This week (humidity spike >20% in 10min)</div></div>
<div class="anaBox"><div class="anaTitle">Windows Opened</div><div class="anaVal" id="windowCount">--</div><div class="anaSub">This week (temp + CO2 drop)</div></div>
<div class="anaBox"><div class="anaTitle">Clothes Drying</div><div class="anaVal" id="dryingCount">--</div><div class="anaSub">This week (sustained high humidity)</div></div>
<div class="anaBox"><div class="anaTitle">Recent Events</div><div id="eventList" style="font-size:13px;color:var(--dim)">No events detected</div></div>
</div>
<div id="tabInsights" class="tabContent" style="display:none">
<div id="insightsList"></div>
</div>
</div></div></div>
<div class="modal" id="aModal"><div class="mbox" style="max-width:420px"><div class="mh"><span class="mt">Add Device</span><button class="mc" id="aClose">X</button></div><div class="mb"><button id="scanBtn" style="width:100%;padding:14px;background:var(--info);border:none;border-radius:8px;font-weight:600;cursor:pointer;color:#fff;font-size:16px">Scan Network</button><div id="scanSt" style="text-align:center;margin:14px 0;color:var(--dim)"></div><div style="margin-top:20px;padding-top:20px;border-top:1px solid var(--border)"><div style="font-size:15px;font-weight:600;margin-bottom:10px">Or enter IP:</div><div style="display:flex;gap:10px"><input type="text" id="ipIn" placeholder="192.168.40.xxx" style="flex:1;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:16px"><button id="addBtn" style="padding:12px 20px;background:var(--info);border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer;font-size:16px">Add</button></div><div id="addRes" style="margin-top:10px;font-size:14px"></div></div><div style="margin-top:20px"><div style="font-size:15px;font-weight:600;margin-bottom:10px">Devices:</div><div id="devList"></div></div></div></div></div>
<div class="modal" id="rModal"><div class="mbox" style="max-width:320px"><div class="mh"><span class="mt">Rename</span><button class="mc" id="rClose">X</button></div><div class="mb"><input type="text" id="rIn" style="width:100%;padding:14px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:16px"><input type="hidden" id="rIp"><button id="rSave" style="width:100%;margin-top:14px;padding:14px;background:var(--info);border:none;border-radius:8px;font-weight:600;cursor:pointer;color:#fff;font-size:16px">Save</button></div></div></div>
<div id="fireOverlay" style="display:none;position:fixed;inset:0;background:rgba(200,0,0,0.95);z-index:9999;flex-direction:column;justify-content:center;align-items:center;animation:firePulse 0.5s infinite alternate"><div style="font-size:100px">🔥</div><div id="fireMsg" style="font-size:42px;font-weight:bold;color:#fff;text-align:center;margin:20px;text-shadow:2px 2px 4px #000">FIRE WARNING!</div><button onclick="stopFireAlarm()" style="padding:28px 70px;font-size:32px;font-weight:bold;background:#fff;color:#c00;border:none;border-radius:16px;cursor:pointer;box-shadow:0 4px 20px rgba(0,0,0,0.3)">STOP ALARM</button></div>
<style>@keyframes firePulse{from{background:rgba(200,0,0,0.95)}to{background:rgba(255,50,0,0.95)}}</style>
<script>
var WKEY="dd4519dc05e2f5c7757e7c758e2c0bb8";
var data=[],weather={},pHist=[],pollen="Low",hist={devices:{},names:{}};
var COL=["#ff6b6b","#00bfff","#22c55e","#a855f7","#eab308","#f97316"];
var tempHistory={};
var fireAlarmActive=false;
var fireAlarmInterval=null;
var audioCtx=null;
var editMode=false;

function getHidden(){
  try{var h=localStorage.getItem("hiddenParams");return h?JSON.parse(h):[];}catch(e){return[];}
}
function setHidden(arr){
  try{localStorage.setItem("hiddenParams",JSON.stringify(arr));}catch(e){}
}
function hideParam(p){
  var h=getHidden();
  if(!h.includes(p)){h.push(p);setHidden(h);update();}
}
function resetHidden(){
  localStorage.removeItem("hiddenParams");
  editMode=false;
  update();
}
function setupHideListeners(){
  var els=document.querySelectorAll("[data-param]");
  for(var i=0;i<els.length;i++){
    els[i].onclick=function(e){
      var el=e.currentTarget;
      if(el.classList.contains("editing")){
        hideParam(el.getAttribute("data-param"));
      }else{
        var allEd=document.querySelectorAll(".editing");
        for(var j=0;j<allEd.length;j++)allEd[j].classList.remove("editing");
        el.classList.add("editing");
        editMode=true;
      }
      e.stopPropagation();
    };
  }
}
document.addEventListener("click",function(e){
  if(editMode && !e.target.closest("[data-param]")){
    var allEd=document.querySelectorAll(".editing");
    for(var j=0;j<allEd.length;j++)allEd[j].classList.remove("editing");
    editMode=false;
  }
});

function playSiren(){
  try{
    if(!audioCtx)audioCtx=new(window.AudioContext||window.webkitAudioContext)();
    var o=audioCtx.createOscillator();
    var g=audioCtx.createGain();
    o.connect(g);g.connect(audioCtx.destination);
    o.type="sawtooth";
    o.frequency.setValueAtTime(800,audioCtx.currentTime);
    o.frequency.linearRampToValueAtTime(1200,audioCtx.currentTime+0.5);
    o.frequency.linearRampToValueAtTime(800,audioCtx.currentTime+1);
    g.gain.setValueAtTime(0.4,audioCtx.currentTime);
    o.start();o.stop(audioCtx.currentTime+1);
  }catch(e){}
}

function speakAlert(msg){
  try{
    var u=new SpeechSynthesisUtterance(msg);
    u.rate=0.9;u.pitch=1.1;u.volume=1;
    window.speechSynthesis.speak(u);
  }catch(e){}
}

function startFireAlarm(msg){
  if(fireAlarmActive)return;
  fireAlarmActive=true;
  document.getElementById("fireMsg").textContent=msg;
  document.getElementById("fireOverlay").style.display="flex";
  playSiren();speakAlert(msg);
  fireAlarmInterval=setInterval(function(){
    if(fireAlarmActive){playSiren();speakAlert(msg);}
  },4000);
}

function stopFireAlarm(){
  fireAlarmActive=false;
  if(fireAlarmInterval)clearInterval(fireAlarmInterval);
  fireAlarmInterval=null;
  document.getElementById("fireOverlay").style.display="none";
  try{window.speechSynthesis.cancel();}catch(e){}
}

function checkFireRisk(d){
  if(!d.online)return;
  var t=d.inside_temperature/10;
  var voc=d.voc||0;
  var ip=d.ip;
  var now=Date.now();
  if(!tempHistory[ip])tempHistory[ip]=[];
  tempHistory[ip].push({time:now,temp:t,voc:voc});
  tempHistory[ip]=tempHistory[ip].filter(function(x){return now-x.time<30000;});
  if(tempHistory[ip].length<2)return;
  var oldest=tempHistory[ip][0];
  var rise=t-oldest.temp;
  var vocRise=voc-oldest.voc;
  // FIRE DETECTION SCENARIOS:
  // 1. VOC + Temp: VOC > 500 AND temp +3C in 30 sec
  if(voc>500 && rise>=3){startFireAlarm("FIRE! FIRE! "+d.name+"!");}
  // 2. VOC + Temp: VOC > 300 AND temp +2C in 30 sec
  else if(voc>300 && rise>=2){startFireAlarm("WARNING! Possible fire in "+d.name+"!");}
  // 3. VOC-ONLY: VOC triples or rises by 300+ in 30 sec (smoke without temp rise)
  else if(oldest.voc>0 && voc>=oldest.voc*3 && voc>200){startFireAlarm("SMOKE DETECTED! "+d.name+"!");}
  else if(vocRise>=300 && voc>400){startFireAlarm("WARNING! High VOC rise in "+d.name+"!");}
}

// Prana 150 speed to m3/h - FIXED with array lookup
var SPEED_MAP = [0, 5, 14, 21, 32, 52, 70];
function toM3h(level) {
  var n = parseInt(level, 10);
  if (isNaN(n) || n < 0) return 0;
  if (n > 6) return 70;
  return SPEED_MAP[n];
}

function dew(t,h){var a=17.27,b=237.7;return(b*((a*t)/(b+t)+Math.log(h/100)))/(a-((a*t)/(b+t)+Math.log(h/100)));}
function fmtTime(ts){var d=new Date(ts*1000);return d.toLocaleDateString([],{day:"numeric",month:"short"})+" "+d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"});}
function co2St(v){return v<600?{t:"Fresh",c:"good"}:v<800?{t:"Good",c:"good"}:v<1000?{t:"Moderate",c:"warn"}:{t:"High",c:"bad"};}
function vocSt(v){return v<50?{t:"Good",c:"good"}:v<100?{t:"Elevated",c:"warn"}:{t:"High",c:"bad"};}
function humSt(h){return h<30?{t:"Dry",c:"warn"}:h<60?{t:"Good",c:"good"}:h<70?{t:"High",c:"warn"}:{t:"Very High",c:"bad"};}
function tmpSt(t){return t<16?{t:"Cold",c:"warn"}:t<19?{t:"Cool",c:"good"}:t<24?{t:"Comfort",c:"good"}:t<27?{t:"Warm",c:"warn"}:{t:"Hot",c:"bad"};}
function condSt(t,h){var d=t-dew(t,h);return d>10?{t:"Low",c:"good"}:d>5?{t:"Medium",c:"warn"}:d>3?{t:"High",c:"bad"}:{t:"Critical",c:"bad"};}
function presSt(p){return p<1000?{t:"Low",c:"warn"}:p<1020?{t:"Normal",c:"good"}:{t:"High",c:"warn"};}
function mouldSt(h,m){return h>75||m>360?{t:"High",c:"bad"}:h>65||m>180?{t:"Medium",c:"warn"}:{t:"Low",c:"good"};}

var savedLoc=null;
try{savedLoc=JSON.parse(localStorage.getItem("weatherLoc"));}catch(e){}

function showLocModal(){
  document.getElementById("locModal").classList.add("show");
  document.getElementById("locInput").value=savedLoc?savedLoc.name:"";
  document.getElementById("locStatus").textContent="";
}

function saveLocation(){
  var city=document.getElementById("locInput").value.trim();
  if(!city){document.getElementById("locStatus").textContent="Enter a city name";return;}
  document.getElementById("locStatus").innerHTML="<span style=\"color:var(--info)\">Searching...</span>";
  fetch("https://api.openweathermap.org/geo/1.0/direct?q="+encodeURIComponent(city)+"&limit=1&appid="+WKEY)
  .then(function(r){return r.json();})
  .then(function(d){
    if(d&&d.length){
      savedLoc={lat:d[0].lat,lon:d[0].lon,name:d[0].name+(d[0].country?", "+d[0].country:"")};
      localStorage.setItem("weatherLoc",JSON.stringify(savedLoc));
      document.getElementById("locModal").classList.remove("show");
      getWeather();
    }else{document.getElementById("locStatus").innerHTML="<span style=\"color:var(--bad)\">City not found</span>";}
  }).catch(function(){document.getElementById("locStatus").innerHTML="<span style=\"color:var(--bad)\">Search failed</span>";});
}

function useMyLocation(){
  document.getElementById("locStatus").innerHTML="<span style=\"color:var(--info)\">Getting location...</span>";
  if(navigator.geolocation){
    navigator.geolocation.getCurrentPosition(function(pos){
      savedLoc={lat:pos.coords.latitude,lon:pos.coords.longitude,name:"My Location"};
      localStorage.setItem("weatherLoc",JSON.stringify(savedLoc));
      document.getElementById("locModal").classList.remove("show");
      getWeather();
    },function(){document.getElementById("locStatus").innerHTML="<span style=\"color:var(--bad)\">Location denied. Enter city manually.</span>";},{timeout:10000});
  }else{document.getElementById("locStatus").innerHTML="<span style=\"color:var(--bad)\">GPS not available</span>";}
}

function getWeather(){
  if(!savedLoc){
    if(navigator.geolocation){
      navigator.geolocation.getCurrentPosition(function(pos){
        savedLoc={lat:pos.coords.latitude,lon:pos.coords.longitude,name:"My Location"};
        localStorage.setItem("weatherLoc",JSON.stringify(savedLoc));
        fetchWeather(savedLoc.lat,savedLoc.lon);
      },function(){document.getElementById("wL").textContent="Set Location";},{timeout:5000});
    }else{document.getElementById("wL").textContent="Set Location";}
    return;
  }
  fetchWeather(savedLoc.lat,savedLoc.lon);
}

function fetchWeather(lt,ln){
    fetch("https://api.openweathermap.org/data/2.5/weather?lat="+lt+"&lon="+ln+"&units=metric&appid="+WKEY)
    .then(function(r){return r.json();})
    .then(function(w){
      weather=w;
      document.getElementById("wT").textContent=Math.round(w.main.temp)+"C";
      document.getElementById("wH").textContent=w.main.humidity+"%";
      var p=w.main.pressure;
      pHist.push({t:Date.now(),p:p});
      pHist=pHist.filter(function(x){return Date.now()-x.t<3600000;});
      var pD=p-(pHist[0]?pHist[0].p:p);
      var arr=pD<-2?"vv":pD<-1?"v":pD>2?"^^":pD>1?"^":"";
      document.getElementById("wP").textContent=p+arr;
      var ps=presSt(p);
      document.getElementById("wPS").textContent=ps.t;
      document.getElementById("wPS").className="ws ws-"+ps.c;
      document.getElementById("wL").textContent=savedLoc.name||w.name;
      document.getElementById("wL").style.textDecoration="none";
      document.getElementById("wL").style.color="var(--text)";
      var sunset=new Date(w.sys.sunset*1000);
      var sunrise=new Date(w.sys.sunrise*1000);
      var now=new Date();
      if(now<sunset&&now>sunrise){document.getElementById("wSun").textContent=sunset.getHours()+":"+(sunset.getMinutes()<10?"0":"")+sunset.getMinutes();}
      else{document.getElementById("wSun").textContent=sunrise.getHours()+":"+(sunrise.getMinutes()<10?"0":"")+sunrise.getMinutes();document.querySelector("#wSun+.wl").textContent="Sunrise";}
      var wind=w.wind?w.wind.speed:0;
      document.getElementById("wWind").textContent=Math.round(wind)+"m/s";
      document.getElementById("wWind").style.color=wind<5?"var(--good)":wind<10?"var(--warn)":"var(--bad)";
      fetch("/api/save_weather?temp="+w.main.temp+"&humidity="+w.main.humidity+"&pressure="+p);
    }).catch(function(e){document.getElementById("wL").textContent="Weather error";});
    fetch("https://api.openweathermap.org/data/2.5/air_pollution?lat="+lt+"&lon="+ln+"&appid="+WKEY)
    .then(function(r){return r.json();})
    .then(function(aq){
      var aqi=aq.list[0].main.aqi;
      var aqiTxt=["","Good","Fair","Moderate","Poor","Very Poor"][aqi]||"--";
      document.getElementById("wAQI").textContent=aqiTxt;
      document.getElementById("wAQI").style.color=aqi<=2?"var(--good)":aqi<=3?"var(--warn)":"var(--bad)";
    }).catch(function(){document.getElementById("wAQI").textContent="--";});
    fetch("https://api.openweathermap.org/data/2.5/uvi?lat="+lt+"&lon="+ln+"&appid="+WKEY)
    .then(function(r){return r.json();})
    .then(function(uv){
      var val=uv.value||0;
      document.getElementById("wUV").textContent=val.toFixed(1);
      document.getElementById("wUV").style.color=val<3?"var(--good)":val<6?"var(--warn)":"var(--bad)";
    }).catch(function(){document.getElementById("wUV").textContent="--";});
}
function getCarbon(){
  fetch("https://api.carbonintensity.org.uk/intensity")
  .then(function(r){return r.json();})
  .then(function(c){
    var intensity=c.data[0].intensity.actual||c.data[0].intensity.forecast;
    var idx=c.data[0].intensity.index;
    document.getElementById("wCarbon").textContent=intensity+"g";
    var cs=idx==="very low"||idx==="low"?"good":idx==="moderate"?"warn":"bad";
    document.getElementById("wCarbonS").textContent=idx.charAt(0).toUpperCase()+idx.slice(1);
    document.getElementById("wCarbonS").className="ws ws-"+cs;
  }).catch(function(){document.getElementById("wCarbon").textContent="--";});
}
getWeather();getCarbon();setInterval(getWeather,600000);setInterval(getCarbon,1800000);

function buildIns(){
  var on=data.filter(function(d){return d.online;});
  if(!on.length)return[];
  var ins=[];
  var aC=0,aH=0,aV=0;
  for(var i=0;i<on.length;i++){aC+=on[i].co2||0;aH+=on[i].humidity||0;aV+=on[i].voc||0;}
  aC/=on.length;aH/=on.length;aV/=on.length;
  var p=weather.main?weather.main.pressure:1013;
  var pD=pHist.length>1?(pHist[0].p-p):0;
  var h=0;
  if(aC>1200)h+=40;else if(aC>1000)h+=25;else if(aC>800)h+=10;
  if(pD>5)h+=30;else if(pD>3)h+=20;else if(p<1000)h+=15;
  if(aV>100)h+=20;else if(aV>60)h+=10;
  if(pD>5)ins.push({c:"in-warn",t:"Storm Coming"});
  return ins;
}

function renderRoom(d){
  if(!d.online)return "";
  var t=d.inside_temperature/10;
  var h=d.humidity;
  var co2=d.co2;
  var voc=d.voc;
  var p=d.air_pressure;
  var tS=tmpSt(t);
  var hS=humSt(h);
  var cS=co2St(co2);
  var vS=vocSt(voc);
  var condS2=condSt(t,h);
  var pS=presSt(p);
  var mS=mouldSt(h,d.humidity_minutes||0);
  
  var inSpeed = d.supply ? d.supply.speed : 0;
  var outSpeed = d.extract ? d.extract.speed : 0;
  var inF = toM3h(inSpeed);
  var outF = toM3h(outSpeed);
  var hid=getHidden();
  
  var x="<div class=\"room\"><div class=\"rh\"><span class=\"rn\" data-ip=\""+d.ip+"\" data-name=\""+d.name+"\">"+d.name+"</span><span class=\"rb rb-on\">Online</span></div>";
  x+="<div class=\"metrics"+(hid.length?" has-hidden":"")+"\">";
  if(!hid.includes("temp"))x+="<div class=\"m\" data-param=\"temp\"><div class=\"mv\" style=\"color:#ff6b6b\">"+t.toFixed(1)+"</div><div class=\"ml\">Temp C</div><div class=\"ms "+tS.c+"\">"+tS.t+"</div></div>";
  if(!hid.includes("humidity"))x+="<div class=\"m\" data-param=\"humidity\"><div class=\"mv\" style=\"color:#00bfff\">"+h+"%</div><div class=\"ml\">Humidity</div><div class=\"ms "+hS.c+"\">"+hS.t+"</div></div>";
  if(!hid.includes("co2"))x+="<div class=\"m\" data-param=\"co2\"><div class=\"mv "+cS.c+"\">"+co2+"</div><div class=\"ml\">CO2 ppm</div><div class=\"ms "+cS.c+"\">"+cS.t+"</div></div>";
  if(!hid.includes("voc"))x+="<div class=\"m\" data-param=\"voc\"><div class=\"mv "+vS.c+"\">"+voc+"</div><div class=\"ml\">VOC</div><div class=\"ms "+vS.c+"\">"+vS.t+"</div></div>";
  if(!hid.includes("outside"))x+="<div class=\"m\" data-param=\"outside\"><div class=\"mv\" style=\"color:#f97316\">"+(d.outside_temperature/10).toFixed(1)+"</div><div class=\"ml\">Outside C</div></div>";
  if(!hid.includes("pressure"))x+="<div class=\"m\" data-param=\"pressure\"><div class=\"mv\" style=\"color:#eab308\">"+p+"</div><div class=\"ml\">hPa</div><div class=\"ms "+pS.c+"\">"+pS.t+"</div></div>";
  x+="</div>";
  x+="<div class=\"stats"+(hid.length?" has-hidden":"")+"\">";
  if(!hid.includes("condensation"))x+="<div class=\"st\" data-param=\"condensation\"><div class=\"stl\">Condensation</div><div class=\"stv "+condS2.c+"\">"+condS2.t+"</div></div>";
  if(!hid.includes("mould"))x+="<div class=\"st\" data-param=\"mould\"><div class=\"stl\">Mould Risk</div><div class=\"stv "+mS.c+"\">"+mS.t+"</div></div>";
  if(!hid.includes("presstatus"))x+="<div class=\"st\" data-param=\"presstatus\"><div class=\"stl\">Pressure</div><div class=\"stv "+pS.c+"\">"+pS.t+"</div></div>";
  x+="</div>";
  x+="<div class=\"air"+(hid.length?" has-hidden":"")+"\">";
  if(!hid.includes("freshin"))x+="<div class=\"ab"+(inF?"":" ab-off")+"\" data-param=\"freshin\"><div class=\"al\">Fresh In (Spd "+inSpeed+")</div><div class=\"av\">"+(inF||"OFF")+"</div>"+(inF?"<div class=\"au\">m3/h</div>":"")+"</div>";
  if(!hid.includes("staleout"))x+="<div class=\"ab"+(outF?"":" ab-off")+"\" data-param=\"staleout\"><div class=\"al\">Stale Out (Spd "+outSpeed+")</div><div class=\"av\">"+(outF||"OFF")+"</div>"+(outF?"<div class=\"au\">m3/h</div>":"")+"</div>";
  x+="</div>";
  x+="<div class=\"modes"+(hid.length?" has-hidden":"")+"\">";
  if(!hid.includes("auto"))x+="<div class=\"mo"+(d.auto?" mo-on":"")+"\" data-param=\"auto\">Auto</div>";
  if(!hid.includes("autoplus"))x+="<div class=\"mo"+(d.auto_plus?" mo-on":"")+"\" data-param=\"autoplus\">Auto+</div>";
  if(!hid.includes("heat"))x+="<div class=\"mo"+(d.heater?" mo-on":"")+"\" data-param=\"heat\">Heat</div>";
  if(!hid.includes("winter"))x+="<div class=\"mo"+(d.winter?" mo-on":"")+"\" data-param=\"winter\">Winter</div>";
  x+="</div></div>";
  return x;
}

function update(){
  fetch("/api/data").then(function(r){return r.json();}).then(function(d){
    data=d;
    if(!data.length){
      document.getElementById("grid").innerHTML="<div style=\"text-align:center;padding:60px;color:var(--dim)\"><p style=\"font-size:18px\">No devices found</p><button onclick=\"document.getElementById('aModal').classList.add('show')\" style=\"margin-top:16px;padding:12px 28px;background:var(--info);border:none;border-radius:8px;color:#fff;cursor:pointer;font-size:16px\">+ Add Device</button></div>";
      document.getElementById("ins").innerHTML="";
      return;
    }
    for(var i=0;i<data.length;i++){checkFireRisk(data[i]);}
    var ins=buildIns();
    var insHtml="";
    for(var i=0;i<ins.length;i++)insHtml+="<div class=\"in "+ins[i].c+"\">"+ins[i].t+"</div>";
    document.getElementById("ins").innerHTML=insHtml;
    var gridHtml="";
    for(var i=0;i<data.length;i++)gridHtml+=renderRoom(data[i]);
    document.getElementById("grid").innerHTML=gridHtml;
    var names=document.querySelectorAll(".rn");
    for(var i=0;i<names.length;i++)names[i].onclick=function(){document.getElementById("rIp").value=this.getAttribute("data-ip");document.getElementById("rIn").value=this.getAttribute("data-name");document.getElementById("rModal").classList.add("show");};
    setupHideListeners();
  });
}
update();setInterval(update,2000);

document.getElementById("hBtn").onclick=function(){
  document.getElementById("hModal").classList.add("show");
  populateRoomSelect();
  fetch("/api/history_all").then(function(r){return r.json();}).then(function(h){hist=h;updateAnalytics();});
};
document.getElementById("hClose").onclick=function(){document.getElementById("hModal").classList.remove("show");};

var selectedRoom=null;
var roomSizes={};
try{roomSizes=JSON.parse(localStorage.getItem("roomSizes"))||{};}catch(e){}

function populateRoomSelect(){
  var sel=document.getElementById("roomSelect");
  var html="";
  for(var i=0;i<data.length;i++){
    if(data[i].online){
      html+="<option value=\""+data[i].ip+"\">"+data[i].name+" ("+data[i].ip+")</option>";
      if(!selectedRoom)selectedRoom=data[i].ip;
    }
  }
  sel.innerHTML=html;
  if(selectedRoom)sel.value=selectedRoom;
}
document.getElementById("roomSelect").onchange=function(){selectedRoom=this.value;updateAnalytics();};

var tabs=document.querySelectorAll(".htab");
for(var i=0;i<tabs.length;i++){
  tabs[i].onclick=function(){
    for(var j=0;j<tabs.length;j++)tabs[j].classList.remove("htab-on");
    this.classList.add("htab-on");
    var t=this.getAttribute("data-tab");
    document.getElementById("tabCharts").style.display=t==="charts"?"block":"none";
    document.getElementById("tabRoom").style.display=t==="room"?"block":"none";
    document.getElementById("tabHeating").style.display=t==="heating"?"block":"none";
    document.getElementById("tabEvents").style.display=t==="events"?"block":"none";
    document.getElementById("tabInsights").style.display=t==="insights"?"block":"none";
    if(t==="charts")drawRoomCharts();
  };
}

function saveRoomSize(){
  var size=parseFloat(document.getElementById("roomSize").value);
  if(size>0&&selectedRoom){roomSizes[selectedRoom]=size;localStorage.setItem("roomSizes",JSON.stringify(roomSizes));updateRoomInfo();}
}

function getRoomData(){
  for(var i=0;i<data.length;i++)if(data[i].ip===selectedRoom)return data[i];
  return null;
}

function updateAnalytics(){
  updateRoomInfo();
  drawRoomCharts();
  analyzeHeating();
  analyzeEvents();
  generateInsights();
}

function updateRoomInfo(){
  var d=getRoomData();
  var size=roomSizes[selectedRoom]||0;
  document.getElementById("roomSize").value=size||"";
  var vol=size*2.5;
  document.getElementById("roomVol").textContent=size?vol.toFixed(0)+" m³":"Enter room size";
  var flow=0;
  if(d&&d.supply)flow=toM3h(d.supply.speed);
  if(size&&flow){
    var ach=(flow/vol).toFixed(2);
    var refresh=Math.round(vol/flow*60);
    document.getElementById("airChanges").textContent=ach+"/hr";
    document.getElementById("refreshTime").textContent=refresh+" min";
  }else{
    document.getElementById("airChanges").textContent="--";
    document.getElementById("refreshTime").textContent="--";
  }
}

function drawRoomCharts(){
  if(!selectedRoom||!hist.devices[selectedRoom])return;
  var dd=hist.devices[selectedRoom];
  drawSingle("cT",dd,"temp","tT","tR");
  drawSingle("cH",dd,"humidity","hT","hR");
  drawSingle("cC",dd,"co2","cT2","cR");
  drawSingle("cV",dd,"voc","vT","vR");
}

function drawSingle(id,dd,m,tId,rId){
  var c=document.getElementById(id);if(!c||!dd.length)return;
  var ctx=c.getContext("2d");
  var b=c.parentElement.getBoundingClientRect();
  c.width=b.width*2;c.height=b.height*2;ctx.scale(2,2);
  var w=b.width,ht=b.height;ctx.clearRect(0,0,w,ht);
  var all=dd.map(function(x){return x[m]||0;});
  var t0=dd[0].time,t1=dd[dd.length-1].time;
  var min=Math.min.apply(null,all),max=Math.max.apply(null,all);
  var rng=max-min||1;
  ctx.strokeStyle="rgba(255,255,255,0.05)";ctx.lineWidth=1;
  for(var i=0;i<4;i++){var y=ht*0.05+i*(ht*0.9)/3;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
  ctx.strokeStyle="#3b82f6";ctx.lineWidth=2;ctx.beginPath();
  for(var j=0;j<dd.length;j++){
    var xx=j/(dd.length-1)*w;
    var yy=ht*0.95-((dd[j][m]||0)-min)/rng*ht*0.9;
    if(j===0)ctx.moveTo(xx,yy);else ctx.lineTo(xx,yy);
  }
  ctx.stroke();
  document.getElementById(tId).textContent=fmtTime(t0)+" - "+fmtTime(t1);
  document.getElementById(rId).textContent=min.toFixed(1)+" - "+max.toFixed(1);
}

function analyzeHeating(){
  if(!selectedRoom||!hist.devices[selectedRoom]){
    document.getElementById("heatToday").textContent="--";
    document.getElementById("heatWeek").textContent="--";
    document.getElementById("heatLoss").textContent="--";
    return;
  }
  var dd=hist.devices[selectedRoom];
  var now=Date.now()/1000;
  var dayAgo=now-86400,weekAgo=now-604800;
  var heatMinsToday=0,heatMinsWeek=0;
  var coolRates=[];
  for(var i=1;i<dd.length;i++){
    var prev=dd[i-1],curr=dd[i];
    var dt=(curr.time-prev.time)/60;
    var dTemp=curr.temp-prev.temp;
    if(dTemp>0.3&&dt<10){
      if(curr.time>dayAgo)heatMinsToday+=dt;
      if(curr.time>weekAgo)heatMinsWeek+=dt;
    }
    if(dTemp<-0.1&&dt>5&&dt<30){coolRates.push((dTemp/dt)*60);}
  }
  document.getElementById("heatToday").textContent=(heatMinsToday/60).toFixed(1)+" hrs";
  document.getElementById("heatWeek").textContent=(heatMinsWeek/60).toFixed(1)+" hrs";
  if(coolRates.length){
    var avgCool=coolRates.reduce(function(a,b){return a+b;},0)/coolRates.length;
    document.getElementById("heatLoss").textContent=Math.abs(avgCool).toFixed(2)+" °C/hr";
  }else{document.getElementById("heatLoss").textContent="--";}
  drawHeatingChart(dd,dayAgo);
}

function drawHeatingChart(dd,dayAgo){
  var c=document.getElementById("cHeat");if(!c)return;
  var ctx=c.getContext("2d");
  var b=c.parentElement.getBoundingClientRect();
  c.width=b.width*2;c.height=b.height*2;ctx.scale(2,2);
  var w=b.width,ht=b.height;ctx.clearRect(0,0,w,ht);
  var dayData=dd.filter(function(x){return x.time>dayAgo;});
  if(dayData.length<2)return;
  var t0=dayData[0].time,t1=dayData[dayData.length-1].time,tRng=t1-t0||1;
  for(var i=1;i<dayData.length;i++){
    var prev=dayData[i-1],curr=dayData[i];
    if(curr.temp-prev.temp>0.3){
      var x1=(prev.time-t0)/tRng*w;
      var x2=(curr.time-t0)/tRng*w;
      ctx.fillStyle="rgba(234,179,8,0.6)";
      ctx.fillRect(x1,0,Math.max(x2-x1,2),ht);
    }
  }
}

function analyzeEvents(){
  if(!selectedRoom||!hist.devices[selectedRoom]){
    document.getElementById("showerCount").textContent="--";
    document.getElementById("windowCount").textContent="--";
    document.getElementById("dryingCount").textContent="--";
    document.getElementById("eventList").innerHTML="No data";
    return;
  }
  var dd=hist.devices[selectedRoom];
  var now=Date.now()/1000;
  var weekAgo=now-604800;
  var showers=0,windows=0,drying=0;
  var events=[];
  for(var i=5;i<dd.length;i++){
    var prev=dd[i-5],curr=dd[i];
    var dt=(curr.time-prev.time)/60;
    if(dt>15)continue;
    var dHum=curr.humidity-prev.humidity;
    var dTemp=curr.temp-prev.temp;
    var dCo2=curr.co2-prev.co2;
    if(curr.time>weekAgo){
      if(dHum>20&&dt<12){showers++;events.push({time:curr.time,type:"Shower detected",icon:"🚿"});}
      if(dTemp<-2&&dCo2<-100){windows++;events.push({time:curr.time,type:"Window opened",icon:"🪟"});}
      if(curr.humidity>70&&dHum>5&&dHum<15){drying++;events.push({time:curr.time,type:"Possible drying",icon:"👕"});}
    }
  }
  document.getElementById("showerCount").textContent=showers;
  document.getElementById("windowCount").textContent=windows;
  document.getElementById("dryingCount").textContent=drying;
  events.sort(function(a,b){return b.time-a.time;});
  var evHtml="";
  for(var i=0;i<Math.min(events.length,8);i++){
    evHtml+="<div style=\"padding:6px 0;border-bottom:1px solid var(--border)\">"+events[i].icon+" "+events[i].type+" <span style=\"float:right\">"+fmtTime(events[i].time)+"</span></div>";
  }
  document.getElementById("eventList").innerHTML=evHtml||"No events detected";
}

function generateInsights(){
  var ins=[];
  var d=getRoomData();
  var dd=hist.devices[selectedRoom]||[];
  var size=roomSizes[selectedRoom];
  if(d&&d.online){
    if(d.co2>1000)ins.push({c:"bad",t:"High CO2",m:"CO2 is "+d.co2+"ppm. Increase ventilation or open windows."});
    if(d.humidity>70)ins.push({c:"warn",t:"High Humidity",m:"Humidity at "+d.humidity+"%. Risk of mould if sustained."});
    if(d.humidity<30)ins.push({c:"warn",t:"Low Humidity",m:"Air is dry at "+d.humidity+"%. Consider a humidifier."});
    if(d.voc>100)ins.push({c:"warn",t:"Elevated VOC",m:"VOC at "+d.voc+". Check for sources like paint, cleaners."});
  }
  var heatLoss=document.getElementById("heatLoss").textContent;
  if(heatLoss&&heatLoss!=="--"){
    var rate=parseFloat(heatLoss);
    if(rate>2)ins.push({c:"bad",t:"Poor Insulation",m:"Room loses "+heatLoss+" when heating off. Consider insulation."});
    else if(rate>1)ins.push({c:"warn",t:"Moderate Heat Loss",m:"Room loses "+heatLoss+" when heating off."});
    else ins.push({c:"good",t:"Good Insulation",m:"Heat loss is low at "+heatLoss+"."});
  }
  if(size){
    var vol=size*2.5;
    var flow=d&&d.supply?toM3h(d.supply.speed):0;
    if(flow){
      var ach=flow/vol;
      if(ach<0.5)ins.push({c:"warn",t:"Low Air Changes",m:"Only "+ach.toFixed(2)+"/hr. Recommended 0.5-1.0 for homes."});
      else if(ach>2)ins.push({c:"warn",t:"High Air Changes",m:ach.toFixed(2)+"/hr may cause excess heat loss."});
      else ins.push({c:"good",t:"Good Ventilation",m:ach.toFixed(2)+" air changes/hr is optimal."});
    }
  }else{ins.push({c:"warn",t:"Set Room Size",m:"Enter room m² in Room Info tab for air change calculations."});}
  var iHtml="";
  for(var i=0;i<ins.length;i++){
    iHtml+="<div class=\"insight insight-"+ins[i].c+"\"><div class=\"insightTitle\">"+ins[i].t+"</div><div class=\"insightText\">"+ins[i].m+"</div></div>";
  }
  document.getElementById("insightsList").innerHTML=iHtml||"<div class=\"insight\">No insights yet. Collect more data.</div>";
}

document.getElementById("aBtn").onclick=function(){document.getElementById("aModal").classList.add("show");document.getElementById("scanSt").textContent="";document.getElementById("addRes").textContent="";updDevList();};
document.getElementById("aClose").onclick=function(){document.getElementById("aModal").classList.remove("show");};
document.getElementById("rClose").onclick=function(){document.getElementById("rModal").classList.remove("show");};
document.getElementById("scanBtn").onclick=function(){document.getElementById("scanSt").innerHTML="<span style=\"color:var(--info)\">Scanning...</span>";fetch("/api/scan").then(function(r){return r.json();}).then(function(d){document.getElementById("scanSt").innerHTML="<span style=\"color:var(--good)\">Found "+d.found+"</span>";updDevList();update();});};
document.getElementById("addBtn").onclick=function(){var ip=document.getElementById("ipIn").value.trim();if(!ip){document.getElementById("addRes").innerHTML="<span style=\"color:var(--warn)\">Enter IP</span>";return;}document.getElementById("addRes").innerHTML="<span style=\"color:var(--info)\">Checking...</span>";fetch("/api/add_device?ip="+ip).then(function(r){return r.json();}).then(function(d){document.getElementById("addRes").innerHTML=d.success?"<span style=\"color:var(--good)\">Added</span>":"<span style=\"color:var(--bad)\">Not found</span>";if(d.success){document.getElementById("ipIn").value="";updDevList();update();}});};
document.getElementById("rSave").onclick=function(){var ip=document.getElementById("rIp").value;var name=document.getElementById("rIn").value.trim();if(!name)return;fetch("/api/rename?ip="+ip+"&name="+encodeURIComponent(name)).then(function(){document.getElementById("rModal").classList.remove("show");update();});};
function updDevList(){fetch("/api/data").then(function(r){return r.json();}).then(function(d){var html="";for(var i=0;i<d.length;i++)html+="<div style=\"display:flex;justify-content:space-between;padding:10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;margin-bottom:6px;font-size:14px\"><span>"+d[i].name+" <span style=\"color:var(--dim)\">"+d[i].ip+"</span></span><span style=\"color:"+(d[i].online?"var(--good)":"var(--bad)")+"\">*</span></div>";document.getElementById("devList").innerHTML=html||"<span style=\"color:var(--dim)\">None</span>";});}
var modals=document.querySelectorAll(".modal");for(var i=0;i<modals.length;i++)modals[i].onclick=function(e){if(e.target===this)this.classList.remove("show");};
function toggleTheme(){var b=document.body;if(b.classList.contains("light")){b.classList.remove("light");document.getElementById("themeBtn").textContent="🌙";localStorage.setItem("theme","dark");}else{b.classList.add("light");document.getElementById("themeBtn").textContent="☀️";localStorage.setItem("theme","light");}}
if(localStorage.getItem("theme")==="light"){document.body.classList.add("light");document.getElementById("themeBtn").textContent="☀️";}
</script>
</body>
</html>'''
            self.wfile.write(html.encode())
        else:
            self.send_error(404)
    def log_message(self, format, *args): pass

load_config()
load_history()
get_client_id()
get_property_name()
print("")
print("Prana Air Quality Dashboard v"+VERSION)
print("="*40)
print("Property: "+PROPERTY_NAME)
print("Client ID: "+CLIENT_ID)
print("Firebase: "+("Yes" if FIREBASE_ENABLED else "No"))
print("="*40)
print("")
print("Scanning...")
quick_scan()
print("")
print(str(len(DEVICES))+" device(s)")
print("="*40)
print("http://localhost:8000")
print("="*40)
print("")
threading.Thread(target=auto_discovery_thread,daemon=True).start()
HTTPServer(("",8000),Handler).serve_forever()
