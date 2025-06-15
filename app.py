import json
import threading
import time
import datetime
from flask import Flask, render_template, request, jsonify

import re
import requests
from pathlib import Path

app = Flask(__name__)
SAVE_FILE = "smart_home_state.json"
RANGE_MAPPING_FILE = "range_mappings.json"
range_mappings = {}  # Например: { "0": "192.168.1.12" }


def hsv_to_rgb(h, s, v):
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h / 360, s / 100, v / 100)
    return int(r * 255), int(g * 255), int(b * 255)


def save_range_mappings():
    try:
        with open(RANGE_MAPPING_FILE, "w") as f:
            json.dump(range_mappings, f, indent=2)
    except Exception as e:
        print("Ошибка сохранения range-mappings:", e)


def load_range_mappings():
    global range_mappings
    try:
        with open(RANGE_MAPPING_FILE) as f:
            range_mappings = json.load(f)
    except Exception:
        range_mappings = {}

def poll_sensors():
    while True:
        for room in smart_home.rooms.values():
            for device in room.devices.values():
                if isinstance(device, SmartSensor):
                    try:
                        resp = requests.get(f"http://{device.ip}", timeout=2)
                        if resp.status_code == 200:
                            device.value = resp.text.strip()
                            device.last_seen = datetime.datetime.now()
                    except Exception as e:
                        print(f"[Опрос датчика] Ошибка {device.name}: {e}")
        time.sleep(60)  # раз в минуту


class SmartDevice:
    def __init__(self, name, device_type, ip_address):
        self.name = name
        self.type = device_type
        self.ip = ip_address
        self.status = False
        self.last_seen = None

    def toggle(self):
        self.status = not self.status
        return self.status

    def get_info(self):
        return {
            'name': self.name,
            'type': self.type,
            'ip': self.ip,
            'status': self.status,
            'last_seen': self.last_seen.strftime('%Y-%m-%d %H:%M:%S') if self.last_seen else None
        }

    def get_additional_state(self):
        """Возвращает специфичные данные устройства для сохранения"""
        return {}


class SmartLight(SmartDevice):
    """Умная лампа (только вкл/выкл)"""

    def __init__(self, name, ip_address):
        super().__init__(name, 'light', ip_address)

    def get_info(self):
        return super().get_info()


class SmartRGBLight(SmartDevice):
    def __init__(self, name, ip_address):
        super().__init__(name, 'rgb_light', ip_address)
        self.status = True  # Всегда включен
        self.hsv = [0, 100, 100]
        self.mode = "hue"
        self.color = "#ffffff"  # Белый по умолчанию

    def set_color(self, hex_color):
        """Установка цвета в формате HEX"""
        if re.match(r'^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$', hex_color):
            self.color = hex_color
            return True
        return False

    def get_info(self):
        info = super().get_info()
        info.update({
            'color': self.color,
            'status': True  # Всегда возвращаем True
        })
        return info

    def get_additional_state(self):
        return {"color": self.color}


class SmartRoom:
    def __init__(self, name):
        self.name = name
        self.devices = {}

    def add_device(self, device):
        self.devices[device.name] = device

    def get_devices_info(self):
        return {name: device.get_info() for name, device in self.devices.items()}


class SmartSensor(SmartDevice):
    """Датчик (только показания)"""

    def __init__(self, name, ip_address):
        super().__init__(name, 'sensor', ip_address)
        self.value = 21.5

    def get_info(self):
        info = super().get_info()
        info['value'] = self.value
        return info


class SmartHome:
    def __init__(self):
        self.rooms = {}

    def add_room(self, room):
        self.rooms[room.name] = room

    def get_all_devices(self):
        all_devices = {}
        for room_name, room in self.rooms.items():
            all_devices[room_name] = room.get_devices_info()
        return all_devices

    def find_device_by_ip(self, ip):
        for room in self.rooms.values():
            for device in room.devices.values():
                if device.ip == ip:
                    return device
        return None


