#!/usr/bin/env python3
import os
import re
import time
import json
import socket
import platform
import psutil
import paho.mqtt.client as mqtt

# -------------------- CONFIG --------------------
MQTT_HOST = os.getenv("MQTT_HOST", "homeassistant.local")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")

DEVICE_NAME = os.getenv("DEVICE_NAME", "HA remote helper 01")
ROOT_FS_PATH = os.getenv("ROOT_FS_PATH", "/")
SCRIPT_VERSION = "1.2.0"

def slugify(name):
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name)

DEVICE_SLUG = slugify(DEVICE_NAME)

BASE_TOPIC = "homeassistant"
CLIENT_ID = f"emmc-monitor-{DEVICE_SLUG}"
LWT_TOPIC = f"{DEVICE_SLUG}/status"
LWT_PAYLOAD_ONLINE = "online"
LWT_PAYLOAD_OFFLINE = "offline"
PROGRAM_START_TIME = time.time()

# -------------------- NETWORK --------------------
def detect_real_interface():
    """Return the first real, physical IPv4 interface (skip docker, loopback, virtual)."""
    addrs = psutil.net_if_addrs()
    for iface in addrs:
        if iface.startswith(("lo", "docker", "veth", "br-", "tun")):
            continue
        for snic in addrs[iface]:
            if snic.family == socket.AF_INET:
                return iface
    return "unknown"

NETWORK_INTERFACE = detect_real_interface()

def get_host_ip(interface=NETWORK_INTERFACE):
    try:
        addrs = psutil.net_if_addrs()
        for snic in addrs.get(interface, []):
            if snic.family == socket.AF_INET:
                return snic.address
        return "unknown"
    except:
        return "unknown"

def get_primary_mac(interface):
    try:
        addrs = psutil.net_if_addrs()
        for snic in addrs.get(interface, []):
            if snic.family == psutil.AF_LINK:
                return snic.address.replace(":", "")
        return "000000000000"
    except:
        return "000000000000"

PRIMARY_MAC = get_primary_mac(NETWORK_INTERFACE)
UNIQUE_ID_PREFIX = f"{DEVICE_SLUG}_{PRIMARY_MAC}"

# -------------------- DEVICE INFO --------------------
device = {
    "identifiers": [DEVICE_NAME],
    "name": DEVICE_NAME,
    "manufacturer": "Mecool",
    "model": "M8S Pro+",
    "sw_version": SCRIPT_VERSION,
    "hw_version": platform.uname().release,
    "connections": [["host", socket.gethostname()]],
}

# -------------------- SYSTEM METRICS --------------------
def get_emmc():
    try:
        with open("/sys/block/mmcblk2/device/life_time") as f:
            life = int(f.read().split()[0], 16)
        with open("/sys/block/mmcblk2/device/pre_eol_info") as f:
            eol = int(f.read().strip(), 16)
        percent = life * 10
        return percent, eol
    except:
        return None, None

def get_disk():
    return psutil.disk_usage("/").percent

def get_root_fs_usage(path=ROOT_FS_PATH):
    try:
        usage = psutil.disk_usage(path)
        free_gb = round(usage.free / (1024 ** 3), 2)
        return usage.percent, free_gb
    except:
        return None, None

def get_mem():
    mem = psutil.virtual_memory()
    return mem.percent, mem.available // (1024*1024)

def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000.0
    except:
        return None

def get_cpu_governor():
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor") as f:
            return f.read().strip()
    except:
        return "unknown"

def get_cpu_freq():
    try:
        base = "/sys/devices/system/cpu/cpu0/cpufreq/"
        with open(base + "scaling_cur_freq") as f:
            cur = int(f.read()) / 1000
        with open(base + "scaling_min_freq") as f:
            minf = int(f.read()) / 1000
        with open(base + "scaling_max_freq") as f:
            maxf = int(f.read()) / 1000
        return cur, minf, maxf
    except:
        return None, None, None

def get_uptime_seconds():
    try:
        return int(time.time() - psutil.boot_time())
    except:
        return None

def get_program_uptime_seconds():
    try:
        return int(time.time() - PROGRAM_START_TIME)
    except:
        return None

# -------------------- HOME ASSISTANT DISCOVERY --------------------
def publish_discovery(client):
    # Core sensors
    sensors = {
        "emmc_life": {"name":"eMMC Lifetime","unit_of_measurement":"%","device_class":None,"icon":"mdi:chip","state_topic":f"{DEVICE_SLUG}/emmc/life"},
        "disk_used": {"name":"Disk Used","unit_of_measurement":"%","device_class":None,"state_class":"measurement","icon":"mdi:harddisk","state_topic":f"{DEVICE_SLUG}/disk/used"},
        "root_fs_used": {"name":"Root FS Used","unit_of_measurement":"%","device_class":None,"state_class":"measurement","icon":"mdi:harddisk","state_topic":f"{DEVICE_SLUG}/rootfs/used"},
        "root_fs_free": {"name":"Root FS Free","unit_of_measurement":"GiB","device_class":"data_size","icon":"mdi:harddisk","state_topic":f"{DEVICE_SLUG}/rootfs/free"},
        "mem_used": {"name":"Memory Used","unit_of_measurement":"%","device_class":None,"icon":"mdi:memory","state_topic":f"{DEVICE_SLUG}/mem/used"},
        "mem_free": {"name":"Memory Free","unit_of_measurement":"MB","device_class":None,"icon":"mdi:memory","state_topic":f"{DEVICE_SLUG}/mem/free"},
        "cpu_temp": {"name":"CPU Temperature","unit_of_measurement":"°C","device_class":"temperature","icon":"mdi:thermometer","state_topic":f"{DEVICE_SLUG}/cpu/temp"},
        "cpu_freq": {"name":"CPU Frequency","unit_of_measurement":"MHz","device_class":"frequency","icon":"mdi:speedometer","state_topic":f"{DEVICE_SLUG}/cpu/freq"},
        "cpu_freq_min": {"name":"CPU Min Frequency","unit_of_measurement":"MHz","device_class":"frequency","icon":"mdi:speedometer-slow","state_topic":f"{DEVICE_SLUG}/cpu/freq_min"},
        "cpu_freq_max": {"name":"CPU Max Frequency","unit_of_measurement":"MHz","device_class":"frequency","icon":"mdi:speedometer-medium","state_topic":f"{DEVICE_SLUG}/cpu/freq_max"},
    }

    # Diagnostic sensors
    diagnostics = {
        "host_ip": {"name":"Host IP","device_class":None,"icon":"mdi:ip-network","state_topic":f"{DEVICE_SLUG}/host/ip","entity_category":"diagnostic"},
        "sys_uptime": {"name":"System Uptime","unit_of_measurement":"s","device_class":"duration","icon":"mdi:timer-outline","state_topic":f"{DEVICE_SLUG}/system/sys_uptime","entity_category":"diagnostic","value_template":"{{ value | int }}","suggested_display_precision":0},
        "program_uptime": {"name":"Program Uptime","unit_of_measurement":"s","device_class":"duration","icon":"mdi:timer-cog-outline","state_topic":f"{DEVICE_SLUG}/system/program_uptime","entity_category":"diagnostic","value_template":"{{ value | int }}","suggested_display_precision":0},
    }

    binary_sensors = {
        "emmc_warning": {"name":"eMMC Warning","device_class":"problem","icon":"mdi:alert-outline","state_topic":f"{DEVICE_SLUG}/emmc/warn","payload_on":"ON","payload_off":"OFF"},
        "emmc_critical": {"name":"eMMC Critical","device_class":"problem","icon":"mdi:alert-circle-outline","state_topic":f"{DEVICE_SLUG}/emmc/crit","payload_on":"ON","payload_off":"OFF"},
    }

    # Publish core sensors
    for key, cfg in sensors.items():
        topic = f"{BASE_TOPIC}/sensor/{DEVICE_SLUG}/{key}/config"
        payload = {**cfg,"unique_id": f"{UNIQUE_ID_PREFIX}_{key}","device":device,"availability_topic":LWT_TOPIC,"payload_available":LWT_PAYLOAD_ONLINE,"payload_not_available":LWT_PAYLOAD_OFFLINE}
        client.publish(topic, json.dumps(payload), retain=True)

    # Publish diagnostic sensors
    for key, cfg in diagnostics.items():
        topic = f"{BASE_TOPIC}/sensor/{DEVICE_SLUG}/{key}/config"
        payload = {**cfg,"unique_id": f"{UNIQUE_ID_PREFIX}_{key}","device":device,"availability_topic":LWT_TOPIC,"payload_available":LWT_PAYLOAD_ONLINE,"payload_not_available":LWT_PAYLOAD_OFFLINE}
        client.publish(topic, json.dumps(payload), retain=True)

    # Publish binary sensors
    for key, cfg in binary_sensors.items():
        topic = f"{BASE_TOPIC}/binary_sensor/{DEVICE_SLUG}/{key}/config"
        payload = {**cfg,"unique_id": f"{UNIQUE_ID_PREFIX}_{key}","device":device,"availability_topic":LWT_TOPIC,"payload_available":LWT_PAYLOAD_ONLINE,"payload_not_available":LWT_PAYLOAD_OFFLINE}
        client.publish(topic, json.dumps(payload), retain=True)

    # CPU governor
    topic = f"{BASE_TOPIC}/sensor/{DEVICE_SLUG}/cpu_governor/config"
    client.publish(topic,json.dumps({"name":"CPU Governor","state_topic":f"{DEVICE_SLUG}/cpu/governor","unique_id":f"{UNIQUE_ID_PREFIX}_cpu_governor","device":device,"device_class":None,"icon":"mdi:tune","availability_topic":LWT_TOPIC,"payload_available":LWT_PAYLOAD_ONLINE,"payload_not_available":LWT_PAYLOAD_OFFLINE}),retain=True)