def save_state():
    """Сохраняет только текущее состояние без дефолтных значений"""
    state = {
        "rooms": {room.name: [] for room in smart_home.rooms.values()},
        "devices": []
    }

    for room in smart_home.rooms.values():
        for device in room.devices.values():
            device_info = {
                "name": device.name,
                "type": device.type,
                "ip": device.ip,
                "room": room.name,
                "status": device.status
            }

            # Добавляем специфичные поля
            if hasattr(device, 'brightness'):
                device_info["brightness"] = device.brightness
            if hasattr(device, 'color'):
                device_info["color"] = device.color
            if hasattr(device, 'value'):
                device_info["value"] = device.value

            state["devices"].append(device_info)

    try:
        with open(SAVE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения состояния: {str(e)}")


def load_state():
    """Загружает состояние системы из файла без создания устройств по умолчанию"""
    if not Path(SAVE_FILE).exists():
        return  # Не создаём ничего, если файла нет

    try:
        with open(SAVE_FILE) as f:
            state = json.load(f)

        smart_home.rooms.clear()  # Очищаем текущее состояние

        # Создаём только комнаты и устройства из файла
        for room_name in state.get("rooms", {}):
            smart_home.add_room(SmartRoom(room_name))

        for device_data in state.get("devices", []):
            room = smart_home.rooms.get(device_data["room"])
            if not room:
                continue

            device = create_device_from_data(device_data)
            if device:
                device.status = device_data.get("status", False)
                room.add_device(device)

    except Exception as e:
        print(f"Ошибка загрузки состояния: {str(e)}")
        # Не создаём устройства по умолчанию при ошибке


def create_device_from_data(device_data):
    """Создаёт устройство на основе данных"""
    device_type = device_data.get("type")
    if not device_type:
        return None

    try:
        if device_type == "light":
            device = SmartLight(device_data["name"], device_data["ip"])
            device.brightness = device_data.get("brightness", 100)
            return device
        elif device_type == "rgb_light":
            device = SmartRGBLight(device_data["name"], device_data["ip"])
            device.color = device_data.get("color", "#ffffff")
            return device
        elif device_type == "sensor":
            device = SmartSensor(device_data["name"], device_data["ip"])
            device.value = device_data.get("value", "Нет данных")
            return device
    except KeyError as e:
        print(f"Ошибка создания устройства: отсутствует поле {str(e)}")
        return None


# Инициализация умного дома
smart_home = SmartHome()


# Веб-роуты
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/rooms', methods=['GET'])
def get_rooms():
    return jsonify(list(smart_home.rooms.keys()))


@app.route('/api/room/add', methods=['POST'])
def add_room():
    data = request.json
    room_name = data['name']

    if room_name in smart_home.rooms:
        return jsonify({'error': 'Room already exists'}), 400

    smart_home.add_room(SmartRoom(room_name))
    save_state()
    return jsonify({'status': 'success'})


@app.route('/api/device/types', methods=['GET'])
def get_device_types():
    try:
        return jsonify({
            "status": "success",
            "types": [
                {"value": "light", "name": "Обычная лампа"},
                {"value": "rgb_light", "name": "RGB-светильник"},
                {"value": "sensor", "name": "Датчик"}
            ]
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/api/device/add', methods=['POST'])
def add_device():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Обязательные поля
        required_fields = ['room', 'name', 'type', 'ip']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing field: {field}'}), 400

        room_name = data['room']
        device_name = data['name']
        device_type = data['type']
        ip_address = data['ip']

        # Проверка существования комнаты
        if room_name not in smart_home.rooms:
            return jsonify({'error': 'Room does not exist'}), 404

        # Проверка уникальности имени устройства
        if device_name in smart_home.rooms[room_name].devices:
            return jsonify({'error': 'Device name already exists in this room'}), 400

        # Создание устройства
        if device_type == 'light':
            device = SmartLight(device_name, ip_address)
        elif device_type == 'rgb_light':
            device = SmartRGBLight(device_name, ip_address)
        elif device_type == 'sensor':
            device = SmartSensor(device_name, ip_address)
        else:
            return jsonify({'error': 'Invalid device type'}), 400

        # Добавление устройства
        smart_home.rooms[room_name].add_device(device)

        save_state()
        return jsonify({
            'status': 'success',
            'message': f'Device {device_name} added successfully',
            'device': device.get_info()
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/devices', methods=['GET'])
def get_devices():
    room_name = request.args.get('room')
    device_name = request.args.get('device')

    if room_name and device_name:
        # Возвращаем данные конкретного устройства
        room = smart_home.rooms.get(room_name)
        if not room:
            return jsonify({'error': 'Room not found'}), 404
        device = room.devices.get(device_name)
        if not device:
            return jsonify({'error': 'Device not found'}), 404
        return jsonify({room_name: {device_name: device.get_info()}})

    # Возвращаем все устройства
    return jsonify(smart_home.get_all_devices())


@app.route('/api/device/control', methods=['POST'])
def control_device():
    data = request.json
    room_name = data['room']
    device_name = data['device']
    action = data['action']
    value = data.get('value')

    room = smart_home.rooms.get(room_name)
    if not room:
        return jsonify({'error': 'Room not found'}), 404

    device = room.devices.get(device_name)
    if not device:
        return jsonify({'error': 'Device not found'}), 404

    try:
        if action == 'toggle':
            device.toggle()
            try:
                requests.get(f"http://{device.ip}/svet", timeout=1)
            except Exception as e:
                print(f"[Ошибка toggle] {device.name} → {e}")

        elif action == 'set_color' and isinstance(device, SmartRGBLight):
            if device.set_color(value):
                try:
                    requests.get(f"http://{device.ip}/color", params={'color': "%23"+str(value[1:7])}, timeout=1)
                except Exception as e:
                    print(f"[Ошибка set_color] {device.name} → {e}")
            else:
                return jsonify({'error': 'Invalid color format'}), 400

        save_state()

        return jsonify({
            'status': 'success',
            'device': device.get_info()
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 400



def validate_ip(ip):
    parts = ip.split('/')[0].split('.')
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


@app.route('/api/range-mapping', methods=['GET', 'POST'])
def handle_range_mapping():
    global range_mappings
    if request.method == 'GET':
        return jsonify(range_mappings)
    elif request.method == 'POST':
        data = request.json
        range_mappings = data.get("mappings", {})
        save_range_mappings()
        return jsonify({"status": "success"})


@app.route('/update', methods=['GET'])
def remote_control():
    range_val = request.args.get("range")
    encoder = int(request.args.get("encoder", 0))
    button = request.args.get("button") == "1"

    device_ip = range_mappings.get(str(range_val))
    if not device_ip:
        return jsonify({"error": "Unknown range"}), 404

    device = smart_home.find_device_by_ip(device_ip)
    if not device:
        return jsonify({"error": "Device not found"}), 404

    try:
        if isinstance(device, SmartLight):
            if button:
                device.toggle()
                requests.get(f"http://{device.ip}/svet", timeout=1)
                save_state()
                return jsonify({'status': 'toggled', 'device': device.get_info()})


        elif isinstance(device, SmartRGBLight):
            if not hasattr(device, 'hsv'):
                device.hsv = [0, 100, 100]
                device.mode = 'hue'
            if button:
                device.mode = 'saturation' if device.mode == 'hue' else 'hue'
            if encoder != 0:
                if device.mode == 'hue':
                    device.hsv[0] = (device.hsv[0] + encoder * 10) % 360
                elif device.mode == 'saturation':
                    device.hsv[1] = max(0, min(100, device.hsv[1] + encoder * 5))
                r, g, b = hsv_to_rgb(*device.hsv)
                hex_color = '#{:02x}{:02x}{:02x}'.format(r, g, b)
                device.set_color(hex_color)
                try:
                    requests.get(f"http://{device.ip}/color", params={"color": hex_color}, timeout=1)
                except Exception as e:
                    print(f"Ошибка при отправке цвета: {e}")
                save_state()
                return jsonify({'status': 'color_changed', 'color': hex_color, 'device': device.get_info()})

        return jsonify({'status': 'no_action'})  # Ничего не произошло
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    load_state()
    load_range_mappings()

    threading.Thread(target=poll_sensors, daemon=True).start()

    app.run(debug=True, host="0.0.0.0", port=8080)