# -------------------- MAIN LOOP --------------------
def main():
    client = mqtt.Client(client_id=CLIENT_ID, callback_api_version=2)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.will_set(LWT_TOPIC, payload=LWT_PAYLOAD_OFFLINE, qos=1, retain=True)
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()
    client.publish(LWT_TOPIC, payload=LWT_PAYLOAD_ONLINE, retain=True)

    publish_discovery(client)

    while True:
        life, eol = get_emmc()
        disk_used = get_disk()
        root_fs_used, root_fs_free = get_root_fs_usage()
        mem_used, mem_free = get_mem()
        cpu_temp = get_cpu_temp()
        cpu_gov = get_cpu_governor()
        cpu_cur, cpu_min, cpu_max = get_cpu_freq()
        host_ip = get_host_ip()
        uptime_seconds = get_uptime_seconds()
        program_uptime_seconds = get_program_uptime_seconds()

        # Publish metrics
        if life is not None:
            client.publish(f"{DEVICE_SLUG}/emmc/life", life)
            client.publish(f"{DEVICE_SLUG}/emmc/warn", "ON" if life >= 70 or eol >= 2 else "OFF")
            client.publish(f"{DEVICE_SLUG}/emmc/crit", "ON" if life >= 90 or eol >= 3 else "OFF")

        client.publish(f"{DEVICE_SLUG}/disk/used", disk_used)
        if root_fs_used is not None:
            client.publish(f"{DEVICE_SLUG}/rootfs/used", root_fs_used)
        if root_fs_free is not None:
            client.publish(f"{DEVICE_SLUG}/rootfs/free", root_fs_free)
        client.publish(f"{DEVICE_SLUG}/mem/used", mem_used)
        client.publish(f"{DEVICE_SLUG}/mem/free", mem_free)

        # Diagnostics
        client.publish(f"{DEVICE_SLUG}/host/ip", host_ip)
        if uptime_seconds is not None:
            client.publish(f"{DEVICE_SLUG}/system/sys_uptime", uptime_seconds)
        if program_uptime_seconds is not None:
            client.publish(f"{DEVICE_SLUG}/system/program_uptime", program_uptime_seconds)

        if cpu_temp is not None:
            client.publish(f"{DEVICE_SLUG}/cpu/temp", round(cpu_temp,1))
        client.publish(f"{DEVICE_SLUG}/cpu/governor", cpu_gov)
        if cpu_cur is not None:
            client.publish(f"{DEVICE_SLUG}/cpu/freq", cpu_cur)
            client.publish(f"{DEVICE_SLUG}/cpu/freq_min", cpu_min)
            client.publish(f"{DEVICE_SLUG}/cpu/freq_max", cpu_max)

        client.publish(LWT_TOPIC, payload=LWT_PAYLOAD_ONLINE, retain=True)
        time.sleep(30)

if __name__ == "__main__":
    main()
